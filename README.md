# VERA — magicpin AI Challenge

A deterministic merchant-engagement engine. `compose(category, merchant,
trigger, customer?)` decides the next message, its CTA, the send-as identity, a
suppression key, and a rationale — using only facts present in the supplied
context.

**No LLM in the decision path.** Composition is pure Python standard library: no
network calls, no randomness, no external model. The same four contexts always
produce the same message, so replaying the judge's post-submission context
injection is safe.

---

## Live endpoint

| | |
|---|---|
| Base URL | *(set after deploy — see Deploy)* |
| Health | `GET /v1/healthz` |
| Metadata | `GET /v1/metadata` |
| Ingest | `POST /v1/context` |
| Decide | `POST /v1/tick` |
| Converse | `POST /v1/reply` |
| Reset | `POST /v1/teardown` |

Local run: `python bot.py --port 8131` (honors `$PORT`, binds `0.0.0.0`).

## Deploy

Standard library only, so there is no build step and no dependency install. Any of
these work:

- **Docker:** `docker build -t vera . && docker run -p 8080:8080 vera`
- **Render:** `render.yaml` is included (health check on `/v1/healthz`)
- **Railway / Heroku:** `Procfile` is included (`web: python bot.py`)
- **Fly.io / Cloud Run / App Runner:** run `python bot.py`; the service reads `$PORT`

## Approach

Every message is assembled from a trigger strategy that may cite only values
present in the current contexts. Trigger payload facts take priority; the
merchant's own performance snapshot is the fallback, and when neither supplies a
figure the message says so instead of estimating one. Salutation, offer framing,
and language come from the category pack, so a dentist is addressed as a
colleague while a salon reads warm and practical. Customer-facing sends are gated
on a recorded opt-in whose scope covers the trigger's purpose — missing consent
is denial, never an assumption. Suppression namespaces keys by merchant and
customer so a shared category key cannot silence unrelated recipients, and a tick
emits at most one message per merchant. Unknown triggers, unknown categories, and
stub payloads all degrade to a truthful generic path rather than a guess.

## Model choice

**The production path uses no model at all**, and that is the central design
decision. An LLM in the send path would make three scored properties unreliable:
reproducibility across replays, factual grounding (the engine may only assert
figures that exist in the supplied context), and p99 latency on a high-volume
tick. Deterministic composition makes all three structural rather than best-effort.

An LLM is used in exactly one place: **the judge**, for offline evaluation. That
provider is configurable via `LLM_PROVIDER` and is *not* part of the deployed
service. The move from Gemini to NVIDIA NIM was driven by measurement:

| Model | Result |
|---|---|
| `gemini-3.5-flash-lite` | HTTP 429 quota exhaustion mid-run; scores incomplete |
| `nvidia/llama-3.3-70b-instruct` | **410 Gone** — deployment retired |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | **410 Gone** — deployment retired |
| `nvidia/nemotron-3-super-120b-a12b` | HTTP 503, overloaded |
| `z-ai/glm-5.3`, `moonshotai/kimi-k3`, `openai/gpt-oss-20b` | cold-start timeouts |
| **`nvidia/nemotron-3-ultra-550b-a55b`** | **27.9 s, valid JSON — selected** |

Lesson worth recording: NVIDIA is decommissioning its model-specific NIM
endpoints, so `GET /v1/models` is the only reliable source of currently-valid
model IDs. Run `probe_nvidia_quick.py` to check availability before a scored run.

## Results

Official judge on `nvidia/nemotron-3-ultra-550b-a55b`:

- A clean `phase2_short` pass returned **44/50 (88%)** with zero heuristic
  fallbacks, confirming the judge wiring is sound end to end.
- The `full_evaluation` pass was interrupted partway through; the **7** messages
  scored before interruption averaged **32.9/50**. This is a **partial sample, not
  a final verdict** — read it as a signal that some message families still need
  work, not as a score.

`proxy_score.py` is a **local stand-in, not the official score**. It measures the
same five dimensions from verifiable properties of each message (numeric tokens,
prices, dates, citations, category vocabulary and taboos, merchant anchors, payload
echoes, jargon, CTA shape). Current proxy: **29.16/50 (58.3%)** — specificity
5.28, category fit 4.52, merchant fit 6.72, decision quality 4.56, engagement 8.08.
Use it to compare revisions, never to predict the judge's verdict.

## Tradeoffs

- **Determinism over fluency.** Templated wording is less varied than a sampled
  model, but it never invents a fact and never changes under replay.
- **Restraint over coverage.** A trigger that cannot be grounded is described
  honestly as unquantified rather than given a fabricated magnitude.
- **One message per merchant per tick.** Trades short-term reach for anti-spam.
- **State is in-memory.** Conversation history and suppression keys do not survive a
  restart, and the service must run as a *single* instance — horizontal scaling
  would need shared state. Acceptable for this challenge's stateless scoring path,
  and the first thing to fix before real production use.

## Running the judge locally

`judge_simulator.py` needs an LLM key, so it cannot run in CI. Copy `.env.example`
to `.env` and set your key — `.env` is git-ignored and `.dockerignore` excludes it
from the image.

```env
LLM_API_KEY=PASTE_FRESH_NVIDIA_NIM_KEY_HERE
LLM_PROVIDER=nvidia
LLM_MODEL=nvidia/nemotron-3-ultra-550b-a55b
TEST_SCENARIO=phase2_short
```

```powershell
python run_judge_scored.py phase2_short      # 3 scored calls, writes judge_output.txt
python parse_judge_output.py --hints         # strips ANSI, separates real vs fallback
```

The proxy scorer referenced above runs standalone with `python proxy_score.py`.

## Checks

```bash
python -m unittest discover -s tests -v   # 30 tests, including live HTTP
python run_judge_checks.py --port 8131    # warmup, auto-reply hell, intent, hostile
python generate_submission.py             # regenerate the 30-row submission.jsonl
```

Customer-scoped rows are only emitted when the customer's opt-in scope covers the
trigger's purpose; a pair that fails that check is written as an explicitly
suppressed row rather than a message the bot would refuse to send.

## What extra context would help most

1. A canonical list of what Vera can execute after a merchant accepts, so a
   commitment reply can promise a real action rather than a draft.
2. A stable booked slot/time on appointment and refill triggers, so reminders can
   name a real time instead of asking for one.
3. A per-merchant outcome signal (did the last message get a reply?) to steer topic
   choice more sharply than `engagement` tags allow.
4. A consent-purpose taxonomy, replacing substring matching on free-text scopes.

See [docs/decision_engine.md](docs/decision_engine.md) for thresholds, design
rationale, and known limitations.

