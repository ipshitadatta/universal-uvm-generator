#!/usr/bin/env python3
"""
run_loop.py — Protocol-agnostic 20-iteration coverage loop.
Usage: python3 run_loop.py <protocol_name>
Example: python3 run_loop.py axi4_slave
         python3 run_loop.py i2c_master
"""
import sys, os, time, random, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
os.environ['PATH'] = os.path.expanduser('~/.opencode/bin') + ':' + os.environ['PATH']

from llm_client import LLMClient
from all_modules import (run_simulation, merge_ucdb, analyze_coverage,
                         print_coverage_report, generate_test_sequences)
from checkpoint import run_checkpoint

# Load protocol
protocol = sys.argv[1] if len(sys.argv) > 1 else 'axi4_slave'
output_dir = os.path.expanduser(f'~/uvmgen/output/{protocol}')
spec_file  = os.path.join(output_dir, 'proto_spec.json')

if not os.path.exists(spec_file):
    print(f"ERROR: No proto_spec.json found at {spec_file}")
    print(f"Run main.py first to generate the environment.")
    sys.exit(1)

with open(spec_file) as f:
    proto_spec = json.load(f)

print(f"Protocol: {proto_spec['name']} | Output: {output_dir}")

llm = LLMClient()
print(f"LLM backend: {llm.backend}")
start = time.time()
merged = os.path.join(output_dir, 'sim', 'regress_logs', 'merged.ucdb')
cov_history = []
all_gaps = []
all_tests = []
all_exclusions = []

# Baseline
sanity_test = f"{proto_spec['sv_name']}_sanity_test"
print(f"\n[ITER 0] Baseline ({sanity_test})...")
run_simulation(output_dir, proto_spec, sanity_test, seed=1, iter_num=0)
merge_ucdb(output_dir)
if os.path.exists(merged):
    cov = analyze_coverage(merged, output_dir)
    cov_history.append({'iter':0,**cov})
    print_coverage_report(cov, iter_num=0)
    all_gaps = cov.get('gaps', [])

for iteration in range(1, 21):
    print(f"\n{'─'*50}")
    print(f"ITERATION {iteration:2d}/20  ({int(time.time()-start)}s)")
    print(f"{'─'*50}")
    mode = 'broad' if iteration <= 4 else 'targeted'

    if iteration in {5, 10, 15, 20}:
        user_ideas, excls = run_checkpoint(
            iteration, proto_spec, cov_history, all_gaps,
            output_dir, llm, exclusions=all_exclusions,
            final=(iteration==20))
        all_exclusions.extend(excls)
        if user_ideas:
            extra = generate_test_sequences(proto_spec, all_gaps, output_dir,
                llm, 'user_ideas', user_ideas=user_ideas,
                num_ideas=len(user_ideas), iteration=iteration)
            all_tests.extend(extra)

    tests = generate_test_sequences(proto_spec, all_gaps, output_dir,
        llm, mode, num_ideas=6 if mode=='broad' else 3,
        iteration=iteration)
    all_tests.extend(tests)

    for t in tests:
        seed = random.randint(1, 99999)
        print(f"  [SIM] {t['name']} seed={seed}")
        run_simulation(output_dir, proto_spec, t['name'], seed=seed, iter_num=iteration)

    merge_ucdb(output_dir)
    if os.path.exists(merged):
        cov = analyze_coverage(merged, output_dir)
        if cov:
            cov_history.append({'iter':iteration,**cov})
            prev = cov_history[-2] if len(cov_history) > 1 else {}
            print_coverage_report(cov, iter_num=iteration, prev=prev)
            all_gaps = cov.get('gaps', [])

print(f"\n{'='*50}")
print(f"DONE — {int(time.time()-start)}s | Tests: {len(all_tests)}")
