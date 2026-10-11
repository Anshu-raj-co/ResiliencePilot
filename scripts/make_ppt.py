#!/usr/bin/env python3
"""Generate the 6-slide submission deck: docs/submission/ResiliencePilot.pptx.

    cd backend && .venv/bin/python -m pip install -e '.[slides]'      # python-pptx (or: uv pip install python-pptx)
    .venv/bin/python ../scripts/make_ppt.py [--video-url URL] [--out PATH]

Every number on the slides is read from committed evidence at build time (run records under reports/, scores via
resiliencepilot.scoring) except the test counts, which are pinned below and must match the last `pytest -q`.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from resiliencepilot.scoring import load_runs, score_run, summarise  # noqa: E402

# Pinned from `cd backend && .venv/bin/python -m pytest -q` on feat/data-quality (macOS): update when tests change.
TESTS_CORE = 317          # existing suite, unchanged
TESTS_DQ = 79             # backend/tests/test_dq.py
TESTS_SKIPPED = 11        # live-stack tests, skipped without RP_LIVE=1

REPO_URL = "https://github.com/Anshu-raj-co/ResiliencePilot"

# Palette: deep night-navy for the "control room", teal = fresh data, coral = stale/fault, amber = quarantine.
NAVY = RGBColor(0x0E, 0x1A, 0x2B)
NAVY_2 = RGBColor(0x18, 0x2A, 0x42)
INK = RGBColor(0x14, 0x21, 0x33)
MUTED = RGBColor(0x55, 0x60, 0x6E)
LINE = RGBColor(0xD5, 0xDB, 0xE3)
TINT = RGBColor(0xF1, 0xF4, 0xF8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TEAL = RGBColor(0x00, 0x9C, 0x8F)
TEAL_SOFT = RGBColor(0x5F, 0xD6, 0xC8)
CORAL = RGBColor(0xE0, 0x4F, 0x3A)
AMBER = RGBColor(0xE8, 0xA3, 0x17)
ICE = RGBColor(0xC9, 0xD6, 0xE6)

HEAD, BODY = "Cambria", "Calibri"
W, H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.6)


# ---------------------------------------------------------------- evidence

def evidence() -> dict:
    runs = sorted(p for d in ("day1", "day2", "guardrail-demo") for p in (REPO / "reports" / d).glob("2026*.json"))
    scores = [score_run(r) for r in load_runs(REPO / "reports" / "day2")]
    full = [s["score"] for s in scores if s["scorable"] and not s["partial"]]
    return {"runs": len(runs), "median": statistics.median(full), "n_full": len(full), "by_type": summarise(scores)}


# ---------------------------------------------------------------- helpers

def bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def text(slide, x, y, w, h, runs, *, size=16, color=INK, font=BODY, bold=False, italic=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, name=None, spacing=None):
    """`runs`: a string, or a list of paragraphs; each paragraph a string or a list of (text, overrides) tuples."""
    box = slide.shapes.add_textbox(x, y, w, h)
    if name:
        box.name = name
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paras = [runs] if isinstance(runs, str) else runs
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing:
            p.space_after = Pt(spacing)
        for t, o in ([(para, {})] if isinstance(para, str) else para):
            r = p.add_run()
            r.text = t
            f = r.font
            f.name, f.size = o.get("font", font), Pt(o.get("size", size))
            f.bold, f.italic = o.get("bold", bold), o.get("italic", italic)
            f.color.rgb = o.get("color", color)
            if "link" in o:
                r.hyperlink.address = o["link"]
    return box


def shape(slide, kind, x, y, w, h, fill=None, line=None, name=None, radius=None, shadow=False):
    s = slide.shapes.add_shape(kind, x, y, w, h)
    if name:
        s.name = name
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(1)
    if radius is not None and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    if not shadow:
        # explicit empty effect list: no theme shadow on cards
        sp_pr = s._element.spPr
        sp_pr.append(sp_pr.makeelement(qn("a:effectLst"), {}))
    s.text_frame.text = ""
    return s


def badge(slide, x, y, d, label, fill, color=WHITE, size=16, name=None):
    """The deck's motif: a solid circle with a short label (initials, step number, icon glyph)."""
    c = shape(slide, MSO_SHAPE.OVAL, x, y, d, d, fill=fill, name=name)
    tf = c.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.name, r.font.size, r.font.bold, r.font.color.rgb = BODY, Pt(size), True, color
    return c


