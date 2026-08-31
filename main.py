"""
Universal UVM Generator — main.py
==================================
Fully interactive coverage-driven UVM environment generator.
Inspired by Siemens EDA CAT-46518 (uvmf_agent).

Mode 1: Existing DUT RTL provided → analyze ports → generate UVM → 20-iter loop
Mode 2: Protocol description only → LLM generates UVM + DUT stub → 20-iter loop

20-Iteration Loop (exact CAT-46518 cadence):
  Iter 1-4:  LLM generates 6 test ideas → SV sequences → compile → run
  Iter 5:    CHECKPOINT — print full coverage, read ideas.txt, idea> prompt
  Iter 6-9:  Targeted gap closure
  Iter 10:   BACKTRACE — read manual sequences, LLM learns patterns
  Iter 11:   Continue gap closure with improved patterns
  Iter 12:   WHITE-BOX — parse UCDB file/line, extract RTL context
  Iter 13-14:Continue RTL-targeted
  Iter 15:   LLM RE-ANALYSIS — 5 corner-case sequences
  Iter 16-19:Advanced gap closure
  Iter 20:   FINAL — mentor report, exclusion candidates one-by-one
"""

import os
import sys
import time
import json
import subprocess
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from llm_client      import LLMClient
from discover_protocol import discover_protocol
from generate_uvm    import generate_uvm_environment
from validate_compile import validate_compile
from fix_errors      import fix_errors
from run_sim         import run_simulation, merge_ucdb
from analyze_coverage import analyze_coverage, classify_gaps
from gen_tests       import idea_to_sv_sequence, validate_sv_12rules

