"""
gen_scoreboard.py — Scoreboard with golden model (LLM-generated check logic)
gen_coverage.py   — Coverage with protocol-specific bins (LLM-generated)
gen_sva.py        — SVA property skeletons (LLM-generated bodies)
gen_env.py        — tb_top.sv wiring DUT to UVM
gen_scripts.py    — run.sh and regress.sh
"""

import re

# ═══════════════════════════════════════════════════════════════
# gen_scoreboard.py
# ═══════════════════════════════════════════════════════════════

def gen_scoreboard_class(proto_spec: dict, llm) -> str:
    """Generate scoreboard with LLM-generated check logic."""
    name     = proto_spec['sv_name']
    strategy = proto_spec.get('scoreboard_strategy', 'Compare request and response data')

    check_logic = _gen_check_logic(proto_spec, llm)

    return f"""  class {name}_scoreboard extends uvm_scoreboard;
    `uvm_component_utils({name}_scoreboard)
    uvm_analysis_imp #({name}_seq_item, {name}_scoreboard) analysis_export;

    // Golden model — {strategy}
    logic [31:0] golden_mem [logic [15:0]];
    int pass_count = 0;
    int fail_count = 0;

    function new(string name="{name}_scoreboard", uvm_component parent=null);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      analysis_export = new("analysis_export", this);
    endfunction

    function void write({name}_seq_item t);
{check_logic}
    endfunction

    function void report_phase(uvm_phase phase);
      `uvm_info("SB",$sformatf(
        "=== SCOREBOARD === PASS:%0d FAIL:%0d",
        pass_count, fail_count), UVM_NONE)
      if (fail_count > 0) `uvm_error("SB","TEST FAILED")
      else `uvm_info("SB","TEST PASSED — scoreboard clean",UVM_NONE)
    endfunction
  endclass"""


def _gen_check_logic(proto_spec: dict, llm) -> str:
    """LLM generates scoreboard check logic."""
    prompt = f"""Protocol: {proto_spec['name']}
Scoreboard strategy: {proto_spec.get('scoreboard_strategy', '')}
Transactions: {[t['name'] for t in proto_spec.get('transactions', [])]}

Write the body of a UVM scoreboard write() function for this protocol.
The function receives a seq_item 't'.
Implement golden model checks — update golden state on writes, check on reads.
Use: pass_count++; fail_count++; `uvm_error / `uvm_info as appropriate.
Return ONLY the function body lines (no declaration).
Max 25 lines."""

    result = llm.call(prompt, max_tokens=500)
    if not result:
        name = proto_spec['sv_name']
        return f"      // TODO: implement {proto_spec['name']} scoreboard check\n      pass_count++;"

    return _indent(result, 6)


# ═══════════════════════════════════════════════════════════════
# gen_coverage.py
# ═══════════════════════════════════════════════════════════════

def gen_coverage_class(proto_spec: dict, llm) -> str:
    """Generate coverage with LLM-generated covergroup bins."""
    name       = proto_spec['sv_name']
    key_states = proto_spec.get('coverage_key_states', [])

    covergroups = _gen_covergroups(proto_spec, llm)

    return f"""  class {name}_coverage extends uvm_subscriber #({name}_seq_item);
    `uvm_component_utils({name}_coverage)
    virtual {name}_if vif;
    {name}_seq_item pkt;

{covergroups}

    function new(string name="{name}_coverage", uvm_component parent=null);
      super.new(name, parent);
      cg_main = new();
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db #(virtual {name}_if)::get(this,"","vif",vif))
        `uvm_fatal("NOVIF","{name}_coverage: no vif")
    endfunction

    function void write({name}_seq_item t);
      pkt = t;
      cg_main.sample();
    endfunction

    function void report_phase(uvm_phase phase);
      `uvm_info("COV",$sformatf(
        "=== COVERAGE === main=%.1f%%",
        cg_main.get_coverage()), UVM_NONE)
    endfunction
  endclass"""


def gen_coverage(proto_spec: dict, output_dir: str, llm) -> str:
    """Standalone call — coverage is merged into tb_pkg."""
    return gen_coverage_class(proto_spec, llm)


def gen_scoreboard(proto_spec: dict, output_dir: str, llm) -> str:
    """Standalone call — scoreboard is merged into tb_pkg."""
    return gen_scoreboard_class(proto_spec, llm)


