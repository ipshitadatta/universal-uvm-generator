# UVMGen — Universal UVM Generator

An agentic Python tool that generates complete, compilable UVM verification environments from plain English protocol descriptions — and runs a 20-iteration coverage-closure loop against your DUT.

Directly inspired by work done at **Siemens EDA** (CAT-46518) during a summer 2026 internship: same iterative coverage loop, same checkpoint cadence, same exclusion workflow requiring written technical justification.

---

## What it does

**Mode 1 — Verify an existing RTL DUT:**
```
You: "AXI4 slave with 5 channels: AR, R, AW, W, B"
UVMGen: generates pkg, interface, driver, monitor, scoreboard, coverage, SVA, tb_top
QuestaSim: Errors: 0, UVM_ERROR: 0, TEST PASSED
Loop: 20-iteration coverage closure with interactive checkpoints
```

**Mode 2 — RTL-free verification:**
```
You: "I2C slave with SDA/SCL, 7-bit address, ACK/NACK"
UVMGen: generates full UVM environment + behavioral DUT stub
QuestaSim: Errors: 0, TEST PASSED — verify before RTL exists
```

---

## Coverage results

### AXI4 slave (Mode 1, real DUT)
| Metric | Coverage |
|--------|----------|
| Statements | 92.6% |
| Branches | 77.4% |
| FSM States | 100.0% |
| Covergroups | 100.0% |
| Expressions | 100.0% (1 excluded with written justification) |

### I2C slave (Mode 2, generated DUT stub)
| Metric | Coverage |
|--------|----------|
| Statements | 100.0% |
| Branches | 100.0% |
| Covergroups | 100.0% |

---

## Quick start

```bash
# Generate environment and run loop
python3 main.py

# Run coverage loop for an existing protocol
python3 run_loop.py axi4_slave
python3 run_loop.py i2c_slave
```

### Requirements
- Python 3.9+
- QuestaSim 2026.1 (`module load questa` on NC State grendel)
- UVM 1.1d (bundled with QuestaSim)
- An LLM backend (see below)

---

## How it works

```
Plain English description
        ↓
  discover_protocol.py     ← LLM extracts channels, signals, transactions
        ↓
  generate_uvm.py          ← 8-component UVM environment
  gen_dut_stub.py          ← behavioral RTL stub (Mode 2 only)
        ↓
  validate_compile.py      ← QuestaSim compile, LLM fixes errors (3 retries)
        ↓
  run_loop.py              ← 20-iteration coverage loop
        ↓
  checkpoint.py            ← interactive idea> prompt at iter 5/10/15/20
        ↓
  all_modules.py           ← coverage analysis, gap classification, exclusions
```

### Generated files (per protocol)
```
output/<protocol>/
├── rtl/
│   ├── <protocol>_pkg.sv          ← types, enums, structs
│   └── <protocol>.sv              ← behavioral DUT stub (Mode 2)
├── tb/
│   ├── <protocol>_if.sv           ← interface with clocking blocks
│   └── <protocol>_tb_pkg.sv       ← driver, monitor, scoreboard, coverage, tests
├── assertions/
│   └── <protocol>_sva.sv          ← SVA properties
├── sim/
│   ├── tb_top.sv                  ← top-level testbench
│   └── run.sh                     ← compile + simulate script
└── proto_spec.json                ← discovered protocol spec
```

---

## The 20-iteration coverage loop

```
ITERATION  5/20  (211s)
──────────────────────────────────────────────────

╔══════════════════════════════════════════════════╗
║  CHECKPOINT — Iteration 5/20  │  ~10m elapsed  ║
╚══════════════════════════════════════════════════╝

  Protocol:  AXI4_Slave
  Coverage trajectory:
  Metric           Current     Start     Delta
  Statements         92.6%     59.3%    +33.3%
  Branches           77.4%      0.0%    +77.4%
  Covergroups       100.0%     50.0%    +50.0%

  Uncovered bins (2 total):
    1. [needs_test] bin rd — ZERO hits
    2. [needs_test] bin wr — ZERO hits

  Your test ideas — describe what to test:
  idea> WRAP burst write awburst=2 awlen=3 then read back

  [+] Added: WRAP burst write...
  Checkpoint complete. Continuing...
```

### Coverage gap classification (same as Siemens CAT-46518)
| Category | Description |
|----------|-------------|
| `needs_test` | Reachable — generate targeted stimulus |
| `back_pressure` | Add ready deassertion to driver |
| `unreachable` | Tool/instrument limitation — exclude with justification |