# ─── Constants (from CAT-46518) ───────────────────────────────
MAX_ITERS       = 20
MAX_FIX_RETRIES = 3
MAX_LLM_RETRIES = 3
CHECKPOINT_ITERS = {5, 10, 15, 20}
IDEAS_FILE      = os.path.join(os.path.dirname(__file__), 'ideas.txt')
REPORT_FILE     = os.path.join(os.path.dirname(__file__), 'mentor_report.txt')

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          Universal UVM Generator  v1.0                       ║
║          Inspired by Siemens EDA CAT-46518                   ║
╚══════════════════════════════════════════════════════════════╝
"""

# ─── Entry point ──────────────────────────────────────────────
def main():
    print(BANNER)

    # Initialize LLM
    llm = LLMClient()
    print(f"[LLM] Backend: {llm.backend}")
    print()

    # Mode selection — exact CAT-46518 prompt
    print("Select mode:")
    print("  1 = Existing DUT RTL  (provide RTL files, agent generates UVM VIP)")
    print("  2 = Protocol description only  (agent generates UVM + DUT stub)")
    print()

    while True:
        mode = input("Mode > ").strip()
        if mode in ('1', '2'):
            break
        print("  Enter 1 or 2")

    print()

    if mode == '1':
        proto_spec, output_dir = mode1_setup(llm)
    else:
        proto_spec, output_dir = mode2_setup(llm)

    if proto_spec is None:
        print("[ERROR] Setup failed. Exiting.")
        sys.exit(1)

    # Ask for ideas before starting (like CAT-46518)
    print()
    print("  TIP: Write test ideas to ideas.txt before iteration 5:")
    print(f"       {IDEAS_FILE}")
    print("  They will be read and converted to SV sequences automatically.")
    print()
    input("  Press Enter to start the 20-iteration coverage loop...")
    print()

    # Run the 20-iteration loop
    run_coverage_loop(proto_spec, output_dir, llm)


# ─── Mode 1: Existing DUT RTL ─────────────────────────────────
def mode1_setup(llm):
    print("═" * 60)
    print("  MODE 1: Existing DUT RTL")
    print("═" * 60)
    print()
    print("  Enter path(s) to your DUT RTL files (space-separated):")
    print("  Example: ~/my_proj/rtl/dut_top.sv ~/my_proj/rtl/dut_pkg.sv")
    print()

    rtl_input = input("  RTL > ").strip()
    rtl_paths = [os.path.expanduser(p) for p in rtl_input.split()]

    # Validate files exist
    missing = [p for p in rtl_paths if not os.path.exists(p)]
    if missing:
        print(f"\n  [ERROR] Files not found: {missing}")
        return None, None

    # Read RTL content
    print()
    print("  [PARSE] Reading RTL files...")
    rtl_content = ''
    for path in rtl_paths:
        with open(path) as f:
            rtl_content += f'\n// FILE: {os.path.basename(path)}\n' + f.read()

    # LLM analyzes RTL to extract protocol spec
    print("  [LLM]  Analyzing DUT ports and protocol...")
    proto_spec = discover_protocol(
        description=f"Analyze this RTL and extract the protocol: {' '.join(os.path.basename(p) for p in rtl_paths)}",
        rtl_source=rtl_content[:3000],
        llm=llm
    )

    print()
    print(f"  [DISCOVERED] Protocol: {proto_spec['name']}")
    print(f"    Channels:     {len(proto_spec.get('channels', []))}")
    print(f"    Transactions: {len(proto_spec.get('transactions', []))}")
    signals = sum(len(ch.get('signals', {})) for ch in proto_spec.get('channels', []))
    print(f"    Signals:      {signals}")
    print()

    # Set output directory
    name = proto_spec['sv_name']
    output_dir = os.path.join(os.path.dirname(__file__), 'output', name)
    os.makedirs(output_dir, exist_ok=True)

    # Copy RTL files to output
    rtl_dir = os.path.join(output_dir, 'rtl')
    os.makedirs(rtl_dir, exist_ok=True)
    import shutil
    for path in rtl_paths:
        shutil.copy2(path, rtl_dir)
    print(f"  [OK]   RTL copied to {rtl_dir}/")

    # Generate UVM environment
    _generate_and_validate(proto_spec, output_dir, llm)

    return proto_spec, output_dir


# ─── Mode 2: Protocol description only ───────────────────────
def mode2_setup(llm):
    print("═" * 60)
    print("  MODE 2: Protocol Description → Full UVM + DUT Stub")
    print("═" * 60)
    print()
    print("  Describe your protocol in plain English.")
    print("  Include: channel names, signal names, handshake type,")
    print("           transaction types, what correctness means.")
    print()
    print("  Examples:")
    print("    'AXI4 slave with 5 channels: AR, R, AW, W, B...'")
    print("    'UART TX: serialize 8-bit bytes at configurable baud rate...'")
    print("    'I2C master: START, 7-bit addr, R/W bit, ACK, data bytes, STOP'")
    print()
    print("  (Press Enter twice when done)")
    print()

    lines = []
    while True:
        line = input("  > ").rstrip()
        if line == '' and lines:
            break
        if line == '' and not lines:
            continue
        lines.append(line)

    description = '\n'.join(lines)

    print()
    print("  [LLM]  Analyzing protocol description...")

    proto_spec = discover_protocol(description, None, llm)

    print()
    print(f"  [DISCOVERED] Protocol: {proto_spec['name']}")
    print(f"    {proto_spec.get('description', '')}")
    print()
    print(f"    Channels ({len(proto_spec.get('channels', []))}):")
    for ch in proto_spec.get('channels', []):
        sigs = list(ch.get('signals', {}).keys())
        print(f"      {ch['name']:8s} [{ch.get('direction','?'):20s}] {', '.join(sigs[:4])}")
    print()
    print(f"    Transactions: {[t['name'] for t in proto_spec.get('transactions', [])]}")
    print()
    print(f"    Verification challenges:")
    for c in proto_spec.get('verification_challenges', [])[:3]:
        print(f"      • {c}")
    print()

    confirm = input("  Looks correct? (y/n/edit) > ").strip().lower()
    if confirm == 'n':
        print("  Re-describe the protocol:")
        return mode2_setup(llm)  # recurse

    # Set output directory
    name = proto_spec['sv_name']
    output_dir = os.path.join(os.path.dirname(__file__), 'output', name)
    os.makedirs(output_dir, exist_ok=True)

    # Generate UVM + DUT stub
    _generate_and_validate(proto_spec, output_dir, llm)

    return proto_spec, output_dir


# ─── Generate UVM and validate ────────────────────────────────
def _generate_and_validate(proto_spec, output_dir, llm):
    print()
    print("═" * 60)
    print("  GENERATING UVM ENVIRONMENT")
    print("═" * 60)

    files = generate_uvm_environment(proto_spec, output_dir, llm)

    print()
    print("  [COMPILE] Validating with QuestaSim...")
    errors = validate_compile(output_dir, proto_spec)

    attempt = 0
    while errors and attempt < MAX_FIX_RETRIES:
        attempt += 1
        print(f"  [FIX]   {len(errors)} errors — LLM fix attempt {attempt}/{MAX_FIX_RETRIES}")
        for e in errors[:3]:
            print(f"    Line {e.get('line','?')}: {e.get('message','')[:70]}")
        fixed = fix_errors(errors, output_dir, proto_spec, llm)
        errors = validate_compile(output_dir, proto_spec)

    if errors:
        print(f"\n  [WARN]  {len(errors)} compile errors remain after {MAX_FIX_RETRIES} attempts")
        print("          Continuing — some tests may not run correctly")
    else:
        print(f"  [OK]    Compilation successful — {len(files)} files generated")


# ─── 20-Iteration Coverage Loop ───────────────────────────────
def run_coverage_loop(proto_spec, output_dir, llm):
    name       = proto_spec['sv_name']
    sim_dir    = os.path.join(output_dir, 'sim')
    log_dir    = os.path.join(sim_dir, 'regress_logs')
    os.makedirs(log_dir, exist_ok=True)

    start_time    = time.time()
    cov_history   = []     # [{iter, stmts, branches, exprs, covergroups}]
    all_tests     = []     # [{name, idea, iter}]
    all_gaps      = []
    all_exclusions= []

    print()
    print("═" * 60)
    print(f"  20-ITERATION COVERAGE LOOP — {proto_spec['name']}")
    print(f"  Output: {output_dir}/")
    print("═" * 60)

    # ── Iteration 0: baseline simulation ──────────────────────
    print()
    print("  [ITER 0] Running baseline simulation...")
    ucdb = run_simulation(output_dir, proto_spec,
                          f'{name}_sanity_test', seed=12345, iter_num=0)
    if ucdb:
        merge_ucdb(output_dir)
        cov = _read_coverage(output_dir)
        cov_history.append({'iter': 0, **cov})
        _print_coverage(cov, iter_num=0, elapsed=_elapsed(start_time))

    # ── Main loop ─────────────────────────────────────────────
    for iteration in range(1, MAX_ITERS + 1):
        print()
        print(f"  {'─'*56}")
        print(f"  ITERATION {iteration:2d}/20   │   {_elapsed(start_time)} elapsed")
        print(f"  {'─'*56}")

        # Get current gaps
        merged = os.path.join(log_dir, 'merged.ucdb')
        if os.path.exists(merged):
            all_gaps = classify_gaps(merged, output_dir)

        # ── Determine what to do this iteration ───────────────
        if iteration <= 4:
            tests = _iter_broad(iteration, proto_spec, all_gaps, output_dir, llm)

        elif iteration == 5:
            # CHECKPOINT 5
            user_ideas = _checkpoint(iteration, proto_spec, cov_history,
                                     all_gaps, all_exclusions, output_dir, llm)
            tests = _ideas_to_tests(user_ideas, iteration, proto_spec, output_dir, llm)

        elif iteration <= 9:
            tests = _iter_targeted(iteration, proto_spec, all_gaps, output_dir, llm)

        elif iteration == 10:
            # BACKTRACE
            print(f"\n  [BACKTRACE] Reading manual sequences for pattern learning...")
            tests = _iter_backtrace(iteration, proto_spec, all_gaps, output_dir, llm)
            user_ideas = _checkpoint(iteration, proto_spec, cov_history,
                                     all_gaps, all_exclusions, output_dir, llm)
            tests += _ideas_to_tests(user_ideas, iteration, proto_spec, output_dir, llm)

        elif iteration == 12:
            # WHITE-BOX RTL targeting
            print(f"\n  [WHITEBOX] Parsing UCDB file:line numbers for RTL context...")
            tests = _iter_whitebox(iteration, proto_spec, all_gaps, output_dir, llm)

        elif iteration == 15:
            # LLM RE-ANALYSIS
            print(f"\n  [RE-ANALYSIS] LLM generating 5 corner-case sequences...")
            tests = _iter_reanalysis(iteration, proto_spec, all_gaps, output_dir, llm)
            user_ideas = _checkpoint(iteration, proto_spec, cov_history,
                                     all_gaps, all_exclusions, output_dir, llm)
            tests += _ideas_to_tests(user_ideas, iteration, proto_spec, output_dir, llm)

        elif iteration == 20:
            # FINAL
            tests = _iter_advanced(iteration, proto_spec, all_gaps, output_dir, llm)
            user_ideas = _checkpoint(iteration, proto_spec, cov_history,
                                     all_gaps, all_exclusions, output_dir, llm,
                                     final=True)
            tests += _ideas_to_tests(user_ideas, iteration, proto_spec, output_dir, llm)

        else:
            tests = _iter_targeted(iteration, proto_spec, all_gaps, output_dir, llm)

        all_tests.extend(tests)

        # ── Run generated tests ───────────────────────────────
        for test in tests:
            seed = random.randint(1, 99999)
            print(f"    [SIM] {test['name']}  seed={seed}")
            ucdb = run_simulation(output_dir, proto_spec,
                                  test['name'], seed=seed, iter_num=iteration)
            if ucdb:
                print(f"    [OK]  simulation complete")
            else:
                print(f"    [WARN] simulation failed or timed out")

        # ── Merge UCDBs and print coverage ────────────────────
        merge_ucdb(output_dir)
        cov = _read_coverage(output_dir)
        if cov:
            cov_history.append({'iter': iteration, **cov})
            prev = cov_history[-2] if len(cov_history) > 1 else {}
            _print_coverage(cov, iter_num=iteration, elapsed=_elapsed(start_time),
                            prev=prev)

        # ── Zero-delta reflection ─────────────────────────────
        if len(cov_history) >= 2:
            curr_total = _total_cov(cov_history[-1])
            prev_total = _total_cov(cov_history[-2])
            if curr_total - prev_total < 0.5 and iteration > 3:
                print(f"\n  [REFLECT] Coverage delta < 0.5% — LLM diagnosing why...")
                _zero_delta_reflect(proto_spec, all_gaps, output_dir, llm)

        # ── Write mentor report every 5 iters ─────────────────
        if iteration % 5 == 0:
            _write_mentor_report(proto_spec, output_dir, iteration,
                                 cov_history, all_tests, all_gaps,
                                 all_exclusions, _elapsed(start_time))
            print(f"\n  [REPORT] mentor_report.txt updated")

    # ── Final summary ─────────────────────────────────────────
    print()
    print("═" * 60)
    print(f"  LOOP COMPLETE — {_elapsed(start_time)}")
    print(f"  Tests generated: {len(all_tests)}")
    print(f"  Exclusions:      {len(all_exclusions)}")
    if cov_history:
        final = cov_history[-1]
        print(f"\n  Final Coverage:")
        print(f"    Statements:  {final.get('stmts', 0):.1f}%")
        print(f"    Branches:    {final.get('branches', 0):.1f}%")
        print(f"    Expressions: {final.get('exprs', 0):.1f}%")
        print(f"    Covergroups: {final.get('covergroups', 0):.1f}%")
    print()
    print(f"  UVM environment:  {output_dir}/")
    print(f"  Mentor report:    {REPORT_FILE}")
    print(f"  Run manually:     cd {output_dir}/sim && ./regress.sh 10")
    print("═" * 60)


# ─── Iteration strategies ─────────────────────────────────────

def _iter_broad(iteration, proto_spec, gaps, output_dir, llm):
    """Iters 1-4: LLM generates 6 broad test ideas."""
    print(f"\n  [LLM] Generating 6 test ideas from protocol spec...")

    prompt = f"""Protocol: {proto_spec['name']}
