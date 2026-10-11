#!/usr/bin/env python3
"""Generate the 8-slide submission deck: docs/submission/ResiliencePilot_Presentation.pptx.

    cd backend && .venv/bin/python -m pip install -e '.[slides]'      # python-pptx (or: uv pip install python-pptx)
    .venv/bin/python ../scripts/make_ppt.py [--video-url URL] [--out PATH]

The deck follows the demo video (reports/generated/demo-video/, chapters in VIDEO_CHAPTERS below) and the
submission guideline sections. Run counts and score medians are read from committed run records at build time;
the other numbers are pinned below with the file they come from. Screenshots are the committed ones in docs/.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from resiliencepilot.scoring import load_runs, score_run, summarise  # noqa: E402

# ---- pinned facts (source in the comment; update when the source changes)
TESTS_CORE, TESTS_DQ, TESTS_SKIPPED = 317, 79, 11   # `pytest -q` on main after PR #34: 396 passed, 11 skipped
TEAM_NAME, TEAM_NO = "Team ResiliencePilot", "26"
INSTITUTE, BATCH = "Manipal Institute of Technology, Bengaluru", "Batch of 2027"
TEAM = [("VR", "Vaibhav Raghav", "Lead & Safety Architecture"),
        ("AR", "Anshu Raj", "Full-Stack & Observability"),
        ("NT", "Nishant Tripathi", "Systems & Chaos Engineering"),
        ("J", "Jahnavi", "Data Pipelines & Analytics"),
        ("S", "Samhithaa", "Validation & Test Automation")]
BLUEPRINT_PIN = "3d514ac72a"                       # README.md (submodule pin)
STALL_RUN = dict(id="3155f1", ok=34, n=34, stale=17)  # reports/day1/20261010T094045Z-3155f1.json
TTD_MEDIAN_DAY1 = 4.85                             # docs/demo/DAY1_REPORT.md (6 full-duration runs)
# Guardrails tripped live: reports/guardrail-demo/README.md, reports/day2/20261010T182500Z-4aa9f7.json
GUARDS = [("Error rate", "77b84c", "first non-200 aborts; then fail-closed latch"),
          ("Latency p95", "8ad2b8", "deliberately tight 30 ms cap; tripped at 1.88 s"),
          ("Blast radius", "4aa9f7", "neighbour container changed; rollback + latch")]
# Live run recorded in the demo video (backend/data/runs/20261011T082839Z-2b1f95.json)
VIDEO_RUN = dict(id="2b1f95", ttd=4.99, rec=3.78, max_age=11.84, score=47.6, approver="Nishant Tripathi")
# Demo video chapters (reports/generated/demo-video/ResiliencePilot_demo_voiceover.md), total 2:56, narrated
VIDEO_LEN = "2:56"
VIDEO_CHAPTERS = [("0:00", "Team, architecture, our sandbox, healthy live dashboard"),
                  ("0:38", "Live telemetry_stall: human approval, HTTP 200 while data freezes"),
                  ("1:07", "Verified recovery (3 advancing timestamps), metrics, Resilience Score"),
                  ("1:31", "Findings + 3D replay of the InfluxDB outage that latched"),
                  ("1:57", "AI analysis: 1 of 7 answers accepted; safety controls, kill switch"),
                  ("2:34", "Data-quality gate (2/100 committed vs 10/100 rolled back), results")]
REPO_URL = "https://github.com/Anshu-raj-co/ResiliencePilot"
SHOT = {
    "hero": "docs/scene/screenshots/layout-retina-2x-1280x800.png",
    "explainer": "docs/demo/explainer-chart.png",
    "building": "docs/scene/screenshots/m2-building-fms-forwarder.png",
    "road": "docs/scene/screenshots/m2-road-forwarder-router.png",
    "dash": "docs/demo/dashboard-day2.png",
    "hud": "docs/scene/screenshots/m3-hud-impact-graph-gauges.png",
    "stale": "docs/demo/dashboard-stale.png",
    "findings": "docs/scene/screenshots/m3-findings-score-tab.png",
}

# Palette: night navy "control room", teal = fresh data, coral = stale/fault, amber = quarantine/caution.
NAVY = RGBColor(0x0E, 0x1A, 0x2B)
NAVY_2 = RGBColor(0x18, 0x2A, 0x42)
INK = RGBColor(0x14, 0x21, 0x33)
MUTED = RGBColor(0x55, 0x60, 0x6E)
LINE = RGBColor(0xD5, 0xDB, 0xE3)
TINT = RGBColor(0xF1, 0xF4, 0xF8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TEAL = RGBColor(0x00, 0x8C, 0x80)
TEAL_SOFT = RGBColor(0x5F, 0xD6, 0xC8)
TEAL_TINT = RGBColor(0xE3, 0xF5, 0xF2)
CORAL = RGBColor(0xD9, 0x48, 0x34)
CORAL_TINT = RGBColor(0xFB, 0xE9, 0xE6)
AMBER = RGBColor(0xC9, 0x86, 0x00)
AMBER_TINT = RGBColor(0xFD, 0xF3, 0xDC)
ICE = RGBColor(0xC9, 0xD6, 0xE6)
VIOLET = RGBColor(0x6A, 0x4C, 0xB8)

HEAD, BODY, MONO = "Cambria", "Calibri", "Courier New"
W, H = Inches(13.333), Inches(7.5)
MARGIN = Inches(0.55)
N_SLIDES = 8


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


def text(slide, x, y, w, h, runs, *, size=14, color=INK, font=BODY, bold=False, italic=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, name=None, spacing=None):
    """`runs`: a string, or a list of paragraphs; each paragraph a string or a list of (text, overrides) tuples."""
    box = slide.shapes.add_textbox(int(x), int(y), int(w), int(h))
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
    s = slide.shapes.add_shape(kind, int(x), int(y), int(w), int(h))
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
        sp_pr = s._element.spPr
        sp_pr.append(sp_pr.makeelement(qn("a:effectLst"), {}))
    s.text_frame.text = ""
    return s


def card(slide, x, y, w, h, fill=TINT, line=None, name=None, radius=0.06):
    return shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill=fill, line=line, name=name, radius=radius)


def badge(slide, x, y, d, label, fill, color=WHITE, size=14, name=None):
    """The deck's motif: a solid circle with a short label (initials, step number, glyph)."""
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


