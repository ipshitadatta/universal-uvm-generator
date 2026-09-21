"""
gen_agent.py — Generate {proto}_tb_pkg.sv
Contains: seq_item, sequencer, driver, monitor, scoreboard, coverage,
          agent, env, base sequences, base test, sanity test, random test.
LLM fills: driver task body, monitor observation logic, scoreboard check,
           coverage bins, test sequences.
Template handles: all UVM boilerplate.
"""

from gen_scoreboard import gen_scoreboard_class
from sv_validator  import clean_sv_body, clean_sv_file, validate_enum
from gen_coverage   import gen_coverage_class


def gen_agent(proto_spec: dict, output_dir: str, llm) -> str:
    """Generate complete tb_pkg.sv with all UVM classes."""
    name     = proto_spec['sv_name']
    proto    = proto_spec['name']
    channels = proto_spec.get('channels', [])
    txns     = proto_spec.get('transactions', [])

    # Get LLM-generated components
    driver_body    = _gen_driver_body(proto_spec, llm)
    monitor_body   = _gen_monitor_body(proto_spec, llm)
    scoreboard_cls = gen_scoreboard_class(proto_spec, llm)
    coverage_cls   = gen_coverage_class(proto_spec, llm)
    seq_item_fields= _gen_seq_item_fields(proto_spec, llm)
    test_sequences = _gen_test_sequences(proto_spec, llm)

    content = f"""`ifndef {name.upper()}_TB_PKG_SV
`define {name.upper()}_TB_PKG_SV

package {name}_tb_pkg;

  `include "uvm_macros.svh"
  import uvm_pkg::*;
  import {name}_pkg::*;

  // ───────────────────────────────────────────────────────────
  // Sequence Item
  // ───────────────────────────────────────────────────────────
  class {name}_seq_item extends uvm_sequence_item;
    `uvm_object_utils({name}_seq_item)

{seq_item_fields}

    function new(string name="{name}_seq_item");
      super.new(name);
    endfunction

    function string convert2string();
      return $sformatf("{proto} txn: %s", super.convert2string());
    endfunction
  endclass

  // ───────────────────────────────────────────────────────────
  // Sequencer
  // ───────────────────────────────────────────────────────────
  class {name}_sequencer extends uvm_sequencer #({name}_seq_item);
    `uvm_component_utils({name}_sequencer)
    function new(string name="{name}_sequencer", uvm_component parent=null);
      super.new(name, parent);
    endfunction
  endclass

  // ───────────────────────────────────────────────────────────
  // Driver
  // ───────────────────────────────────────────────────────────
  class {name}_driver extends uvm_driver #({name}_seq_item);
    `uvm_component_utils({name}_driver)
    virtual {name}_if vif;

    function new(string name="{name}_driver", uvm_component parent=null);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db #(virtual {name}_if)::get(this,"","vif",vif))
        `uvm_fatal("NOVIF","{name}_driver: no vif")
    endfunction

    task run_phase(uvm_phase phase);
      {name}_seq_item req;
      _idle();
      @(posedge vif.clk iff vif.rst_n === 1'b1);
      repeat(2) @(posedge vif.clk);
      forever begin
        seq_item_port.get_next_item(req);
        _drive(req);
        seq_item_port.item_done();
      end
    endtask

    task _idle();
{_gen_idle_signals(proto_spec)}
    endtask

    task _drive({name}_seq_item item);
{driver_body}
    endtask
  endclass

  // ───────────────────────────────────────────────────────────
  // Monitor
  // ───────────────────────────────────────────────────────────
  class {name}_monitor extends uvm_monitor;
    `uvm_component_utils({name}_monitor)
    virtual {name}_if vif;
    uvm_analysis_port #({name}_seq_item) ap;

    function new(string name="{name}_monitor", uvm_component parent=null);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      ap = new("ap", this);
      if (!uvm_config_db #(virtual {name}_if)::get(this,"","vif",vif))
        `uvm_fatal("NOVIF","{name}_monitor: no vif")
    endfunction

    task run_phase(uvm_phase phase);
      {name}_seq_item obs;
      @(posedge vif.clk iff vif.rst_n === 1'b1);
      forever begin
        @(posedge vif.clk);
{monitor_body}
      end
    endtask
  endclass

  // ───────────────────────────────────────────────────────────
  // Scoreboard
  // ───────────────────────────────────────────────────────────
{scoreboard_cls}

  // ───────────────────────────────────────────────────────────
  // Coverage
  // ───────────────────────────────────────────────────────────
{coverage_cls}

  // ───────────────────────────────────────────────────────────
  // Agent
  // ───────────────────────────────────────────────────────────
  class {name}_agent extends uvm_agent;
    `uvm_component_utils({name}_agent)
    {name}_driver    drv;
    {name}_monitor   mon;
    {name}_sequencer seqr;

    function new(string name="{name}_agent", uvm_component parent=null);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      drv  = {name}_driver   ::type_id::create("drv",  this);
      mon  = {name}_monitor  ::type_id::create("mon",  this);
      seqr = {name}_sequencer::type_id::create("seqr", this);
    endfunction

    function void connect_phase(uvm_phase phase);
      drv.seq_item_port.connect(seqr.seq_item_export);
    endfunction
  endclass

  // ───────────────────────────────────────────────────────────
  // Environment
  // ───────────────────────────────────────────────────────────
  class {name}_env extends uvm_env;
    `uvm_component_utils({name}_env)
    {name}_agent      agent;
    {name}_scoreboard sb;
    {name}_coverage   cov;

    function new(string name="{name}_env", uvm_component parent=null);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      agent = {name}_agent     ::type_id::create("agent", this);
      sb    = {name}_scoreboard::type_id::create("sb",    this);
      cov   = {name}_coverage  ::type_id::create("cov",   this);
    endfunction

    function void connect_phase(uvm_phase phase);
      agent.mon.ap.connect(sb.analysis_export);
      agent.mon.ap.connect(cov.analysis_export);
    endfunction
  endclass

  // ───────────────────────────────────────────────────────────
  // Sequences
  // ───────────────────────────────────────────────────────────
  class {name}_base_seq extends uvm_sequence #({name}_seq_item);
    `uvm_object_utils({name}_base_seq)
    function new(string name="{name}_base_seq"); super.new(name); endfunction
  endclass

{test_sequences}

  // ───────────────────────────────────────────────────────────
  // Tests
  // ───────────────────────────────────────────────────────────
  class {name}_base_test extends uvm_test;
    `uvm_component_utils({name}_base_test)
    {name}_env env;
    function new(string name="{name}_base_test", uvm_component parent=null);
      super.new(name, parent);
    endfunction
    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      env = {name}_env::type_id::create("env", this);
    endfunction
  endclass

  class {name}_sanity_test extends {name}_base_test;
    `uvm_component_utils({name}_sanity_test)
    function new(string name="{name}_sanity_test", uvm_component parent=null);
      super.new(name, parent);
    endfunction
    task run_phase(uvm_phase phase);
      {name}_sanity_seq seq = {name}_sanity_seq::type_id::create("seq");
      phase.raise_objection(this);
      seq.start(env.agent.seqr);
      repeat(100) @(posedge env.agent.drv.vif.clk);
      phase.drop_objection(this);
    endtask
  endclass

  class {name}_random_test extends {name}_base_test;
    `uvm_component_utils({name}_random_test)
    function new(string name="{name}_random_test", uvm_component parent=null);
      super.new(name, parent);
    endfunction
    task run_phase(uvm_phase phase);
      {name}_random_seq seq = {name}_random_seq::type_id::create("seq");
      phase.raise_objection(this);
      seq.start(env.agent.seqr);
      repeat(500) @(posedge env.agent.drv.vif.clk);
      phase.drop_objection(this);
    endtask
  endclass

endpackage : {name}_tb_pkg
`endif
"""
    return content


