"""Summarise judge_output.txt: per-dimension averages, per-message detail, hints.

Strips the judge's ANSI colour codes before parsing so the bar-chart rows can be
read programmatically. Usage:  python parse_judge_output.py [--hints]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "judge_output.txt"
DIMS = ("Specificity", "Category Fit", "Merchant Fit", "Decision Quality", "Engagement")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# The bar itself is drawn with block glyphs, so match anything up to the bracket.
SCORE_ROW = re.compile(r"^\s*(%s)\s+\[[^\]]*\]\s*(\d+)/10" % "|".join(DIMS), re.M)
TOTAL_ROW = re.compile(r"TOTAL:\s*(\d+)/50")
MESSAGE_ROW = re.compile(r'^Message:\s+"(.*?)"')
HINT_ROW = re.compile(r"^\s*Hint:\s+(.*)$", re.M)
LLM_ERROR_ROW = re.compile(r"^\s*\[WARN\] LLM error", re.M)


def split_real_vs_fallback(text: str) -> tuple[list[dict], list[dict]]:
    """Separate genuine LLM scores from the judge's own error fallback.

    When scoring raises, the judge substitutes a fixed heuristic: every
    dimension is 5 except specificity, which is 3 + 2 * (digit count). Those
    rows are not judgements and must not be averaged in with real ones.
    """
    messages: list[dict] = []
    current: dict = {}
    failed = False
    for line in text.splitlines():
        if LLM_ERROR_ROW.match(line):
            failed = True
        row = SCORE_ROW.match(line)
        if row:
            current[row.group(1)] = int(row.group(2))
            continue
        total = TOTAL_ROW.search(line)
        if total and len(current) == len(DIMS):
            current["total"] = int(total.group(1))
            current["fallback"] = failed
            messages.append(current)
            current, failed = {}, False
    real = [m for m in messages if not m["fallback"]]
    fallback = [m for m in messages if m["fallback"]]
    return real, fallback



def main() -> int:
    if not SOURCE.exists():
        print("judge_output.txt not found")
        return 1
    text = ANSI.sub("", SOURCE.read_text(encoding="utf-8", errors="replace"))
    messages, fallback = split_real_vs_fallback(text)

    batches = re.findall(r"\[INFO\] Batch (\d+): (\d+) actions", text)
    print(f"scored messages: {len(messages)}  "
          f"(real LLM judgements: {len(messages)}; "
          f"judge fallback after API errors: {len(fallback)})")
    if batches:
        print("batches: " + ", ".join(f"{b}->{n}" for b, n in batches))
        print("actions emitted: " + str(sum(int(n) for _, n in batches)))
    if not messages:
        print("no completed scores yet")
        return 1

    print()
    for name in DIMS:
        values = [m[name] for m in messages]
        print(f"  {name:16} {sum(values) / len(values):5.1f} / 10")
    totals = [m["total"] for m in messages]
    average = sum(totals) / len(totals)
    print(f"  {'TOTAL':16} {average:5.1f} / 50   ({average * 2:.0f}%)")
    print()
    for i, m in enumerate(messages, 1):
        cells = "  ".join(f"{m[d]:>2}" for d in DIMS)
        print(f"   msg {i:>2}: {cells}   -> {m['total']}/50")

    if "--hints" in sys.argv:
        print("\nper-message judge hints:")
        for i, (msg, hint) in enumerate(zip(MESSAGE_ROW.findall(text),
                                            HINT_ROW.findall(text)), 1):
            print(f"  msg {i}: {msg[:70]}")
            print(f"     -> {hint[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
