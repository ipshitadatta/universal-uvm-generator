from all_modules import analyze_coverage, _parse_vcover_output, _classify_gap, print_coverage_report
# Re-exported from all_modules.py


def classify_gaps(ucdb_path, output_dir):
    """Parse UCDB and classify uncovered bins into categories."""
    import subprocess, re, os
    if not ucdb_path or not os.path.exists(ucdb_path):
        return []
    sim_dir = os.path.join(output_dir, 'sim')
    try:
        result = subprocess.run(
            ['vcover', 'report', '-details', '-zeros', ucdb_path],
            cwd=sim_dir, capture_output=True, text=True, timeout=60
        )
        gaps = []
        for line in result.stdout.split('\n'):
            if ' 0 ' in line and len(line.strip()) > 10:
                line_lower = line.lower()
                if any(k in line_lower for k in ['vip', 'bfm', '_vip']):
                    cat = 'vip_config'
                elif any(k in line_lower for k in ['stall', 'backpressure', 'back_pressure']):
                    cat = 'back_pressure'
                elif any(k in line_lower for k in ['internal', 'hls_', '_sched']):
                    cat = 'hls_internal'
                else:
                    cat = 'needs_test'
                m = re.search(r'(\S+\.sv):(\d+)', line)
                gaps.append({
                    'description': line.strip()[:100],
                    'category': cat,
                    'signal': line.strip()[:40],
                    'line': m.group(2) if m else '',
                    'file': m.group(1) if m else '',
                })
        return gaps
    except Exception:
        return []
