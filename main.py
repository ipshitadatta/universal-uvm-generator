"""
Universal UVM Generator (UVMGen)
Inspired by Siemens EDA CAT-46518

Usage:
    python3 main.py
"""

import os
import sys
import time
import json
from pathlib import Path

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from discover_protocol import discover_protocol
from generate_uvm import generate_uvm_environment
from validate_compile import validate_compile
from fix_errors import fix_errors
from run_sim import run_simulation, merge_ucdb
from analyze_coverage import analyze_coverage, print_coverage_report
from gen_tests import generate_test_sequences
from checkpoint import run_checkpoint
from mentor_report import write_mentor_report
from llm_client import LLMClient

BANNER = """
╔══════════════════════════════════════════════════════════╗
║          Universal UVM Generator  v1.0                   ║
║          Inspired by Siemens EDA CAT-46518               ║
║                                                          ║
║  Any protocol. Any RTL. Complete UVM environment.        ║
╚══════════════════════════════════════════════════════════╝
"""

MAX_ITERS        = 20
CHECKPOINT_ITERS = {5, 10, 15, 20}
MAX_FIX_RETRIES  = 3
MAX_LLM_RETRIES  = 3

def get_protocol_description():
    """Interactive protocol discovery."""
    print("\nDescribe your protocol in plain English.")
    print("(Or type 'load <file>' to load a description from a file)")
    print("(Or type 'rtl <file>' to provide DUT RTL for analysis)")
    print("Press Enter twice when done.\n")

    lines = []
    while True:
        line = input("> ").rstrip()
        if line.lower().startswith('load '):
            fpath = line[5:].strip()
            try:
                with open(fpath) as f:
                    content = f.read()
                print(f"[INFO] Loaded {fpath}")
                return content, None
            except FileNotFoundError:
                print(f"[ERROR] File not found: {fpath}")
                continue
        if line.lower().startswith('rtl '):
            fpath = line[4:].strip()
            try:
                with open(fpath) as f:
                    rtl = f.read()
                print(f"[INFO] RTL loaded from {fpath}")
                # Get description too
                print("Also describe the protocol briefly:")
                desc = input("> ").strip()
                return desc, rtl
            except FileNotFoundError:
                print(f"[ERROR] File not found: {fpath}")
                continue
        if line == '' and lines:
            break
        lines.append(line)

    return '\n'.join(lines), None


def run_generation_phase(proto_spec, output_dir, llm):
    """Generate UVM environment and validate compilation."""
    print(f"\n[GEN] Generating UVM environment for: {proto_spec['name']}")

    files = generate_uvm_environment(proto_spec, output_dir, llm)

    print(f"\n[COMPILE] Validating with QuestaSim...")
    errors = validate_compile(output_dir, proto_spec)

    attempt = 0
    while errors and attempt < MAX_FIX_RETRIES:
        attempt += 1
        print(f"[FIX] {len(errors)} errors — LLM fix attempt {attempt}/{MAX_FIX_RETRIES}")
        fixed = fix_errors(errors, output_dir, proto_spec, llm)
        if not fixed:
            print(f"[WARN] LLM could not fix all errors on attempt {attempt}")
        errors = validate_compile(output_dir, proto_spec)

    if errors:
        print(f"\n[ERROR] {len(errors)} compile errors remain after {MAX_FIX_RETRIES} fix attempts.")
        print("  The generated environment may still be useful as a starting point.")
        print("  Check output/ directory for generated files.")
    else:
        print(f"\n[OK] Compilation successful — UVM environment is valid.")

    return len(errors) == 0