Description: {proto_spec.get('description', '')}
Verification challenges: {', '.join(proto_spec.get('verification_challenges', [])[:4])}

Generate 6 specific test ideas to verify this protocol.
Cover basic functionality and important corner cases.
Each idea is one sentence describing WHAT to send/do.
Return JSON array of 6 strings."""

    ideas = llm.call_json(prompt)
    if not isinstance(ideas, list):
        ideas = [f"Random {proto_spec['name']} transaction {i+1}" for i in range(6)]
    ideas = ideas[:6]

    print(f"\n  LLM test ideas:")
    for i, idea in enumerate(ideas, 1):
        print(f"    {i}. {idea}")

    return _ideas_to_tests(ideas, iteration, proto_spec, output_dir, llm)


def _iter_targeted(iteration, proto_spec, gaps, output_dir, llm):
    """Iters 6-9, 11, 13-14: targeted gap closure."""
    testable = [g for g in gaps if g.get('category') == 'needs_test'][:5]
    if not testable:
        return _iter_broad(iteration, proto_spec, gaps, output_dir, llm)

    print(f"\n  [LLM] Generating targeted sequences for {len(testable)} uncovered bins...")
    gaps_str = '\n'.join([f"  - {g.get('description', '')[:80]}" for g in testable])

    prompt = f"""Protocol: {proto_spec['name']}
