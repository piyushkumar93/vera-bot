"""Probe NVIDIA NIM: list models and test which candidates respond.

Reads the key from the environment (GEMINI_API_KEY, as configured in .env).
Writes nvidia_models.txt. Never prints the key.

Usage:  python probe_nvidia.py            # list catalog + probe candidates
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "nvidia_models.txt"
BASE = "https://integrate.api.nvidia.com/v1"

CANDIDATES = (
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-nano-3-30b-a3b",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "z-ai/glm-5.3",
    "z-ai/glm-5.3-flash",
    "moonshotai/kimi-k3",
    "openai/gpt-oss-20b",
    "mistralai/mistral-large-2-instruct",
    "mistralai/mistral-nemotron",
    "google/gemma-4-31b-it",
)


def load_dotenv() -> None:
    """Read .env into os.environ without overriding anything already set."""
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def key() -> str:
    load_dotenv()
    return (os.environ.get("NVIDIA_API_KEY") or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("LLM_API_KEY") or "").strip()


def list_models(token: str) -> tuple[int, list[str]]:
    request = urllib.request.Request(f"{BASE}/models",
                                     headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    return 200, [m.get("id", "") for m in data.get("data", [])]


def probe(token: str, model: str) -> str:
    """One tiny chat call to confirm the model is callable right now."""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        "temperature": 0.2,
        "max_tokens": 20,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
            reply = data["choices"][0]["message"]["content"].strip().replace("\n", " ")
            return f"OK -> {reply[:40]}"
    except urllib.error.HTTPError as exc:
        raw = " ".join(exc.read().decode("utf-8", "replace").split())
        return f"HTTP {exc.code} | {raw[:110]}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {str(exc)[:90]}"


def main() -> int:
    token = key()
    lines = [f"key prefix={token[:6]!r} length={len(token)}", ""]
    if not token:
        lines.append("No key found in NVIDIA_API_KEY / GEMINI_API_KEY / LLM_API_KEY")
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return 1
    try:
        _, models = list_models(token)
        lines.append(f"{len(models)} models in the catalog")
        for name in sorted(models):
            lines.append(f"    {name}")
    except urllib.error.HTTPError as exc:
        raw = " ".join(exc.read().decode("utf-8", "replace").split())
        lines.append(f"ListModels HTTP {exc.code} | {raw[:300]}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"ListModels {type(exc).__name__}: {exc}")

    lines += ["", "candidate probes:"]
    for model in CANDIDATES:
        lines.append(f"  {model:52} {probe(token, model)}")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
