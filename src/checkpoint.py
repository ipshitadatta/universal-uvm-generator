"""
Checkpoint — fires at iterations 5, 10, 15, 20
Shows full coverage report, reads ideas.txt, asks user for ideas,
generates sequences from user ideas via LLM.
Identical cadence to CAT-46518 checkpoint.py.
"""

import os
import time
from analyze_coverage import print_coverage_report, classify_gaps


CHECKPOINT_BANNER = """
╔══════════════════════════════════════════════════════════╗
║  CHECKPOINT — Iteration {iter:2d}/20  │  {elapsed:>10s} elapsed  ║
╚══════════════════════════════════════════════════════════╝"""


def run_checkpoint(iteration, proto_spec, coverage_history, gaps, output_dir, llm):
    """
    Run interactive checkpoint.
    Returns: (user_ideas, approved_exclusions)
    """
    elapsed = _elapsed(coverage_history)
    print(CHECKPOINT_BANNER.format(iter=iteration, elapsed=elapsed))

    # 1. Print full coverage report
    if coverage_history:
        curr = coverage_history[-1]['coverage']
        prev = coverage_history[0]['coverage'] if len(coverage_history) > 1 else {}
        print(f"\n  Protocol:  {proto_spec['name']}")
        print(f"  Coverage trajectory:")
        print(f"  {'Metric':<15} {'Current':>8}  {'Start':>8}  {'Delta':>8}")
        print(f"  {'-'*45}")
        for metric in ['Statements', 'Branches', 'Expressions', 'Covergroups', 'Assertions']:
            curr_val = curr.get(metric, 0.0)
            prev_val = prev.get(metric, 0.0)
            delta = curr_val - prev_val
            delta_str = f"+{delta:.1f}%" if delta >= 0 else f"{delta:.1f}%"
            print(f"  {metric:<15} {curr_val:>7.1f}%  {prev_val:>7.1f}%  {delta_str:>8}")

    # 2. Show uncovered bins
    print(f"\n  Uncovered bins ({len(gaps)} total):")
    testable = [g for g in gaps if g.get('category') == 'needs_test']
    backpressure = [g for g in gaps if g.get('category') == 'back_pressure']
    unreachable = [g for g in gaps if g.get('category') in ('vip_config', 'unreachable', 'hls_internal')]

    for i, gap in enumerate(gaps[:8], 1):
        cat = gap.get('category', 'unknown')
        desc = gap.get('description', gap.get('signal', 'unknown'))
        print(f"    {i}. [{cat}] {desc}")
    if len(gaps) > 8:
        print(f"    ... and {len(gaps)-8} more")

    # 3. LLM generates ideas for uncovered bins
    if testable or backpressure:
        print(f"\n  LLM generating test ideas for {len(testable)} testable gaps...")
        llm_ideas = _llm_generate_ideas(proto_spec, testable[:5], backpressure[:3], llm)
        if llm_ideas:
            print(f"\n  LLM test ideas:")
            for i, idea in enumerate(llm_ideas, 1):
                print(f"    {i}. {idea}")

    # 4. Read ideas.txt if it exists
    ideas_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ideas.txt')
    file_ideas = []
    if os.path.exists(ideas_file):
        with open(ideas_file) as f:
            file_ideas = [l.strip() for l in f if l.strip()]
        if file_ideas:
            print(f"\n  Found {len(file_ideas)} ideas in ideas.txt:")
            for i, idea in enumerate(file_ideas, 1):
                print(f"    {i}. {idea}")
        # Delete after reading (CAT-46518 behavior)
        os.unlink(ideas_file)
        print(f"  (ideas.txt consumed)")

    # 5. Interactive idea prompt
    print(f"\n  Your test ideas — describe what to test (Enter blank line when done):")
    print(f"  (Or press Enter to skip)")
    user_ideas = list(file_ideas)  # start with file ideas

    while True:
        try:
            line = input("  idea> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        user_ideas.append(line)
        print(f"    [+] Added: {line}")

    # 6. Handle exclusion candidates (unreachable coverage)
    approved_exclusions = []
    if unreachable and iteration >= 10:
        print(f"\n  Exclusion candidates ({len(unreachable)} unreachable gaps):")
        print(f"  NOTE: Exclusions require explicit approval + written reason.")
        print(f"        Never auto-excluded (CAT-46518 golden rule).\n")

        for gap in unreachable[:3]:  # Show max 3 at a time
            approved = _handle_exclusion(gap, output_dir, llm)
            if approved:
                approved_exclusions.append(approved)

    print(f"\n  Checkpoint complete. Continuing coverage loop...")
    return user_ideas, approved_exclusions


def _llm_generate_ideas(proto_spec, testable_gaps, backpressure_gaps, llm):
    """LLM generates 6 test ideas from gaps."""
    if not (testable_gaps or backpressure_gaps):
        return []

    gaps_desc = '\n'.join([
        f"- {g.get('description', g.get('signal', '?'))} [{g.get('category', '?')}]"
        for g in (testable_gaps + backpressure_gaps)[:8]
    ])

    prompt = f"""Protocol: {proto_spec['name']}
Description: {proto_spec.get('description', '')}

Uncovered coverage gaps:
{gaps_desc}

Generate 6 specific test ideas to close these gaps.
Each idea should be one sentence describing WHAT to send/do to hit the gap.
Respond with a JSON array of 6 strings.
Example: ["Send AWLEN=255 to hit max burst length bin", "Hold RREADY low for 10 cycles to test back-pressure"]"""

    result = llm.call_json(prompt)
    if isinstance(result, list):
        return result[:6]
    if isinstance(result, dict) and 'ideas' in result:
        return result['ideas'][:6]
    return []


def _handle_exclusion(gap, output_dir, llm):
    """Present one exclusion candidate for user approval."""
    signal = gap.get('signal', 'unknown')
    category = gap.get('category', 'unreachable')
    description = gap.get('description', '')

    # LLM generates justification
    prompt = f"""Signal: {signal}
Coverage category: {category}
Gap: {description}

Write a 2-sentence technical justification for excluding this coverage gap.
Explain: (1) why the signal/condition is unreachable, (2) why exclusion is acceptable."""

    justification = llm.call(prompt)

    print(f"  ┌─ Exclusion Candidate ───────────────────────────────")
    print(f"  │  Signal:    {signal}")
    print(f"  │  Category:  {category}")
    if description:
        print(f"  │  Gap:       {description}")
    print(f"  │")
    print(f"  │  Justification:")
    for line in justification.split('\n')[:3]:
        if line.strip():
            print(f"  │    {line.strip()}")
    print(f"  └────────────────────────────────────────────────────")

    answer = input("  Approve exclusion? (y/n): ").strip().lower()
    if answer != 'y':
        print(f"  [SKIP] Exclusion rejected for {signal}")
        return None

    reason = input("  Written reason (required): ").strip()
    if not reason:
        print(f"  [SKIP] Reason required — exclusion not applied")
        return None

    # Apply exclusion via vcover
    _apply_exclusion(gap, reason, output_dir)
    print(f"  [OK] Exclusion applied: {signal}")

    return {
        'signal': signal,
        'category': category,
        'reason': reason,
        'justification': justification
    }


def _apply_exclusion(gap, reason, output_dir):
    """Apply coverage exclusion to current UCDB."""
    import subprocess

    ucdb = os.path.join(output_dir, 'sim', 'regress_logs', 'merged.ucdb')
    if not os.path.exists(ucdb):
        print(f"  [WARN] No UCDB found — exclusion not applied")
        return

    category = gap.get('category', '')
    signal = gap.get('signal', '')
    line = gap.get('line', '')

    if category == 'vip_config':
        # Covergroup exclusion
        cmd = f'vcover exclude -cvgpath "{signal}" -reason E -comment "{reason}" {ucdb}'
    elif line:
        # Expression/branch exclusion
        cmd = f'vcover exclude -fecexprrow {line} -reason E -comment "{reason}" {ucdb}'
    else:
        cmd = f'vcover exclude -fecexprrow 0 -reason E -comment "{reason}" {ucdb}'

    try:
        # Write to .do file — avoid shell metacharacter issues (CAT-46518 lesson)
        do_file = os.path.join(output_dir, 'sim', '_excl.do')
        with open(do_file, 'w') as f:
            f.write(cmd + '\n')
        subprocess.run(['vsim', '-batch', '-do', f'do {do_file}; quit -f'],
                       capture_output=True, timeout=30)
    except Exception as e:
        print(f"  [WARN] Exclusion command failed: {e}")


def _elapsed(coverage_history):
    """Get elapsed time string."""
    if not coverage_history:
        return '0m 0s'
    # coverage_history entries don't track timestamps — use iteration count
    iters = len(coverage_history)
    return f"~{iters * 2}m"