Uncovered coverage bins:
{gaps_str}

Generate 3 test ideas to specifically hit these uncovered bins.
Return JSON array of 3 strings."""

    ideas = llm.call_json(prompt)
    if not isinstance(ideas, list):
        ideas = [f"Targeted test for gap {i+1}" for i in range(3)]
    ideas = ideas[:3]

    print(f"\n  Targeted ideas:")
    for i, idea in enumerate(ideas, 1):
        print(f"    {i}. {idea}")

    return _ideas_to_tests(ideas, iteration, proto_spec, output_dir, llm)


def _iter_backtrace(iteration, proto_spec, gaps, output_dir, llm):
    """Iter 10: read manual sequences, LLM learns pattern."""
    # Look for manually written sequences
    manual_dir = os.path.join(output_dir, 'manual_sequences')
    manual_seqs = []
    if os.path.exists(manual_dir):
        for f in os.listdir(manual_dir):
            if f.endswith('.sv'):
                with open(os.path.join(manual_dir, f)) as fp:
                    manual_seqs.append(fp.read()[:500])

    if manual_seqs:
        print(f"  Found {len(manual_seqs)} manual sequences — feeding to LLM as examples")
        prompt = f"""Protocol: {proto_spec['name']}
Here are working manual test sequences:
{chr(10).join(manual_seqs[:2])}

