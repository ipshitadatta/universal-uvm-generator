"""
sv_validator.py — Post-generation SV prose injection detector and cleaner.
Removes English prose lines from LLM-generated SV code before writing to disk.
This is the #1 cause of compile failures in UVMGen.
"""

import re

# Lines that are clearly NOT SystemVerilog
PROSE_PATTERNS = [
    re.compile(r'^\s*\*'),                          # bullet points
    re.compile(r'^\s*-\s+[A-Z]'),                  # dash + capital (prose list)
    re.compile(r'^\s*\d+\.\s+[A-Z]'),              # numbered list
    re.compile(r'^\s*[A-Z][a-z]+\s+[a-z]+\s+[a-z]+\s+[a-z]'),  # plain English sentence
    re.compile(r'(?i)^\s*(note|this|the|we|you|here|for example|in this|make sure|remember|first|then|next|finally|also|additionally|important|please)\b'),
    re.compile(r'^\s*\.\s*$'),                      # lone period
    re.compile(r'(?i)^\s*(channel|signal|task|module|class)\s+[a-z]+\s+[a-z]+'),  # prose about signals
    re.compile(r'under\s+\d+\s+lines'),             # "under 40 lines" type prose
    re.compile(r'level abstraction'),               # known injection pattern
    re.compile(r'`pkt\.',),                         # backtick prose in covergroup
    re.compile(r'^\s*Wait,\s'),                     # "Wait, ..." LLM hesitation
    re.compile(r'^\s*I\s+(need|want|will|would|can|should)\b'),  # LLM first person
]

# Lines that ARE valid SV (never remove these)
SV_PATTERNS = [
    re.compile(r'^\s*//'),                          # comment
    re.compile(r'^\s*(always|assign|module|endmodule|package|endpackage|class|endclass|function|endfunction|task|endtask|if|else|end|begin|for|while|case|endcase|typedef|struct|enum|logic|bit|int|wire|reg|input|output|inout|parameter|localparam|import|export|return|forever|repeat|fork|join)\b'),
    re.compile(r'^\s*`'),                           # macros
    re.compile(r'^\s*\$'),                          # system tasks
    re.compile(r'^\s*@'),                           # event control
    re.compile(r'^\s*#'),                           # delay (we catch this separately)
    re.compile(r'^\s*\w+\s*[<:=!]+'),              # assignment
    re.compile(r'^\s*\w+\.\w+'),                   # dotted access
    re.compile(r'^\s*\}'),                          # closing brace
    re.compile(r'^\s*\{'),                          # opening brace
    re.compile(r'^\s*;'),                           # semicolon
    re.compile(r'^\s*$'),                           # empty line
]


def clean_sv_body(code: str, proto_spec: dict = None) -> tuple:
    """
    Remove prose lines from LLM-generated SV code.
    Returns (cleaned_code, list_of_removed_lines).
    """
    lines = code.split('\n')
    cleaned = []
    removed = []

    for line in lines:
        if _is_sv_line(line):
            cleaned.append(line)
        elif _is_prose_line(line):
            removed.append(line.strip()[:60])
        else:
            # Ambiguous — keep it but flag
            cleaned.append(line)

    return '\n'.join(cleaned), removed


def clean_sv_file(content: str, proto_spec: dict = None) -> tuple:
    """
    Clean an entire SV file — remove prose from inside task/function bodies.
    Only cleans inside task/function/always blocks, not at module level.
    Returns (cleaned_content, num_fixes).
    """
    lines = content.split('\n')
    cleaned = []
    fixes = 0
    in_task = False
    depth = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track if we're inside a task/function body
        if re.search(r'\b(task|function)\b.*\(', stripped):
            in_task = True
            depth = 0

        if in_task:
            depth += stripped.count('begin') - stripped.count('end')
            if stripped in ('endtask', 'endfunction'):
                in_task = False

        # Only clean prose inside task/function bodies
        if in_task and depth > 0 and _is_prose_line(line) and not _is_sv_line(line):
            fixes += 1
            cleaned.append(f'      // [REMOVED PROSE: {stripped[:40]}]')
            continue

        cleaned.append(line)

    return '\n'.join(cleaned), fixes


def validate_enum(content: str) -> str:
    """
    Fix truncated typedef enum declarations.
    If an enum is opened but never closed, remove it entirely.
    """
    lines = content.split('\n')
    cleaned = []
    in_enum = False
    enum_buf = []

    for line in lines:
        stripped = line.strip()
        if 'typedef enum' in stripped and '{' in stripped and ';' not in stripped:
            in_enum = True
            enum_buf = [line]
            continue
        if in_enum:
            enum_buf.append(line)
            if ';' in stripped:
                # Complete enum — keep it
                cleaned.extend(enum_buf)
                in_enum = False
                enum_buf = []
            elif stripped.startswith('//') or stripped.startswith('typedef') or \
                 stripped.startswith('parameter') or (stripped == '' and len(enum_buf) > 3):
                # Truncated enum — drop it, add comment
                cleaned.append('  // [REMOVED: incomplete typedef enum]')
                in_enum = False
                enum_buf = []
                cleaned.append(line)
            continue
        cleaned.append(line)

    if in_enum:
        cleaned.append('  // [REMOVED: truncated typedef enum]')

    return '\n'.join(cleaned)


def shorten_sv_name(proto_spec: dict) -> dict:
    """
    Fix overly long protocol names that break file names.
    Max 20 chars for sv_name.
    """
    sv_name = proto_spec.get('sv_name', '')
    if len(sv_name) <= 20:
        return proto_spec

    # Try to shorten intelligently
    name = proto_spec.get('name', '')
    words = name.replace('-', ' ').replace('_', ' ').split()

    # Use acronym if > 3 words
    if len(words) > 3:
        short = ''.join(w[0].lower() for w in words[:4])
    else:
        short = sv_name[:20]

    proto_spec = proto_spec.copy()
    proto_spec['sv_name'] = short
    print(f"  [FIX] sv_name shortened: {sv_name} → {short}")
    return proto_spec


def _is_sv_line(line: str) -> bool:
    """Return True if this line looks like valid SV."""
    for pattern in SV_PATTERNS:
        if pattern.search(line):
            return True
    return False


def _is_prose_line(line: str) -> bool:
    """Return True if this line looks like English prose."""
    stripped = line.strip()
    if not stripped:
        return False
    for pattern in PROSE_PATTERNS:
        if pattern.search(stripped):
            return True
    return False


def strip_markdown_fences(content: str) -> str:
    import re
    content = re.sub(r"```\w*\n?", "", content)
    content = re.sub(r"```", "", content)
    return content


def dedup_properties(content: str) -> str:
    """Remove duplicate SVA property/assert declarations."""
    import re
    seen = set()
    lines = content.split("\n")
    cleaned = []
    for line in lines:
        m = re.match(r"\s*(property|a_\w+:|c_\w+:)\s+(\w+)", line)
        if m:
            name = m.group(2)
            if name in seen:
                cleaned.append(f"  // [REMOVED DUPLICATE: {name}]")
                continue
            seen.add(name)
        cleaned.append(line)
    return "\n".join(cleaned)