def pill(slide, x, y, label, fill, color=WHITE, size=12, font=BODY, name=None, h=Inches(0.36), pad=Inches(0.16)):
    """Rounded label sized to its text; returns its width."""
    w = int(Pt(size) * (0.6 if font == MONO else 0.55) * len(label) + 2 * pad)
    s = shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill=fill, name=name or f"Pill {label}", radius=0.5)
    tf = s.text_frame
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.name, r.font.size, r.font.bold, r.font.color.rgb = font, Pt(size), True, color
    return w


def arrow(slide, x1, y1, x2, y2, color=MUTED, width=1.75, dashed=False):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, *(int(v) for v in (x1, y1, x2, y2)))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    if dashed:
        ln.append(ln.makeelement(qn("a:prstDash"), {"val": "dash"}))
    ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return c


def picture(slide, key, x, y, w, h, crop=(0, 0, 0, 0), name=None, border=LINE):
    """Place a screenshot to fill the box exactly: `crop` (l, t, r, b fractions) picks the region, then the
    remaining excess is trimmed (centred) so the aspect ratio is never distorted."""
    path = REPO / SHOT[key]
    iw, ih = Image.open(path).size
    l, t, r, b = crop
    cw, ch = iw * (1 - l - r), ih * (1 - t - b)
    box_ratio = w / h
    if cw / ch > box_ratio:                       # too wide: trim the sides
        extra = (cw - ch * box_ratio) / iw
        l, r = l + extra / 2, r + extra / 2
    else:                                         # too tall: trim the bottom (keep the top of the page)
        b = b + (ch - cw / box_ratio) / ih
    pic = slide.shapes.add_picture(str(path), int(x), int(y), int(w), int(h))
    pic.crop_left, pic.crop_top, pic.crop_right, pic.crop_bottom = l, t, r, b
    pic.name = name or f"Screenshot {key}"
    if border is not None:
        pic.line.color.rgb = border
        pic.line.width = Pt(0.75)
    return pic


def caption(slide, x, y, w, t, dark=False):
    text(slide, x, y, w, Inches(0.4), t, size=10, italic=True, color=ICE if dark else MUTED, name="Caption")


def title(slide, t, sub=None, dark=False, kicker=None):
    if kicker:
        text(slide, MARGIN, Inches(0.28), Inches(9), Inches(0.3), kicker.upper(), size=11, bold=True,
             color=TEAL_SOFT if dark else TEAL, name="Kicker")
    text(slide, MARGIN, Inches(0.55), W - 2 * MARGIN, Inches(0.7), t, size=30, font=HEAD, bold=True,
         color=WHITE if dark else INK, name="Title")
    if sub:
        text(slide, MARGIN, Inches(1.2), W - 2 * MARGIN, Inches(0.4), sub, size=15, color=ICE if dark else MUTED,
             name="Subtitle")


def footer(slide, n, dark=False):
    text(slide, MARGIN, H - Inches(0.38), Inches(9), Inches(0.25),
         f"ResiliencePilot SDV  ·  {TEAM_NAME} (Team {TEAM_NO})  ·  Bosch Hackathon 2026", size=10,
         color=ICE if dark else MUTED, name="Footer")
    text(slide, W - MARGIN - Inches(1), H - Inches(0.38), Inches(1), Inches(0.25), f"{n} / {N_SLIDES}", size=10,
         color=ICE if dark else MUTED, align=PP_ALIGN.RIGHT, name="Slide number")


def notes(slide, t):
    slide.notes_slide.notes_text_frame.text = t


def new(prs, dark=False):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg(s, NAVY if dark else WHITE)
    return s


# ---------------------------------------------------------------- slides

