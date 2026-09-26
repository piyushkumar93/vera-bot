"""Run the official judge_simulator scenarios that do not require an LLM key.

The scoring path needs a provider key, so this exercises only the operational
scenarios: warmup, auto-reply hell, intent transition, and hostile handling.
Usage:  python run_judge_checks.py [--port 8080]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import judge_simulator as js

ROOT = Path(__file__).resolve().parent


class _NoLLM:
    """Stand-in provider so operational scenarios run without an API key."""

    def name(self) -> str:
        return "none (operational scenarios only)"

    def complete(self, prompt: str, system: str = None) -> str:  # pragma: no cover
        return "{}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    server = subprocess.Popen(
        [sys.executable, str(ROOT / "bot.py"), "--port", str(args.port)],
        cwd=str(ROOT),
    )
    time.sleep(1.5)
    js.BOT_URL = f"http://127.0.0.1:{args.port}"
    try:
        judge = js.JudgeSimulator(_NoLLM())
        results = {}
        for name, fn in (("warmup", judge._warmup), ("auto_reply_hell", judge._auto_reply),
                         ("intent_transition", judge._intent), ("hostile", judge._hostile)):
            try:
                results[name] = fn()
            except Exception as exc:  # a crash in one scenario must not hide the rest
                js.print_fail(f"{name} crashed: {exc}")
                results[name] = False
        js.print_section("SCENARIO RESULTS")
        for name, passed in results.items():
            (js.print_success if passed else js.print_fail)(name)
        return 0 if all(results.values()) else 1
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