def arrow(slide, x1, y1, x2, y2, color=MUTED, width=1.75, dashed=False):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, *(int(v) for v in (x1, y1, x2, y2)))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    if dashed:
        ln.append(ln.makeelement(qn("a:prstDash"), {"val": "dash"}))
    ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return c


def title(slide, t, sub=None, dark=False):
    text(slide, MARGIN, Inches(0.45), W - 2 * MARGIN, Inches(0.75), t, size=36, font=HEAD, bold=True,
         color=WHITE if dark else INK, name="Title")
    if sub:
        text(slide, MARGIN, Inches(1.2), W - 2 * MARGIN, Inches(0.4), sub, size=16, color=ICE if dark else MUTED,
             name="Subtitle")


def footer(slide, n, dark=False):
    text(slide, MARGIN, H - Inches(0.45), Inches(8), Inches(0.3), "ResiliencePilot SDV  ·  Bosch Hackathon 2026",
         size=10, color=ICE if dark else MUTED, name="Footer")
    text(slide, W - MARGIN - Inches(1), H - Inches(0.45), Inches(1), Inches(0.3), f"{n} / 6", size=10,
         color=ICE if dark else MUTED, align=PP_ALIGN.RIGHT, name="Slide number")


# ---------------------------------------------------------------- slides

def slide_1(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg(s, NAVY)
    badge(s, MARGIN, Inches(0.8), Inches(0.5), "RP", TEAL, size=14, name="Logo")
    text(s, MARGIN + Inches(0.7), Inches(0.86), Inches(8), Inches(0.4), "Team & project overview", size=14,
         color=ICE, name="Kicker")
    text(s, MARGIN, Inches(1.6), Inches(12), Inches(1.0), "ResiliencePilot SDV", size=54, font=HEAD, bold=True,
         color=WHITE, name="Title")
    text(s, MARGIN, Inches(2.65), Inches(12), Inches(0.6), "HTTP 200 is not recovery. Fresh data is.", size=26,
         font=HEAD, italic=True, color=TEAL_SOFT, name="Tagline")
    text(s, MARGIN, Inches(3.35), Inches(11.5), Inches(0.7),
         "A local-first resilience workbench for the Eclipse SDV Fleet Management Blueprint: bounded fault "
         "experiments behind a deterministic safety controller, recovery verified from advancing vehicle telemetry.",
         size=16, color=ICE, name="Summary")

    team = [("VR", "Vaibhav Raghav", "Lead & Safety Architecture"),
            ("AR", "Anshu Raj", "Full-Stack & Observability"),
            ("NT", "Nishant Tripathi", "Systems & Chaos Engineering"),
            ("J", "Jahnavi", "Data Pipelines & Analytics"),
            ("S", "Samhithaa", "Validation & Test Automation")]
    gap = Inches(0.25)
    cw = int((W - 2 * MARGIN - 4 * gap) / 5)
    y = Inches(4.55)
    for i, (ini, name, role) in enumerate(team):
        x = MARGIN + i * (cw + gap)
        shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, cw, Inches(2.15), fill=NAVY_2, radius=0.08, name=f"Team card {i+1}")
        badge(s, x + Inches(0.25), y + Inches(0.25), Inches(0.7), ini, TEAL, size=18, name=f"Initials {i+1}")
        text(s, x + Inches(0.25), y + Inches(1.1), cw - Inches(0.5), Inches(0.4), name, size=16, bold=True,
             color=WHITE, name=f"Name {i+1}")
        text(s, x + Inches(0.25), y + Inches(1.5), cw - Inches(0.5), Inches(0.55), role, size=13, color=ICE,
             name=f"Role {i+1}")
    s.notes_slide.notes_text_frame.text = (
        "ResiliencePilot SDV. Our claim: an API that answers HTTP 200 is not proof the fleet data is fresh. "
        "Introduce the five team members and their areas.")