def slide_1(prs, ev):
    s = new(prs, dark=True)
    text(s, MARGIN, Inches(0.5), Inches(6.2), Inches(0.3), "BOSCH HACKATHON 2026  ·  TEAM " + TEAM_NO, size=12,
         bold=True, color=TEAL_SOFT, name="Kicker")
    text(s, MARGIN, Inches(0.9), Inches(6.4), Inches(0.9), "ResiliencePilot SDV", size=46, font=HEAD, bold=True,
         color=WHITE, name="Title")
    text(s, MARGIN, Inches(1.8), Inches(6.2), Inches(0.7), "AI-Driven Chaos Engineering for Cloud-Native Fleet "
         "Resilience", size=18, color=ICE, name="Subtitle")
    text(s, MARGIN, Inches(2.55), Inches(6.2), Inches(0.5), "“HTTP 200 is not recovery. Fresh data is.”", size=22,
         font=HEAD, italic=True, color=TEAL_SOFT, name="Punchline")
    text(s, MARGIN, Inches(3.2), Inches(6.0), Inches(0.9),
         "A local resilience workbench and deterministic safety controller for the Eclipse SDV Fleet Management "
         "Blueprint: it injects bounded, reversible faults and proves recovery from advancing vehicle telemetry.",
         size=13, color=ICE, name="Core message")
    # team card
    ty = Inches(4.25)
    card(s, MARGIN, ty, Inches(6.0), Inches(2.6), fill=NAVY_2, name="Team card")
    text(s, MARGIN + Inches(0.25), ty + Inches(0.18), Inches(5.6), Inches(0.5),
         [[(f"{TEAM_NAME}  ·  Team No. {TEAM_NO}", {"bold": True, "size": 14, "color": WHITE})],
          [(f"{INSTITUTE}  ·  {BATCH}", {"size": 12, "color": ICE})]], name="Team header")
    for i, (ini, name, role) in enumerate(TEAM):
        col, row = i % 2, i // 2
        x = MARGIN + Inches(0.25) + col * Inches(2.9)
        y = ty + Inches(0.85) + row * Inches(0.58)
        badge(s, x, y, Inches(0.42), ini, TEAL, size=11, name=f"Initials {i+1}")
        text(s, x + Inches(0.52), y - Inches(0.02), Inches(2.3), Inches(0.5),
             [[(name, {"bold": True, "size": 12, "color": WHITE})], [(role, {"size": 10, "color": ICE})]],
             name=f"Member {i+1}")
    # hero screenshot
    hx = Inches(6.95)
    picture(s, "hero", hx, Inches(0.9), W - hx - MARGIN, Inches(4.1), crop=(0.235, 0, 0, 0), border=NAVY_2,
            name="Hero 3D digital twin")
    caption(s, hx, Inches(5.08), W - hx - MARGIN, "Offline 3D digital twin replaying a recorded telemetry_stall run "
            "(docs/scene/index.html)", dark=True)
    stats = [(str(ev["runs"]), "recorded runs"), (str(TESTS_CORE + TESTS_DQ), "tests passing"),
             ("3", "reversible fault types"), ("30 s", "hard cap per fault")]
    sw = (W - hx - MARGIN - Inches(0.3)) / 4
    for i, (big, lab) in enumerate(stats):
        x = hx + i * (sw + Inches(0.1))
        card(s, x, Inches(5.55), sw, Inches(1.3), fill=NAVY_2, name=f"Hero stat {i+1}")
        text(s, x + Inches(0.15), Inches(5.65), sw - Inches(0.3), Inches(0.6), big, size=26, bold=True, font=HEAD,
             color=TEAL_SOFT, name=f"Hero stat value {i+1}")
        text(s, x + Inches(0.15), Inches(6.25), sw - Inches(0.3), Inches(0.5), lab, size=11, color=ICE,
             name=f"Hero stat label {i+1}")
    footer(s, 1, dark=True)
    notes(s, "Hook: a fleet API that says 200 is not proof that the trucks' data is fresh. We built a workbench that "
             "proves the gap on the real Eclipse SDV blueprint and only ever declares recovery from fresh data.")


def slide_2(prs, ev):
    s = new(prs)
    title(s, "The “Illusion of Health”: HTTP 200 hides frozen vehicles",
          kicker="Problem understanding & approach")
    lx, lw = MARGIN, Inches(6.1)
    y = Inches(1.4)
    text(s, lx, y, lw, Inches(0.35), "The flaw in standard monitoring", size=17, bold=True, font=HEAD,
         name="Flaw head")
    bullets = [
        ("Health checks look at ", "docker ps = Up", " and ", "HTTP 200 OK", "."),
        ("We proved the Eclipse SDV rFMS API keeps answering ", "HTTP 200", " while vehicle telemetry is frozen.", "",
         ""),
        ("Root cause: ", "receivedDateTime", " is the cloud server's clock; ", "createdDateTime",
         " is stamped on the vehicle side."),
    ]
    paras = []
    for parts in bullets:
        runs = []
        for k, p in enumerate(parts):
            if p:
                runs.append((p, {"font": MONO, "size": 12, "bold": True} if k % 2 else {"size": 13}))
        paras.append([("›  ", {"color": CORAL, "bold": True, "size": 14})] + runs)
    text(s, lx, y + Inches(0.45), lw, Inches(1.6), paras, spacing=6, name="Flaw bullets")

    ay = Inches(3.35)
    card(s, lx, ay, lw, Inches(1.55), fill=TEAL_TINT, name="Approach card")
    text(s, lx + Inches(0.25), ay + Inches(0.15), lw - Inches(0.5), Inches(1.35), [
        [("Our approach: an independent black-box observer", {"bold": True, "size": 14, "color": TEAL})],
        [("Polls the API like a client every 0.5 s and computes true data age = ", {"size": 12}),
         ("server_time − createdDateTime", {"font": MONO, "size": 11, "bold": True}),
         (". Older than 5 s → STALE, even under HTTP 200. Faults run only through a deterministic safety "
          "controller; recovery needs 3 fresh, advancing vehicle timestamps.", {"size": 12})]],
        spacing=4, name="Approach text")
    by = Inches(5.05)
    card(s, lx, by, lw, Inches(1.65), fill=TINT, name="AI card")
    text(s, lx + Inches(0.25), by + Inches(0.15), lw - Inches(0.5), Inches(1.45), [
        [("How AI is used (advisory only)", {"bold": True, "size": 14, "color": VIOLET})],
        [("Plan: ", {"bold": True, "size": 12}), ("a local Qwen2.5 model may only reorder the rule-based, "
                                                   "allowlisted experiment ranking.", {"size": 12})],
        [("Analyze: ", {"bold": True, "size": 12}), ("it drafts root causes and verify / mitigate / fix playbooks; "
                                                      "a grounding check rejects anything the evidence does not "
                                                      "support. It never executes anything.", {"size": 12})]],
        spacing=3, name="AI text")

    rx = lx + lw + Inches(0.4)
    rw = W - MARGIN - rx
    picture(s, "explainer", rx, Inches(1.45), rw, Inches(2.65), crop=(0.02, 0.10, 0.02, 0.32), name="Explainer chart")
    caption(s, rx, Inches(4.2), rw, f"Run {STALL_RUN['id']}: data age climbs past 5 s while every poll chip stays "
                                     "green (HTTP 200). docs/demo/explainer-chart.png")
    cw = (rw - Inches(0.25)) / 2
    for i, (big, lab, col, tint) in enumerate([
            (f"{STALL_RUN['ok']} / {STALL_RUN['n']}", f"polls returned HTTP 200 during the run, {STALL_RUN['stale']} "
                                                      "of them while the data was stale", CORAL, CORAL_TINT),
            (f"{TTD_MEDIAN_DAY1} s", "median time to detect the stall (6 full-length Day-1 runs)", TEAL, TEAL_TINT)]):
        x = rx + i * (cw + Inches(0.25))
        card(s, x, Inches(4.85), cw, Inches(1.85), fill=tint, name=f"Callout {i+1}")
        text(s, x + Inches(0.2), Inches(4.97), cw - Inches(0.4), Inches(0.7), big, size=34, bold=True, font=HEAD,
             color=col, name=f"Callout value {i+1}")
        text(s, x + Inches(0.2), Inches(5.72), cw - Inches(0.4), Inches(0.9), lab, size=12, color=INK,
             name=f"Callout label {i+1}")
    footer(s, 2)
    notes(s, "receivedDateTime always looks fresh because the server stamps it at request time. createdDateTime comes "
             "from the vehicle side; when it stops advancing the fleet is blind, yet the API still says 200.")


