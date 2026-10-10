from resiliencepilot.__main__ import _result_lines


def _rec(detect, first_fresh, verified):
    return {
        "impact": {"fault_verified": True, "stale_detected": detect is not None, "time_to_detection_s": detect,
                   "http_ok_ratio_during_fault": 1.0, "max_data_age_s": 4.079, "healthy_but_stale_observations": 0},
        "rollback": {"verified": True, "by": "orchestrator"},
        "recovery": {"verified": verified is not None, "time_to_first_fresh_s": first_fresh, "time_to_verified_s": verified},
    }


def test_result_lines_show_na_instead_of_none_when_nothing_went_stale():
    text = "\n".join(_result_lines(_rec(None, 1.076, 2.725)))  # emergency stop before the stale threshold
    assert "None" not in text
    assert "time to detection n/a" in text
    assert "first fresh after 1.076s, verified after 2.725s" in text


def test_result_lines_show_na_for_unverified_recovery():
    text = "\n".join(_result_lines(_rec(4.92, None, None)))
    assert "None" not in text
    assert "time to detection 4.92s" in text
    assert "first fresh after n/a, verified after n/a" in text