### Exclusion workflow
Every exclusion requires written technical justification — never auto-excluded:
```
┌─ Exclusion Candidate ──────────────────────────────
│  Signal:    unknown
│  Category:  unreachable
│  Gap:       (rd_len == 0) — No hits
│
│  Justification: (LLM-generated)
│    QuestaSim vcover does not instrument RHS expressions
│    of non-blocking assignments...
└────────────────────────────────────────────────────
Approve exclusion? (y/n): y
Written reason (required): QuestaSim NBA RHS artifact...
[OK] Exclusion applied
```

---

## Smart test generation

Ideas at the `idea>` prompt are converted to SV sequences using keyword-based constraints:

| Keyword in idea | Generated constraint |
|----------------|---------------------|
| `WRAP burst` | `awburst == 2'b10; arburst == 2'b10` |
| `FIXED burst` | `awburst == 2'b00; arburst == 2'b00` |
| `back-pressure` | 8 transactions, bready holdoff |
| `single-beat` | `awlen == 0; arlen == 0` |
| `unaligned` | `awaddr[1:0] != 2'b00` |
| `burst` | `awlen inside {[3:15]}` |

---

## LLM backend

The tool uses an LLM for:
1. **Protocol discovery** — extract channels/signals from plain English
2. **SV generation** — driver bodies, scoreboard logic, SVA properties
3. **Idea generation** — targeted test ideas from coverage gaps
4. **Error fixing** — fix compile errors (3 retries)

Current backend: `opencode` CLI (free, but times out at 120s per call).

**For production use**, set a paid API key in `src/config.py`:
```python
GEMINI_API_KEY = "your-key"      # ~$5/month
ANTHROPIC_API_KEY = "your-key"   # ~$10/month
```

With a paid API, all LLM timeouts become 2-second successful calls. The architecture is ready — it just needs a backend that doesn't hang.

---

## Validated protocols

| Protocol | Mode | DUT | Statements | Branches | Covergroups |
|----------|------|-----|-----------|---------|------------|
| AXI4 slave | 1 | Real RTL | 92.6% | 77.4% | 100% |
| I2C slave | 2 | Generated stub | 100% | 100% | 100% |
| I2C master | 1 | No DUT | — | — | 50% |

---

## Key bugs fixed (lessons from the internship)

1. **Clocking blocks** — UVM class drivers can't drive interface signals with `<=` without clocking blocks. Signal appears to drive. DUT never sees it.

2. **UVM_FATAL false positive** — QuestaSim prints `UVM_FATAL : 0` as a counter. Parser flagged every simulation as failed. 60+ simulations ran against zero UCDBs.

3. **Test wrapper** — `vsim +UVM_TESTNAME=X` needs a `uvm_test` class, not a `uvm_sequence`. Every generated test now has an auto-generated wrapper.

4. **Coverage key mismatch** — vcover reports `Statements`, code stored `stmts`. Display showed 0% while UCDB had 92%.

5. **Hardcoded awburst** — driver was hardcoding `awburst=2'b01` regardless of sequence constraints. WRAP/FIXED bursts never reached the DUT.

---

## Repo structure

```
uvmgen/
├── main.py              ← interactive entry point (Mode 1/2)
├── run_loop.py          ← 20-iteration coverage loop (protocol-agnostic)
├── src/
│   ├── discover_protocol.py   ← LLM protocol extraction
│   ├── generate_uvm.py        ← 8-component environment generator
│   ├── gen_dut_stub.py        ← Mode 2 behavioral DUT stub
│   ├── gen_agent.py           ← driver/monitor/seq generation
│   ├── gen_pkg.py             ← package generation
│   ├── gen_sva.py             ← SVA assertion generation
│   ├── all_modules.py         ← coverage loop, analysis, test generation
│   ├── checkpoint.py          ← interactive checkpoint + exclusion workflow
│   ├── gen_tests.py           ← smart keyword-based sequence fallback
│   ├── sv_validator.py        ← 12-rule SV validator
│   ├── llm_client.py          ← LLM backend abstraction
│   └── config.py              ← API keys (gitignored)
└── output/
    ├── axi4_slave/            ← Mode 1 validated environment
    └── i2c_slave/             ← Mode 2 validated environment
```

---

## Stack

Python · QuestaSim 2026.1 · SystemVerilog · UVM 1.1d · opencode CLI

---

*Built by Ipshita Datta — MS Computer Engineering, NC State University*  