def _gen_covergroups(proto_spec: dict, llm) -> str:
    """LLM generates protocol-specific covergroup bins."""
    key_states = proto_spec.get('coverage_key_states', [])
    name       = proto_spec['sv_name']

    prompt = f"""Protocol: {proto_spec['name']}
Key states to cover: {', '.join(key_states[:6])}
Seq item fields available from protocol channels.

Write ONE SystemVerilog covergroup named 'cg_main' for this protocol.
Include coverpoints for the most important protocol states.
Use 'pkt.*' to reference seq_item fields.
Return ONLY the covergroup declaration (no class wrapper).
Max 20 lines."""

    result = llm.call(prompt, max_tokens=400)
    if not result:
        return f"    covergroup cg_main;\n      cp_default: coverpoint pkt;\n    endgroup"

    return _indent(result, 4)


# ═══════════════════════════════════════════════════════════════
# gen_sva.py
# ═══════════════════════════════════════════════════════════════

def gen_sva(proto_spec: dict, output_dir: str, llm) -> str:
    """Generate SVA module with LLM-generated property bodies."""
    name       = proto_spec['sv_name']
    sva_hints  = proto_spec.get('sva_key_properties', [])
    channels   = proto_spec.get('channels', [])

    # Collect all signal names for port list
    all_sigs = []
    for ch in channels:
        for sig_name, sig_info in ch.get('signals', {}).items():
            w = sig_info.get('width', 1)
            if isinstance(w, int) and w == 1:
                all_sigs.append(f"  input logic               {sig_name.lower()}")
            else:
                all_sigs.append(f"  input logic [{w}-1:0]     {sig_name.lower()}")

    properties = _gen_sva_properties(proto_spec, llm)

    # The LLM may reference protocol signals that were not listed in the
    # channel config (e.g. ancillary AXI4 signals such as wlast/rvalid).
    # Undeclared identifiers in the property bodies make QuestaSim fail with
    # vlog-2163 ("Macro `wlast is undefined"), so scan the generated property
    # text and declare every referenced signal that is not yet a module input.
    _sv_keywords = frozenset("""assert always always_comb always_ff always_latch and assign
        assume automatic begin bind bit break byte case casex casez checker class clocking
        const constraint continue cover covergroup coverpoint cross default defparam design
        disable dist do edge else end endcase endchecker endclass endclocking endconfig
        endfunction endgenerate endgroup endinterface endmodule endpackage endprogram
        endproperty endsequence endtable endtask enum event eventually expect export extends
        extern final first_match for foreach forever fork function generate genvar global
        highz0 highz1 if iff ignore_bins illegal_bins import inout input inside int integer
        interface intersect join join_any join_none let localparam logic longint macromodule
        matches medium modport module nand negedge nettype new nexttime nmos nor not notif0
        notif1 null or output packed parameter pmos posedge primitive priority program
        property protected pulldown pullup pure rand randc randcase randsequence real
        realtime ref reg reject_on release repeat restrict return s_always s_eventually
        s_nexttime s_until s_until_with scalared sequence shortint shortreal signed small
        soft solve specify specparam static string strong struct super supply0 supply1
        sync_accept_on sync_reject_on table tagged task this throughout time timeprecision
        timeunit tri tri0 tri1 triand trior trireg type typedef union unique unique0 unsigned
        use uwire var vectored virtual void wait wait_order wand weak while wildcard wire with
        within wor xnor xor""".split())

    def _referenced_sigs(body):
        body = re.sub(r'`[A-Za-z_]\w*', '', body)   # drop macro refs (e.g. `uvm_*)
        body = re.sub(r'\$[A-Za-z_]\w*', '', body)  # drop $system calls
        return {m for m in re.findall(r'\b[a-z_][a-z0-9_]*\b', body)
                if m not in _sv_keywords}

    declared = {s.split()[-1].lower() for s in all_sigs} | {'clk', 'rst_n'}
    for sig in sorted(_referenced_sigs(properties) - declared):
        all_sigs.append(f"  input logic               {sig}")

    ports_sv = ',\n'.join(all_sigs)

    return f"""`ifndef {name.upper()}_SVA_SV
`define {name.upper()}_SVA_SV

import {name}_pkg::*;

// {proto_spec['name']} SVA Properties
// Generated by Universal UVM Generator
module {name}_sva (
  input logic clk,
  input logic rst_n{(',' + chr(10) + ports_sv) if all_sigs else ''}
);

{properties}

endmodule : {name}_sva
`endif
"""


