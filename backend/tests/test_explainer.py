"""The explainer page must be standalone, read-only and built only from recorded runs."""
import json
import re

from resiliencepilot.config import REPO_ROOT
from resiliencepilot.explainer import collect, load_runs, render


def test_default_run_is_latest_full_length_completed():
    data = collect(REPO_ROOT, None)
    runs = load_runs(REPO_ROOT)
    full = [rid for rid, (r, _) in runs.items() if r["state"] == "COMPLETED" and r["stop_reason"] == "duration_elapsed"]
    assert data["run"]["run_id"] == full[-1]
    assert len(data["full_runs"]) == len(full)


def test_page_is_standalone_and_has_no_experiment_controls():
    html = render(collect(REPO_ROOT, None))
    assert not re.search(r"<(script|link|img)[^>]+(src|href)=", html), "no external resources"
    assert "cdn" not in html.lower()
    urls = set(re.findall(r"https?://[^\s\"'<)]+", html))
    assert urls <= {"http://www.w3.org/2000/svg"}, urls      # only the SVG namespace, never fetched
    code = re.sub(r'<script type="application/json" id="data">.*?</script>', "", html, flags=re.S)  # data excluded
    assert not re.search(r"fetch\(|XMLHttpRequest|WebSocket|/rfms/|/api/", code), "page must not call any API"
    buttons = re.findall(r"<button[^>]*>(.*?)</button>", code)
    assert buttons and all(b in ("Normal", "Fault view", "▶ Replay") for b in buttons), buttons


def test_embedded_numbers_come_from_the_run_record():
    data = collect(REPO_ROOT, None)
    rec, _ = load_runs(REPO_ROOT)[data["run"]["run_id"]]
    html = render(data)
    embedded = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)
                          .group(1).replace("<\\/", "</"))
    assert embedded["run"]["impact"] == rec["impact"]
    assert len(embedded["run"]["observations"]) == len(rec["observations"])