def run_coverage_loop(proto_spec, output_dir, llm):
    """20-iteration coverage-driven test generation loop."""
    print(f"\n[LOOP] Starting 20-iteration coverage loop...")
    print("=" * 60)

    start_time = time.time()
    coverage_history = []
    all_tests = []
    all_gaps = []
    all_exclusions = []

    # Initial simulation run
    print(f"\n[ITER 0] Running initial simulation...")
    ucdb_path = run_simulation(output_dir, proto_spec, test_name='uvmgen_base_test',
                               seed=12345, iter_num=0)
    if ucdb_path:
        cov = analyze_coverage(ucdb_path, output_dir)
        coverage_history.append({'iter': 0, 'coverage': cov})
        print_coverage_report(cov, iter_num=0)

    for iteration in range(1, MAX_ITERS + 1):
        print(f"\n{'='*60}")
        print(f"[ITER {iteration}/{MAX_ITERS}]  Elapsed: {elapsed_str(start_time)}")
        print(f"{'='*60}")

        # Get current coverage state
        last_cov = coverage_history[-1]['coverage'] if coverage_history else {}
        gaps = analyze_coverage(
            os.path.join(output_dir, 'sim', 'regress_logs', 'merged.ucdb'),
            output_dir
        ) if os.path.exists(os.path.join(output_dir, 'sim', 'regress_logs', 'merged.ucdb')) else {}

        all_gaps = gaps.get('gaps', [])

        # Generate test sequences
        if iteration <= 4:
            # Early iters: LLM generates broad test ideas
            print(f"[GEN] LLM generating 6 test ideas from protocol spec...")
            tests = generate_test_sequences(
                proto_spec=proto_spec,
                gaps=all_gaps,
                output_dir=output_dir,
                llm=llm,
                mode='broad',
                num_ideas=6,
                iteration=iteration
            )
        elif iteration <= 9:
            # Mid iters: targeted gap closure
            print(f"[GEN] Generating targeted sequences for top uncovered bins...")
            tests = generate_test_sequences(
                proto_spec=proto_spec,
                gaps=all_gaps,
                output_dir=output_dir,
                llm=llm,
                mode='targeted',
                num_ideas=3,
                iteration=iteration
            )
        elif iteration <= 14:
            # RTL white-box targeting
            print(f"[GEN] RTL white-box targeting — extracting RTL context from UCDB...")
            tests = generate_test_sequences(
                proto_spec=proto_spec,
                gaps=all_gaps,
                output_dir=output_dir,
                llm=llm,
                mode='whitebox',
                num_ideas=3,
                iteration=iteration
            )
        else:
            # Advanced: back-pressure + corner cases
            print(f"[GEN] Advanced corner-case + back-pressure sequences...")
            tests = generate_test_sequences(
                proto_spec=proto_spec,
                gaps=all_gaps,
                output_dir=output_dir,
                llm=llm,
                mode='advanced',
                num_ideas=4,
                iteration=iteration
            )

        all_tests.extend(tests)

        # Run generated tests
        for test in tests:
            import random
            seed = random.randint(1, 99999)
            print(f"  [SIM] Running {test['name']} (seed={seed})...")
            ucdb = run_simulation(
                output_dir=output_dir,
                proto_spec=proto_spec,
                test_name=test['name'],
                seed=seed,
                iter_num=iteration
            )

        # Merge UCDBs
        merge_ucdb(output_dir)

        # Analyze coverage delta
        merged_ucdb = os.path.join(output_dir, 'sim', 'regress_logs', 'merged.ucdb')
        if os.path.exists(merged_ucdb):
            cov = analyze_coverage(merged_ucdb, output_dir)
            coverage_history.append({'iter': iteration, 'coverage': cov})
            print_coverage_report(cov, iter_num=iteration,
                                  prev=coverage_history[-2]['coverage'] if len(coverage_history) > 1 else {})

        # Checkpoint every 5 iterations
        if iteration in CHECKPOINT_ITERS:
            user_ideas, user_exclusions = run_checkpoint(
                iteration=iteration,
                proto_spec=proto_spec,
                coverage_history=coverage_history,
                gaps=all_gaps,
                output_dir=output_dir,
                llm=llm
            )
            all_exclusions.extend(user_exclusions)

            # Generate sequences from user ideas
            if user_ideas:
                print(f"\n[GEN] Converting {len(user_ideas)} user ideas to SV sequences...")
                user_tests = generate_test_sequences(
                    proto_spec=proto_spec,
                    gaps=all_gaps,
                    output_dir=output_dir,
                    llm=llm,
                    mode='user_ideas',
                    user_ideas=user_ideas,
                    num_ideas=len(user_ideas),
                    iteration=iteration
                )
                all_tests.extend(user_tests)

            # Write mentor report every 5 iters
            write_mentor_report(
                proto_spec=proto_spec,
                output_dir=output_dir,
                iteration=iteration,
                coverage_history=coverage_history,
                all_tests=all_tests,
                all_gaps=all_gaps,
                all_exclusions=all_exclusions,
                elapsed=elapsed_str(start_time)
            )
            print(f"[REPORT] mentor_report.txt updated")

    # Final summary
    print(f"\n{'='*60}")
    print(f"[DONE] 20-iteration loop complete")
    print(f"       Total time: {elapsed_str(start_time)}")
    print(f"       Tests generated: {len(all_tests)}")
    print(f"       Exclusions applied: {len(all_exclusions)}")
    if coverage_history:
        final = coverage_history[-1]['coverage']
        print(f"\n  Final Coverage:")
        for metric, val in final.items():
            if metric != 'gaps':
                print(f"    {metric:15s}: {val:.1f}%")
    print(f"{'='*60}")

    return coverage_history, all_tests, all_exclusions


