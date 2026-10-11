"""`python -m resiliencepilot.dq <generate|ingest|show-quarantine|report>` (docs/DATA_QUALITY.md).

Exit codes: 0 ok; 1 bad input; 2 batch rolled back (error rate above threshold, operator review needed).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import REPO_ROOT
from .batch_engine import DEFAULT_QUARANTINE_THRESHOLD, BatchStatus, ingest_batch
from .generator import generate_feed
from .report import render_report
from .store import DEFAULT_DB, DQStore


def _path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else Path.cwd() / path


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m resiliencepilot.dq",
                                 description="Data-quality gate for the synthetic logistics feed")
    ap.add_argument("--db", default=str(DEFAULT_DB), help=f"SQLite store (default {DEFAULT_DB.relative_to(REPO_ROOT)})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write a dirty multi-region synthetic feed")
    g.add_argument("--count", type=int, default=100)
    g.add_argument("--error-count", type=int, default=2)
    g.add_argument("--seed", type=int, default=2026)
    g.add_argument("--out", required=True)

    i = sub.add_parser("ingest", help="run the batch gate on a feed file")
    i.add_argument("--file", required=True)
    i.add_argument("--threshold", type=float, default=DEFAULT_QUARANTINE_THRESHOLD,
                   help="max error rate that still commits (default 0.05, max 0.5)")
    i.add_argument("--json", action="store_true")

    q = sub.add_parser("show-quarantine", help="list quarantined records and batches awaiting operator review")
    q.add_argument("--batch")
    q.add_argument("--limit", type=int, default=50)
    q.add_argument("--json", action="store_true")

    r = sub.add_parser("report", help="write the standalone HTML report")
    r.add_argument("--out", default="docs/dq/report.html")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "generate":
        try:
            feed = generate_feed(args.count, args.error_count, seed=args.seed)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        out = _path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(feed, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out} ({feed['count']} synthetic records, {len(feed['expected_invalid'])} deliberately invalid)")
        return 0

    store = DQStore(_path(args.db))

    if args.cmd == "ingest":
        src = _path(args.file)
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
            records = data["records"] if isinstance(data, dict) else data
            result = ingest_batch(records, store, threshold=args.threshold, source=src.name)
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result.as_dict(), indent=2))
        else:
            print(f"batch {result.batch_id}: {result.status.value} ({result.event})")
            print(f"  records {result.total}: valid {result.valid}, invalid {result.invalid} "
                  f"-> error rate {result.error_rate:.1%} vs threshold {result.threshold:.1%}")
            print(f"  written: clean {result.clean_written}, quarantine {result.quarantined_written}")
            for code, n in result.error_counts.items():
                print(f"  {n:>4} x {code}")
            if result.needs_operator_review:
                print("  OPERATOR REVIEW: batch rolled back, nothing committed. "
                      "Inspect with `show-quarantine` (rolled-back batches) or `report`.")
        return 2 if result.status is BatchStatus.ROLLED_BACK else 0

    if args.cmd == "show-quarantine":
        rows = store.quarantine(batch_id=args.batch, limit=args.limit)
        review = [a for a in store.audits() if a["needs_operator_review"] and args.batch in (None, a["batch_id"])]
        if args.json:
            print(json.dumps({"quarantine": rows, "rolled_back_batches": review}, indent=2, default=str))
            return 0
        print(f"quarantined records: {len(rows)}{' (limit ' + str(args.limit) + ')' if len(rows) == args.limit else ''}")
        for q in rows:
            print(f"- {q['record_id']}  batch {q['batch_id']}")
            for e in q["errors"]:
                print(f"    {e['code']:<26} {e['field']}: {e['detail']}")
        print(f"rolled-back batches awaiting operator review: {len(review)}")
        for a in review:
            print(f"- {a['batch_id']}  {a['event']}  {a['invalid']}/{a['total']} invalid "
                  f"({a['error_rate']:.1%} > {a['threshold']:.1%})  source={a['source']}")
            for r in a["details"].get("rejected", [])[:args.limit]:
                print(f"    {r['record_id']}: {', '.join(e['code'] for e in r['errors'])}")
        return 0

    if args.cmd == "report":
        out = _path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_report(store), encoding="utf-8")
        print(f"wrote {out}")
        return 0
    return 1
