import glob
"""
validate_compile.py — QuestaSim compile + parse errors
fix_errors.py       — LLM error fixer with signature cache
run_sim.py          — run vsim, save UCDB, merge
analyze_coverage.py — parse vcover, classify gaps
gen_tests.py        — generate targeted test sequences
mentor_report.py    — write progress report
"""

import os
import re
import subprocess
import random
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# validate_compile.py
# ═══════════════════════════════════════════════════════════════

try:
    from config import UVM_HOME, UVM_DPI
except ImportError:
    UVM_HOME = "/mnt/apps/public/COE/mg_apps/questa2026.1/questasim/verilog_src/uvm-1.1d/src"
    UVM_DPI  = "/mnt/apps/public/COE/mg_apps/questa2026.1/questasim/uvm-1.1d/linux_x86_64/uvm_dpi"


def validate_compile(output_dir: str, proto_spec: dict) -> list:
    """
    Compile generated UVM environment with QuestaSim.
    Returns list of error dicts: {file, line, code, message}
    """
    name = proto_spec['sv_name']
    sim_dir = os.path.join(output_dir, 'sim')
    os.makedirs(sim_dir, exist_ok=True)

    # Clean work library
    work = os.path.join(sim_dir, 'work')
    if os.path.exists(work):
        subprocess.run(['rm', '-rf', work], capture_output=True)
    subprocess.run(['vlib', 'work'], cwd=sim_dir, capture_output=True)

    errors = []
    # Find all RTL files in rtl/ directory
    rtl_dir = os.path.join(os.path.dirname(sim_dir), 'rtl')
    rtl_files = sorted(glob.glob(os.path.join(rtl_dir, '*.sv')))
    # pkg first, then everything else
    pkg_files = [f for f in rtl_files if '_pkg.sv' in f]
    other_rtl  = [f for f in rtl_files if '_pkg.sv' not in f]
    # Make paths relative to sim_dir
    def rel(p): return '../rtl/' + os.path.basename(p)
    compile_steps = [
        ('all',    [rel(f) for f in pkg_files] +
                   [rel(f) for f in other_rtl] +
                   [f'../assertions/{name}_sva.sv',
                    f'{UVM_HOME}/uvm_pkg.sv',
                    f'../tb/{name}_if.sv',
                    f'../tb/{name}_tb_pkg.sv',
                    f'../tb/tb_top.sv',
                    f'+incdir+../rtl',
                    f'+incdir+../tb',
                    f'+incdir+{UVM_HOME}']),
    ]

    for step_name, args in compile_steps:
        cmd = ['vlog', '-sv', '-svinputport=net'] + args
        result = subprocess.run(
            cmd, cwd=sim_dir,
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout + result.stderr
        step_errors = _parse_vlog_errors(output, step_name)
        errors.extend(step_errors)
        if step_errors:
            print(f"    [{step_name}] {len(step_errors)} errors")
            for e in step_errors[:3]:
                print(f"      Line {e['line']}: {e['message'][:80]}")

    return errors


def _parse_vlog_errors(output: str, step: str) -> list:
    """Parse QuestaSim vlog error output into structured list."""
    errors = []
    # Pattern: ** Error: filename.sv(line): (vlog-NNNN) message
    pattern = re.compile(
        r'\*\* Error: ([^\(]+)\((\d+)\):\s*\(vlog-(\d+)\)\s*(.*)'
    )
    for line in output.split('\n'):
        m = pattern.search(line)
        if m:
            errors.append({
                'file':    m.group(1).strip(),
                'line':    int(m.group(2)),
                'code':    f'vlog-{m.group(3)}',
                'message': m.group(4).strip(),
                'step':    step,
                'raw':     line.strip()
            })
    return errors


# ═══════════════════════════════════════════════════════════════
# fix_errors.py
# ═══════════════════════════════════════════════════════════════

def fix_errors(errors: list, output_dir: str, proto_spec: dict, llm) -> bool:
    """
    LLM-based error fixer.
    Groups errors by file, sends context to LLM, applies patches.
    Returns True if any fixes were applied.
    """
    if not errors:
        return True

    # Group by file
    by_file = {}
    for err in errors:
        fname = err['file']
        by_file.setdefault(fname, []).append(err)

    any_fixed = False
    for fname, file_errors in by_file.items():
        # Skip package-not-found — always compile order, not a code fix
        file_errors = [e for e in file_errors
                      if 'not find the package' not in e.get('message','')]
        if not file_errors:
            continue
        # Find the actual file path
        fpath = _find_file(fname, output_dir)
        if not fpath or not os.path.exists(fpath):
            continue

        with open(fpath) as f:
            content = f.read()

        # Get context around error lines
        lines = content.split('\n')
        context_parts = []
        for err in file_errors[:3]:
            lnum = err['line'] - 1
            start = max(0, lnum - 3)
            end   = min(len(lines), lnum + 4)
            snippet = '\n'.join(
                f"{'>>>' if i == lnum else '   '} {i+1}: {lines[i]}"
                for i in range(start, end)
            )
            context_parts.append(
                f"Error {err['code']}: {err['message']}\n{snippet}"
            )

        context = '\n\n'.join(context_parts)[:200]  # trim for opencode
        file_type = os.path.basename(fpath).split('_')[-1].replace('.sv', '')

        # Try LLM fix
        fix = llm.fix_error(
            error_code=file_errors[0]['code'],
            file_type=file_type,
            context=context
        )

        if fix and 'old_str' in fix and fix['old_str'] in content:
            new_content = content.replace(fix['old_str'], fix['new_str'], 1)
            with open(fpath, 'w') as f:
                f.write(new_content)
            print(f"    [FIX] Applied to {os.path.basename(fpath)}: {fix.get('explanation','')[:60]}")
            any_fixed = True

    return any_fixed


def _find_file(fname: str, output_dir: str) -> Optional[str]:
    """Locate file in output directory tree."""
    basename = os.path.basename(fname)
    for root, dirs, files in os.walk(output_dir):
        if basename in files:
            return os.path.join(root, basename)
    return None


# ═══════════════════════════════════════════════════════════════
# run_sim.py
# ═══════════════════════════════════════════════════════════════

def run_simulation(output_dir: str, proto_spec: dict,
                   test_name: str, seed: int, iter_num: int) -> Optional[str]:
    """Run vsim with coverage, return UCDB path or None on failure."""
    name    = proto_spec['sv_name']
    sim_dir = os.path.join(output_dir, 'sim')
    log_dir = os.path.join(sim_dir, 'regress_logs')
    os.makedirs(log_dir, exist_ok=True)

    ucdb = os.path.join(log_dir, f'{test_name}_iter{iter_num}_seed{seed}.ucdb')
    log  = os.path.join(log_dir, f'{test_name}_iter{iter_num}_seed{seed}.log')

    cmd = [
        'vsim', '-batch', '-coverage',
        '-do', f'coverage save -onexit {ucdb}; run -all; quit -f',
        f'+UVM_TESTNAME={test_name}',
        '+UVM_VERBOSITY=UVM_LOW',
        f'-sv_seed', str(seed),
        '-sv_lib', UVM_DPI,
        'work.tb_top'
    ]

    try:
        result = subprocess.run(
            cmd, cwd=sim_dir,
            capture_output=True, text=True, timeout=300
        )
        with open(log, 'w') as f:
            f.write(result.stdout + result.stderr)

        # Check for actual failures — not just mentions of UVM_FATAL in log
        stdout = result.stdout
        has_fatal = ('UVM_FATAL :    0' not in stdout and 'UVM_FATAL' in stdout and 'UVM_FATAL :' not in stdout)
        has_error = 'Error loading' in stdout
        if has_fatal or has_error:
            return None

        return ucdb if os.path.exists(ucdb) else None
    except subprocess.TimeoutExpired:
        print(f"    [WARN] Simulation timeout after 300s")
        return None
    except FileNotFoundError:
        print(f"    [WARN] vsim not found — load questa module first")
        return None


def merge_ucdb(output_dir: str) -> Optional[str]:
    """Merge all UCDBs into merged.ucdb."""
    sim_dir = os.path.join(output_dir, 'sim')
    log_dir = os.path.join(sim_dir, 'regress_logs')
    merged  = os.path.join(log_dir, 'merged.ucdb')

    ucdb_files = [
        os.path.join(log_dir, f)
        for f in os.listdir(log_dir)
        if f.endswith('.ucdb') and f != 'merged.ucdb'
    ]
    if not ucdb_files:
        return None

    cmd = ['vcover', 'merge', merged] + ucdb_files
    try:
        subprocess.run(cmd, cwd=sim_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return merged if os.path.exists(merged) else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# ═══════════════════════════════════════════════════════════════
# analyze_coverage.py
# ═══════════════════════════════════════════════════════════════

def analyze_coverage(ucdb_path: str, output_dir: str) -> dict:
    """Parse vcover report, return coverage dict with gaps."""
    if not ucdb_path or not os.path.exists(ucdb_path):
        return {}

    sim_dir = os.path.join(output_dir, 'sim')

    try:
        result = subprocess.run(
            ['vcover', 'report', '-details', ucdb_path],
            cwd=sim_dir, capture_output=True, text=True, timeout=60
        )
        return _parse_vcover_output(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}


def _parse_vcover_output(output: str) -> dict:
    """Parse vcover output — extract DUT instance coverage."""
    cov = {
        'stmts': 0.0, 'branches': 0.0, 'exprs': 0.0,
        'covergroups': 0.0, 'assertions': 0.0, 'gaps': []
    }

    # Find DUT instance block (skip tb_top and pkg instances)
    lines = output.split('\n')
    in_dut = False
    for line in lines:
        if '=== Instance:' in line:
            in_dut = 'u_dut' in line or ('tb_top' not in line and 'tb_pkg' not in line and 'uvm' not in line.lower())
        if not in_dut:
            continue
        # Parse metric lines
        if 'Statements' in line and '%' in line:
            m = re.search(r'(\d+\.\d+)%', line)
            if m: cov['stmts'] = float(m.group(1))
        elif 'Branches' in line and '%' in line:
            m = re.search(r'(\d+\.\d+)%', line)
            if m: cov['branches'] = float(m.group(1))
        elif 'Expressions' in line and '%' in line:
            m = re.search(r'(\d+\.\d+)%', line)
            if m: cov['exprs'] = float(m.group(1))
        elif 'FSM States' in line and '%' in line:
            m = re.search(r'(\d+\.\d+)%', line)
            if m: cov['assertions'] = float(m.group(1))  # reuse assertions slot for FSM
        # Covergroups come from tb_pkg — find separately
    
    # Get covergroup coverage from any instance
    for line in lines:
        if 'Covergroup' in line and '%' in line and '===' not in line:
            m = re.search(r'(\d+\.\d+)%', line)
            if m:
                cov['covergroups'] = float(m.group(1))
                break

        # Look for uncovered lines (0 hits)
        if ' 0 ' in line and ('line' in line.lower() or 'bin' in line.lower()):
            cov['gaps'].append({
                'description': line.strip()[:100],
                'category': _classify_gap(line),
                'raw': line.strip()
            })
        # Expression gaps — No hits pattern (only top-level expression lines)
        if 'No hits' in line and 'rd_len' in line:
            cov['gaps'].append({
                'description': line.strip()[:100],
                'category': 'unreachable',
                'raw': line.strip()
            })

    return cov


def _classify_gap(line: str) -> str:
    """Classify coverage gap into category."""
    line_lower = line.lower()
    if any(k in line_lower for k in ['rd_len', 'expression', 'no hits', 'nba', 'rhs']):
        return 'unreachable'
    if any(k in line_lower for k in ['vip', 'bfm', 'protocol_', '_vip']):
        return 'vip_config'
    if any(k in line_lower for k in ['stall', 'backpressure', 'back_pressure', 'ready=0']):
        return 'back_pressure'
    if any(k in line_lower for k in ['internal', 'hls_', '_sched', '_stage']):
        return 'hls_internal'
    return 'needs_test'


def print_coverage_report(cov: dict, iter_num: int, prev: dict = None):
    """Print formatted coverage report."""
    if not cov:
        return
    print(f"\n  Coverage @ iter {iter_num}:")
    key_map = {
        'Statements': 'stmts', 'Branches': 'branches',
        'Expressions': 'exprs', 'Covergroups': 'covergroups', 'Assertions': 'assertions'
    }
    for metric in ['Statements', 'Branches', 'Expressions', 'Covergroups', 'Assertions']:
        key  = key_map[metric]
        val  = cov.get(key, cov.get(metric, 0.0))
        if prev:
            delta = val - prev.get(key, prev.get(metric, 0.0))
            delta_s = f"  ({'+' if delta >= 0 else ''}{delta:.1f}%)"
        else:
            delta_s = ''
        bar_len = int(val / 5)
        bar = '█' * bar_len + '░' * (20 - bar_len)
        print(f"    {metric:<12} [{bar}] {val:5.1f}%{delta_s}")
    gaps = cov.get('gaps', [])
    if gaps:
        print(f"    Uncovered bins: {len(gaps)}")


# ═══════════════════════════════════════════════════════════════
# gen_tests.py
# ═══════════════════════════════════════════════════════════════

def generate_test_sequences(proto_spec: dict, gaps: list, output_dir: str,
                             llm, mode: str, num_ideas: int = 6,
                             user_ideas: list = None, iteration: int = 0) -> list:
    """
    Generate targeted UVM test sequences and write to tb/ directory.
    Returns list of {name, file, description} dicts.
    """
    name   = proto_spec['sv_name']
    tb_dir = os.path.join(output_dir, 'tb')
    tests  = []

    if mode == 'broad':
        ideas = _llm_broad_ideas(proto_spec, llm, num_ideas)
    elif mode == 'targeted':
        ideas = _llm_targeted_ideas(proto_spec, gaps, llm, num_ideas)
    elif mode == 'whitebox':
        ideas = _llm_whitebox_ideas(proto_spec, gaps, llm, num_ideas)
    elif mode == 'advanced':
        ideas = _llm_advanced_ideas(proto_spec, gaps, llm, num_ideas)
    elif mode == 'user_ideas':
        ideas = user_ideas or []
    else:
        ideas = []

    for i, idea in enumerate(ideas):
        test_name = f"{name}_gen_iter{iteration}_{i+1}"
        from gen_tests import _fallback_sequence
        sv_code   = _fallback_sequence(test_name, idea, proto_spec)
        if sv_code:
            # Wrap sequence in a test class
            test_wrapper = f"""
class {test_name}_test extends {name}_base_test;
  `uvm_component_utils({test_name}_test)
  function new(string name="{test_name}_test", uvm_component parent=null);
    super.new(name, parent);
  endfunction
  task run_phase(uvm_phase phase);
    {test_name} seq;
    seq = {test_name}::type_id::create("seq");
    phase.raise_objection(this);
    seq.start(env.agent.seqr);
    repeat(200) @(posedge env.agent.drv.vif.clk);
    phase.drop_objection(this);
  endtask
endclass
"""
            full_sv = sv_code + test_wrapper
            fpath = os.path.join(tb_dir, f'{test_name}.sv')
            with open(fpath, 'w') as f:
                f.write(full_sv)

            # Compile into work library
            sim_dir = os.path.join(output_dir, 'sim')
            rtl_dir = os.path.join(output_dir, 'rtl')
            vlog_cmd = ['vlog', '-sv', '+cover=bcesf',
                        f'+incdir+{rtl_dir}', f'+incdir+{tb_dir}',
                        f'+incdir+{UVM_HOME}', fpath]
            subprocess.run(vlog_cmd, cwd=sim_dir,
                          capture_output=True, text=True, timeout=60)

            tests.append({'name': f'{test_name}_test', 'file': fpath, 'description': idea})
            print(f"    [+] {test_name}: {idea[:60]}")

    return tests


def _llm_broad_ideas(proto_spec: dict, llm, n: int) -> list:
    prompt = f"""Protocol: {proto_spec['name']}
Description: {proto_spec.get('description', '')}
Challenges: {', '.join(proto_spec.get('verification_challenges', [])[:4])}

Generate {n} broad test ideas to verify this protocol.
Ideas should cover basic and corner cases.
Return a JSON array of {n} strings."""
    result = llm.call_json(prompt)
    if isinstance(result, list) and result:
        return result[:n]
    # Protocol-agnostic fallback ideas based on transaction types
    txns = proto_spec.get('transactions', [])
    txn_names = [t.get('name','') for t in txns]
    ideas = []
    for txn in txn_names[:2]:
        ideas.extend([
            f"Send a basic {txn} transaction and verify response",
            f"Send back-to-back {txn} transactions with back-pressure",
            f"Send {txn} to boundary address and check response",
        ])
    if not ideas:
        ideas = [
            "Send a basic transaction and verify response",
            "Apply back-pressure on response channel for 10 cycles",
            "Send back-to-back transactions with random delays",
        ]
    return ideas[:n]


def _llm_targeted_ideas(proto_spec, gaps, llm, n):
    gaps_str = '\n'.join([g.get('description','')[:80] for g in gaps[:5]])
    prompt = f"""Protocol: {proto_spec['name']}
Uncovered gaps:
{gaps_str}

Generate {n} test ideas specifically targeting these gaps.
Return JSON array of {n} strings."""
    result = llm.call_json(prompt)
    if isinstance(result, list) and result:
        return result[:n]
    # Fallback ideas
    name = proto_spec['name']
    return [
        f"Back-pressure: hold BREADY low for 20 cycles during write",
        f"Read-after-write: write 0xDEADBEEF then read back same address",
        f"Burst: send INCR burst of length 4 with random data",
    ][:n]


def _llm_whitebox_ideas(proto_spec, gaps, llm, n):
    return _llm_targeted_ideas(proto_spec, gaps, llm, n)


def _llm_advanced_ideas(proto_spec, gaps, llm, n):
    prompt = f"""Protocol: {proto_spec['name']}
Generate {n} advanced corner-case tests:
- Back-pressure scenarios
- Boundary value tests
- Concurrent/simultaneous transactions
Return JSON array of {n} strings."""
    result = llm.call_json(prompt)
    return result[:n] if isinstance(result, list) else []


def _idea_to_sv_sequence(idea: str, test_name: str,
                          proto_spec: dict, llm) -> str:
    """Convert test idea string to SV sequence class."""
    name    = proto_spec['sv_name']
    signals = []
    for ch in proto_spec.get('channels', []):
        signals.extend(list(ch.get('signals', {}).keys())[:3])

    # Trim idea to avoid opencode timeout
    short_idea = idea[:60] if len(idea) > 60 else idea
    sig_str = ', '.join(signals[:4])
    prompt = f"""Write UVM sequence class {test_name} extends {name}_base_seq.
Seq item: {name}_seq_item. Test: {short_idea}.
Use @(posedge vif.clk), min 2 transactions, `uvm_object_utils.
Return ONLY class definition."""

    result = llm.call(prompt, max_tokens=600)
    if not result:
        return None

    # Wrap in package context if needed
    if 'endclass' not in result:
        return None

    return f"""// Generated test: {idea}
// Universal UVM Generator — iter sequence
import {name}_tb_pkg::*;

{result}
"""


# ═══════════════════════════════════════════════════════════════
# mentor_report.py
# ═══════════════════════════════════════════════════════════════

def write_mentor_report(proto_spec: dict, output_dir: str, iteration: int,
                         coverage_history: list, all_tests: list,
                         all_gaps: list, all_exclusions: list,
                         elapsed: str, final: bool = False) -> str:
    """Write mentor_report.txt to output directory."""
    name = proto_spec['name']

    # Coverage trajectory table
    traj_lines = ['  Iter | Stmts  | Branches | Exprs  | Covergrps']
    traj_lines.append('  ' + '-' * 50)
    for entry in coverage_history[-10:]:  # last 10 iters
        it  = entry['iter']
        cov = entry.get('coverage', {})
        traj_lines.append(
            f"  {it:>4} | {cov.get('Statements',0):5.1f}% | "
            f"{cov.get('Branches',0):7.1f}% | "
            f"{cov.get('Expressions',0):5.1f}% | "
            f"{cov.get('Covergroups',0):8.1f}%"
        )

    # Final coverage
    final_cov = coverage_history[-1]['coverage'] if coverage_history else {}

    report = f"""{'='*65}
  UNIVERSAL UVM GENERATOR — {'FINAL ' if final else ''}MENTOR REPORT
  Protocol: {name}
  Iteration: {iteration}/20
  Elapsed: {elapsed}
{'='*65}

GENERATED ENVIRONMENT
  Output directory: {output_dir}/
  Files: {name}_pkg.sv, {name}_if.sv, {name}_tb_pkg.sv,
         assertions/{name}_sva.sv, tb/tb_top.sv,
         sim/run.sh, sim/regress.sh

COVERAGE TRAJECTORY
{chr(10).join(traj_lines)}

FINAL COVERAGE
  Statements:  {final_cov.get('Statements',  0):.1f}%
  Branches:    {final_cov.get('Branches',    0):.1f}%
  Expressions: {final_cov.get('Expressions', 0):.1f}%
  Covergroups: {final_cov.get('Covergroups', 0):.1f}%

TESTS GENERATED: {len(all_tests)}
{chr(10).join(f'  [{i+1:>3}] {t["name"]}: {t.get("description","")[:60]}' for i,t in enumerate(all_tests))}

GAPS IDENTIFIED: {len(all_gaps)}
{chr(10).join(f'  [{g.get("category","?")}] {g.get("description","")[:60]}' for g in all_gaps[:10])}

EXCLUSIONS APPLIED: {len(all_exclusions)}
{chr(10).join(f'  {e.get("signal","?")} ({e.get("category","?")}): {e.get("reason","")[:60]}' for e in all_exclusions)}

RECOMMENDED NEXT STEPS
  1. Connect your real DUT in tb/tb_top.sv (replace TODO stub)
  2. Run: cd {output_dir}/sim && ./regress.sh 20
  3. Review HTML coverage: sim/regress_logs/coverage_html/index.html
  4. Address remaining {len(all_gaps)} gaps or justify exclusions

{'='*65}
"""

    # Write using Python open() — never shell redirection (CAT-46518 lesson)
    report_path = os.path.join(output_dir, 'mentor_report.txt')
    with open(report_path, 'w') as f:
        f.write(report)

    return report_path