Generate 3 new sequences following the EXACT same pattern but with different values.
Return JSON array of 3 idea strings."""
    else:
        print(f"  No manual sequences found — using pattern inference")
        prompt = f"""Protocol: {proto_spec['name']}
Generate 3 back-pressure test ideas:
- Hold output ready low while driving input
- Stall for varying numbers of cycles
- Test maximum queue depth scenarios
Return JSON array of 3 strings."""

    ideas = llm.call_json(prompt)
    if not isinstance(ideas, list):
        ideas = ["Back-pressure test 1", "Back-pressure test 2", "Stall test"]
    return _ideas_to_tests(ideas[:3], iteration, proto_spec, output_dir, llm)


def _iter_whitebox(iteration, proto_spec, gaps, output_dir, llm):
    """Iter 12: parse UCDB file/line → extract RTL context → LLM targets."""
    merged = os.path.join(output_dir, 'sim', 'regress_logs', 'merged.ucdb')
    rtl_context = _extract_rtl_context(merged, output_dir)

    if rtl_context:
        print(f"  RTL context extracted — {len(rtl_context)} chars")
        prompt = f"""Protocol: {proto_spec['name']}
Uncovered RTL conditions:
{rtl_context[:800]}

Generate 3 test sequences specifically targeting these RTL conditions.
Return JSON array of 3 idea strings."""
    else:
        print(f"  No RTL context available — falling back to targeted")
        return _iter_targeted(iteration, proto_spec, gaps, output_dir, llm)

    ideas = llm.call_json(prompt)
    if not isinstance(ideas, list):
        ideas = ["RTL whitebox test 1", "RTL whitebox test 2", "Condition test"]
    return _ideas_to_tests(ideas[:3], iteration, proto_spec, output_dir, llm)


def _iter_reanalysis(iteration, proto_spec, gaps, output_dir, llm):
    """Iter 15: full re-analysis, 5 corner-case sequences."""
    all_testable = [g for g in gaps if g.get('category') == 'needs_test']
    gaps_str = '\n'.join([f"  - {g.get('description','')[:80]}" for g in all_testable[:10]])

    prompt = f"""Protocol: {proto_spec['name']}
After 14 iterations, these bins remain uncovered:
{gaps_str}

Generate 5 CORNER-CASE test ideas to close these stubborn gaps.
Think about: boundary values, concurrent transactions, protocol violations.
Return JSON array of 5 strings."""

    ideas = llm.call_json(prompt)
    if not isinstance(ideas, list):
        ideas = [f"Corner case {i+1}" for i in range(5)]
    ideas = ideas[:5]

    print(f"\n  Corner-case ideas:")
    for i, idea in enumerate(ideas, 1):
        print(f"    {i}. {idea}")

    return _ideas_to_tests(ideas, iteration, proto_spec, output_dir, llm)


def _iter_advanced(iteration, proto_spec, gaps, output_dir, llm):
    """Iters 16-20: back-pressure + boundary + concurrent."""
    prompt = f"""Protocol: {proto_spec['name']}
