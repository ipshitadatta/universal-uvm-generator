"""
Protocol Discovery — Phase 0
LLM extracts structured ProtocolSpec from plain English description.
No YAML needed — just describe the protocol and the LLM figures it out.
"""

import json
from typing import Optional


DISCOVERY_SYSTEM = """You are an expert in digital communication protocols and SystemVerilog UVM verification.
Your job is to extract a structured protocol specification from a plain English description.
You must identify: channels, signals, handshake types, transactions, and verification challenges.
Respond ONLY with valid JSON — no markdown, no explanation outside the JSON."""


def discover_protocol(description: str, rtl_source: Optional[str], llm) -> dict:
    """
    Extract ProtocolSpec from plain English description + optional RTL.
    Returns a dict with protocol structure.
    """
    prompt = build_discovery_prompt(description, rtl_source)
    spec = llm.call_json(prompt, system=DISCOVERY_SYSTEM, max_tokens=3000)

    if not spec:
        # Fallback: build minimal spec from description
        print("[WARN] LLM discovery failed — building minimal spec")
        spec = build_minimal_spec(description)

    # Validate and fill in defaults
    spec = normalize_spec(spec)
    return spec


def build_discovery_prompt(description: str, rtl_source: Optional[str]) -> str:
    rtl_section = ""
    if rtl_source:
        # Trim RTL to first 2000 chars — just enough for interface discovery
        rtl_section = f"\n\nDUT RTL (first section):\n```systemverilog\n{rtl_source[:2000]}\n```"

    return f"""Analyze this protocol and return ONLY compact JSON (no markdown).

PROTOCOL:
{description}
{rtl_section}

Return JSON:
{{"name":"<name>","description":"<1 sentence>","parameters":{{"DATA_WIDTH":32,"ADDR_WIDTH":16}},"channels":[{{"name":"<CH>","direction":"master_to_slave|slave_to_master","handshake":"valid_ready","signals":{{"SIG":{{"width":1,"role":"valid"}}}}}}],"transactions":[{{"name":"<txn>","request_channel":"<ch>","response_channel":"<ch>","id_field":null,"ordering":"in_order","description":"<what>"}}],"verification_challenges":["challenge1"],"scoreboard_strategy":"<how>","coverage_key_states":["state1"],"sva_key_properties":["prop1"]}}

Keep signal lists SHORT — max 5 signals per channel. Max 3 transactions. Max 3 challenges."""


def normalize_spec(spec: dict) -> dict:
    """Fill in defaults and normalize the spec."""
    # Required fields
    if 'name' not in spec:
        spec['name'] = 'custom_protocol'
    if 'parameters' not in spec:
        spec['parameters'] = {'DATA_WIDTH': 32, 'ADDR_WIDTH': 16}
    if 'channels' not in spec:
        spec['channels'] = []
    if 'transactions' not in spec:
        spec['transactions'] = []
    if 'verification_challenges' not in spec:
        spec['verification_challenges'] = []
    if 'scoreboard_strategy' not in spec:
        spec['scoreboard_strategy'] = 'Compare expected vs actual responses'
    if 'coverage_key_states' not in spec:
        spec['coverage_key_states'] = []
    if 'sva_key_properties' not in spec:
        spec['sva_key_properties'] = []

    # Normalize channel signals
    for ch in spec['channels']:
        if 'signals' not in ch:
            ch['signals'] = {}
        if 'handshake' not in ch:
            ch['handshake'] = 'valid_ready'

    # Generate SV-safe protocol name
    spec['sv_name'] = (spec['name']
                       .lower()
                       .replace(' ', '_')
                       .replace('-', '_')
                       .replace('/', '_'))

    return spec


def build_minimal_spec(description: str) -> dict:
    """Emergency fallback — build a minimal spec from description keywords."""
    name = 'custom_protocol'
    words = description.lower().split()

    # Try to detect common protocols
    for proto in ['axi4', 'axi', 'apb', 'ahb', 'chi', 'pcie', 'uart',
                  'i2c', 'spi', 'ddr5', 'usb', 'ethernet', 'can']:
        if proto in words:
            name = proto.upper()
            break

    return {
        'name': name,
        'description': description[:100],
        'parameters': {'DATA_WIDTH': 32, 'ADDR_WIDTH': 16},
        'channels': [
            {
                'name': 'REQ',
                'direction': 'master_to_slave',
                'handshake': 'valid_ready',
                'signals': {
                    'req_valid': {'width': 1, 'role': 'valid'},
                    'req_ready': {'width': 1, 'role': 'ready'},
                    'req_data':  {'width': 'DATA_WIDTH', 'role': 'data'},
                    'req_addr':  {'width': 'ADDR_WIDTH', 'role': 'address'},
                }
            },
            {
                'name': 'RSP',
                'direction': 'slave_to_master',
                'handshake': 'valid_ready',
                'signals': {
                    'rsp_valid': {'width': 1, 'role': 'valid'},
                    'rsp_ready': {'width': 1, 'role': 'ready'},
                    'rsp_data':  {'width': 'DATA_WIDTH', 'role': 'data'},
                }
            }
        ],
        'transactions': [
            {
                'name': 'request',
                'request_channel': 'REQ',
                'response_channel': 'RSP',
                'id_field': None,
                'ordering': 'in_order',
                'description': 'Basic request-response transaction'
            }
        ],
        'verification_challenges': ['back-to-back transactions', 'protocol violations'],
        'scoreboard_strategy': 'Compare request data with response data',
        'coverage_key_states': ['idle', 'active', 'back_pressure'],
        'sva_key_properties': ['valid must not deassert before ready']
    }
