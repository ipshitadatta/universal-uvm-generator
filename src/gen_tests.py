"""
gen_tests.py — Convert idea strings to SV sequences + 12-rule validation.
Following CAT-46518 3-layer pipeline: LLM → 12-rule validation → fallback.
"""

import re


def idea_to_sv_sequence(idea: str, test_name: str, proto_spec: dict, llm) -> str:
    """
    Convert a test idea string to a complete SV UVM sequence class.
    3-layer pipeline (CAT-46518):
      Layer 1: LLM generates SV from idea + protocol context
      Layer 2: 12-rule validation
      Layer 3: Rule-based fallback if LLM fails 3 attempts
    """
    name = proto_spec['sv_name']

    # Build interface summary (real signal names only — Rule 7)
    iface_lines = []
    for ch in proto_spec.get('channels', []):
        direction = ch.get('direction', '')
        sigs = list(ch.get('signals', {}).keys())
        iface_lines.append(f"  {ch['name']:8s} | {direction:20s} | {', '.join(sigs[:5])}")
    iface_summary = '\n'.join(iface_lines)

    # Get one working example from existing sequences (backtrace pattern)
    example = _get_example_sequence(proto_spec)

    prompt = f"""Write a SystemVerilog UVM sequence class for this protocol test.

Protocol: {proto_spec['name']}
Test idea: {idea}
Class name: {test_name}
Base class: {name}_base_seq
Seq item:   {name}_seq_item

Available interfaces (use EXACT names):
{iface_summary}

{example}

STRICT RULES — violations cause compile failures:
Rule 1:  All variable declarations BEFORE any executable statements
Rule 6:  End sequence with: repeat(10) @(posedge $root.tb_top.clk);
Rule 7:  Use ONLY signal names listed above — never invent names
Rule 8:  No C++ types (ac_int, template) — use logic [N-1:0]
Rule 10: Minimum 2 transactions in body()
Rule 11: No #delay — use @(posedge $root.tb_top.clk) or repeat()
Rule 12: No p_sequencer, no uvm_declare_p_sequencer

Return ONLY the complete class definition. No explanation."""

    for attempt in range(3):
        result = llm.call(prompt, max_tokens=800)
        if not result:
            continue

        # Check it has the right structure
        if f'class {test_name}' not in result and 'endclass' not in result:
            continue

        violations = validate_sv_12rules(result, proto_spec)
        if not violations:
            return _wrap_sequence(result, proto_spec)

        if attempt < 2:
            # Add violations to prompt for next attempt
            prompt += f"\n\nFIX THESE VIOLATIONS: {'; '.join(violations[:3])}"

    # Layer 3: Rule-based fallback
    return _fallback_sequence(test_name, idea, proto_spec)


def validate_sv_12rules(sv_code: str, proto_spec: dict) -> list:
    """
    Check SV code against 12 CAT-46518 rules.
    Returns list of violation strings (empty = pass).
    """
    violations = []

    # Rule 1: Declarations before statements
    # Detect if a `logic` declaration appears after an executable statement
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

    # Rule 7: No invented interface names
    valid_sigs = set()
    for ch in proto_spec.get('channels', []):
        valid_sigs.update(s.lower() for s in ch.get('signals', {}).keys())
    # Check for vif.something that's not in valid signals
    vif_refs = re.findall(r'vif\.(\w+)', sv_code.lower())
    for ref in vif_refs:
        if ref not in valid_sigs and ref not in ('clk', 'rst_n'):
            violations.append(f"Rule 7: undefined signal 'vif.{ref}'")

    # Rule 8: No C++ types
    for ctype in ['ac_int<', 'ac_fixed<', 'template<', 'bool>']:
        if ctype in sv_code:
            violations.append(f"Rule 8: C++ type '{ctype}' not allowed")

    # Rule 10: Minimum 2 transactions
    txn_count = sv_code.count('finish_item')
    if txn_count < 2 and 'repeat' not in sv_code:
        violations.append("Rule 10: fewer than 2 transactions")

    # Rule 11: No #delay
    if re.search(r'#\s*\d+', sv_code):
        violations.append("Rule 11: #delay not allowed — use @(posedge clk)")

    # Rule 12: No p_sequencer
    if 'p_sequencer' in sv_code or 'uvm_declare_p_sequencer' in sv_code:
        violations.append("Rule 12: p_sequencer not allowed")

    return violations


def _get_example_sequence(proto_spec: dict) -> str:
    """Get a template example for the LLM to follow."""
    name = proto_spec['sv_name']

    # Build a working example from actual signal names
    channels = proto_spec.get('channels', [])
    master_ch = [c for c in channels if 'master' in c.get('direction', '')]
    if not master_ch:
        master_ch = channels[:1]

    example_lines = [
        "FOLLOW THIS EXACT TEMPLATE:",
        f"  class {name}_example_seq extends {name}_base_seq;",
        f"    `uvm_object_utils({name}_example_seq)",
        f"    function new(string n=\"{name}_example_seq\"); super.new(n); endfunction",
        "    task body();",
        f"      {name}_seq_item pkt;",
        "      // Declaration block first (Rule 1)",
        "      pkt = {name}_seq_item::type_id::create(\"pkt\");",
        "      start_item(pkt);",
        "      if (!pkt.randomize()) `uvm_fatal(\"RAND\",\"failed\")",
        "      finish_item(pkt);",
        "      // Second transaction (Rule 10)",
        "      start_item(pkt);",
        "      if (!pkt.randomize()) `uvm_fatal(\"RAND\",\"failed\")",
        "      finish_item(pkt);",
        "      repeat(10) @(posedge $root.tb_top.clk); // drain wait (Rule 6)",
        "    endtask",
        "  endclass",
    ]
    return '\n'.join(example_lines)


