import sys, os, time, random
sys.path.insert(0, 'src')
os.environ['PATH'] = os.path.expanduser('~/.opencode/bin') + ':' + os.environ['PATH']

from llm_client import LLMClient
from all_modules import (run_simulation, merge_ucdb, analyze_coverage,
                         print_coverage_report, generate_test_sequences)
from checkpoint import run_checkpoint

output_dir = os.path.expanduser('~/uvmgen/output/axi4_slave')
proto_spec = {
    'name': 'AXI4_Slave', 'sv_name': 'axi4_slave',
    'description': 'AXI4 full slave',
    'channels': [
        {'name':'AR','direction':'master_to_slave','signals':{'arvalid':{'role':'valid'},'arready':{'role':'ready'},'araddr':{'role':'address'}}},
        {'name':'AW','direction':'master_to_slave','signals':{'awvalid':{'role':'valid'},'awready':{'role':'ready'},'awaddr':{'role':'address'}}},
        {'name':'W','direction':'master_to_slave','signals':{'wvalid':{'role':'valid'},'wready':{'role':'ready'},'wdata':{'role':'data'}}},
        {'name':'R','direction':'slave_to_master','signals':{'rvalid':{'role':'valid'},'rready':{'role':'ready'},'rdata':{'role':'data'}}},
        {'name':'B','direction':'slave_to_master','signals':{'bvalid':{'role':'valid'},'bready':{'role':'ready'}}},
    ],
    'transactions': [{'name':'read'},{'name':'write'}],
    'verification_challenges': ['back-pressure','burst handling'],
    'scoreboard_strategy': 'golden memory model',
    'coverage_key_states': ['read','write'],
    'sva_key_properties': ['VALID stable until READY'],
    'parameters': {'DATA_WIDTH':32,'ADDR_WIDTH':32}
}

llm = LLMClient()
start = time.time()
merged = os.path.join(output_dir,'sim','regress_logs','merged.ucdb')
cov_history = []
all_gaps = []
all_tests = []
all_exclusions = []
os.makedirs(os.path.join(output_dir,'sim','regress_logs'), exist_ok=True)

print("\n[ITER 0] Baseline...")
run_simulation(output_dir, proto_spec, 'axi4_slave_sanity_test', seed=1, iter_num=0)
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
        seed = random.randint(1,99999)
        print(f"  [SIM] {t['name']} seed={seed}")
        run_simulation(output_dir, proto_spec, t['name'], seed=seed, iter_num=iteration)

    merge_ucdb(output_dir)
    if os.path.exists(merged):
        cov = analyze_coverage(merged, output_dir)
        if cov:
            cov_history.append({'iter':iteration,**cov})
            prev = cov_history[-2] if len(cov_history)>1 else {}
            print_coverage_report(cov, iter_num=iteration, prev=prev)
            all_gaps = cov.get('gaps', [])

print(f"\n{'='*50}")
print(f"DONE — {int(time.time()-start)}s | Tests: {len(all_tests)}")