def slide_3(prs, ev):
    s = new(prs)
    title(s, "Target: Eclipse SDV Fleet Management Blueprint",
          sub=f"Vendored unchanged (submodule {BLUEPRINT_PIN}, Apache-2.0) · 8 containers in a local Docker Compose "
              "project · no cloud, fully reproducible", kicker="Sample application")
    nodes = [("csv-provider", "replays CAN-like signals"), ("databroker", "Eclipse KUKSA.val (VSS)"),
             ("fms-forwarder", "stamps createdDateTime"), ("fms-zenoh-router", "Eclipse Zenoh · uProtocol"),
             ("fms-consumer", "decodes, writes"), ("InfluxDB", "time-series store"),
             ("fms-server", "rFMS REST API")]
    n = len(nodes)
    gap = Inches(0.22)
    nw = (W - 2 * MARGIN - (n - 1) * gap) / n
    ny = Inches(1.85)
    for i, (nm, sub) in enumerate(nodes):
        x = MARGIN + i * (nw + gap)
        hot = nm in ("fms-forwarder", "InfluxDB", "fms-server")
        card(s, x, ny, nw, Inches(0.95), fill=NAVY if hot else NAVY_2, name=f"Node {nm}", radius=0.12)
        text(s, x + Inches(0.06), ny + Inches(0.08), nw - Inches(0.12), Inches(0.8),
             [[(nm, {"bold": True, "size": 13, "color": WHITE})], [(sub, {"size": 10, "color": ICE})]],
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, name=f"Node label {nm}")
        if i:
            arrow(s, x - gap + Inches(0.02), ny + Inches(0.475), x - Inches(0.02), ny + Inches(0.475), color=TEAL,
                  width=2)
    text(s, MARGIN, ny + Inches(1.05), W - 2 * MARGIN, Inches(0.3),
         "Vehicle edge  →  transport  →  cloud backend.   Dark nodes: where ResiliencePilot reads (fms-server) or "
         "injects allowlisted faults (fms-forwarder, InfluxDB). Grafana is the 8th container.", size=11,
         italic=True, color=MUTED, name="Flow note")
    ly = Inches(3.45)
    lw = Inches(3.6)
    facts = [("Deployment", "Docker Compose project resiliencepilot-sdv, local only; our changes live in a Compose "
                            "override, never in the blueprint."),
             ("One-command start", "./scripts/stack-up.sh (normalises Windows CRLF line endings for bind-mounted "
                                   "files), then stack-status.sh checks all 8 containers + HTTP 200."),
             ("Tested on", "Windows 11 x86-64 and macOS Apple Silicon (amd64 images under emulation)."),
             ("Repository", REPO_URL.replace("https://", ""))]
    paras = []
    for h_, b_ in facts:
        paras.append([(h_, {"bold": True, "size": 13, "color": TEAL})])
        paras.append([(b_, {"size": 12})])
    box = text(s, MARGIN, ly, lw, Inches(3.3), paras, name="Deployment facts")
    for j, p in enumerate(box.text_frame.paragraphs):
        p.space_after = Pt(7 if j % 2 else 1)
    px = MARGIN + lw + Inches(0.3)
    pw = (W - MARGIN - px - Inches(0.25)) / 2
    for i, (key, cap) in enumerate([("building", "fms-forwarder inspector: image, ports, role, edges with evidence"),
                                    ("road", "forwarder → Zenoh router road: protocol, direction, source line")]):
        x = px + i * (pw + Inches(0.25))
        picture(s, key, x, ly, pw, Inches(2.95), crop=(0.22, 0.04, 0.0, 0.12), name=f"Screenshot {key}")
        caption(s, x, ly + Inches(3.0), pw, cap)
    footer(s, 3)
    notes(s, "Seven-hop telemetry journey. The forwarder stamps createdDateTime; the server answers the rFMS API. We "
             "never edit the blueprint: everything is adapted through a Compose override.")


