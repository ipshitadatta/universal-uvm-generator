# Universal UVM Generator (UVMGen)

> **Work in progress** — core pipeline working, active development on Mode 2 and coverage loop reliability.

An agentic Python tool that takes any RTL protocol description in plain English and generates a complete, compilable UVM verification environment — then runs a 20-iteration coverage-driven loop to close functional coverage automatically.

Inspired by [Siemens EDA CAT-46518](https://eda.sw.siemens.com/en-US/ic/catapult-high-level-synthesis/) internship work, extended to work for any RTL protocol.

---

## What It Does

```
You:    "AXI4 slave with 5 channels: AR, R, AW, W, B.
         Valid/ready handshake. Read: AR→R. Write: AW+W→B.
         Scoreboard: RDATA must match last write to same address."

UVMGen: → extracts protocol spec via LLM
        → generates complete UVM environment
        → compiles with QuestaSim (errors fixed by LLM, max 3 retries)
        → runs 20-iteration coverage loop
        → asks you for test ideas at iterations 5, 10, 15, 20
        → writes mentor_report.txt with coverage trajectory
```

**Generated files:**
```
output/<protocol>/
├── rtl/<proto>_pkg.sv          ← types, structs, enums
├── tb/<proto>_if.sv            ← SystemVerilog interface
├── tb/<proto>_tb_pkg.sv        ← seq_item, driver, monitor,
│                                  scoreboard, coverage, agent,
│                                  env, sequences, tests
├── assertions/<proto>_sva.sv   ← SVA property skeletons
├── tb/tb_top.sv                ← DUT + UVM start
├── sim/run.sh                  ← single test
├── sim/regress.sh              ← regression + vcover merge
└── mentor_report.txt           ← coverage trajectory + gaps
```

---

## Modes

**Mode 1 — Existing DUT RTL:**
Provide your RTL files → tool analyzes ports → generates matching UVM environment → run coverage loop against your DUT.

**Mode 2 — Protocol description only *(in development)*:**
Describe the protocol → LLM generates UVM + behavioral DUT stub → run coverage loop to validate the VIP before connecting to real DUT.

---

## Architecture

18 Python modules:

| Module | Role |
|---|---|
| `main.py` | Interactive Mode 1/2 selection, 20-iteration loop |
| `llm_client.py` | Claude API → Gemini API → opencode CLI fallback |
| `discover_protocol.py` | LLM extracts ProtocolSpec from plain English |
| `generate_uvm.py` | Orchestrates 8 sub-generators |
| `gen_pkg.py` | Generates `*_pkg.sv` |
| `gen_interface.py` | Generates `*_if.sv` |
| `gen_agent.py` | Generates complete `*_tb_pkg.sv` |
| `gen_scoreboard.py` | Golden model + check logic |
| `gen_coverage.py` | Covergroups from protocol states |
| `gen_sva.py` | SVA property skeletons |
| `gen_env.py` | `tb_top.sv` |
| `gen_scripts.py` | `run.sh` + `regress.sh` |
| `validate_compile.py` | QuestaSim compile + error parsing |
| `fix_errors.py` | LLM error fixer with signature cache |
| `run_sim.py` | vsim + UCDB save |
| `analyze_coverage.py` | vcover parsing + gap classification |
| `gen_tests.py` | Idea → SV sequence (12-rule validation) |
| `checkpoint.py` | Iter 5/10/15/20 interactive checkpoints |

---

## 20-Iteration Coverage Loop

Same cadence as Siemens EDA CAT-46518:

```
Iter 1-4:  LLM generates 6 broad test ideas → SV sequences → simulate
Iter 5:    CHECKPOINT — print coverage, read ideas.txt, ask user for ideas
Iter 6-9:  Targeted gap closure
Iter 10:   BACKTRACE — learn from manual sequences, CHECKPOINT
Iter 12:   WHITE-BOX — parse UCDB file:line, target RTL conditions
Iter 15:   LLM RE-ANALYSIS — 5 corner-case sequences, CHECKPOINT
Iter 20:   FINAL — mentor_report.txt, exclusions one-by-one (y/n + reason)
```

Coverage printed as bar chart after every iteration:
```
  Statements   [████████████░░░░░░░░]  62.3%  (+14.1%)
  Branches     [████████░░░░░░░░░░░░]  41.5%  (+8.2%)
  Expressions  [█████░░░░░░░░░░░░░░░]  28.7%  (+6.1%)
  Covergroups  [██████████░░░░░░░░░░]  50.0%  (+15.0%)
```

---

## 12-Rule SV Validation

Every LLM-generated sequence is validated before writing to disk (from CAT-46518):

1. Declarations before statements
2. No undefined signal names
3. No C++ types (`ac_int`, `template<>`)
4. UVM object creation before fork/start
5. Drain wait at end of sequence
6. No invented UVM methods
7. No `p_sequencer`
8. No hardcoded protocol values
9. No `$unit` scope
10. Minimum 2 transactions
11. No `#delay` — use `@(posedge clk)`
12. `begin/end` balance

---

## CAT-46518 Lessons Applied

| Lesson | Applied |
|---|---|
| Prompt trimming (full-file prompts → timeout) | Max 4000 tokens per LLM call |
| Error signature caching | `{error_code + file_type}` → fix patch |
| Pure Python file writes | Never shell redirection |
| Hard retry limit | Max 3 LLM retries per error |
| Never auto-exclude | y/n + written reason per exclusion |
| Interactive idea prompt | `idea>` at every checkpoint |

---

## Validation

Tested on AXI4 full slave (5 channels, 2 transactions):

```
Protocol discovery:  ✅ 5 channels, 2 transactions extracted
UVM generation:      ✅ 6 files generated
QuestaSim compile:   ✅ Errors:0, Warnings:0
Simulation:          ✅ UVM_ERROR:0, TEST PASSED
```

---

## Current Limitations

- **Gemini free tier 429 rate limiting** — many LLM calls fall back to templates, generating incomplete SV. Needs a paid API key for reliable generation.
- **Mode 2 (DUT stub generation)** — not yet implemented
- **Coverage loop** — UCDB analysis works but gap classification needs tuning per protocol

---

## Setup

```bash
git clone https://github.com/ipshitadatta/universal-uvm-generator
cd universal-uvm-generator

# Set LLM API key (one of):
export ANTHROPIC_API_KEY="your-key"   # Claude — most reliable
export GEMINI_API_KEY="your-key"      # Gemini — free tier available

# Load QuestaSim (NC State grendel):
module load questa

# Run
python3 main.py
```

---

## Usage

```
╔══════════════════════════════════════════════════════════════╗
║          Universal UVM Generator  v1.0                       ║
║          Inspired by Siemens EDA CAT-46518                   ║
╚══════════════════════════════════════════════════════════════╝

[LLM] Backend: claude

Select mode:
  1 = Existing DUT RTL  (provide RTL files, agent generates UVM VIP)
  2 = Protocol description only  (agent generates UVM + DUT stub)

Mode > 2

  Describe your protocol in plain English:
  > I2C master controller. Two signals: SCL and SDA.
    Master generates START, 7-bit address + R/W, ACK, data bytes, STOP.

  [DISCOVERED] Protocol: I2C_Master
    Channels: SCL (output), SDA (bidirectional)
    Transactions: write_txn, read_txn

  [GEN] Generating UVM environment...
  [COMPILE] Errors:0
  [LOOP] Starting 20-iteration coverage loop...
```

---

## Stack

Python · SystemVerilog · UVM 1.1d · QuestaSim 2026.1 · Gemini API · Claude API

---

## Related Projects

- [AMBA CHI Cache Coherence Verifier](https://github.com/ipshitadatta/chi-coherence-uvm)
- [DDR5 SoC Memory Controller Verifier](https://github.com/ipshitadatta/ddr5-mc-uvm)
- [GPU Warp Scheduler](https://github.com/ipshitadatta/gpu-warp-scheduler-uvm)