def slide_2(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg(s, WHITE)
    title(s, "The illusion of health", "The rFMS API keeps answering HTTP 200 while vehicle telemetry is frozen")

    # Left: what a health check sees vs what the vehicle data says (shape of a recorded telemetry_stall poll)
    x, y, w = MARGIN, Inches(1.95), Inches(5.9)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, Inches(4.6), fill=TINT, radius=0.04, name="Contrast panel")
    text(s, x + Inches(0.35), y + Inches(0.3), w - Inches(0.7), Inches(0.35), "One poll during a telemetry stall",
         size=14, bold=True, color=MUTED, name="Panel label")
    rows = [("✓", TEAL, "Health check", "HTTP 200 · container running · receivedDateTime fresh"),
            ("✖", CORAL, "Vehicle data", "createdDateTime frozen: no new telemetry from the truck"),
            ("!", AMBER, "Verdict", "STALE while HTTP 200: a silent failure")]
    for i, (glyph, col, head, body) in enumerate(rows):
        ry = y + Inches(0.85) + i * Inches(1.2)
        badge(s, x + Inches(0.35), ry, Inches(0.6), glyph, col, size=18, name=f"Signal {i+1}")
        text(s, x + Inches(1.15), ry - Inches(0.02), w - Inches(1.5), Inches(0.35), head, size=17, bold=True,
             name=f"Signal head {i+1}")
        text(s, x + Inches(1.15), ry + Inches(0.36), w - Inches(1.5), Inches(0.6), body, size=14, color=MUTED,
             name=f"Signal body {i+1}")
    text(s, x + Inches(0.35), y + Inches(3.95), w - Inches(0.7), Inches(0.5),
         "Recorded: HTTP 200 on 100 % of fault polls in every telemetry_stall and service_crash run",
         size=11, italic=True, color=MUTED, name="Evidence note")

    # Right: objectives
    ox = MARGIN + w + Inches(0.6)
    ow = W - MARGIN - ox
    text(s, ox, Inches(1.95), ow, Inches(0.4), "Objectives", size=22, bold=True, font=HEAD, name="Objectives head")
    objs = [("Continuous resilience auditing", "Observe the rFMS API like a client; judge freshness by the vehicle's "
                                               "own createdDateTime, not by status codes."),
            ("Deterministic safety controller", "Allowlisted targets only, one experiment at a time, 30 s cap, "
                                                "watchdog, kill switch, rollback in every exit path."),
            ("Automated recovery verification", "Recovered only after 3 successive fresh, advancing vehicle "
                                                "timestamps; otherwise latch for a human."),
            ("Universal data-quality normalization", "One canonical UTC format for multi-region timestamps; "
                                                     "quarantine what cannot be parsed without guessing.")]
    for i, (h, b) in enumerate(objs):
        oy = Inches(2.6) + i * Inches(1.0)
        badge(s, ox, oy, Inches(0.5), str(i + 1), NAVY, size=16, name=f"Objective number {i+1}")
        text(s, ox + Inches(0.7), oy - Inches(0.04), ow - Inches(0.7), Inches(0.9),
             [[(h, {"bold": True, "size": 16})], [(b, {"size": 14, "color": MUTED})]], name=f"Objective {i+1}")
    footer(s, 2)
    s.notes_slide.notes_text_frame.text = (
        "receivedDateTime is the server's clock, so it always looks fresh. createdDateTime is stamped on the vehicle "
        "side. During a telemetry stall every poll returned 200 while createdDateTime stopped advancing.")


def _node(s, x, y, w, h, label, sub, fill, color=WHITE, subcolor=ICE, name=None):
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill=fill, radius=0.12, name=name)
    text(s, x + Inches(0.05), y + Inches(0.06), w - Inches(0.1), h - Inches(0.12),
         [[(label, {"bold": True, "size": 12, "color": color})], [(sub, {"size": 10, "color": subcolor})]],
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, name=(name or label) + " label")


