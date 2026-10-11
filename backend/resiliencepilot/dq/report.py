"""Standalone HTML data-quality report (no network, no JavaScript): totals, outcome donut, error breakdown, batch
audit and quarantine log. Every number is read from the SQLite store."""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from html import escape

from .normalizer import to_canonical
from .store import DQStore

CSS = """
:root{--surface:#fcfcfb;--panel:#ffffff;--text:#0b0b0b;--text2:#52514e;--grid:#e4e3df;
 --good:#0ca30c;--warning:#fab219;--critical:#d03b3b;--bar:#2a78d6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--surface:#1a1a19;--panel:#232321;--text:#f0efec;
 --text2:#c3c2b7;--grid:#3a3936;--bar:#3987e5}}
:root[data-theme="dark"]{--surface:#1a1a19;--panel:#232321;--text:#f0efec;--text2:#c3c2b7;--grid:#3a3936;--bar:#3987e5}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--text);
 font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}h1{font-size:26px;margin:0 0 4px}
h2{font-size:18px;margin:32px 0 10px}.muted{color:var(--text2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:20px}
.tile{background:var(--panel);border:1px solid var(--grid);border-radius:10px;padding:14px}
.tile b{display:block;font-size:28px;font-variant-numeric:tabular-nums}.tile span{color:var(--text2);font-size:13px}
.row{display:flex;flex-wrap:wrap;gap:24px;align-items:center}.legend{list-style:none;padding:0;margin:0}
.legend li{display:flex;align-items:center;gap:8px;margin:4px 0}.sw{width:12px;height:12px;border-radius:3px}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--text2);font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums}
code{font-size:12px;word-break:break-all}.badge{font-weight:600}
"""

OUTCOMES = [("clean", "Committed clean", "var(--good)", "✔"),
            ("quarantined", "Quarantined", "var(--warning)", "⚠"),
            ("rolled_back", "In rolled-back batches", "var(--critical)", "✖")]


def _donut(parts: list[tuple[str, int, str]]) -> str:
    total = sum(v for _, v, _ in parts)
    if total == 0:
        return '<p class="muted">No records ingested yet.</p>'
    r, w, cx = 70, 26, 90
    svg = [f'<svg viewBox="0 0 180 180" width="180" height="180" role="img" aria-label="Record outcomes">']
    start = -math.pi / 2
    for label, value, color in parts:
        if value == 0:
            continue
        frac = value / total
        if frac >= 0.9999:
            svg.append(f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="{color}" stroke-width="{w}">'
                       f'<title>{escape(label)}: {value} (100%)</title></circle>')
            break
        end = start + frac * 2 * math.pi
        x1, y1, x2, y2 = cx + r * math.cos(start), cx + r * math.sin(start), cx + r * math.cos(end), cx + r * math.sin(end)
        large = 1 if end - start > math.pi else 0
        svg.append(f'<path d="M{x1:.2f},{y1:.2f} A{r},{r} 0 {large} 1 {x2:.2f},{y2:.2f}" fill="none" stroke="{color}" '
                   f'stroke-width="{w}"><title>{escape(label)}: {value} ({frac:.1%})</title></path>')
        # 2px surface gap between segments
        svg.append(f'<line x1="{cx + (r - w / 2 - 1) * math.cos(end):.2f}" y1="{cx + (r - w / 2 - 1) * math.sin(end):.2f}" '
                   f'x2="{cx + (r + w / 2 + 1) * math.cos(end):.2f}" y2="{cx + (r + w / 2 + 1) * math.sin(end):.2f}" '
                   f'stroke="var(--surface)" stroke-width="2"/>')
        start = end
    svg.append(f'<text x="{cx}" y="{cx - 2}" text-anchor="middle" font-size="24" font-weight="700" '
               f'fill="var(--text)">{total}</text><text x="{cx}" y="{cx + 18}" text-anchor="middle" font-size="12" '
               f'fill="var(--text2)">records</text></svg>')
    return "".join(svg)


