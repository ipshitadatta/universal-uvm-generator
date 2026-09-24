"""
gen_tests.py — Test sequence generation with smart keyword-based fallback.
"""

import re
import os


def idea_to_sv_sequence(idea: str, test_name: str, proto_spec: dict, llm) -> str:
    """Convert a test idea to a compilable SV sequence. 3-layer approach."""
    name = proto_spec['sv_name']

    for attempt in range(2):
        prompt = f"""Write UVM sequence class {test_name} extends {name}_base_seq.
Seq item: {name}_seq_item. Test: {idea[:60]}.
Use @(posedge vif.clk), min 2 transactions, `uvm_object_utils.
Return ONLY class definition."""

        result = llm.call(prompt, max_tokens=600)
        if result:
            violations = validate_sv_12rules(result, proto_spec)
            if not violations:
                return _wrap_sequence(result, proto_spec)
            if attempt < 2:
                prompt += f"\n\nFIX THESE VIOLATIONS: {'; '.join(violations[:3])}"

    # Layer 3: Rule-based fallback
    return _fallback_sequence(test_name, idea, proto_spec)


def validate_sv_12rules(sv_code: str, proto_spec: dict) -> list:
    """Check SV code against 12 CAT-46518 rules."""
    violations = []
    in_task = False
    saw_statement = False

    for line in sv_code.split('\n'):
        stripped = line.strip()
        if 'task body' in stripped or 'task run_phase' in stripped:
            in_task = True
            saw_statement = False
        if in_task and 'endtask' in stripped:
            in_task = False
        if in_task and saw_statement:
            if re.match(r'^\s*(logic|int|bit|byte|string|typedef)\s', line):
                violations.append("Rule 1: declaration after statement in task")
                break
        if in_task and not stripped.startswith('//') and stripped and \
           not re.match(r'^\s*(logic|int|bit|byte|string|typedef|begin|end|task|function)\s', line) and \
           any(op in stripped for op in ['<=', '=', '`uvm', 'start_item', 'finish_item', 'randomize']):
            saw_statement = True

    return violations


def _fallback_sequence(test_name: str, idea: str, proto_spec: dict) -> str:
    """Smart template — parses idea keywords to generate targeted constraints."""
    name = proto_spec['sv_name']
    idea_lower = idea.lower()
    idea_short = idea[:60].replace('"', "'")

    # Detect transaction type
    is_read  = any(k in idea_lower for k in ['read','arvalid','araddr','rdata','rvalid','rready'])
    is_write = any(k in idea_lower for k in ['write','awvalid','awaddr','wdata','wvalid','bvalid'])
    if is_read and not is_write:
        rw_constraint = "is_write == 0;"
    elif is_write and not is_read:
        rw_constraint = "is_write == 1;"
    else:
        rw_constraint = "is_write dist {0:=1, 1:=1};"

    # Detect burst length
    if any(k in idea_lower for k in ['awlen=255','arlen=255','max burst','256-beat']):
        len_constraint = "awlen == 8'hFF; arlen == 8'hFF;"
    elif any(k in idea_lower for k in ['burst','awlen','arlen','multi-beat','multi beat']):
        len_constraint = "awlen inside {[3:15]}; arlen inside {[3:15]};"
    elif any(k in idea_lower for k in ['single','single-beat','single beat']):
        len_constraint = "awlen == 0; arlen == 0;"
    else:
        len_constraint = "awlen inside {[0:7]}; arlen inside {[0:7]};"

    # Detect burst type
    if 'wrap' in idea_lower:
        burst_constraint = "awburst == 2'b10; arburst == 2'b10;"
    elif 'fixed' in idea_lower:
        burst_constraint = "awburst == 2'b00; arburst == 2'b00;"
    else:
        burst_constraint = "awburst == 2'b01; arburst == 2'b01;"

    # Detect strobe pattern
    if any(k in idea_lower for k in ['partial','strobe','narrow','unaligned']):
        strobe_constraint = "wstrb inside {4'h1, 4'h3, 4'h7, 4'hE};"
    else:
        strobe_constraint = "wstrb == 4'hF;"

    # Detect address pattern
    if any(k in idea_lower for k in ['boundary','0x1000','same address']):
        addr_constraint = "awaddr == 32'h1000; araddr == 32'h1000;"
    elif 'unaligned' in idea_lower:
        addr_constraint = "awaddr[1:0] != 2'b00; araddr[1:0] != 2'b00;"
    else:
        addr_constraint = "awaddr[1:0] == 2'b00; araddr[1:0] == 2'b00;"

    # Number of transactions
    if any(k in idea_lower for k in ['back-to-back','multiple','concurrent','stress']):
        num_txns = 8
    else:
        num_txns = 4

    # Get known fields from proto_spec channels
    known_fields = set()
    for ch in proto_spec.get('channels', []):
        for sig in ch.get('signals', {}).keys():
            known_fields.add(sig.lower())

    # Only add constraints for fields that exist in this protocol
    constraints = []
    if 'is_write' in known_fields or not known_fields:
        constraints.append(rw_constraint)
    if any(f in known_fields for f in ['awlen','arlen']) or not known_fields:
        constraints.append(len_constraint)
    if any(f in known_fields for f in ['awburst','arburst']) or not known_fields:
        constraints.append(burst_constraint)
    if 'wstrb' in known_fields or not known_fields:
        constraints.append(strobe_constraint)
    if any(f in known_fields for f in ['awaddr','araddr']) or not known_fields:
        constraints.append(addr_constraint)

    constraint_str = chr(10).join(f"        {c}" for c in constraints if c)

    return f"""
class {test_name} extends {name}_base_seq;
  `uvm_object_utils({test_name})
  function new(string name="{test_name}"); super.new(name); endfunction
  // Idea: {idea_short}
  task body();
    {name}_seq_item pkt;
    repeat ({num_txns}) begin
      pkt = {name}_seq_item::type_id::create("pkt");
      start_item(pkt);
      if (!pkt.randomize() with {{
{constraint_str}
      }}) `uvm_fatal("RAND","randomize failed")
      finish_item(pkt);
    end
    repeat(10) @(posedge $root.tb_top.clk);
  endtask
endclass
"""


def _get_example_sequence(proto_spec: dict) -> str:
    name = proto_spec['sv_name']
    return f"""class {name}_example_seq extends {name}_base_seq;
  `uvm_object_utils({name}_example_seq)
  function new(string name="{name}_example_seq"); super.new(name); endfunction
  task body();
    {name}_seq_item pkt;
    pkt = {name}_seq_item::type_id::create("pkt");
    start_item(pkt);
    if (!pkt.randomize()) `uvm_fatal("RAND","randomize failed")
    finish_item(pkt);
    repeat(10) @(posedge $root.tb_top.clk);
  endtask
endclass"""


def _wrap_sequence(sv_code: str, proto_spec: dict) -> str:
    """Ensure sequence has proper class structure."""
    if 'endclass' in sv_code:
        return sv_code
    name = proto_spec['sv_name']
    return f"""class gen_seq extends {name}_base_seq;
  `uvm_object_utils(gen_seq)
  function new(string name="gen_seq"); super.new(name); endfunction
  task body();
    {sv_code}
  endtask
endclass"""