def _gen_sva_properties(proto_spec: dict, llm) -> str:
    """LLM generates SVA property bodies from hints."""
    hints = proto_spec.get('sva_key_properties', [])
    if not hints:
        hints = ['valid must not deassert before ready',
                 'response must come after request']

    hints_str = '\n'.join([f"- {h}" for h in hints[:6]])
    channels  = proto_spec.get('channels', [])
    sig_names = []
    for ch in channels:
        sig_names.extend(list(ch.get('signals', {}).keys())[:3])

    prompt = f"""Protocol: {proto_spec['name']}
Available signals (use exact names): {', '.join(sig_names[:10])}
Key properties to verify:
{hints_str}

Write SystemVerilog SVA properties for these requirements.
Use: property p_name; @(posedge clk) disable iff(!rst_n) ...; endproperty
     a_name: assert property(p_name) else $error("...");
Use signal names from the list above.
Return ONLY the property/assert pairs.
Max 30 lines."""

    result = llm.call(prompt, max_tokens=600)
    if not result:
        return "  // TODO: add SVA properties for " + proto_spec['name']

    # Strip markdown code fences (``` / ```sv) that the LLM may wrap the
    # property bodies in. A bare ``` line is parsed by QuestaSim as a leading
    # backtick macro reference, raising vlog-2163 ("Macro `property undefined")
    # and cascading into vlog-13205 by breaking the enclosing module scope.
    result = re.sub(r'(?ms)^[ \t]*```[A-Za-z]*[ \t]*$', '', result).strip()

    return result


# ═══════════════════════════════════════════════════════════════
# gen_env.py
# ═══════════════════════════════════════════════════════════════

def gen_env(proto_spec: dict, output_dir: str, llm) -> str:
    """Generate tb_top.sv wiring DUT (stub) to UVM."""
    name     = proto_spec['sv_name']
    channels = proto_spec.get('channels', [])

    # Generate port connections
    port_lines = []
    for ch in channels:
        for sig_name in ch.get('signals', {}).keys():
            port_lines.append(
                f"    .{sig_name.lower():<25}(dut_if.{sig_name.lower()})"
            )
    ports_sv = ',\n'.join(port_lines)

    # Generate interface signal initializations
    init_lines = []
    for ch in channels:
        if ch.get('direction') == 'master_to_slave':
            for sig_name, sig_info in ch.get('signals', {}).items():
                if sig_info.get('role') == 'valid':
                    init_lines.append(f"    dut_if.{sig_name.lower()} = 0;")

    inits_sv = '\n'.join(init_lines)

    return f"""`include "uvm_macros.svh"
import uvm_pkg::*;
import {name}_pkg::*;
import {name}_tb_pkg::*;

// Testbench top — Generated by Universal UVM Generator
// Connect your DUT here in place of the stub
module tb_top;

  logic clk;
  initial clk = 0;
  always #5 clk = ~clk;

  {name}_if dut_if(.clk(clk));

  // ── DUT (replace stub with your real DUT) ────────────────
  // TODO: instantiate your DUT here
  // Example:
  // my_dut u_dut (
  //   .clk     (clk),
  //   .rst_n   (dut_if.rst_n),
{chr(10).join(['  // ' + l for l in ports_sv.split(chr(10))])}
  // );

  initial begin
    dut_if.rst_n = 0;
{inits_sv}
    repeat(5) @(posedge clk);
    dut_if.rst_n = 1;
  end

  initial begin
    uvm_config_db #(virtual {name}_if)::set(
      null, "uvm_test_top.*", "vif", dut_if);
    run_test();
  end

endmodule : tb_top
"""


# ═══════════════════════════════════════════════════════════════
# gen_scripts.py
# ═══════════════════════════════════════════════════════════════

