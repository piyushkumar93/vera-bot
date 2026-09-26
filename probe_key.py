"""List the Gemini models this key can call. Standalone, no project imports.

Run:  python list_gemini_models.py
Writes models.txt next to this file. Never prints the key.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "models.txt"
LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models?key={key}"


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    lines = [f"key length={len(key)} prefix={key[:4]!r}", ""]
    if not key:
        lines.append("No GEMINI_API_KEY in the environment.")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return 1
    try:
        with urllib.request.urlopen(LIST_URL.format(key=key), timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace").replace("\n", " ")
        lines.append(f"ListModels HTTP {exc.code}")
        lines.append("  " + " ".join(raw.split())[:400])
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return 1
    except Exception as exc:  # noqa: BLE001
        lines.append(f"{type(exc).__name__}: {exc}")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return 1

    usable = [m for m in data.get("models", [])
              if "generateContent" in m.get("supportedGenerationMethods", [])]
    lines.append(f"{len(usable)} model(s) support generateContent:")
    for model in sorted(usable, key=lambda m: m.get("name", "")):
        name = model.get("name", "").replace("models/", "")
        limit = model.get("inputTokenLimit", "?")
        lines.append(f"  {name}   (input tokens: {limit})")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0


CANDIDATES = ("gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite",
              "gemini-3.5-flash", "gemini-2.5-flash")


def probe(key: str, model: str) -> str:
    """One tiny call to see whether this model still has quota."""
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    payload = json.dumps({"contents": [{"parts": [{"text": "ok"}]}]}).encode()
    request = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            json.loads(response.read().decode("utf-8"))
            return "OK  <- usable"
    except urllib.error.HTTPError as exc:
        raw = " ".join(exc.read().decode("utf-8", "replace").split())
        if exc.code == 429:
            index = raw.find("limit:")
            return "429 throttled " + (raw[index:index + 60] if index != -1 else "")
        if exc.code == 404:
            return "404 not available"
        return f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


def check_candidates() -> int:
    """Report which candidate models still have quota left."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    lines = [f"key prefix={key[:4]!r} length={len(key)}", ""]
    for model in CANDIDATES:
        lines.append(f"  {model:26} {probe(key, model)}")
    OUT.with_name("candidates.txt").write_text("\n".join(lines), encoding="utf-8")
    return 0


"""Summarise judge_output.txt: per-dimension averages and the headline score.

Parses the judge's own bar-chart output so results can be read without
scrolling a long transcript. Usage:  python parse_judge_output.py
"""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "judge_output.txt"
DIMS = ("Specificity", "Category Fit", "Merchant Fit", "Decision Quality", "Engagement")
PATTERN = re.compile(
    r"Specificity\s+\[[█ ]*\]\s*(\d+)/10"
    r".*?Category Fit\s+\[[█ ]*\]\s*(\d+)/10"
    r".*?Merchant Fit\s+\[[█ ]*\]\s*(\d+)/10"
    r".*?Decision Quality\s+\[[█ ]*\]\s*(\d+)/10"
    r".*?Engagement\s+\[[█ ]*\]\s*(\d+)/10"
    r".*?TOTAL:\s*(\d+)/50",
    re.S,
)


def main() -> int:
    if not SOURCE.exists():
        print("judge_output.txt not found")
        return 1
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    blocks = PATTERN.findall(text)
    print(f"scored messages: {len(blocks)}")
    batches = re.findall(r"\[INFO\] Batch (\d+): (\d+) actions", text)
    if batches:
        print("batches: " + ", ".join(f"{b}->{n}" for b, n in batches))
    if not blocks:
        print("no completed scores yet")
        return 1
    print()
    for index, name in enumerate(DIMS):
        values = [int(b[index]) for b in blocks]
        print(f"  {name:16} {sum(values) / len(values):5.1f} / 10")
    totals = [int(b[5]) for b in blocks]
    average = sum(totals) / len(totals)
    print(f"  {'TOTAL':16} {average:5.1f} / 50   ({average * 2:.0f}%)")
    print()
    for i, b in enumerate(blocks, 1):
        print(f"   msg {i}: " + "  ".join(f"{x:>2}" for x in b[:5]) + f"   -> {b[5]}/50")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



