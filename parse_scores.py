"""Summarise a judge transcript into a sorted per-message score table.

Run:  python parse_scores.py [judge_output.txt]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIMS = ("Specificity", "Category Fit", "Merchant Fit", "Decision Quality", "Engagement")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def main() -> int:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "judge_output.txt"
    raw = source.read_text(encoding="utf-8", errors="replace")
    # The transcript is written to a terminal, so it carries colour escapes.
    lines = [ANSI.sub("", line) for line in raw.splitlines()]

    current = ""
    rows: list[list] = []
    for line in lines:
        found = re.search(r'Message: "(.*)', line)
        if found:
            current = found.group(1)[:56]
            continue
        found = re.search(r"(Specificity|Category Fit|Merchant Fit|Decision Quality|Engagement)\s+\[.*?(\d+)/10", line)
        if found and current:
            if not rows or rows[-1][0] != current:
                rows.append([current, {}])
            rows[-1][1][found.group(1)] = int(found.group(2))
            continue
        found = re.search(r"TOTAL: (\d+)/50", line)
        if found and rows:
            rows[-1][1]["T"] = int(found.group(1))

    scored = [r for r in rows if r[1].get("T")]
    scored.sort(key=lambda r: r[1]["T"])
    for message, s in scored:
        values = " ".join(f"{d[0]}{s.get(d, 0)}" for d in DIMS)
        print(f"{s['T']:>3} | {values:<34} | {message}")
    if scored:
        count = len(scored)
        mean = lambda d: sum(s[1].get(d, 0) for s in scored) // count  # noqa: E731
        print(f"\nmessages={count} avg_total={sum(s[1]['T'] for s in scored) // count}")
        for d in DIMS:
            print(f"  avg {d:<18} {mean(d)}/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