def slide_3(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg(s, WHITE)
    title(s, "Architecture & process flow", "Blueprint data path (unchanged) with ResiliencePilot observing and "
                                            "controlling it from outside")

    lanes = [("Vehicle edge", [("csv-provider", "replays signals"), ("KUKSA databroker", "VSS signals"),
                               ("fms-forwarder", "builds VehicleStatus")]),
             ("Transport", [("Zenoh router", "edge → cloud")]),
             ("Cloud backend", [("fms-consumer", "decodes"), ("InfluxDB", "time series"),
                                ("fms-server", "rFMS REST API")])]
    nw, nh, ng = Inches(1.3), Inches(0.9), Inches(0.3)
    lane_gap = Inches(0.28)
    y_lane, lane_h = Inches(1.9), Inches(1.75)
    x = MARGIN
    centers = []
    for li, (lname, nodes) in enumerate(lanes):
        lw = len(nodes) * nw + (len(nodes) - 1) * ng + Inches(0.4)
        shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y_lane, lw, lane_h, fill=TINT, radius=0.06, name=f"Lane {lname}")
        text(s, x + Inches(0.2), y_lane + Inches(0.12), lw - Inches(0.4), Inches(0.3), lname.upper(), size=11,
             bold=True, color=MUTED, name=f"Lane label {lname}")
        for ni, (n, sub) in enumerate(nodes):
            nx = x + Inches(0.2) + ni * (nw + ng)
            ny = y_lane + Inches(0.6)
            _node(s, nx, ny, nw, nh, n, sub, NAVY, name=n)
            centers.append((nx, ny, nx + nw, ny + nh / 2))
        x += lw + lane_gap
    for (ax, ay, ar, am), (bx, by, br, bm) in zip(centers, centers[1:]):
        arrow(s, ar + Inches(0.02), am, bx - Inches(0.02), bm, color=TEAL, width=2)

    # ResiliencePilot layer
    ry = Inches(4.35)
    text(s, MARGIN, ry, Inches(6), Inches(0.3), "RESILIENCEPILOT (local, outside the blueprint)", size=11, bold=True,
         color=MUTED, name="RP lane label")
    rp = [("Observer", "polls GET /rfms/vehiclestatuses, read-only", TEAL),
          ("Safety controller", "allowlist · lock · 30 s watchdog · guardrails", NAVY),
          ("Fault adapters", "docker pause/stop on allowlisted containers", CORAL),
          ("Recovery verifier", "3 fresh, advancing createdDateTime", TEAL)]
    bw, bg_ = Inches(2.85), Inches(0.2)
    for i, (n, sub, col) in enumerate(rp):
        bx = MARGIN + i * (bw + bg_)
        _node(s, bx, ry + Inches(0.4), bw, Inches(0.85), n, sub, col, name=f"RP {n}")
    server = centers[-1]
    arrow(s, server[0] + nw / 2, server[1] + nh, MARGIN + bw / 2, ry + Inches(0.4), color=TEAL, dashed=True)
    fwd = centers[2]
    arrow(s, MARGIN + 2 * (bw + bg_) + bw / 2, ry + Inches(0.4), fwd[0] + nw / 2, fwd[1] + nh, color=CORAL,
          dashed=True)

    # Data-quality gate
    dy = Inches(5.8)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, MARGIN, dy, W - 2 * MARGIN, Inches(0.95), fill=None, line=AMBER,
          radius=0.12, name="DQ gate frame")
    badge(s, MARGIN + Inches(0.2), dy + Inches(0.2), Inches(0.55), "DQ", AMBER, size=13, name="DQ badge")
    text(s, MARGIN + Inches(0.95), dy + Inches(0.12), W - 2 * MARGIN - Inches(1.2), Inches(0.75),
         [[("New: data-quality normalization gate before storage  ", {"bold": True, "size": 15}),
           ("synthetic logistics feed → normalize to UTC ISO-8601 → validate → batch policy (≤ 5 % errors: commit + "
            "quarantine; above: roll back) → SQLite clean / quarantine / audit", {"size": 13, "color": MUTED})],
          [("Runs alongside the real rFMS telemetry; it does not modify the blueprint's InfluxDB path.",
            {"size": 11, "italic": True, "color": MUTED})]], name="DQ gate text")
    footer(s, 3)
    s.notes_slide.notes_text_frame.text = (
        "Telemetry: csv-provider → KUKSA databroker → fms-forwarder → Zenoh → fms-consumer → InfluxDB → fms-server. "
        "ResiliencePilot reads the API like a client and controls only allowlisted containers. The data-quality gate "
        "is a separate module on a synthetic logistics feed with its own SQLite store.")


