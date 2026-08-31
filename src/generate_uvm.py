"""
generate_uvm.py — Phase 1 UVM Environment Generator
Orchestrates all 8 sub-generators from a ProtocolSpec.
Writes all files using Python open() — never shell redirection (CAT-46518 lesson).
"""

import os
from pathlib import Path

from gen_pkg        import gen_pkg
from gen_interface  import gen_interface
from gen_agent      import gen_agent
from gen_scoreboard import gen_scoreboard
from gen_coverage   import gen_coverage
from gen_sva        import gen_sva
from gen_env        import gen_env
from gen_scripts    import gen_scripts


def generate_uvm_environment(proto_spec: dict, output_dir: str, llm) -> list:
    """
    Generate complete UVM environment from ProtocolSpec.
    Returns list of generated file paths.
    """
    name = proto_spec['sv_name']
    files = []

    # Create directory structure
    dirs = [
        output_dir,
        os.path.join(output_dir, 'rtl'),
        os.path.join(output_dir, 'tb'),
        os.path.join(output_dir, 'assertions'),
        os.path.join(output_dir, 'sim'),
        os.path.join(output_dir, 'sim', 'regress_logs'),
        os.path.join(output_dir, 'sim', 'cov_logs'),
        os.path.join(output_dir, 'docs'),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)

    generators = [
        ('pkg',        gen_pkg,        f'rtl/{name}_pkg.sv'),
        ('interface',  gen_interface,  f'tb/{name}_if.sv'),
        ('agent',      gen_agent,      f'tb/{name}_tb_pkg.sv'),
        ('scoreboard', gen_scoreboard, None),   # merged into tb_pkg
        ('coverage',   gen_coverage,   None),   # merged into tb_pkg
        ('sva',        gen_sva,        f'assertions/{name}_sva.sv'),
        ('env',        gen_env,        f'tb/tb_top.sv'),
        ('scripts',    gen_scripts,    f'sim/run.sh'),
    ]

    for gen_name, gen_fn, rel_path in generators:
        print(f"  ✦ {gen_name:<12}", end='', flush=True)
        try:
            result = gen_fn(proto_spec, output_dir, llm)
            if rel_path:
                fpath = os.path.join(output_dir, rel_path)
                _write_file(fpath, result)
                files.append(fpath)
                print(f" → {rel_path}")
            else:
                print(f" → (merged)")
        except Exception as e:
            print(f" ✗ FAILED: {e}")

    # regress.sh
    regress = gen_scripts(proto_spec, output_dir, llm, script='regress')
    _write_file(os.path.join(output_dir, 'sim', 'regress.sh'), regress)
    os.chmod(os.path.join(output_dir, 'sim', 'run.sh'), 0o755)
    os.chmod(os.path.join(output_dir, 'sim', 'regress.sh'), 0o755)

    print(f"\n  Generated {len(files)} files in {output_dir}/")
    return files


def _write_file(path: str, content: str):
    """Write file safely using Python open() — never shell redirection."""
    with open(path, 'w') as f:
        f.write(content)
