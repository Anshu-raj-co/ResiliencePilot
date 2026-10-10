"""The CLI must not crash when its output is piped on Windows (no PYTHONIOENCODING / PYTHONUTF8 needed)."""
import os
import subprocess
import sys

from resiliencepilot.config import REPO_ROOT


def test_piped_output_with_non_ansi_characters_does_not_crash():
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    baseline = REPO_ROOT / "reports" / "day1" / "baseline-20261010T094007Z.json"
    # 'report' and 'show-baseline' need no Docker; render a page path containing → and a baseline table
    proc = subprocess.run([sys.executable, "-c",
                           "import sys; from resiliencepilot.__main__ import main; "
                           "rc = main(['show-baseline', sys.argv[1]]); print('\u2192 \u2502 \u2588'); sys.exit(rc)",
                           str(baseline)],
                          capture_output=True, env=env, cwd=REPO_ROOT / "backend")
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-400:]
    out = proc.stdout.decode("utf-8")
    assert "\u2192" in out and "Baseline" in out