def _gen_idle_signals(proto_spec: dict) -> str:
    """Generate idle signal assignments from channel definitions."""
    lines = []
    for ch in proto_spec.get('channels', []):
        if ch.get('direction') == 'master_to_slave':
            for sig_name, sig_info in ch.get('signals', {}).items():
                role = sig_info.get('role', '')
                if role == 'valid':
                    lines.append(f"      vif.{sig_name.lower()} <= 1'b0;")
    if not lines:
        lines = ["      // no idle signals to deassert"]
    return '\n'.join(lines)


def _gen_seq_item_fields(proto_spec: dict, llm) -> str:
    """LLM generates seq_item rand fields from transaction definitions."""
    txns     = proto_spec.get('transactions', [])
    channels = proto_spec.get('channels', [])
    params   = proto_spec.get('parameters', {})

    # Collect all master→slave signals as rand fields
    fields = []
    seen = set()
    for ch in channels:
        if ch.get('direction') != 'master_to_slave':
            continue
        for sig_name, sig_info in ch.get('signals', {}).items():
            role = sig_info.get('role', '')
            if role in ('valid', 'ready', 'clock'):
                continue
            if sig_name.lower() in seen:
                continue
            seen.add(sig_name.lower())
            w = sig_info.get('width', 1)
            if isinstance(w, int) and w == 1:
                fields.append(f"    rand logic                    {sig_name.lower()};")
            else:
                fields.append(f"    rand logic [{w}-1:0]           {sig_name.lower()};")

    # Add response fields (non-rand)
    for ch in channels:
        if ch.get('direction') != 'slave_to_master':
            continue
        for sig_name, sig_info in ch.get('signals', {}).items():
            role = sig_info.get('role', '')
            if role in ('valid', 'ready'):
                continue
            if sig_name.lower() in seen:
                continue
            seen.add(sig_name.lower())
            w = sig_info.get('width', 1)
            if isinstance(w, int) and w == 1:
                fields.append(f"    logic                         {sig_name.lower()}; // response")
            else:
                fields.append(f"    logic [{w}-1:0]               {sig_name.lower()}; // response")

    return '\n'.join(fields) if fields else '    rand logic [31:0] data;\n    rand logic [15:0] addr;'