Generate 4 advanced test ideas:
1. Back-pressure: stall output while flooding input
2. Boundary: minimum and maximum field values simultaneously
3. Concurrent: multiple transactions in-flight at once
4. Stress: maximum rate sustained for 100+ cycles
Return JSON array of 4 strings."""

    ideas = llm.call_json(prompt)
    if not isinstance(ideas, list):
        ideas = ["Advanced test 1", "Advanced test 2", "Stress test", "Boundary test"]
    return _ideas_to_tests(ideas[:4], iteration, proto_spec, output_dir, llm)


# ─── Convert ideas to SV sequences ───────────────────────────

def _ideas_to_tests(ideas, iteration, proto_spec, output_dir, llm):
    """Convert list of idea strings to SV sequence files. Returns list of test dicts."""
    if not ideas:
        return []

    name    = proto_spec['sv_name']
    tb_dir  = os.path.join(output_dir, 'tb')
    tests   = []

    for i, idea in enumerate(ideas):
        if not idea or not isinstance(idea, str):
            continue

        test_name = f"{name}_gen_i{iteration:02d}_{i+1}"
        print(f"\n    [GEN] Converting idea to SV: '{idea[:55]}...'")

        sv_code = idea_to_sv_sequence(idea, test_name, proto_spec, llm)

        if sv_code:
            # 12-rule validation (CAT-46518 pattern)
            violations = validate_sv_12rules(sv_code, proto_spec)
            if violations:
                print(f"    [VAL] {len(violations)} rule violations — retrying...")
                sv_code = idea_to_sv_sequence(
                    idea + f". Fix: {'; '.join(violations[:2])}",
                    test_name, proto_spec, llm
                )

            if sv_code:
                fpath = os.path.join(tb_dir, f'{test_name}.sv')
                with open(fpath, 'w') as f:
                    f.write(sv_code)
                tests.append({'name': test_name, 'idea': idea, 'iter': iteration})
                print(f"    [OK]  {test_name}.sv written")
            else:
                print(f"    [SKIP] Could not generate valid SV after retry")
        else:
            print(f"    [SKIP] LLM returned empty response")

    return tests


# ─── Checkpoint (iters 5, 10, 15, 20) ────────────────────────

def _checkpoint(iteration, proto_spec, cov_history, gaps, exclusions,
                output_dir, llm, final=False):
    """
    Interactive checkpoint — exact CAT-46518 behavior:
    1. Print full coverage with all metrics
    2. Read ideas.txt if it exists
    3. Show idea> prompt for user input
    4. For final: present exclusions one-by-one
    """
    print()
    print(f"  {'═'*56}")
    if final:
        print(f"  FINAL CHECKPOINT — Iteration {iteration}/20")
    else:
        print(f"  CHECKPOINT — Iteration {iteration}/20")
    print(f"  {'═'*56}")

    # 1. Full coverage report
    if cov_history:
        curr = cov_history[-1]
        first = cov_history[0]
        print(f"\n  Coverage Summary:")
        print(f"  {'Metric':<15} {'Current':>8}  {'Start':>8}  {'Delta':>8}")
        print(f"  {'─'*45}")
        for metric, key in [('Statements','stmts'), ('Branches','branches'),
                             ('Expressions','exprs'), ('Covergroups','covergroups'),
                             ('Assertions','assertions')]:
            curr_v = curr.get(key, 0.0)
            first_v = first.get(key, 0.0)
            delta = curr_v - first_v
            bar = '█' * int(curr_v/5) + '░' * (20 - int(curr_v/5))
            print(f"  {metric:<15} {curr_v:7.1f}%  {first_v:7.1f}%  "
                  f"{'↑' if delta>=0 else '↓'}{abs(delta):.1f}%  [{bar}]")

    # 2. Gap summary
    testable     = [g for g in gaps if g.get('category') == 'needs_test']
    backpressure = [g for g in gaps if g.get('category') == 'back_pressure']
    unreachable  = [g for g in gaps if g.get('category') in
                    ('vip_config', 'unreachable', 'hls_internal')]

    print(f"\n  Uncovered bins: {len(gaps)} total")
    print(f"    needs_test:    {len(testable)}")
    print(f"    back_pressure: {len(backpressure)}")
    print(f"    unreachable:   {len(unreachable)}")

    if gaps:
        print(f"\n  Top uncovered bins:")
        for i, g in enumerate(gaps[:5], 1):
            cat = g.get('category', '?')
            desc = g.get('description', '')[:65]
            print(f"    {i}. [{cat}] {desc}")

    # 3. Read ideas.txt
    user_ideas = []
    if os.path.exists(IDEAS_FILE):
        with open(IDEAS_FILE) as f:
            file_ideas = [l.strip() for l in f if l.strip()]
        if file_ideas:
            print(f"\n  Found {len(file_ideas)} ideas in ideas.txt:")
            for i, idea in enumerate(file_ideas, 1):
                print(f"    {i}. {idea}")
            user_ideas.extend(file_ideas)
        os.unlink(IDEAS_FILE)  # consume the file (CAT-46518 behavior)
        print(f"  (ideas.txt consumed)")

    # 4. Interactive idea prompt (exact CAT-46518 idea> prompt)
    print(f"\n  Your test ideas — type one per line, blank line to finish:")
    print(f"  (Or press Enter to skip)")
    while True:
        try:
            line = input("  idea> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        user_ideas.append(line)
        print(f"    [+] Added: {line}")

    # 5. Exclusion candidates (at final checkpoint or iter >= 10)
    if (final or iteration >= 10) and unreachable:
        print(f"\n  {'─'*56}")
        print(f"  EXCLUSION CANDIDATES ({len(unreachable)} unreachable gaps)")
        print(f"  NOTE: Never auto-excluded. Each requires y/n + written reason.")
        print(f"  {'─'*56}")

        for gap in unreachable[:5]:  # max 5 at a time
            excl = _present_exclusion(gap, output_dir, llm)
            if excl:
                exclusions.append(excl)

    return user_ideas


# ─── Exclusion handling (exact CAT-46518 flow) ───────────────

def _present_exclusion(gap, output_dir, llm):
    """Present one exclusion candidate. User types y/n + reason."""
    signal = gap.get('signal', 'unknown')
    category = gap.get('category', 'unreachable')
    description = gap.get('description', '')

    # LLM generates justification
    prompt = f"""Signal: {signal}
