"""Direct Gemini API probe. Reads the key from .env, never prints it.

Answers two questions with evidence rather than assumption:
  1. Is the configured key actually accepted by the Gemini endpoint?
  2. Which model names does that key accept?

Run:  python probe_gemini.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip("\"'"))


def main() -> int:
    load_env()
    key = (os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip()
    base = (os.environ.get("GEMINI_BASE_URL") or
            "https://generativelanguage.googleapis.com").rstrip("/")
    model = os.environ.get("LLM_MODEL", "gemini-3.1-flash")

    print("=" * 70)
    print(f"  key present : {bool(key)} (length {len(key)}, never printed)")
    print(f"  base url    : {base}")
    print(f"  model       : {model}")
    print("=" * 70)

    # List models this key can see.
    request = urllib.request.Request(f"{base}/v1beta/models?key={key}",
                                     headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models", [])]
        names = [n for n in names if "generateContent" in
                 str(json.loads(json.dumps(data)) if False else "")]
        print(f"\n  KEY ACCEPTED. {len(data.get('models', []))} models visible.")
        listed = [m.get("name", "") for m in data.get("models", [])]
        interesting = [n for n in listed if "flash" in n.lower() or "pro" in n.lower()]
        for name in interesting[:25]:
            print(f"    {name}")
    except urllib.error.HTTPError as exc:
        detail = " ".join(exc.read().decode("utf-8", "replace").split())
        print(f"\n  LIST MODELS FAILED: HTTP {exc.code}")
        print(f"  {detail[:400]}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  LIST MODELS FAILED: {type(exc).__name__}: {str(exc)[:200]}")

    # Attempt generation across every model the key advertises, so the verdict
    # is based on what actually works rather than a guess at a name.
    with urllib.request.urlopen(
            urllib.request.Request(f"{base}/v1beta/models?key={key}",
                                   headers={"Accept": "application/json"}),
            timeout=30) as response:
        catalogue = json.loads(response.read().decode("utf-8"))
    candidates = [m.get("name", "").replace("models/", "") for m in catalogue.get("models", [])]
    preferred = [model, "gemini-3.6-flash", "gemini-3.5-flash-lite",
                 "gemini-3.1-flash-lite", "gemini-omni-1.1-flash",
                 "gemini-flash-latest"]
    ordered = [c for c in preferred if c in candidates]
    ordered += [c for c in candidates if c not in ordered]

    working: list[str] = []
    for candidate in ordered:
        url = f"{base}/v1beta/models/{candidate}:generateContent?key={key}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": "Reply with the single word: ready"}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 20},
        }).encode("utf-8")
        gen = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(gen, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8"))
            reply = data["candidates"][0]["content"]["parts"][0]["text"]
            working.append(candidate)
            print(f"  OK      {candidate}")
            if not working[:-1]:
                print(f"  GEMINI_DIRECT_TEST: PASS  model={candidate}")
                print(f"  reply={reply.strip()[:80]!r}")
        except urllib.error.HTTPError as exc:
            detail = " ".join(exc.read().decode("utf-8", "replace").split())
            code = exc.code
            short = ("denied access" if code == 403 else
                     detail.split('"message": "')[-1][:60] if '"message"' in detail
                     else detail[:60])
            print(f"  {code}     {candidate:34} {short}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ERR     {candidate:34} {type(exc).__name__}")

    if not working:
        print("\n  GEMINI_DIRECT_TEST: FAIL  this key cannot generate with ANY model")
        return 1
    print(f"\n  working models: {', '.join(working[:8])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