def gen_scripts(proto_spec: dict, output_dir: str, llm, script: str = 'run') -> str:
    """Generate run.sh or regress.sh."""
    name = proto_spec['sv_name']

    UVM_HOME = "/mnt/apps/public/COE/mg_apps/questa2026.1/questasim/verilog_src/uvm-1.1d/src"
    UVM_DPI  = "/mnt/apps/public/COE/mg_apps/questa2026.1/questasim/uvm-1.1d/linux_x86_64/uvm_dpi"

    if script == 'run':
        return f"""#!/bin/bash
set -e
RTL_DIR="../rtl"
TB_DIR="../tb"
AST_DIR="../assertions"
UVM_HOME="{UVM_HOME}"
UVM_DPI="{UVM_DPI}"
TEST=${{1:-{name}_sanity_test}}
LOG_DIR="cov_logs"
mkdir -p ${{LOG_DIR}}

echo "=== Cleaning ==="
rm -rf work && vlib work

echo "=== Compiling RTL/DUT ==="
vlog -sv -svinputport=net +incdir+${{RTL_DIR}} \\
  ${{RTL_DIR}}/{name}_pkg.sv

echo "=== Compiling Assertions ==="
vlog -sv -svinputport=net +incdir+${{RTL_DIR}} \\
  ${{AST_DIR}}/{name}_sva.sv

echo "=== Compiling TB ==="
vlog -sv -svinputport=net \\
  +incdir+${{TB_DIR}} +incdir+${{RTL_DIR}} +incdir+${{UVM_HOME}} \\
  ${{UVM_HOME}}/uvm_pkg.sv \\
  ${{TB_DIR}}/{name}_if.sv \\
  ${{TB_DIR}}/{name}_tb_pkg.sv \\
  ${{TB_DIR}}/tb_top.sv

echo "=== Running: ${{TEST}} ==="
vsim -batch -coverage \\
  -do "coverage save -onexit ${{LOG_DIR}}/${{TEST}}_cov.ucdb; run -all; quit -f" \\
  +UVM_TESTNAME=${{TEST}} \\
  +UVM_VERBOSITY=UVM_LOW \\
  -sv_seed random \\
  -sv_lib ${{UVM_DPI}} \\
  work.tb_top

echo "=== Done ==="
"""
    else:  # regress
        return f"""#!/bin/bash
RTL_DIR="../rtl"
TB_DIR="../tb"
AST_DIR="../assertions"
UVM_HOME="{UVM_HOME}"
UVM_DPI="{UVM_DPI}"
SEEDS=${{1:-10}}
PASS=0; FAIL=0; TOTAL=0
LOG_DIR="regress_logs"
mkdir -p ${{LOG_DIR}}

echo "================================================"
echo "  {proto_spec['name']} Regression -- ${{SEEDS}} seeds"
echo "================================================"

echo "[1/2] Compiling..."
rm -rf work && vlib work > /dev/null 2>&1
vlog -sv -svinputport=net +incdir+${{RTL_DIR}} \\
  ${{RTL_DIR}}/{name}_pkg.sv > /dev/null 2>&1
vlog -sv -svinputport=net +incdir+${{RTL_DIR}} \\
  ${{AST_DIR}}/{name}_sva.sv > /dev/null 2>&1
vlog -sv -svinputport=net \\
  +incdir+${{TB_DIR}} +incdir+${{RTL_DIR}} +incdir+${{UVM_HOME}} \\
  ${{UVM_HOME}}/uvm_pkg.sv \\
  ${{TB_DIR}}/{name}_if.sv \\
  ${{TB_DIR}}/{name}_tb_pkg.sv \\
  ${{TB_DIR}}/tb_top.sv > /dev/null 2>&1

echo "[2/2] Running..."
for TEST in {name}_sanity_test {name}_random_test; do
  for i in $(seq 1 ${{SEEDS}}); do
    SEED=$RANDOM; TOTAL=$((TOTAL+1))
    LOG="${{LOG_DIR}}/${{TEST}}_seed${{SEED}}.log"
    UCDB="${{LOG_DIR}}/${{TEST}}_seed${{SEED}}.ucdb"
    vsim -batch -coverage \\
      -do "coverage save -onexit ${{UCDB}}; run -all; quit -f" \\
      +UVM_TESTNAME=${{TEST}} +UVM_VERBOSITY=UVM_NONE \\
      -sv_seed ${{SEED}} -sv_lib ${{UVM_DPI}} \\
      work.tb_top > ${{LOG}} 2>&1
    if grep -q "TEST FAILED" ${{LOG}} 2>/dev/null; then
      echo "  FAIL  ${{TEST}}  seed=${{SEED}}"
      FAIL=$((FAIL+1))
    else
      echo "  PASS  ${{TEST}}  seed=${{SEED}}"
      PASS=$((PASS+1))
    fi
  done
done

echo ""
echo "RESULTS: PASS=${{PASS}}  FAIL=${{FAIL}}  TOTAL=${{TOTAL}}"

UCDB_FILES=$(ls ${{LOG_DIR}}/*.ucdb 2>/dev/null | tr '\\n' ' ')
if [ -n "${{UCDB_FILES}}" ]; then
  vcover merge ${{LOG_DIR}}/merged.ucdb ${{UCDB_FILES}} 2>/dev/null
  vcover report -html -output ${{LOG_DIR}}/coverage_html ${{LOG_DIR}}/merged.ucdb 2>/dev/null
  echo "HTML report: ${{LOG_DIR}}/coverage_html/index.html"
fi
[ ${{FAIL}} -eq 0 ] && echo "ALL TESTS PASSED" || echo "${{FAIL}} FAILURES"
"""


# ─── Shared helper ────────────────────────────────────────────
def _indent(text: str, spaces: int) -> str:
    pad = ' ' * spaces
    return '\n'.join(pad + line if line.strip() else line
                     for line in text.split('\n'))