def slide_4(prs, ev):
    s = new(prs)
    title(s, "Advisory AI vs. deterministic safety controller", kicker="Solution architecture & safety controls")
    flow = [("Advisory AI", "reorders · explains", VIOLET), ("Human approver", "types name, recorded", NAVY),
            ("Safety controller", "validates · locks · arms", TEAL), ("Fault adapter", "pause / stop, allowlisted", CORAL),
            ("Recovery verifier", "3 fresh timestamps", TEAL)]
    fy, fw, fg = Inches(1.35), Inches(2.18), Inches(0.33)
    for i, (h_, sub, col) in enumerate(flow):
        x = MARGIN + i * (fw + fg)
        card(s, x, fy, fw, Inches(0.78), fill=col, name=f"Flow {h_}", radius=0.15)
        text(s, x + Inches(0.08), fy + Inches(0.06), fw - Inches(0.16), Inches(0.66),
             [[(h_, {"bold": True, "size": 14, "color": WHITE})], [(sub, {"size": 10, "color": WHITE})]],
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, name=f"Flow label {h_}")
        if i:
            arrow(s, x - fg + Inches(0.03), fy + Inches(0.39), x - Inches(0.03), fy + Inches(0.39), color=MUTED, width=2)
    text(s, MARGIN, fy + Inches(0.85), Inches(11), Inches(0.3),
         "The AI can only suggest. Nothing reaches Docker without a named human and the deterministic controller.",
         size=11, italic=True, color=MUTED, name="Flow note")
    controls = [
        ("Dual-timestamp observer", "True data age from createdDateTime; > 5 s is STALE even under HTTP 200."),
        ("Automated guardrails", "Live auto-abort + rollback on HTTP errors, latency p95 above the armed limit, or "
                                 "any change outside the blast radius."),
        ("Deterministic controller", "Exact-name allowlist + Compose label, one run at a time (run.lock), "
                                     "independent watchdog thread: 30 s hard cap."),
        ("Verified recovery gate", "3 consecutive fresh, advancing vehicle timestamps with HTTP 200. HTTP 200 alone "
                                   "never counts."),
        ("LATCHED_UNSAFE", "Unverified rollback or recovery blocks all experiments until a logged human reset."),
        ("Independent kill switch", "scripts/kill-switch.sh unpauses / starts every allowlisted target even if the "
                                    "backend is dead."),
    ]
    gx, gy = MARGIN, Inches(2.65)
    cw, ch, gg = Inches(3.55), Inches(1.32), Inches(0.18)
    for i, (h_, b_) in enumerate(controls):
        col, row = i % 2, i // 2
        x = gx + col * (cw + gg)
        y = gy + row * (ch + gg)
        card(s, x, y, cw, ch, fill=TINT, name=f"Control {i+1}")
        badge(s, x + Inches(0.15), y + Inches(0.17), Inches(0.42), str(i + 1), CORAL if i in (4, 5) else TEAL,
              size=13, name=f"Control number {i+1}")
        text(s, x + Inches(0.7), y + Inches(0.14), cw - Inches(0.85), ch - Inches(0.2),
             [[(h_, {"bold": True, "size": 13})], [(b_, {"size": 11, "color": MUTED})]], name=f"Control text {i+1}")
    px = gx + 2 * cw + gg + Inches(0.3)
    pw = W - MARGIN - px
    picture(s, "dash", px, gy, pw, Inches(4.25), crop=(0, 0, 0, 0.53), name="Dashboard screenshot")
    caption(s, px, gy + Inches(4.3), pw, "Live dashboard: data-age gauge, HTTP chip, phase timeline, controls")
    footer(s, 4)
    notes(s, "Pipeline: the AI proposes, a human approves, and only the deterministic controller touches Docker. Six "
             "controls; every one has negative-path tests and live evidence in reports/.")


