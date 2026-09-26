"""Run the official judge with a real LLM, keeping every setting out of the file.

Starts the bot, points the judge at it, runs one scenario, and writes the full
transcript to judge_output.txt. All configuration is passed through environment
variables, which judge_simulator.py honours (BOT_URL, LLM_PROVIDER,
GEMINI_API_KEY, LLM_MODEL, TEST_SCENARIO), so no secret is written to disk.

Usage:
    python run_judge_scored.py                 # phase2_short (3 scored calls)
    python run_judge_scored.py full_evaluation # all triggers
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRANSCRIPT = ROOT / "judge_output.txt"
DEFAULT_PORT = 8081
DEFAULT_SCENARIO = "phase2_short"


def find_port(preferred: int) -> int:
    import socket
    for port in [preferred, *range(preferred + 1, preferred + 40)]:
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", port))
            probe.close()
            return port
        except OSError:
            probe.close()
    raise SystemExit("no free port available")


def wait_for_health(base: str, seconds: float = 25) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/v1/healthz", timeout=3) as response:
                return response.status == 200
        except Exception:
            time.sleep(0.3)
    return False


def load_dotenv() -> None:
    """Read .env into os.environ without overriding anything already set.

    Needed because judge_simulator's config block runs once at import; loading
    .env first ensures the provider/model chosen there is the one that wins.
    """
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def install_retry(max_attempts: int = 3, base_delay: float = 65.0) -> None:
    """Wrap every LLM provider with 429/5xx retry.

    The judge calls the provider directly with no backoff, so a free-tier rate
    limit aborts the run on the first throttled request. Patching the provider
    class in memory keeps judge_simulator.py on disk untouched while making a
    long run survivable.

    The initial delay is deliberately long (about a minute). The Gemini free
    tier allows only ~20 requests/minute, so retrying inside that window spends
    the very quota being waited on and never clears it.
    """
    import judge_simulator as js

    def wrap(provider_cls):
        original = provider_cls.complete

        def complete(self, prompt, system=None):
            delay = base_delay
            last = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return original(self, prompt, system)
                except urllib.error.HTTPError as exc:
                    last = exc
                    if exc.code not in (429, 500, 502, 503, 504) or attempt == max_attempts:
                        raise
                    print(f"  [retry] HTTP {exc.code} ({provider_cls.__name__}) "
                          f"attempt {attempt}/{max_attempts}, sleeping {delay:.0f}s")
                    time.sleep(delay)
                    delay = min(delay * 1.7, 75.0)
                except (TimeoutError, urllib.error.URLError, OSError) as exc:
                    # NVIDIA NIM cold-starts a deployment on the first request, so
                    # a socket timeout here is often just a slow spin-up.
                    last = exc
                    if attempt == max_attempts:
                        raise
                    print(f"  [retry] {type(exc).__name__} ({provider_cls.__name__}) "
                          f"attempt {attempt}/{max_attempts}, sleeping {delay:.0f}s")
                    time.sleep(delay)
                    delay = min(delay * 1.7, 75.0)
            raise last  # pragma: no cover

        provider_cls.complete = complete

    for name in ("OpenAIProvider", "AnthropicProvider", "GeminiProvider",
                 "DeepSeekProvider", "GroqProvider", "OllamaProvider",
                 "OpenRouterProvider", "NvidiaProvider"):
        cls = getattr(js, name, None)
        if cls is not None and not getattr(cls, "_vera_patched", False):
            wrap(cls)
            cls._vera_patched = True


def main() -> int:
    scenario = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SCENARIO
    port = find_port(DEFAULT_PORT)
    base = f"http://localhost:{port}"

    # Export configuration BEFORE judge_simulator is first imported, because its
    # module-level config block reads the environment exactly once at import.
    # .env is loaded first so a provider/model chosen there wins.
    load_dotenv()
    os.environ.setdefault("LLM_PROVIDER", "gemini")
    os.environ.setdefault("LLM_MODEL", "gemini-3.8-flash")
    os.environ["BOT_URL"] = base
    os.environ["TEST_SCENARIO"] = scenario
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    install_retry(base_delay=(5.0 if os.environ.get("LLM_PROVIDER") == "nvidia" else 65.0))

    server = subprocess.Popen(
        [sys.executable, str(ROOT / "bot.py"), "--port", str(port)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_health(base):
            raise SystemExit("bot did not become healthy")
        print(f"bot={base} provider={os.environ['LLM_PROVIDER']} "
              f"model={os.environ['LLM_MODEL']} scenario={scenario}")
        # The judge runs in-process so install_retry() actually applies; a child
        # process would not inherit the patched provider classes.
        with TRANSCRIPT.open("w", encoding="utf-8", errors="replace") as log:
            stdout = sys.stdout
            sys.stdout = log
            sys.stdout.reconfigure(line_buffering=True)  # keep the transcript live
            try:
                import judge_simulator as js
                js.main()
                code = 0
            except SystemExit as exc:
                code = int(exc.code or 0)
            finally:
                sys.stdout = stdout
        print(f"judge exit={code}  transcript={TRANSCRIPT.name}")
        return code
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
