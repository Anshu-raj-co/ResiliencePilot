"""Resilience Score (scoring.py): exact component values, what is and is not scorable, and the CLI."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from resiliencepilot.config import REPO_ROOT
from resiliencepilot.scoring import SILENT_PENALTY, WEIGHTS, load_runs, render_text, score_run, summarise

T0 = datetime(2026, 10, 10, 18, 0, 0, tzinfo=timezone.utc)
ALL = ["csv-provider", "databroker", "fms-consumer", "fms-forwarder", "fms-server", "fms-zenoh-router", "grafana",
       "influxDB"]


def _poll(phase, t, status=200, fresh=True):
    return {"phase": phase, "t_s": t,
            "observation": {"observed_at": (T0 + timedelta(seconds=t)).isoformat(), "http_status": status},
            "assessment": {"freshness": "FRESH" if fresh else ("STALE" if status == 200 else "OBSERVER_ERROR"),
                           "healthy_but_stale": status == 200 and not fresh}}


def _rec(polls, *, duration=10, stop="duration_elapsed", state="COMPLETED", ttr=3.0, rollback=True,
         data_gap=None, final=None, triggered=None, fault_verified=True, run_id="20261010T180000Z-aaaaaa"):
    rec = {"run_id": run_id, "state": state, "stop_reason": stop,
           "request": {"experiment_type": "telemetry_stall", "target": "fms-forwarder", "duration_s": duration},
           "limits": {"recovery_timeout_s": 30.0},
           "impact": {"fault_verified": fault_verified},
           "timeline": [{"event": "fault_active", "at": T0.isoformat()}],
           "observations": polls,
           "rollback": {"verified": rollback},
           "recovery": {"verified": ttr is not None, "time_to_verified_s": ttr, "detail": "x"},
           "guardrails": {"blast_radius_baseline": {c: "running" for c in ALL},
                          "blast_radius_allowed": ["fms-forwarder"],
                          "final_blast_check": final if final is not None else {"clean": True, "changes": []},
                          "triggered": triggered}}
    if data_gap:
        rec["data_gap"] = data_gap
    return rec


# 10 s stall: fresh until t=5, stale from t=5 to the end of the window; 4 recovery polls, the first still stale
STALL = ([_poll("fault", t) for t in (1, 2, 3, 4)] + [_poll("fault", t, fresh=False) for t in (5, 6, 7, 8, 9, 10)]
         + [_poll("recovery", 10.5, fresh=False)] + [_poll("recovery", t) for t in (11, 11.5, 12)])


def test_full_window_stall_exact_values():
    s = score_run(_rec(STALL))
    c = s["components"]
    assert s["scorable"] and not s["partial"]
    assert c["detection"] == {"measured": True, "ttd_s": 5.0, "kind": "silent", "score": 0.5}
    assert c["recovery"]["score"] == 0.9                       # 1 - 3/30
    assert c["data_integrity"]["measured"] is False
    assert c["blast_radius"]["score"] == 1.0 and c["blast_radius"]["others"] == 7
    assert s["silent"] == {"polls": 14, "silent_polls": 7, "by_phase": {"fault": 6, "recovery": 1}, "ratio": 0.5}
    mean = (0.25 * 0.5 + 0.30 * 0.9 + 0.25 * 1.0) / (0.25 + 0.30 + 0.25)   # data integrity not measured: dropped
    assert s["not_measured"] == ["data_integrity"]
    assert s["score"] == round(100 * mean * (1 - SILENT_PENALTY * 0.5), 1)


def test_visible_failure_and_data_gap():
    polls = [_poll("fault", 2)] + [_poll("fault", t, status=500) for t in (3, 4, 5)] + [_poll("recovery", 11)]
    s = score_run(_rec(polls, data_gap={"max_gap_s": 4.5, "median_interval_s": 0.5}))
    c = s["components"]
    assert c["detection"]["kind"] == "visible" and c["detection"]["ttd_s"] == 3.0 and c["detection"]["score"] == 0.7
    assert c["data_integrity"] == {"measured": True, "lost_s": 4.0, "score": 0.6}
    assert s["silent"]["ratio"] == 0.0
    expected = 100 * (0.25 * 0.7 + 0.30 * 0.9 + 0.20 * 0.6 + 0.25 * 1.0) / sum(WEIGHTS.values())
    assert s["score"] == round(expected, 1)


def test_no_response_counts_as_visible_detection():
    s = score_run(_rec([_poll("fault", 5, status=None)], stop="observer_lost"))
    assert s["components"]["detection"]["kind"] == "visible" and s["partial"]


def test_undetected_fault_scores_zero_on_detection():
    s = score_run(_rec([_poll("fault", t) for t in (1, 2, 3)]))
    assert s["components"]["detection"]["ttd_s"] is None and s["components"]["detection"]["score"] == 0.0


def test_unverified_recovery_is_scored_zero_not_skipped():
    s = score_run(_rec(STALL, ttr=None, state="LATCHED_UNSAFE"))
    assert s["scorable"] and s["components"]["recovery"]["score"] == 0.0
    assert "not recovered within 30 s" in s["components"]["recovery"]["note"]


def test_recovery_phase_silence_is_penalised():
    quiet = score_run(_rec(STALL))
    loud = score_run(_rec(STALL + [_poll("recovery", 12.5 + i, fresh=False) for i in range(20)]))
    assert loud["silent"]["by_phase"]["recovery"] == 21 and loud["score"] < quiet["score"]


def test_blast_radius_counts_trip_and_final_check_once():
    change = {"container": "grafana"}
    trig = {"guard": "blast_radius", "detail": {"changes": [change]}}
    s = score_run(_rec(STALL, stop="guardrail_blast_radius", triggered=trig,
                       final={"clean": False, "changes": [change, {"container": "fms-server"}]}))
    b = s["components"]["blast_radius"]
    assert b["changed"] == ["fms-server", "grafana"] and b["score"] == round(1 - 2 / 7, 3)
    assert s["partial"]


def test_blast_radius_not_measured_without_any_check():
    rec = _rec(STALL)
    rec["guardrails"]["final_blast_check"] = None
    assert score_run(rec)["components"]["blast_radius"]["measured"] is False


@pytest.mark.parametrize("kwargs, reason", [
    ({"rollback": False}, "rollback not verified"),
    ({"fault_verified": False}, "fault was never verified"),
    ({"stop": "emergency_stop", "state": "ABORTED"}, "emergency_stop"),
    ({"stop": "cancelled", "state": "ABORTED"}, "cancelled"),
    ({"stop": "error", "state": "LATCHED_UNSAFE"}, "error"),
])
def test_not_scorable(kwargs, reason):
    s = score_run(_rec(STALL, **kwargs))
    assert not s["scorable"] and reason in s["reason"] and "score" not in s


def test_summary_excludes_partial_and_unscorable_runs():
    scores = [score_run(_rec(STALL, run_id="20261010T180000Z-a")),
              score_run(_rec(STALL, run_id="20261010T180001Z-b", ttr=6.0)),
              score_run(_rec(STALL, run_id="20261010T180002Z-c", stop="observer_lost")),
              score_run(_rec(STALL, run_id="20261010T180003Z-d", rollback=False))]
    summ = summarise(scores)["telemetry_stall"]
    assert summ["n"] == 2 and summ["min"] == scores[1]["score"] and summ["max"] == scores[0]["score"]
    text = render_text(scores)
    assert "partial (window cut short: observer_lost)" in text and "not scorable" in text


def test_golden_committed_dependency_failure_run():
    """Real run from the live stack (reports/day2): visible 500s, 24.2 s recovery, 3.5 s of stored data lost."""
    rec = next(r for r in load_runs(REPO_ROOT / "reports" / "day2") if r["run_id"] == "20261010T193003Z-e2eacd")
    s = score_run(rec)
    assert not s["partial"] and s["silent"]["silent_polls"] == 0
    assert s["components"]["recovery"]["ttr_s"] == 24.247 and s["components"]["data_integrity"]["lost_s"] == 3.511
    assert s["score"] == 61.6


def test_golden_committed_silent_after_recovery_run():
    rec = next(r for r in load_runs(REPO_ROOT / "reports" / "day2") if r["run_id"] == "20261010T192839Z-179795")
    s = score_run(rec)
    assert s["partial"] and s["components"]["recovery"]["score"] == 0.0
    assert s["silent"]["by_phase"]["recovery"] >= 40 and s["score"] < 20


def test_cli_score(tmp_path, monkeypatch, capsys):
    import resiliencepilot.__main__ as cli
    monkeypatch.setattr(cli, "build", lambda *_: (_ for _ in ()).throw(AssertionError("safety objects built")))
    for name in ("20261010T180000Z-a", "20261010T180001Z-b"):
        (tmp_path / f"{name}.json").write_text(json.dumps(_rec(STALL, run_id=name)), encoding="utf-8")
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")            # not a run record: ignored
    out = tmp_path / "out" / "scores.json"
    assert cli.main(["score", "--dir", str(tmp_path), "--run", "20261010T180001Z-b", "--out", str(out)]) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert [r["run_id"] for r in saved["runs"]] == ["20261010T180001Z-b"] and saved["summary"]["telemetry_stall"]["n"] == 1
    assert cli.main(["score", "--dir", str(tmp_path), "--run", "missing"]) == 1
    assert "no run records for missing" in capsys.readouterr().out