def elapsed_str(start_time):
    elapsed = int(time.time() - start_time)
    return f"{elapsed//60}m {elapsed%60}s"


def main():
    print(BANNER)

    # Initialize LLM client
    llm = LLMClient()
    print(f"[LLM] Using: {llm.backend}")

    # Get protocol description from user
    description, rtl_source = get_protocol_description()

    if not description.strip():
        print("[ERROR] No protocol description provided.")
        sys.exit(1)

    # Phase 0: Discover protocol
    print(f"\n[DISCOVER] Analyzing protocol description...")
    proto_spec = discover_protocol(description, rtl_source, llm)

    print(f"\n[DISCOVERED] Protocol: {proto_spec['name']}")
    print(f"  Channels:     {len(proto_spec.get('channels', []))}")
    print(f"  Transactions: {len(proto_spec.get('transactions', []))}")
    print(f"  Signals:      {sum(len(ch.get('signals', {})) for ch in proto_spec.get('channels', []))}")

    # Confirm with user
    print(f"\nDoes this look correct? (y/n/edit)")
    confirm = input("> ").strip().lower()
    if confirm == 'n':
        print("Please re-describe the protocol:")
        description, rtl_source = get_protocol_description()
        proto_spec = discover_protocol(description, rtl_source, llm)
    elif confirm == 'edit':
        print(f"Protocol spec (JSON):")
        print(json.dumps(proto_spec, indent=2))
        print("\nPaste corrected JSON (Enter blank line when done):")
        lines = []
        while True:
            line = input()
            if line == '' and lines:
                break
            lines.append(line)
        try:
            proto_spec = json.loads('\n'.join(lines))
        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON: {e}")

    # Set up output directory
    proto_name = proto_spec['name'].lower().replace(' ', '_').replace('-', '_')
    output_dir = os.path.join('output', proto_name)
    os.makedirs(output_dir, exist_ok=True)
    print(f"\n[OUT] Output directory: {output_dir}/")

    # Phase 1: Generate UVM environment
    compile_ok = run_generation_phase(proto_spec, output_dir, llm)

    if not compile_ok:
        print("\n[WARN] Generated environment has compile errors.")
        print("       Continue with coverage loop anyway? (y/n)")
        if input("> ").strip().lower() != 'y':
            print(f"Generated files are in: {output_dir}/")
            sys.exit(0)

    # Phase 2: 20-iteration coverage loop
    print(f"\nReady to start 20-iteration coverage loop.")
    print("Before starting, you can write test ideas to ideas.txt")
    print("They will be picked up at iteration 5.")
    print("\nStart coverage loop? (y/n)")
    if input("> ").strip().lower() != 'y':
        print(f"\nGenerated UVM environment: {output_dir}/")
        print("Run manually with: cd output/{proto_name}/sim && ./run.sh")
        sys.exit(0)

    coverage_history, all_tests, all_exclusions = run_coverage_loop(
        proto_spec, output_dir, llm
    )

    # Final report
    write_mentor_report(
        proto_spec=proto_spec,
        output_dir=output_dir,
        iteration=20,
        coverage_history=coverage_history,
        all_tests=all_tests,
        all_gaps=[],
        all_exclusions=all_exclusions,
        elapsed='complete',
        final=True
    )

    print(f"\n[DONE] UVM environment: {output_dir}/")
    print(f"[DONE] Mentor report:   {output_dir}/mentor_report.txt")
    print(f"[DONE] Run manually:    cd {output_dir}/sim && ./regress.sh 10")


if __name__ == '__main__':
    main()