Category: {category}
Gap: {description}
Write 2 sentences explaining why this coverage gap is unreachable and why exclusion is acceptable."""

    justification = llm.call(prompt) if hasattr(_present_exclusion, '_llm') else \
        f"This signal is not controllable via UVM stimulus. Excluding as unreachable."

    print(f"\n  ┌─ Exclusion Candidate {'─'*35}")
    print(f"  │  Signal:   {signal}")
    print(f"  │  Category: {category}")
    if description:
        print(f"  │  Gap:      {description[:60]}")
    print(f"  │")
    print(f"  │  Justification:")
    for line in justification.split('\n')[:2]:
        if line.strip():
            print(f"  │    {line.strip()[:70]}")
    print(f"  └{'─'*58}")

    answer = input("  Approve exclusion? (y/n): ").strip().lower()
    if answer != 'y':
        print(f"  [SKIP] Rejected")
        return None

    reason = input("  Written reason (required): ").strip()
    if not reason:
        print(f"  [SKIP] Reason required — not applied")
        return None

    # Apply via vcover — write to .do file (avoids shell metachar issues)
    _apply_exclusion_cmd(gap, reason, output_dir)
    print(f"  [OK]  Exclusion applied")

    return {'signal': signal, 'category': category, 'reason': reason}


def _apply_exclusion_cmd(gap, reason, output_dir):
    """Write exclusion .do file and run vcover."""
    merged = os.path.join(output_dir, 'sim', 'regress_logs', 'merged.ucdb')
    if not os.path.exists(merged):
        return

    do_file = os.path.join(output_dir, 'sim', '_excl.do')
    signal = gap.get('signal', '')
    line   = gap.get('line', '')
    category = gap.get('category', '')

    if 'vip' in category or 'covergroup' in gap.get('type', ''):
        cmd = f'vcover exclude -cvgpath "{signal}" -reason E -comment "{reason}" {merged}'
    elif line:
        cmd = f'vcover exclude -fecexprrow {line} -reason E -comment "{reason}" {merged}'
    else:
        return

    with open(do_file, 'w') as f:
        f.write(cmd + '\n')

    try:
        subprocess.run(['vsim', '-batch', '-do', f'do {do_file}; quit -f'],
                       capture_output=True, timeout=30)
    except Exception:
        pass


# ─── Zero-delta reflection ────────────────────────────────────

def _zero_delta_reflect(proto_spec, gaps, output_dir, llm):
    """If coverage didn't move, LLM diagnoses why."""
    # Read last simulation log
    log_dir = os.path.join(output_dir, 'sim', 'regress_logs')
    logs = sorted([f for f in os.listdir(log_dir) if f.endswith('.log')
                   if os.path.exists(os.path.join(log_dir, f))]) if os.path.exists(log_dir) else []

    last_log = ''
    if logs:
        with open(os.path.join(log_dir, logs[-1])) as f:
            lines = f.readlines()
            last_log = ''.join(lines[-40:])  # last 40 lines

    prompt = f"""Protocol: {proto_spec['name']}
Coverage didn't improve in the last iteration.
Last simulation output (last 40 lines):
{last_log[:600]}

Diagnose why coverage didn't move and suggest what to change.
Be specific — which signals, which values, which conditions."""

    diagnosis = llm.call(prompt, max_tokens=300)
    if diagnosis:
        print(f"\n  Diagnosis: {diagnosis[:200]}")


# ─── RTL context extraction ───────────────────────────────────

def _extract_rtl_context(ucdb_path, output_dir):
    """Extract RTL file:line context from UCDB for white-box targeting."""
    if not ucdb_path or not os.path.exists(ucdb_path):
        return ''

    try:
        result = subprocess.run(
            ['vcover', 'report', '-details', '-zeros', ucdb_path],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.join(output_dir, 'sim')
        )
        output = result.stdout

        # Extract file:line references
        import re
        refs = re.findall(r'(\S+\.sv):(\d+)', output)
        if not refs:
            return ''

        # Read RTL context around those lines
        context_parts = []
        rtl_dir = os.path.join(output_dir, 'rtl')
        seen = set()
        for fname, lnum in refs[:5]:
            key = f"{fname}:{lnum}"
            if key in seen:
                continue
            seen.add(key)
            fpath = os.path.join(rtl_dir, os.path.basename(fname))
            if os.path.exists(fpath):
                with open(fpath) as f:
                    lines = f.readlines()
                lnum = int(lnum) - 1
                start = max(0, lnum - 2)
                end   = min(len(lines), lnum + 3)
                snippet = ''.join(f"  {i+1}: {lines[i]}"
                                  for i in range(start, end))
                context_parts.append(f"// {fname}:{lnum+1}\n{snippet}")

        return '\n'.join(context_parts)
    except Exception:
        return ''


# ─── Coverage helpers ─────────────────────────────────────────