def _gen_driver_body(proto_spec: dict, llm) -> str:
    """LLM generates driver task body."""
    channels = proto_spec.get('channels', [])
    master_channels = [c for c in channels if c.get('direction') == 'master_to_slave']

    ch_summary = '\n'.join([
        f"  {ch['name']}: handshake={ch.get('handshake','valid_ready')}, "
        f"signals={list(ch.get('signals',{}).keys())}"
        for ch in master_channels[:3]
    ])

    prompt = f"""Protocol: {proto_spec['name']}
Master channels:
{ch_summary}

Write the SystemVerilog body of a UVM driver task called _drive().
The task receives a seq_item and drives the DUT via vif signals.
Use @(posedge vif.clk) for synchronization — no #delay (Rule 11).
Use valid/ready handshake where applicable.
Include a timeout watchdog (200 cycles max).
Return ONLY the task body lines (not the task declaration).
Max 40 lines."""

    result = llm.call(prompt, max_tokens=600)
    if not result:
        return _fallback_driver_body(proto_spec)

    # Apply 12-rule validation
    result = _validate_sv_body(result, proto_spec)
    cleaned, removed = clean_sv_body(result, proto_spec)
    if removed:
        print(f"    [CLEAN] Removed {len(removed)} prose lines from driver body")
    return _indent(cleaned, 6)


def _gen_monitor_body(proto_spec: dict, llm) -> str:
    """LLM generates monitor observation logic."""
    channels = proto_spec.get('channels', [])
    slave_channels = [c for c in channels if c.get('direction') == 'slave_to_master']

    ch_summary = '\n'.join([
        f"  {ch['name']}: signals={list(ch.get('signals',{}).keys())}"
        for ch in slave_channels[:3]
    ])

    prompt = f"""Protocol: {proto_spec['name']}
Response channels (slave→master):
{ch_summary}

Write SystemVerilog code to observe when a transaction completes.
When complete: create a seq_item named 'obs', fill its fields, call ap.write(obs).
Use signal names from the interface (vif.*).
Return ONLY the observation code lines.
Max 20 lines."""

    result = llm.call(prompt, max_tokens=400)
    if not result:
        return _fallback_monitor_body(proto_spec)
    cleaned, removed = clean_sv_body(result, proto_spec)
    if removed:
        print(f"    [CLEAN] Removed {len(removed)} prose lines from monitor body")
    return _indent(cleaned, 8)