def _bars(counts: dict[str, int]) -> str:
    if not counts:
        return '<p class="muted">No errors recorded.</p>'
    peak = max(counts.values())
    rows = []
    for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        pct = 100 * n / peak
        rows.append(f'<tr><td><code>{escape(code)}</code></td><td style="width:55%"><div title="{escape(code)}: {n}" '
                    f'style="height:14px;width:{pct:.1f}%;min-width:4px;background:var(--bar);border-radius:0 4px 4px 0">'
                    f'</div></td><td class="num">{n}</td></tr>')
    return ('<div class="wrap"><table><thead><tr><th>Error code</th><th>Share of errors</th><th>Count</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def render_report(store: DQStore, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    audits = store.audits()
    quarantine = store.quarantine()
    clean = store.count_clean()
    rolled = [a for a in audits if a["status"] == "ROLLED_BACK"]
    rolled_records = sum(a["total"] for a in rolled)
    values = {"clean": clean, "quarantined": len(quarantine), "rolled_back": rolled_records}

    errors: Counter[str] = Counter()
    for q in quarantine:
        errors.update(e["code"] for e in q["errors"])
    for a in rolled:
        errors.update(a["details"].get("error_counts", {}))

    parts = [(label, values[key], color) for key, label, color, _ in OUTCOMES]
    legend = "".join(f'<li><span class="sw" style="background:{c}"></span>{icon} {escape(label)}: '
                     f'<b>{values[k]}</b></li>' for k, label, c, icon in OUTCOMES)

    audit_rows = "".join(
        f'<tr><td><code>{escape(a["batch_id"])}</code></td><td class="badge">{escape(a["status"])}</td>'
        f'<td><code>{escape(a["event"])}</code></td><td class="num">{a["total"]}</td><td class="num">{a["valid"]}</td>'
        f'<td class="num">{a["invalid"]}</td><td class="num">{a["error_rate"]:.1%}</td>'
        f'<td class="num">{a["threshold"]:.1%}</td><td>{"yes" if a["needs_operator_review"] else "no"}</td>'
        f'<td>{escape(a["created_at"])}</td></tr>' for a in audits)

    q_rows = "".join(
        f'<tr><td><code>{escape(str(q["record_id"]))}</code></td><td><code>{escape(q["batch_id"])}</code></td>'
        f'<td>{"<br>".join(f"<code>{escape(e["code"])}</code> {escape(e["detail"])}" for e in q["errors"])}</td>'
        f'<td><code>{escape(json.dumps(q["raw"].get("timestamp_raw")))}</code></td>'
        f'<td><code>{escape(json.dumps(q["raw"].get("region")))}</code></td></tr>' for q in quarantine)

    rb_rows = "".join(
        f'<tr><td><code>{escape(a["batch_id"])}</code></td><td><code>{escape(str(r["record_id"]))}</code></td>'
        f'<td>{"<br>".join(f"<code>{escape(e["code"])}</code> {escape(e["detail"])}" for e in r["errors"])}</td></tr>'
        for a in rolled for r in a["details"].get("rejected", []))

    total_batches = len(audits)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Data Quality Report</title><style>{CSS}</style></head><body><main>
<h1>Data Quality Report</h1>
<p class="muted">ResiliencePilot data-quality gate · generated {escape(to_canonical(now))} · store
<code>{escape(store.path.name)}</code> · <b>Synthetic logistics feed</b> (complements real rFMS telemetry; see
docs/DATA_QUALITY.md).</p>
<div class="tiles">
<div class="tile"><b>{total_batches}</b><span>batches audited</span></div>
<div class="tile"><b>{clean}</b><span>clean records committed</span></div>
<div class="tile"><b>{len(quarantine)}</b><span>records quarantined</span></div>
<div class="tile"><b>{len(rolled)}</b><span>batches rolled back (operator review)</span></div>
</div>
<h2>Record outcomes</h2>
<div class="row">{_donut(parts)}<ul class="legend">{legend}</ul></div>
<h2>Errors by reason code</h2>{_bars(dict(errors))}
<h2>Batch audit</h2>
<div class="wrap"><table><thead><tr><th>Batch</th><th>Status</th><th>Event</th><th>Total</th><th>Valid</th>
<th>Invalid</th><th>Error rate</th><th>Threshold</th><th>Operator review</th><th>At (UTC)</th></tr></thead>
<tbody>{audit_rows or '<tr><td colspan="10" class="muted">No batches yet.</td></tr>'}</tbody></table></div>
<h2>Quarantine log (committed batches)</h2>
<div class="wrap"><table><thead><tr><th>Record</th><th>Batch</th><th>Errors</th><th>timestamp_raw</th><th>region</th>
</tr></thead><tbody>{q_rows or '<tr><td colspan="5" class="muted">Quarantine is empty.</td></tr>'}</tbody></table></div>
<h2>Rejected records in rolled-back batches</h2>
<p class="muted">Nothing from these batches was written to the clean or quarantine tables; the reasons are kept in
the batch audit for the operator.</p>
<div class="wrap"><table><thead><tr><th>Batch</th><th>Record</th><th>Errors</th></tr></thead>
<tbody>{rb_rows or '<tr><td colspan="3" class="muted">None.</td></tr>'}</tbody></table></div>
</main></body></html>
"""