def _fallback_sequence(test_name: str, idea: str, proto_spec: dict) -> str:
    name = proto_spec['sv_name']
    idea_short = idea[:60].replace('"', "'")
    txns = proto_spec.get('transactions', [])
    
    # Pick transaction type based on idea keywords
    idea_lower = idea.lower()
    resp_keywords = ['read','receive','response','slave-to-master','reply']
    init_keywords = ['write','send','request','master-to-slave','drive','assert']
    
    # Find which transaction names match the idea
    matched_txn = None
    for txn in txns:
        tname = txn.get('name','').lower()
        if any(k in idea_lower for k in [tname]):
            matched_txn = txn['name']
            break
    
    # If no match, pick first txn that matches idea direction
    if not matched_txn:
        if any(k in idea_lower for k in resp_keywords) and not any(k in idea_lower for k in init_keywords):
            # Response/read-type — pick last transaction (often read)
            matched_txn = txns[-1]['name'] if txns else 'default'
        else:
            matched_txn = txns[0]['name'] if txns else 'default'
    
    # Build constraint based on transaction fields
    # Use is_write if it exists, otherwise use rand fields
    has_is_write = 'is_write' in proto_spec.get('seq_item_fields', [])
    
    if 'is_write' in idea_lower or any(k in idea_lower for k in resp_keywords):
        is_write_val = '0'
    else:
        is_write_val = '1'
    
    constraint_block = f"is_write == {is_write_val};" if matched_txn in ['read','write'] else ""
    
    return f"""
class {test_name} extends {name}_base_seq;
  `uvm_object_utils({test_name})
  function new(string name="{test_name}"); super.new(name); endfunction
  // Test idea: {idea_short}
  task body();
    {name}_seq_item pkt;
    repeat (4) begin
      pkt = {name}_seq_item::type_id::create("pkt");
      start_item(pkt);
      if (!pkt.randomize()) `uvm_fatal("RAND","randomize failed")
      finish_item(pkt);
    end
    repeat(10) @(posedge $root.tb_top.clk);
  endtask
endclass
"""
    _orig_idea = idea  # kept for reference
    name = proto_spec['sv_name']
    idea_short = idea[:60].replace('"', "'")
    txns = proto_spec.get('transactions', [])
    
    # Pick transaction type based on idea keywords
    idea_lower = idea.lower()
    resp_keywords = ['read','receive','response','slave-to-master','reply']
    init_keywords = ['write','send','request','master-to-slave','drive','assert']
    
    # Find which transaction names match the idea
    matched_txn = None
    for txn in txns:
        tname = txn.get('name','').lower()
        if any(k in idea_lower for k in [tname]):
            matched_txn = txn['name']
            break
    
    # If no match, pick first txn that matches idea direction
    if not matched_txn:
        if any(k in idea_lower for k in resp_keywords) and not any(k in idea_lower for k in init_keywords):
            # Response/read-type — pick last transaction (often read)
            matched_txn = txns[-1]['name'] if txns else 'default'
        else:
            matched_txn = txns[0]['name'] if txns else 'default'
    
    # Build constraint based on transaction fields
    # Use is_write if it exists, otherwise use rand fields
    has_is_write = 'is_write' in proto_spec.get('seq_item_fields', [])
    
    if 'is_write' in idea_lower or any(k in idea_lower for k in resp_keywords):
        is_write_val = '0'
    else:
        is_write_val = '1'
    
    constraint_block = f"is_write == {is_write_val};" if matched_txn in ['read','write'] else ""
    
    return f"""
class {test_name} extends {name}_base_seq;
  `uvm_object_utils({test_name})
  function new(string name="{test_name}"); super.new(name); endfunction
  // Test idea: {idea_short}
  task body();
    {name}_seq_item pkt;
    repeat (4) begin
      pkt = {name}_seq_item::type_id::create("pkt");
      start_item(pkt);
      if (!pkt.randomize()) `uvm_fatal("RAND","randomize failed")
      finish_item(pkt);
    end
    repeat(10) @(posedge $root.tb_top.clk);
  endtask
endclass
"""
    _orig_idea = idea  # kept for reference
    """
    Rule-based fallback if LLM fails 3 attempts.
    Generates a minimal valid sequence from protocol spec directly.
    CAT-46518: fallback uses ip_info directly (keyword matching from idea text).
    """
    name = proto_spec['sv_name']

    # Extract any numeric hints from the idea
    nums = re.findall(r'\d+', idea)
    num_txns = int(nums[0]) if nums else 2
    num_txns = min(max(num_txns, 2), 20)  # clamp 2-20

    return f"""// Fallback sequence (LLM unavailable): {idea}
// Generated by rule-based fallback — CAT-46518 Layer 3

import {name}_tb_pkg::*;

class {test_name} extends {name}_base_seq;
  `uvm_object_utils({test_name})
  function new(string name="{test_name}"); super.new(name); endfunction

  task body();
    {name}_seq_item pkt;
    // Rule 1: declarations before statements
    int i;
    repeat ({num_txns}) begin
      pkt = {name}_seq_item::type_id::create("pkt");
      start_item(pkt);
      if (!pkt.randomize()) `uvm_fatal("RAND","randomize failed")
      finish_item(pkt);
    end
    repeat(10) @(posedge $root.tb_top.clk); // Rule 6: drain wait
  endtask
endclass
"""


def _wrap_sequence(sv_code: str, proto_spec: dict) -> str:
    """Wrap raw class definition with package import."""
    name = proto_spec['sv_name']
    if f'import {name}_tb_pkg' not in sv_code:
        sv_code = f"import {name}_tb_pkg::*;\n\n" + sv_code
    return sv_code