def slide_4(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg(s, WHITE)
    title(s, "Tools & capabilities", "Open-source building blocks, all running locally")
    tools = [("Eclipse KUKSA.val", "In-vehicle VSS databroker", "Vehicle edge (blueprint)"),
             ("Eclipse Zenoh", "Edge-to-cloud transport", "Transport (blueprint)"),
             ("InfluxDB", "Time-series storage of vehicle status", "Cloud backend (blueprint)"),
             ("FastAPI / Uvicorn", "Dashboard, observer API, read-only insights", "ResiliencePilot"),
             ("Docker Compose", "Isolated local sandbox; faults only on allowlisted containers", "Sandbox"),
             ("Ollama · Qwen2.5", "Local advisory AI: explains and ranks, never acts", "ResiliencePilot (advisory)"),
             ("Three.js", "3D digital-twin replay of recorded runs (offline page)", "Visualization"),
             ("SQLite", "Clean records, quarantine and batch audit of the DQ gate", "Data-quality gate")]
    rows, cols = len(tools) + 1, 3
    tx, ty = MARGIN, Inches(1.85)
    tw = W - 2 * MARGIN
    tbl = s.shapes.add_table(rows, cols, tx, ty, tw, Inches(0.5) * rows).table
    s.shapes[-1].name = "Tools table"
    widths = [Inches(2.9), Inches(6.4), tw - Inches(9.3)]
    for i, wd in enumerate(widths):
        tbl.columns[i].width = wd
    header = ("Tool", "Role in ResiliencePilot", "Layer")
    for r in range(rows):
        tbl.rows[r].height = Inches(0.5)
        for c in range(cols):
            cell = tbl.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY if r == 0 else (TINT if r % 2 == 0 else WHITE)
            cell.margin_left = cell.margin_right = Inches(0.15)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = header[c] if r == 0 else tools[r - 1][c]
            f = run.font
            f.name, f.size = BODY, Pt(14 if r == 0 or c != 2 else 13)
            f.bold = r == 0 or c == 0
            f.color.rgb = WHITE if r == 0 else (INK if c < 2 else MUTED)
    text(s, MARGIN, Inches(6.55), tw, Inches(0.35),
         "Blueprint components are vendored unchanged (Apache-2.0); our code adapts them through Compose overrides.",
         size=11, italic=True, color=MUTED, name="Table note")
    footer(s, 4)
    s.notes_slide.notes_text_frame.text = "Walk the table top to bottom: blueprint stack first, then our layer."


def slide_5(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg(s, WHITE)
    title(s, "Issues identified & dual remediation", "What we found, what the system handles on its own, and what a "
                                                     "human follows up")
    cols = [
        ("Issues identified", CORAL, "!", [
            ("Telemetry stall", "HTTP 200 on every fault poll while data froze"),
            ("Service crash", "forwarder ignores SIGTERM (exit 137); same silent stale symptom"),
            ("Dependency cascade", "stale HTTP 200 for ~21–24 s after InfluxDB returned (DNS: hypothesis)"),
            ("Timestamp formats", "EU / US / IN / ISO / epoch mixed in one feed (synthetic)"),
            ("Future timestamps", "records stamped ahead of system UTC (synthetic)")]),
        ("Automated handling", TEAL, "⚙", [
            ("30 s watchdog", "independent thread enforces the fault deadline and rollback"),
            ("Recovery gate", "3 consecutive fresh, advancing timestamps, or LATCHED_UNSAFE"),
            ("5 % batch policy", "≤ 5 % bad: commit + quarantine; above: roll back the batch")]),
        ("Manual follow-up", NAVY, "✎", [
            ("AI analyst playbooks", "verify → mitigate → fix; advisory only, checked vs evidence"),
            ("Quarantine review", "operator inspects reason codes; rolled-back batches flagged"),
            ("Logged reset", "a human clears LATCHED_UNSAFE after checking the stack")]),
    ]
    gap = Inches(0.3)
    cw = int((W - 2 * MARGIN - 2 * gap) / 3)
    y = Inches(1.85)
    for i, (head, col, glyph, items) in enumerate(cols):
        x = MARGIN + i * (cw + gap)
        shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, cw, Inches(4.9), fill=TINT, radius=0.04, name=f"Column {i+1}")
        badge(s, x + Inches(0.25), y + Inches(0.25), Inches(0.55), glyph, col, size=18, name=f"Column icon {i+1}")
        text(s, x + Inches(0.95), y + Inches(0.3), cw - Inches(1.1), Inches(0.45), head, size=20, bold=True,
             font=HEAD, name=f"Column head {i+1}")
        paras = []
        for t, d in items:
            paras.append([(t, {"bold": True, "size": 14})])
            paras.append([(d, {"size": 12, "color": MUTED})])
        box = text(s, x + Inches(0.3), y + Inches(1.05), cw - Inches(0.55), Inches(3.75), paras,
                   name=f"Column body {i+1}")
        for j, p in enumerate(box.text_frame.paragraphs):
            p.space_after = Pt(9 if j % 2 else 1)
    footer(s, 5)
    s.notes_slide.notes_text_frame.text = (
        "The first three issues come from recorded live runs (reports/day2/README.md); the DNS explanation is a "
        "hypothesis, not verified. The last two are handled by the data-quality gate on a synthetic feed.")


def slide_6(prs, ev, video_url):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg(s, NAVY)
    title(s, "Demo, results & resilience score", "All numbers from recorded runs and the test suite", dark=True)
    bt = ev["by_type"]
    stats = [(str(ev["runs"]), "recorded experiment runs", "reports/day1 · day2 · guardrail-demo"),
             (f"{TESTS_CORE + TESTS_DQ}", "unit tests passing",
              f"{TESTS_CORE} core + {TESTS_DQ} data quality · {TESTS_SKIPPED} live skipped"),
             (f"{ev['median']:.2f}".rstrip("0").rstrip("."), "median Resilience Score",
              f"{ev['n_full']} full-window Day-2 runs · stall {bt['telemetry_stall']['median']} · "
              f"crash {bt['service_crash']['median']} · InfluxDB {bt['dependency_failure']['median']}")]
    gap = Inches(0.3)
    cw = int((W - 2 * MARGIN - 2 * gap) / 3)
    for i, (big, label, src) in enumerate(stats):
        x = MARGIN + i * (cw + gap)
        shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, Inches(1.95), cw, Inches(2.3), fill=NAVY_2, radius=0.06,
              name=f"Stat card {i+1}")
        text(s, x + Inches(0.3), Inches(2.1), cw - Inches(0.6), Inches(1.0), big, size=60, bold=True, font=HEAD,
             color=TEAL_SOFT, name=f"Stat {i+1}")
        text(s, x + Inches(0.3), Inches(3.15), cw - Inches(0.6), Inches(0.4), label, size=16, bold=True, color=WHITE,
             name=f"Stat label {i+1}")
        text(s, x + Inches(0.3), Inches(3.55), cw - Inches(0.6), Inches(0.6), src, size=11, color=ICE,
             name=f"Stat source {i+1}")

    # Video panel
    vy = Inches(4.6)
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, MARGIN, vy, W - 2 * MARGIN, Inches(1.9), fill=NAVY_2, radius=0.06,
          name="Video panel")
    play = shape(s, MSO_SHAPE.OVAL, MARGIN + Inches(0.35), vy + Inches(0.4), Inches(1.1), Inches(1.1), fill=CORAL,
                 name="Play button")
    tri = shape(s, MSO_SHAPE.ISOSCELES_TRIANGLE, MARGIN + Inches(0.75), vy + Inches(0.72), Inches(0.42),
                Inches(0.46), fill=WHITE, name="Play glyph")
    tri.rotation = 90
    link = video_url or "../demo/VIDEO_SCRIPT.md"
    for shp in (play, tri):
        shp.click_action.hyperlink.address = link
    video_line = ([("Watch the 2-minute demo  ", {"bold": True, "size": 20, "color": WHITE}),
                   (video_url, {"size": 14, "color": TEAL_SOFT, "link": video_url})] if video_url else
                  [("2-minute demo video  ", {"bold": True, "size": 20, "color": WHITE}),
                   ("link added at submission", {"size": 14, "italic": True, "color": ICE})])
    text(s, MARGIN + Inches(1.8), vy + Inches(0.35), Inches(10.2), Inches(1.4), [
        video_line,
        [("Script: ", {"size": 14, "color": ICE}),
         ("docs/demo/VIDEO_SCRIPT.md", {"size": 14, "color": TEAL_SOFT, "link": "../demo/VIDEO_SCRIPT.md"}),
         ("   ·   Local walkthrough: ", {"size": 14, "color": ICE}),
         ("docs/scene/index.html", {"size": 14, "color": TEAL_SOFT, "link": "../scene/index.html"})],
        [("Repository: ", {"size": 14, "color": ICE}), (REPO_URL, {"size": 14, "color": TEAL_SOFT, "link": REPO_URL})],
    ], spacing=6, name="Video text")
    footer(s, 6, dark=True)
    s.notes_slide.notes_text_frame.text = (
        "Close with the tagline: HTTP 200 is not recovery. Fresh data is. Numbers: run count = committed run records; "
        "median = full-window Day-2 runs scored by rp score; tests = pytest -q on feat/data-quality.")


def build(out: Path, video_url: str | None) -> Path:
    ev = evidence()
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    prs.core_properties.title = "ResiliencePilot SDV"
    prs.core_properties.subject = "HTTP 200 is not recovery. Fresh data is."
    prs.core_properties.author = "ResiliencePilot team"
    slide_1(prs)
    slide_2(prs)
    slide_3(prs)
    slide_4(prs)
    slide_5(prs)
    slide_6(prs, ev, video_url)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO / "docs" / "submission" / "ResiliencePilot.pptx"))
    ap.add_argument("--video-url", help="public URL of the demo video (shown on slide 6)")
    args = ap.parse_args()
    out = build(Path(args.out), args.video_url)
    print(f"wrote {out} (6 slides)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