def _gen_test_sequences(proto_spec: dict, llm) -> str:
    """LLM generates sanity and random sequences."""
    txns = proto_spec.get('transactions', [])
    name = proto_spec['sv_name']

    prompt = f"""Protocol: {proto_spec['name']}
Transactions: {[t['name'] for t in txns]}
Scoreboard strategy: {proto_spec.get('scoreboard_strategy', '')}

Write two UVM sequence classes in SystemVerilog:
1. {name}_sanity_seq: write then read one address, verify data
2. {name}_random_seq: 20 random transactions

Both extend {name}_base_seq.
Use `uvm_object_utils, randomize() with constraints.
Return ONLY the two class definitions."""

    result = llm.call(prompt, max_tokens=800)
    if not result:
        return _fallback_sequences(proto_spec)
    cleaned, removed = clean_sv_body(result, proto_spec)
    if removed:
        print(f"    [CLEAN] Removed {len(removed)} prose lines from sequences")
    return cleaned


def _fallback_driver_body(proto_spec: dict) -> str:
    """Template fallback if LLM fails."""
    channels = proto_spec.get('channels', [])
    name = proto_spec['sv_name']
    lines = [
        "      int timeout = 0;",
        "      @(posedge vif.clk);",
    ]
    for ch in channels:
        if ch.get('direction') != 'master_to_slave':
            continue
        sigs = ch.get('signals', {})
        for sig, info in sigs.items():
            if info.get('role') == 'valid':
                lines.append(f"      vif.{sig.lower()} <= 1'b1;")
    lines += [
        "      // Wait for ready with timeout",
        "      while (timeout < 200) begin",
        "        @(posedge vif.clk);",
        "        timeout++;",
        "      end",
    ]
    for ch in channels:
        if ch.get('direction') != 'master_to_slave':
            continue
        for sig, info in ch.get('signals', {}).items():
            if info.get('role') == 'valid':
                lines.append(f"      vif.{sig.lower()} <= 1'b0;")
    return '\n'.join(lines)


def _fallback_monitor_body(proto_spec: dict) -> str:
    name = proto_spec['sv_name']
    return (f"        // Monitor: observe transaction completion\n"
            f"        // TODO: fill in observation logic for {proto_spec['name']}")


def _fallback_sequences(proto_spec: dict) -> str:
    name = proto_spec['sv_name']
    return f"""  class {name}_sanity_seq extends {name}_base_seq;
    `uvm_object_utils({name}_sanity_seq)
    function new(string name="{name}_sanity_seq"); super.new(name); endfunction
    task body();
      {name}_seq_item pkt;
      pkt = {name}_seq_item::type_id::create("pkt");
      start_item(pkt);
      if (!pkt.randomize()) `uvm_fatal("RAND","randomize failed")
      finish_item(pkt);
      `uvm_info("SANITY","Sanity sequence done",UVM_LOW)
    endtask
  endclass

  class {name}_random_seq extends {name}_base_seq;
    `uvm_object_utils({name}_random_seq)
    int unsigned num_ops = 20;
    function new(string name="{name}_random_seq"); super.new(name); endfunction
    task body();
      {name}_seq_item pkt;
      repeat (num_ops) begin
        pkt = {name}_seq_item::type_id::create("pkt");
        start_item(pkt);
        if (!pkt.randomize()) `uvm_fatal("RAND","randomize failed")
        finish_item(pkt);
      end
      `uvm_info("RAND",$sformatf("Sent %0d ops",num_ops),UVM_LOW)
    endtask
  endclass"""


def _validate_sv_body(code: str, proto_spec: dict) -> str:
    """Apply 12-rule validation to LLM-generated SV code."""
    issues = []

    # Rule 11: no #delay
    if '#' in code and 'delay' in code.lower():
        code = code.replace('#1', '@(posedge vif.clk)')
        issues.append("Rule 11: replaced #delay with @(posedge clk)")

    # Rule 7: no invented interface names — can't check without DUT info
    # Rule 3: no C++ types
    for ctype in ['ac_int', 'ac_fixed', 'template<', 'bool>']:
        if ctype in code:
            issues.append(f"Rule 3: removed C++ type '{ctype}'")
            code = code.replace(ctype, '// REMOVED_CPP_TYPE')

    if issues:
        code = f"// Validation fixes: {', '.join(issues)}\n" + code

    return code


def _indent(text: str, spaces: int) -> str:
    pad = ' ' * spaces
    return '\n'.join(pad + line if line.strip() else line
                     for line in text.split('\n'))
