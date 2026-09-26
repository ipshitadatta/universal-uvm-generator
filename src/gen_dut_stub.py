"""
gen_dut_stub.py - Mode 2: Generate behavioral RTL DUT stub from proto_spec.
Protocol-agnostic: reads ports and roles from proto_spec channels.
"""
import os

def generate_dut_stub(proto_spec: dict, output_dir: str, llm) -> str:
    name = proto_spec['sv_name']
    rtl_dir = os.path.join(output_dir, 'rtl')
    os.makedirs(rtl_dir, exist_ok=True)
    stub_path = os.path.join(rtl_dir, f'{name}.sv')

    inputs, outputs = {}, {}
    for ch in proto_spec.get('channels', []):
        d = ch.get('direction', 'master_to_slave')
        for sig, info in ch.get('signals', {}).items():
            if d == 'master_to_slave':
                inputs[sig] = info.get('role', '')
            else:
                outputs[sig] = info.get('role', '')

    port_lines = []
    for sig in inputs:
        port_lines.append(f"    input  logic        {sig}")
    for sig in outputs:
        port_lines.append(f"    output logic        {sig}")
    ports = ',\n'.join(port_lines)

    ready_out = [s for s,r in outputs.items() if r == 'ready']
    valid_in  = [s for s,r in inputs.items()  if r == 'valid']
    valid_out = [s for s,r in outputs.items() if r == 'valid']
    data_out  = [s for s,r in outputs.items() if r == 'data']
    data_in   = [s for s,r in inputs.items()  if r == 'data']

    body = ["  always_ff @(posedge clk or negedge rst_n) begin",
            "    if (!rst_n) begin"]
    for s in ready_out + valid_out + data_out:
        body.append(f"      {s} <= '0;")
    body.append("    end else begin")
    for s in ready_out:
        body.append(f"      {s} <= 1'b1;  // stub: always ready")
    for i, s in enumerate(valid_out):
        src = valid_in[i] if i < len(valid_in) else "1'b1"
        body.append(f"      {s} <= {src};  // stub: echo valid")
    if data_out and data_in:
        body.append(f"      {data_out[0]} <= {data_in[0]};  // stub: loopback")
    body.append("    end")
    body.append("  end")

    sv = f"""`ifndef {name.upper()}_SV
`define {name.upper()}_SV
import {name}_pkg::*;
// Behavioral DUT stub - UVMGen Mode 2
// Protocol: {proto_spec['name']}
// Replace with real RTL when available.
module {name} (
    input  logic clk,
    input  logic rst_n,
{ports}
);
{chr(10).join(body)}
endmodule : {name}
`endif
"""
    with open(stub_path, 'w') as f:
        f.write(sv)
    print(f"  [Mode 2] DUT stub: {stub_path}")
    return stub_path
