#!/usr/bin/env python3
"""
UVMGen — Universal UVM Generator
Agentic tool that generates complete UVM verification environments from plain English.
"""

import sys, os
sys.path.insert(0, 'src')
os.environ['PATH'] = os.path.expanduser('~/.opencode/bin') + ':' + os.environ['PATH']

from llm_client import LLMClient
from discover_protocol import discover_protocol

def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║         UVMGen — Universal UVM Generator v1.0               ║
║    Agentic verification environment from plain English       ║
╚══════════════════════════════════════════════════════════════╝
""")

def select_mode():
    print("  Select mode:")
    print("  [1] Generate UVM environment for an existing RTL DUT")
    print("  [2] Generate behavioral DUT stub + UVM environment (RTL-free)")
    print()
    while True:
        choice = input("  Mode> ").strip()
        if choice in ['1', '2']:
            return int(choice)
        print("  Enter 1 or 2")

def get_protocol_description():
    print("""
  Describe your protocol in plain English.
  Examples:
    "AXI4 slave with 5 channels: AR, R, AW, W, B"
    "I2C master with SDA/SCL signals, start/stop conditions"
    "PCIe endpoint with TLP read/write transactions"

  Enter description (or press Enter for AXI4 demo):""")
    desc = input("  Protocol> ").strip()
    if not desc:
        desc = "AXI4 slave with 5 channels: AR address read, R data read, AW address write, W data write, B write response"
        print(f"  Using demo: {desc}")
    return desc

def get_dut_path():
    print("""
  Enter path to your RTL DUT file (or press Enter to skip):""")
    path = input("  DUT path> ").strip()
    return path if path and os.path.exists(os.path.expanduser(path)) else None

def main():
    print_banner()
    llm = LLMClient()
    print(f"  LLM backend: {llm.backend}")
    print()

    mode = select_mode()
    desc = get_protocol_description()

    print(f"\n  Discovering protocol from description...")
    proto_spec = discover_protocol(desc, None, llm)
    if not proto_spec:
        print("  ERROR: Could not discover protocol. Check LLM backend.")
        sys.exit(1)

    print(f"\n  Protocol: {proto_spec['name']}")
    print(f"  Channels: {len(proto_spec.get('channels', []))}")
    print(f"  Transactions: {[t['name'] for t in proto_spec.get('transactions', [])]}")

    output_dir = os.path.expanduser(f"~/uvmgen/output/{proto_spec['sv_name']}")
    print(f"  Output: {output_dir}")

    if mode == 1:
        dut_path = get_dut_path()
        print(f"\n  Generating UVM environment...")
        from generate_uvm import generate_uvm_environment
        files = generate_uvm_environment(proto_spec, output_dir, llm)
        print(f"  Generated {len(files)} files")

        if dut_path:
            import shutil
            rtl_dir = os.path.join(output_dir, 'rtl')
            os.makedirs(rtl_dir, exist_ok=True)
            shutil.copy(os.path.expanduser(dut_path), rtl_dir)
            print(f"  DUT copied to {rtl_dir}")

        print(f"\n  Starting 20-iteration coverage loop...")
        print(f"  Run: python3 run_loop.py")

    elif mode == 2:
        print(f"\n  Mode 2: Generating behavioral DUT stub + UVM environment...")
        sys.path.insert(0, 'src')
        from gen_dut_stub import generate_dut_stub
        from generate_uvm import generate_uvm_environment
        print(f"  Generating UVM environment...")
        files = generate_uvm_environment(proto_spec, output_dir, llm)
        dut_path = generate_dut_stub(proto_spec, output_dir, llm)
        if dut_path:
            print(f"  ✦ DUT stub     → {os.path.relpath(dut_path, output_dir)}")
        print(f"  Generated {len(files)+1} files")
        # Auto-fix compile errors
        from all_modules import validate_compile, fix_errors
        print(f"  Validating compile...")
        errors = validate_compile(output_dir, proto_spec)
        if errors:
            print(f"  Found {len(errors)} errors — auto-fixing...")
            fix_errors(errors, output_dir, proto_spec, llm)
            errors2 = validate_compile(output_dir, proto_spec)
            print(f"  After fix: {len(errors2)} errors remaining")
        else:
            print(f"  Compile: Errors 0 ✅")

    print(f"\n  Done! Output: {output_dir}")

if __name__ == '__main__':
    main()