def _read_coverage(output_dir):
    """Read current merged UCDB coverage."""
    merged = os.path.join(output_dir, 'sim', 'regress_logs', 'merged.ucdb')
    if not os.path.exists(merged):
        return {}

    try:
        result = subprocess.run(
            ['vcover', 'report', '-details', merged],
            capture_output=True, text=True, timeout=60,
            cwd=os.path.join(output_dir, 'sim')
        )
        return _parse_coverage(result.stdout)
    except Exception:
        return {}


def _parse_coverage(output):
    """Parse vcover output into dict."""
    import re
    cov = {'stmts': 0.0, 'branches': 0.0, 'exprs': 0.0,
           'covergroups': 0.0, 'assertions': 0.0}

    mapping = {'Statement': 'stmts', 'Branch': 'branches',
               'Expression': 'exprs', 'Covergroup': 'covergroups',
               'Assertion': 'assertions'}

    for line in output.split('\n'):
        for word, key in mapping.items():
            if word in line:
                m = re.search(r'(\d+\.\d+)%', line)
                if m:
                    cov[key] = float(m.group(1))
    return cov


def _print_coverage(cov, iter_num, elapsed, prev=None):
    """Print formatted coverage bar chart."""
    if not cov:
        return
    print(f"\n  Coverage @ iter {iter_num}  ({elapsed}):")
    metrics = [('Statements', 'stmts'), ('Branches', 'branches'),
               ('Expressions', 'exprs'), ('Covergroups', 'covergroups')]
    for label, key in metrics:
        val   = cov.get(key, 0.0)
        delta = val - prev.get(key, 0.0) if prev else 0.0
        bar   = '█' * int(val/5) + '░' * (20 - int(val/5))
        d_str = f" (+{delta:.1f}%)" if delta > 0 else (f" ({delta:.1f}%)" if delta < 0 else "")
        print(f"    {label:<12} [{bar}] {val:5.1f}%{d_str}")


def _total_cov(cov_entry):
    """Average of all coverage metrics."""
    keys = ['stmts', 'branches', 'exprs', 'covergroups']
    vals = [cov_entry.get(k, 0.0) for k in keys]
    return sum(vals) / len(vals) if vals else 0.0


# ─── Mentor report ────────────────────────────────────────────

def _write_mentor_report(proto_spec, output_dir, iteration,
                          cov_history, all_tests, all_gaps,
                          all_exclusions, elapsed):
    """Write mentor_report.txt — exact CAT-46518 format."""
    name = proto_spec['name']

    traj = ['  Iter | Stmts  | Branches | Exprs  | Covergrps']
    traj.append('  ' + '─' * 48)
    for entry in cov_history:
        traj.append(
            f"  {entry['iter']:>4} | {entry.get('stmts',0):5.1f}% | "
            f"{entry.get('branches',0):7.1f}% | "
            f"{entry.get('exprs',0):5.1f}% | "
            f"{entry.get('covergroups',0):8.1f}%"
        )

    final = cov_history[-1] if cov_history else {}
    report = f"""{'='*65}
  UVM GENERATOR MENTOR REPORT — Iteration {iteration}/20
  Protocol: {name}
  Elapsed:  {elapsed}
{'='*65}

COVERAGE TRAJECTORY
{chr(10).join(traj)}

FINAL COVERAGE
  Statements:  {final.get('stmts', 0):.1f}%
  Branches:    {final.get('branches', 0):.1f}%
  Expressions: {final.get('exprs', 0):.1f}%
  Covergroups: {final.get('covergroups', 0):.1f}%

TESTS GENERATED: {len(all_tests)}
{chr(10).join(f"  [{t['iter']:>2}] {t['name']}: {t.get('idea','')[:55]}" for t in all_tests)}

REMAINING GAPS: {len(all_gaps)}
{chr(10).join(f"  [{g.get('category','?')}] {g.get('description','')[:60]}" for g in all_gaps[:10])}

EXCLUSIONS APPLIED: {len(all_exclusions)}
{chr(10).join(f"  {e.get('signal','?')}: {e.get('reason','')[:55]}" for e in all_exclusions)}

NEXT STEPS
  1. Connect your DUT in {output_dir}/tb/tb_top.sv
  2. Run: cd {output_dir}/sim && ./regress.sh 20
  3. Review: sim/regress_logs/coverage_html/index.html
  4. Add ideas to: {IDEAS_FILE}
{'='*65}
"""
    # Always use Python open() — never shell redirection (CAT-46518 rule)
    with open(REPORT_FILE, 'w') as f:
        f.write(report)

    # Also write to output dir
    out_report = os.path.join(output_dir, 'mentor_report.txt')
    with open(out_report, 'w') as f:
        f.write(report)


# ─── Time helper ──────────────────────────────────────────────

def _elapsed(start):
    s = int(time.time() - start)
    return f"{s//60}m {s%60}s"


if __name__ == '__main__':
    main()