def slide_5(prs, ev):
    s = new(prs)
    title(s, "Tools, technologies & platform capabilities", kicker="Tools & capabilities")
    cats = [
        ("Languages & frameworks", TEAL, ["Python 3.13", "FastAPI", "Uvicorn", "Pydantic", "httpx",
                                          f"pytest · {TESTS_CORE + TESTS_DQ} passing"]),
        ("Automotive & networking", NAVY, ["Eclipse KUKSA.val", "COVESA VSS", "Eclipse Zenoh", "uProtocol",
                                           "rFMS REST API"]),
        ("Infrastructure & storage", CORAL, ["Docker Compose", "InfluxDB", "SQLite (DQ quarantine / audit)",
                                             "JSON run evidence"]),
        ("AI & observability", VIOLET, ["Ollama · Qwen2.5:7b (loopback, no shell / Docker)",
                                        "Three.js r160 (offline 3D twin)", "Live dashboard"]),
    ]
    cw, ch, gg = Inches(3.3), Inches(2.15), Inches(0.2)
    for i, (h_, col, items) in enumerate(cats):
        x = MARGIN + (i % 2) * (cw + gg)
        y = Inches(1.4) + (i // 2) * (ch + gg)
        card(s, x, y, cw, ch, fill=TINT, name=f"Category {i+1}")
        badge(s, x + Inches(0.18), y + Inches(0.18), Inches(0.36), "", col, name=f"Category dot {i+1}")
        text(s, x + Inches(0.65), y + Inches(0.18), cw - Inches(0.8), Inches(0.4), h_, size=14, bold=True,
             name=f"Category head {i+1}")
        text(s, x + Inches(0.25), y + Inches(0.68), cw - Inches(0.45), ch - Inches(0.75),
             [[("•  " + it, {"size": 12})] for it in items], spacing=2, name=f"Category items {i+1}")
    px = MARGIN + 2 * cw + gg + Inches(0.3)
    pw = W - MARGIN - px
    picture(s, "hud", px, Inches(1.4), pw, Inches(4.3), crop=(0.2, 0.0, 0.0, 0.0), name="HUD screenshot")
    caption(s, px, Inches(5.75), pw, "3D twin HUD: impact graph, guardrail gauges, event timeline, sources per fact")
    py = Inches(6.45)
    text(s, MARGIN, py - Inches(0.36), Inches(8), Inches(0.3), "Platform CLI (every command goes through the same "
         "safety controller)", size=11, bold=True, color=MUTED, name="CLI label")
    x = MARGIN
    for cmd in ["rp observe", "rp baseline", "rp run", "rp stop", "rp reset", "rp plan", "rp advise", "rp score",
                "rp findings", "rp serve", "python -m resiliencepilot.dq"]:
        x += pill(s, x, py, cmd, NAVY_2, size=10, font=MONO, h=Inches(0.34), pad=Inches(0.09)) + Inches(0.05)
    footer(s, 5)
    notes(s, "Everything is open source and local. The AI model runs on the laptop over loopback and has no access to "
             "the shell, Docker or credentials.")


def slide_6(prs, ev):
    s = new(prs)
    title(s, "Three reversible faults, two vulnerabilities found", kicker="Failure scenarios selected")
    bt = ev["by_type"]
    scen = [
        ("telemetry_stall", "docker pause fms-forwarder", "Publisher freeze / lost cellular uplink",
         [("HTTP 200 on 100 % of fault polls while data froze", INK),
          ("Stale detected in 4.8–5.3 s; recovery verified 2.6–3.7 s after rollback", INK)],
         "V1: silent blackout", CORAL, f"8 completed runs · median score {bt['telemetry_stall']['median']}"),
        ("service_crash", "docker stop / start fms-forwarder", "Container crash and restart",
         [("Same silent symptom: HTTP 200 with stale data", INK),
          ("Forwarder ignores SIGTERM (always SIGKILL, exit 137); recovery 2.5–3.7 s", INK)],
         "V1 confirmed", CORAL, f"5 completed runs · median score {bt['service_crash']['median']}"),
        ("dependency_failure", "docker stop / start influxDB", "Database outage",
         [("Visible failure first (HTTP 500), which is good", INK),
          ("Then ~21–24 s of HTTP 200 with frozen data after InfluxDB was back; fms logs showed 'failed to resolve "
           "host name' (DNS caching: hypothesis, not verified); 4–31 s of stored data lost", CORAL)],
         "V2: stale after recovery", CORAL, f"3 completed runs · median score {bt['dependency_failure']['median']}"),
    ]
    cw, gg = (W - 2 * MARGIN - 2 * Inches(0.25)) / 3, Inches(0.25)
    y = Inches(1.4)
    for i, (name, mech, models, results, vuln, vcol, med) in enumerate(scen):
        x = MARGIN + i * (cw + gg)
        card(s, x, y, cw, Inches(4.15), fill=TINT, name=f"Scenario {i+1}")
        badge(s, x + Inches(0.2), y + Inches(0.2), Inches(0.5), str(i + 1), NAVY, size=16, name=f"Scenario no {i+1}")
        text(s, x + Inches(0.85), y + Inches(0.2), cw - Inches(1.0), Inches(0.75),
             [[(name, {"bold": True, "size": 17, "font": MONO})], [(mech, {"size": 11, "font": MONO, "color": MUTED})]],
             name=f"Scenario head {i+1}")
        text(s, x + Inches(0.25), y + Inches(1.05), cw - Inches(0.5), Inches(0.35), "Models: " + models, size=12,
             bold=True, color=TEAL, name=f"Scenario models {i+1}")
        text(s, x + Inches(0.25), y + Inches(1.45), cw - Inches(0.5), Inches(1.72),
             [[("›  ", {"color": c_, "bold": True, "size": 13}), (r_, {"size": 13, "color": c_})]
              for r_, c_ in results], spacing=6, name=f"Scenario results {i+1}")
        text(s, x + Inches(0.25), y + Inches(3.22), cw - Inches(0.5), Inches(0.3), med, size=11, color=MUTED,
             name=f"Scenario median {i+1}")
        pill(s, x + Inches(0.25), y + Inches(3.55), vuln, vcol, size=12, h=Inches(0.4))
    wy = Inches(5.75)
    card(s, MARGIN, wy, W - 2 * MARGIN, Inches(1.1), fill=TEAL_TINT, name="Why card")
    text(s, MARGIN + Inches(0.25), wy + Inches(0.12), W - 2 * MARGIN - Inches(0.5), Inches(0.9), [
        [("Why these three, in this order  ", {"bold": True, "size": 14, "color": TEAL}),
         ("Each hits a real link of the telemetry chain (publisher, process, storage), is fully reversible "
          "(pause/unpause, stop/start), and leaves the host untouched; no CPU burning. The rule-based planner ranks "
          "the next run by information gain × risk (unresolved runs, latch rate, neighbours in the dependency map); "
          "a human approves each one.", {"size": 12})]], name="Why text")
    footer(s, 6)
    notes(s, "V1 = HTTP 200 while telemetry is frozen (stall, crash). V2 = stale HTTP 200 for 21–24 s after the "
             "database recovered. The DNS explanation is a hypothesis; the logs and timings are measured.")


def slide_7(prs, ev):
    s = new(prs)
    title(s, "The proof: tests, recorded runs, guardrails tripped live", kicker="Implementation & results")
    bt = ev["by_type"]
    cards = [(str(TESTS_CORE + TESTS_DQ), "unit tests passing",
              f"{TESTS_CORE} core + {TESTS_DQ} data quality · 0 failed · {TESTS_SKIPPED} live-stack tests skipped"),
             (str(ev["runs"]), "recorded runs, evidence committed",
              "reports/day1 · day2 · guardrail-demo, every record replayable in the 3D twin"),
             ("3 / 3", "guardrails tripped live",
              " · ".join(f"{g} ({r})" for g, r, _ in GUARDS))]
    cw, gg = (W - 2 * MARGIN - 2 * Inches(0.25)) / 3, Inches(0.25)
    for i, (big, lab, src) in enumerate(cards):
        x = MARGIN + i * (cw + gg)
        card(s, x, Inches(1.35), cw, Inches(1.55), fill=TEAL_TINT if i != 2 else CORAL_TINT, name=f"Metric {i+1}")
        text(s, x + Inches(0.2), Inches(1.42), cw - Inches(0.4), Inches(0.65), big, size=34, bold=True, font=HEAD,
             color=TEAL if i != 2 else CORAL, name=f"Metric value {i+1}")
        text(s, x + Inches(0.2), Inches(2.05), cw - Inches(0.4), Inches(0.8),
             [[(lab, {"bold": True, "size": 13})], [(src, {"size": 10, "color": MUTED})]], name=f"Metric label {i+1}")
    # score formula
    fy = Inches(3.1)
    fw = Inches(6.3)
    card(s, MARGIN, fy, fw, Inches(3.75), fill=TINT, name="Score card")
    text(s, MARGIN + Inches(0.25), fy + Inches(0.15), fw - Inches(0.5), Inches(0.35), "Resilience Score (0–100)",
         size=15, bold=True, font=HEAD, name="Score head")
    text(s, MARGIN + Inches(0.25), fy + Inches(0.6), fw - Inches(0.5), Inches(0.75), [
        [("Score = 100 × M × (1 − 0.5 × P_silent)", {"font": MONO, "size": 14, "bold": True, "color": NAVY})],
        [("M = 0.25·D + 0.30·R + 0.20·G + 0.25·B  (measured parts only)", {"font": MONO, "size": 11})]],
        spacing=3, name="Formula")
    text(s, MARGIN + Inches(0.25), fy + Inches(1.45), fw - Inches(0.5), Inches(1.0), [
        [("D", {"bold": True, "size": 12}), (" detection  ·  ", {"size": 12}), ("R", {"bold": True, "size": 12}),
         (" recovery  ·  ", {"size": 12}), ("G", {"bold": True, "size": 12}), (" data integrity  ·  ", {"size": 12}),
         ("B", {"bold": True, "size": 12}), (" blast radius", {"size": 12})],
        [("P_silent", {"font": MONO, "bold": True, "size": 12}),
         (" = share of polls that returned HTTP 200 with stale data; a run that is silent on every poll keeps at "
          "most half its score.", {"size": 12})]], spacing=4, name="Formula legend")
    meds = [("service_crash", bt["service_crash"]["median"]), ("telemetry_stall", bt["telemetry_stall"]["median"]),
            ("dependency_failure", bt["dependency_failure"]["median"])]
    text(s, MARGIN + Inches(0.25), fy + Inches(2.6), fw - Inches(0.5), Inches(0.3),
         f"Medians, full-window Day-2 runs (overall {ev['median']:.2f}, n = {ev['n_full']}):", size=11, color=MUTED,
         name="Medians label")
    for k, (nm, v) in enumerate(meds):
        ry = fy + Inches(2.92) + k * Inches(0.27)
        text(s, MARGIN + Inches(0.25), ry, Inches(2.2), Inches(0.25), nm, size=11, font=MONO, name=f"Median label {k}")
        bx, bw_ = MARGIN + Inches(2.45), Inches(2.9)
        shape(s, MSO_SHAPE.RECTANGLE, bx, ry + Inches(0.04), bw_, Inches(0.16), fill=LINE, name=f"Median track {k}")
        shape(s, MSO_SHAPE.RECTANGLE, bx, ry + Inches(0.04), int(bw_ * v / 100), Inches(0.16), fill=TEAL,
              name=f"Median bar {k}")
        text(s, bx + bw_ + Inches(0.1), ry, Inches(0.6), Inches(0.25), f"{v}", size=11, bold=True,
             name=f"Median value {k}")
    # screenshots
    px = MARGIN + fw + Inches(0.3)
    pw = (W - MARGIN - px - Inches(0.2)) / 2
    picture(s, "stale", px, fy, pw, Inches(3.4), crop=(0, 0.04, 0.42, 0.0), name="Dashboard stale screenshot")
    caption(s, px, fy + Inches(3.45), pw, "STALE while HTTP 200 (live)")
    picture(s, "findings", px + pw + Inches(0.2), fy, pw, Inches(3.4), crop=(0.745, 0.06, 0.0, 0.3),
            name="Findings and score screenshot")
    caption(s, px + pw + Inches(0.2), fy + Inches(3.45), pw, "Score + findings tab (3D twin)")
    footer(s, 7)
    notes(s, f"Live run in the demo video ({VIDEO_RUN['id']}, approved by {VIDEO_RUN['approver']}): stale detected "
             f"{VIDEO_RUN['ttd']} s after injection, recovery verified {VIDEO_RUN['rec']} s after rollback, score "
             f"{VIDEO_RUN['score']}. The latency guard was proven with a deliberately tight 30 ms cap (tripped at "
             "1.88 s); it proves the abort path, not a real slowdown.")


def slide_8(prs, ev, video_url):
    s = new(prs, dark=True)
    title(s, "Data quality, key learnings & the demo", dark=True, kicker="Data quality · learnings · demo video")
    cw, gg, y, ch = (W - 2 * MARGIN - 2 * Inches(0.25)) / 3, Inches(0.25), Inches(1.4), Inches(5.4)
    cols = [MARGIN + i * (cw + gg) for i in range(3)]
    # left: data quality
    x = cols[0]
    card(s, x, y, cw, ch, fill=NAVY_2, name="DQ card")
    badge(s, x + Inches(0.2), y + Inches(0.2), Inches(0.45), "DQ", AMBER, size=12, name="DQ badge")
    text(s, x + Inches(0.8), y + Inches(0.22), cw - Inches(0.95), Inches(0.45), "Multi-region data-quality gate",
         size=15, bold=True, color=WHITE, name="DQ head")
    text(s, x + Inches(0.25), y + Inches(0.85), cw - Inches(0.45), Inches(2.0), [
        [("Universal format: ", {"bold": True, "size": 12, "color": WHITE}),
         ("EU DD/MM, US MM/DD, IN DD-MM, ISO, epoch → canonical UTC ISO-8601.", {"size": 12, "color": ICE})],
        [("Never guess: ", {"bold": True, "size": 12, "color": WHITE}),
         ("ambiguous dates without a region and timestamps > 5 s in the future are quarantined.",
          {"size": 12, "color": ICE})]], spacing=6, name="DQ rules")
    for k, (big, lab, col) in enumerate([("2 / 100", "≤ 5 % errors: commit 98 + quarantine 2 with reasons, one "
                                                     "atomic transaction", TEAL_SOFT),
                                         ("10 / 100", "> 5 % errors: atomic rollback, 0 saved, operator review "
                                                      "flag", RGBColor(0xFF, 0x8A, 0x78))]):
        by = y + Inches(2.45) + k * Inches(1.3)
        card(s, x + Inches(0.2), by, cw - Inches(0.4), Inches(1.1), fill=NAVY, name=f"DQ policy {k+1}")
        text(s, x + Inches(0.35), by + Inches(0.08), cw - Inches(0.7), Inches(1.0),
             [[(big, {"bold": True, "size": 20, "font": HEAD, "color": col})], [(lab, {"size": 11, "color": ICE})]],
             name=f"DQ policy text {k+1}")
    text(s, x + Inches(0.25), y + ch - Inches(0.45), cw - Inches(0.45), Inches(0.35),
         "backend/resiliencepilot/dq/ · synthetic logistics feed · 79 tests", size=10, italic=True, color=ICE,
         name="DQ source")
    # middle: learnings
    x = cols[1]
    card(s, x, y, cw, ch, fill=NAVY_2, name="Learnings card")
    badge(s, x + Inches(0.2), y + Inches(0.2), Inches(0.45), "i", TEAL, size=15, name="Learnings badge")
    text(s, x + Inches(0.8), y + Inches(0.22), cw - Inches(0.95), Inches(0.45), "Key learnings & what's next",
         size=15, bold=True, color=WHITE, name="Learnings head")
    learn = [
        ("AI hallucination guardrail", "the 7B analyst proposed 7 root causes; our grounding check against recorded "
                                       "evidence accepted 1 and rejected 6. The deterministic layer carries the "
                                       "analysis."),
        ("Health ≠ freshness", "status codes and container state missed every silent failure we injected."),
        ("Limitations", "container pause/stop only (no proxy → no network faults); one vehicle, one host; "
                        "in-process watchdog (kill switch covers a crash); no LICENSE file yet."),
        ("Next", "network-delay faults via a proxy, a larger local model, more vehicles, CI on the live stack."),
    ]
    paras = []
    for h_, b_ in learn:
        paras.append([(h_, {"bold": True, "size": 12, "color": TEAL_SOFT})])
        paras.append([(b_, {"size": 11, "color": ICE})])
    box = text(s, x + Inches(0.25), y + Inches(0.85), cw - Inches(0.45), ch - Inches(1.0), paras,
               name="Learnings text")
    for j, p in enumerate(box.text_frame.paragraphs):
        p.space_after = Pt(6 if j % 2 else 1)
    # right: video
    x = cols[2]
    card(s, x, y, cw, ch, fill=NAVY_2, name="Video card")
    link = video_url or None
    play = shape(s, MSO_SHAPE.OVAL, x + Inches(0.2), y + Inches(0.2), Inches(0.45), Inches(0.45), fill=CORAL,
                 name="Play button")
    tri = shape(s, MSO_SHAPE.ISOSCELES_TRIANGLE, x + Inches(0.36), y + Inches(0.33), Inches(0.18), Inches(0.2),
                fill=WHITE, name="Play glyph")
    tri.rotation = 90
    if link:
        for shp in (play, tri):
            shp.click_action.hyperlink.address = link
    text(s, x + Inches(0.8), y + Inches(0.22), cw - Inches(0.95), Inches(0.45), f"{VIDEO_LEN} live demo video",
         size=15, bold=True, color=WHITE, name="Video head")
    rows = []
    for t_, d_ in VIDEO_CHAPTERS:
        rows.append([(t_ + "  ", {"bold": True, "size": 11, "font": MONO, "color": TEAL_SOFT}),
                     (d_, {"size": 11, "color": ICE})])
    text(s, x + Inches(0.25), y + Inches(0.85), cw - Inches(0.45), Inches(3.2), rows, spacing=5,
         name="Video chapters")
    ly = y + ch - Inches(1.3)
    card(s, x + Inches(0.2), ly, cw - Inches(0.4), Inches(1.1), fill=CORAL if link else NAVY, name="Video link box")
    if link:
        text(s, x + Inches(0.35), ly + Inches(0.1), cw - Inches(0.7), Inches(0.9),
             [[("▶  Watch the demo", {"bold": True, "size": 14, "color": WHITE, "link": link})],
              [(link, {"size": 10, "color": WHITE, "link": link})]], name="Video link")
    else:
        text(s, x + Inches(0.35), ly + Inches(0.1), cw - Inches(0.7), Inches(0.9),
             [[("Video link", {"bold": True, "size": 14, "color": WHITE})],
              [("Google Drive link added at submission (make_ppt.py --video-url)", {"size": 10, "color": ICE})]],
             name="Video link")
    footer(s, 8, dark=True)
    notes(s, "Close: HTTP 200 is not recovery, fresh data is. Everything shown in the video is the real stack; the "
             f"live run is {VIDEO_RUN['id']}. Repository: {REPO_URL}")


def build(out: Path, video_url: str | None) -> Path:
    ev = evidence()
    prs = Presentation()
    prs.slide_width, prs.slide_height = W, H
    prs.core_properties.title = "ResiliencePilot SDV"
    prs.core_properties.subject = "HTTP 200 is not recovery. Fresh data is."
    prs.core_properties.author = TEAM_NAME
    for fn in (slide_1, slide_2, slide_3, slide_4, slide_5, slide_6, slide_7):
        fn(prs, ev)
    slide_8(prs, ev, video_url)
    out.parent.mkdir(parents=True, exist_ok=True)
    prs.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO / "docs" / "submission" / "ResiliencePilot_Presentation.pptx"))
    ap.add_argument("--video-url", help="public URL of the demo video (Google Drive), linked on slide 8")
    args = ap.parse_args()
    out = build(Path(args.out), args.video_url)
    print(f"wrote {out} ({N_SLIDES} slides, {out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
