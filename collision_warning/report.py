"""Standalone SVG chart of the selected vehicle's optical TTC."""


def write_ttc_chart(path, series, warning, critical):
    upper = max(6, warning * 2)
    duration = max((t for t, _ in series), default=1) or 1
    left, top, width, height = 70, 60, 810, 300

    def x(t):
        return left + width * t / duration

    def y(ttc):
        return top + height * (1 - min(ttc, upper) / upper)

    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="460" viewBox="0 0 960 460">',
             '<rect width="960" height="460" fill="#101923"/>',
             '<g font-family="sans-serif" font-size="13" fill="#e4edf5">',
             '<text x="70" y="28" font-size="20">Estimated optical TTC — selected vehicle</text>',
             '<text x="70" y="47">Gaps mean TTC unavailable; selected track can change. Not collision ground truth.</text>']
    for level, color, label in ((warning, "#ffb340", "Warning"), (critical, "#ff6574", "Critical")):
        parts.append(f'<path d="M {left} {y(level):.2f} H {left + width}" stroke="{color}" stroke-dasharray="6 5"/>')
        parts.append(f'<text x="{left + width + 8}" y="{y(level):.2f}" fill="{color}">{label}</text>')
    for i in range(7):
        value = upper * i / 6
        parts.append(f'<text x="20" y="{y(value) + 5:.2f}">{value:.1f}s</text>')
    for i in range(6):
        t = duration * i / 5
        parts.append(f'<text x="{x(t):.2f}" y="385">{t:.1f}s</text>')
    parts.append(f'<path d="M {left} {top} V {top + height} H {left + width}" fill="none" stroke="#aabbcc"/>')
    segment = []
    previous_id = None
    # Each entry is (video timestamp, (track ID, TTC) or None).
    for t, value in [*series, (duration, None)]:
        if value is None or (previous_id is not None and value[0] != previous_id):
            if segment:
                parts.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="#65d4ff" stroke-width="2"/>')
            segment = []
        if value is not None:
            previous_id = value[0]
            segment.append(f'{x(t):.2f},{y(value[1]):.2f}')
        else:
            previous_id = None
    parts.extend([f'<text x="70" y="420">Video time (seconds). Chart caps values at {upper:g}s; CSV retains original estimates.</text>',
                  '</g></svg>'])
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
