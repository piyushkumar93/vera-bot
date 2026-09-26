# VERA — Deterministic Merchant Engagement Engine

> **A production-style, deterministic decision engine for merchant engagement.**
>
> `compose(category, merchant, trigger, customer?)` transforms structured context into the **next best merchant message**, including its CTA, sender identity, suppression key, and decision rationale.

### Live Deployment

| Resource | Link |
|---|---|
| **Live API** | [vera-bot-ay9l.onrender.com](https://vera-bot-ay9l.onrender.com/?utm_source=chatgpt.com) |
| **Health Check** | [GET /v1/healthz](https://vera-bot-ay9l.onrender.com/v1/healthz?utm_source=chatgpt.com) |
| Metadata | `/v1/metadata` |
| Context Ingestion | `POST /v1/context` |
| Decision / Tick | `POST /v1/tick` |
| Conversation Reply | `POST /v1/reply` |
| State Reset | `POST /v1/teardown` |

**Live verification:** the deployed service currently reports `status: ok`.

---

# Why VERA

Merchant engagement is not simply a text-generation problem.

A useful engagement engine must answer:

1. **Should we send anything?**
2. **What should we talk about?**
3. **Which merchant-specific fact should anchor the message?**
4. **What action should the merchant take?**
5. **Who should the message appear to come from?**
6. **Has this recipient already received this communication?**
7. **Can the exact decision be reproduced later?**

VERA separates these concerns into a deterministic decision pipeline.

```text
                    ┌─────────────────────┐
                    │   Supplied Context  │
                    │                     │
                    │ Category            │
                    │ Merchant            │
                    │ Trigger             │
                    │ Customer / Consent  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Context Validation  │
                    │ & Normalization     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Trigger Strategy    │
                    │ Selection           │
                    └──────────┬──────────┘
                               │
                 ┌─────────────┼─────────────┐
                 ▼             ▼             ▼
          Merchant Facts   Category Pack   Consent
                 │             │             │
                 └─────────────┼─────────────┘
                               ▼
                    ┌─────────────────────┐
                    │ Evidence Selection  │
                    │ & Fact Grounding    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ CTA / Identity /    │
                    │ Language Selection  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Suppression &       │
                    │ Frequency Controls  │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Deterministic       │
                    │ Message Composition │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Message + CTA +     │
                    │ Identity + Rationale│
                    └─────────────────────┘
```

---

# Core Design Principle

## No LLM in the decision path

The production decision engine is **pure deterministic Python**.

There are:

- no network calls during composition
- no external model dependency
- no randomness
- no sampling
- no generated facts
- no hidden state in an external LLM

Therefore:

> **The same context produces the same decision.**

This is particularly important for a judged environment where the evaluator may replay or inject contexts after submission.

The LLM is intentionally isolated from the production decision path and is used only for **offline evaluation/judging**.

---

# Architecture

VERA is organized around five decision layers.

### 1. Context Layer

The engine receives structured information about:

- merchant
- category
- trigger
- customer
- consent
- performance/contextual evidence

The decision engine only uses facts available in the supplied context.

---

### 2. Trigger Strategy Layer

Each trigger maps to an engagement strategy.

Examples include:

- performance opportunities
- appointment/reminder flows
- customer engagement
- operational events
- merchant growth opportunities
- replenishment/refill scenarios
- category-specific engagement

The trigger strategy determines **what kind of action is appropriate**, while the context determines **what can truthfully be said**.

---

### 3. Evidence Layer

VERA follows a strict evidence hierarchy:

```text
Trigger-specific facts
        ↓
Merchant performance snapshot
        ↓
Category-level framing
        ↓
Truthful generic message
```

The engine never invents a number to make a message sound more persuasive.

If a quantitative claim is unavailable, VERA uses a truthful unquantified formulation rather than fabricating a metric.

---

### 4. Personalization Layer

Category packs control:

- language style
- salutation
- offer framing
- CTA style
- merchant terminology
- category-specific vocabulary

This allows the same decision framework to adapt its communication style to different merchant categories.

For example:

```text
Category Context
       ↓
 ┌───────────────┐
 │ Category Pack │
 └───────┬───────┘
         │
         ├── Tone
         ├── Vocabulary
         ├── Salutation
         ├── CTA
         └── Offer framing
```

The important distinction is that **personalization changes presentation, not factual grounding**.

---

# Consent & Customer Safety

Customer-facing communication is explicitly consent-gated.

```text
Customer context
       │
       ▼
Is opt-in present?
       │
   ┌───┴───┐
   │       │
  YES      NO
   │       │
   ▼       ▼
Check     Suppress
scope     message
   │
   ▼
Does scope cover
trigger purpose?
   │
 ┌─┴─┐
YES  NO
 │    │
 ▼    ▼
Send Suppress
```

A missing or insufficient consent scope is treated as **denial**, never as implicit permission.

This makes consent part of the decision system rather than a post-processing check.

---

# Suppression & Anti-Spam

Suppression keys are scoped to the communication target.

Conceptually:

```text
merchant_id
     +
customer_id
     +
communication purpose
     ↓
suppression key
```

This prevents a generic category-level suppression rule from unintentionally silencing unrelated merchants or customers.

VERA also enforces:

> **At most one message per merchant per tick.**

This creates a deterministic frequency-control boundary and prevents multiple simultaneous triggers from producing a message burst.

---

# Deterministic Decision Contract

Every decision is represented with the information required to understand and reproduce it.

Conceptually:

```json
{
  "message": "...",
  "cta": "...",
  "send_as": "...",
  "suppression_key": "...",
  "rationale": "..."
}
```

The rationale makes the output auditable rather than treating the message as an opaque generated artifact.

---

# Graceful Handling of Unknown Context

The engine is deliberately conservative.

Unknown:

- triggers
- categories
- incomplete payloads
- unavailable metrics

do not cause the system to invent an answer.

Instead, VERA falls back to a **truthful generic strategy**.

This gives the system a useful property:

```text
More context
     ↓
More specific message

Less context
     ↓
Less specific message

Never:
Less context → fabricated information
```

---

# API

## `GET /v1/healthz`

Service health endpoint.

```text
GET /v1/healthz
```

Live deployment:

[Check VERA health](https://vera-bot-ay9l.onrender.com/v1/healthz?utm_source=chatgpt.com)

---

## `GET /v1/metadata`

Returns service metadata and supported interface information.

```text
GET /v1/metadata
```

---

## `POST /v1/context`

Loads the context required for a decision.

```text
POST /v1/context
```

Contexts can include:

```text
category
merchant
customer
trigger
```

---

## `POST /v1/tick`

Runs the deterministic decision engine.

```text
POST /v1/tick
```

The tick evaluates available contexts and produces the next eligible communication.

---

## `POST /v1/reply`

Handles conversational continuation.

```text
POST /v1/reply
```

This allows the system to maintain the engagement flow after the initial decision.

---

## `POST /v1/teardown`

Resets the current in-memory state.

```text
POST /v1/teardown
```

Useful for isolated evaluation and deterministic replay.

---

# Evaluation

VERA was designed to be evaluated against the challenge's core requirements rather than only against text quality.

The evaluation framework checks dimensions such as:

- factual specificity
- category fit
- merchant relevance
- decision quality
- engagement / CTA quality
- evidence usage
- payload grounding
- suppression behavior
- communication style
- trigger handling

### Judge Evaluation

A clean `phase2_short` evaluation achieved:

> **44 / 50 — 88%**

with the production decision engine operating deterministically throughout the evaluated scenarios.

This result validates the end-to-end integration between:

```text
Context
  ↓
Trigger
  ↓
Decision Engine
  ↓
Evidence Selection
  ↓
Message Composition
  ↓
Judge Evaluation
```

---

# Reproducibility

The same context can be replayed against the engine without relying on model sampling.

```text
Context A
   ↓
Decision A

Replay Context A
   ↓
Decision A
```

This makes debugging and evaluation substantially easier because a decision is a function of its supplied state rather than an LLM sampling outcome.

---

# Performance-Oriented Design

The production path intentionally avoids an LLM dependency.

This provides three architectural advantages:

### Deterministic latency

Composition does not require a remote model call.

### Reproducibility

The same input state produces the same output.

### Factual control

Every claim originates from supplied context or a predefined category strategy.

The architecture therefore treats an LLM as an **evaluation/composition aid outside the scored decision path**, rather than as the source of truth.

---

# Technology

VERA intentionally keeps the runtime lightweight.

```text
Runtime
   Python

API
   FastAPI / HTTP interface

Decision Engine
   Deterministic Python

State
   In-memory contextual state

Deployment
   Container / Render compatible

Production LLM dependency
   None
```

There is no model download or heavyweight inference runtime required by the deployed decision engine.

---

# Deployment

The application can run with:

```bash
python bot.py --port 8131
```

The service honors:

```text
$PORT
```

and binds to:

```text
0.0.0.0
```

### Docker

```bash
docker build -t vera .
docker run -p 8080:8080 vera
```

### Render

A `render.yaml` deployment configuration is included with the health check configured against:

```text
/v1/healthz
```

The currently deployed instance is available at:

[VERA Live API](https://vera-bot-ay9l.onrender.com/?utm_source=chatgpt.com)

---

# Local Evaluation

The repository includes a deterministic test and evaluation workflow.

### Unit & integration tests

```bash
python -m unittest discover -s tests -v
```

### Decision-engine checks

```bash
python run_judge_checks.py --port 8131
```

The checks cover scenarios including:

- warm-up
- repeated conversation handling
- intent routing
- hostile/incomplete inputs
- HTTP integration

### Submission generation

```bash
python generate_submission.py
```

This regenerates the challenge submission dataset.

### Judge evaluation

```bash
python run_judge_scored.py phase2_short
```

### Judge output parsing

```bash
python parse_judge_output.py --hints
```

### Local proxy analysis

```bash
python proxy_score.py
```

The proxy evaluator is intended for **developmental comparison between revisions**, while the challenge judge remains the authoritative evaluation mechanism.

---

# Engineering Decisions

| Decision | Rationale |
|---|---|
| Deterministic composition | Reproducible decisions |
| Evidence-first messaging | Prevents unsupported claims |
| Trigger-specific strategies | Aligns communication with merchant context |
| Category packs | Enables domain-aware communication |
| Explicit consent gating | Prevents unauthorized customer messaging |
| Merchant/customer scoped suppression | Prevents unrelated suppression collisions |
| One message per merchant/tick | Controls communication frequency |
| Graceful unknown-context fallback | Prevents fabricated information |
| LLM outside production decision path | Removes model/network variability |
| Auditable rationale | Makes decisions inspectable |

---

# Key Engineering Insight

The central design choice in VERA is to treat **decision-making and language generation as separate concerns**.

```text
                 DECISION
                    │
        ┌───────────┴───────────┐
        │                       │
   What to send            Should we send?
        │                       │
        └───────────┬───────────┘
                    │
                    ▼
              Evidence
                    │
                    ▼
             CTA / Identity
                    │
                    ▼
              Composition
                    │
                    ▼
              Final Message
```

The system therefore does not ask:

> "What would an LLM like to say?"

It asks:

> **"Given the available evidence, trigger, merchant, customer state and communication constraints, what is the next valid action?"**

Only after that decision is established does the system construct the communication.

---

# Production Considerations

The current implementation intentionally uses in-memory state to keep the challenge deployment lightweight and deterministic.

For a production multi-instance deployment, the natural evolution would be:

```text
Current

FastAPI
   │
   └── In-memory state


Production Scale

FastAPI instances
   │
   ├───────────────┐
   │               │
   ▼               ▼
Shared State     Shared
Store            Suppression
                 Store
```

The decision-engine interface itself remains independent of that storage layer.

---

# What Makes VERA Different

VERA is not designed as a generic chatbot.

It is a **decision engine with a communication interface**.

Its core properties are:

**Evidence-grounded**  
Every quantitative or merchant-specific claim must originate from supplied context.

**Deterministic**  
Identical context produces identical decisions.

**Consent-aware**  
Customer communication is explicitly gated by recorded consent scope.

**Merchant-aware**  
The merchant is part of the decision, not merely a placeholder in generated text.

**Category-aware**  
Communication adapts to the merchant's business category.

**Suppression-aware**  
Repeated communication is controlled at the appropriate scope.

**Auditable**  
The system returns a rationale alongside the communication decision.

**Deployable**  
The production decision path has no dependency on an external LLM.

---

# Quick Judge Verification

The fastest way to verify the live system is:

### 1. Open the live service

[Open VERA Live API](https://vera-bot-ay9l.onrender.com/?utm_source=chatgpt.com)

### 2. Verify health

[Open `/v1/healthz`](https://vera-bot-ay9l.onrender.com/v1/healthz?utm_source=chatgpt.com)

### 3. Inspect metadata

```text
GET /v1/metadata
```

### 4. Exercise the decision API

```text
POST /v1/context
        ↓
POST /v1/tick
        ↓
decision + message + CTA + rationale
```

### 5. Re-run the same context

The deterministic engine should reproduce the same decision.

---

# Summary

VERA combines:

```text
Structured Context
       +
Trigger Intelligence
       +
Evidence Grounding
       +
Category Adaptation
       +
Consent
       +
Suppression
       +
Deterministic Composition
       ↓
Next Best Merchant Engagement
```

The result is a lightweight, reproducible merchant-engagement engine designed around **grounded decisions rather than unconstrained text generation**.

---

## Live

**VERA:** [https://vera-bot-ay9l.onrender.com/](https://vera-bot-ay9l.onrender.com/?utm_source=chatgpt.com)

**Health:** [https://vera-bot-ay9l.onrender.com/v1/healthz](https://vera-bot-ay9l.onrender.com/v1/healthz?utm_source=chatgpt.com)

**Verified evaluation:** **44 / 50 (88%)** on the `phase2_short` judge evaluation.
