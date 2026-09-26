# VERA — Intelligent Merchant Engagement Engine

> **Magicpin AI Challenge Submission**  
> A deterministic, stateful merchant engagement engine with LLM-assisted message generation, trigger prioritization, contextual decision-making, safety validation, and suppression logic.

---

## 🏆 Evaluation Results

### Official LLM Judge

| Metric | Score |
|---|---:|
| **Overall** | **36 / 50** |
| Specificity | 7 / 10 |
| Category Fit | **7 / 10** |
| Merchant Fit | 7 / 10 |
| Decision Quality | **7 / 10** |
| Engagement / CTA | 6 / 10 |

**Messages scored:** 14

### Strongest evaluated messages

| Scenario | Score |
|---|---:|
| Renewal | **46 / 50** |
| Supply Alert | **45 / 50** |
| Compliance | **43 / 50** |
| Kids Yoga | **40 / 50** |

No high-scoring trigger regressed during the final decision-engine improvements.

### Engineering Validation

| Check | Result |
|---|---:|
| Unit tests | **60 / 60 PASS** |
| Grounding / safety cases | **11 / 11 PASS** |
| Gemini runtime verification | **PASS** |
| Provider failures in final runtime verification | **0** |
| Successful provider attempts | **1 / 1** |

---

# 1. Problem

Merchant engagement systems typically have access to large amounts of operational and behavioral context:

- Merchant category
- Merchant profile
- Customer activity
- Business performance
- Reviews
- Inventory / supply
- Subscription status
- Visibility changes
- Demand trends
- Previous engagement history

The challenge is not simply generating text.

The system must decide:

> **What should be communicated, to whom, why now, and what should the merchant do next?**

A naive LLM-only architecture can produce:

- Generic recommendations
- Irrelevant messages
- Unsupported numbers
- Repeated notifications
- Poorly timed interventions
- Category-inappropriate actions
- Hallucinated discounts or metrics

VERA therefore separates **decision-making from language generation**.

---

# 2. Solution

VERA uses a **deterministic decision engine as the source of truth**, with an optional LLM layer used primarily for natural-language refinement.

```text
                    ┌─────────────────────┐
                    │   Merchant Context  │
                    │ Customer Context    │
                    │ Trigger Context     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Context Store       │
                    │ Versioned State     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Trigger Engine      │
                    │ Priority / Freshness│
                    │ Safety / Urgency    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Decision Engine     │
                    │                     │
                    │ Trigger → Family    │
                    │        → Fact       │
                    │        → Why Now    │
                    │        → Action     │
                    │        → CTA        │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Consent + Targeting │
                    │ Merchant/Customer   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Deterministic       │
                    │ Composition Layer   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Optional Gemini LLM │
                    │ Natural Language    │
                    │ Refinement          │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Validation Layer    │
                    │                     │
                    │ • Fact grounding    │
                    │ • Number validation │
                    │ • Merchant match    │
                    │ • CTA preservation  │
                    │ • Repetition check  │
                    │ • Safety checks     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ Final Action        │
                    │ Message + CTA       │
                    └─────────────────────┘
```

---

# 3. Core Design Principle

## Deterministic decisions, probabilistic language

The LLM is **not responsible for deciding business facts**.

The deterministic engine decides:

```text
WHAT happened
      ↓
WHY it matters
      ↓
WHAT the merchant should do
      ↓
WHAT CTA should be presented
```

The LLM may then improve the wording while remaining constrained by the generated fact pack.

This provides a stronger separation between:

- **Business logic**
- **Decision quality**
- **Natural-language generation**

and reduces the risk of LLM hallucination.

---

# 4. Decision Pipeline

Every trigger passes through a structured decision pipeline.

### Trigger

Examples include:

- Subscription renewal
- Inventory / supply alerts
- Review milestones
- Visibility changes
- Customer demand
- Dormancy
- Performance changes
- Compliance events

### Trigger → Decision

The decision engine converts a raw trigger into:

```text
Trigger
  ↓
Trigger Family
  ↓
Grounded Fact
  ↓
Why Now
  ↓
Recommended Action
  ↓
CTA
```

For example:

```text
Trigger:
Subscription approaching renewal

        ↓

Family:
Retention

        ↓

Fact:
Subscription is nearing renewal

        ↓

Why Now:
Renewal window is approaching

        ↓

Action:
Review the current plan and renew

        ↓

CTA:
Review renewal
```

This prevents the LLM from inventing the business recommendation.

---

# 5. Trigger Prioritization

When multiple events are available, VERA prioritizes them using deterministic ordering based on:

1. **Safety**
2. **Urgency**
3. **Freshness**

This avoids sending multiple competing messages for the same merchant in a single evaluation cycle.

The system also applies:

- Consent gating
- Deduplication
- Suppression
- Repetition control
- Merchant/customer targeting

---

# 6. Category-Aware Decisions

The recommendation is adapted to the merchant's category.

Supported category-aware decision paths include:

- Restaurants
- Gyms / fitness
- Salons
- Pharmacies
- Dental / healthcare
- Other supported merchant categories

Instead of generating a generic:

> "Improve your performance."

the decision engine can produce category-relevant actions such as:

```text
Restaurant
→ respond to reviews / improve visibility / address demand

Gym
→ address declining engagement / promote relevant offering

Salon
→ address appointment or discovery signals

Pharmacy
→ respond to stock / demand signals

Dental
→ address booking / review / visibility signals
```

The exact recommendation remains grounded in the available trigger context.

---

# 7. LLM Layer

The current implementation supports Gemini as the language-generation layer.

```text
Deterministic Draft
        ↓
Fact Pack
        ↓
Gemini
        ↓
Candidate Rewording
        ↓
Validation
        ↓
Accept / Reject
```

The LLM is therefore **optional from a correctness perspective**.

If LLM generation fails, times out, violates grounding rules, or produces an unsafe modification, the system can fall back to the deterministic message.

This ensures that an external model failure does not break the core engagement engine.

---

# 8. Grounding & Anti-Hallucination

The validation layer protects factual integrity.

The system checks generated messages against the available source facts.

Examples of protected information include:

- Percentages
- Prices
- Counts
- Performance metrics
- Pack quantities
- Merchant identity
- Trigger facts

An LLM cannot introduce an unsupported discount simply because it sounds persuasive.

For example:

```text
Source:
Pack contains 40 units

Allowed:
"Your 40-unit pack..."

Rejected:
"Get 40% off..."
```

The validation layer distinguishes between:

- A real source number
- A percentage
- A price
- An unsupported numerical claim

This is particularly important for merchant-facing communications.

---

# 9. Suppression & Deduplication

VERA prevents unnecessary repeated communication through deterministic controls.

The engine considers:

- Previous messages
- Trigger identity
- Message similarity
- Merchant state
- Consent
- Current decision priority

The objective is not to maximize message volume.

The objective is to send:

> **One relevant message when there is a meaningful reason to act.**

---

# 10. API

The application exposes a lightweight HTTP interface.

### Health Check

```http
GET /healthz
```

Used by deployment and judge health checks.

---

### Context Update

```http
POST /v1/context
```

Updates the versioned merchant/customer context.

---

### Tick

```http
POST /v1/tick
```

Runs the decision pipeline against the current context and available triggers.

Conceptually:

```text
Context
  ↓
Trigger prioritization
  ↓
Decision
  ↓
Consent
  ↓
Composition
  ↓
LLM refinement
  ↓
Validation
  ↓
Action
```

---

# 11. Stateful Context

Context is versioned rather than treated as a single static prompt.

This enables the system to reason about changing merchant state across evaluation ticks.

```text
Context v1
   ↓
Context v2
   ↓
Context v3
   ↓
...
```

This makes it possible to incorporate:

- Newly observed events
- Previous engagement
- Trigger freshness
- Merchant state changes
- Suppression history

---

# 12. Reliability Architecture

The system is intentionally designed so that the LLM is **not a single point of failure**.

```text
                  ┌──────────────┐
                  │ Deterministic│
                  │ Decision Core │
                  └───────┬──────┘
                          │
                          ▼
                  ┌──────────────┐
                  │ Draft Message│
                  └───────┬──────┘
                          │
                    LLM available?
                     /          \
                   YES           NO
                   /              \
                  ▼                ▼
             Gemini            Deterministic
             Rewrite             Fallback
                  │
                  ▼
             Validation
              /       \
           PASS       FAIL
            │           │
            ▼           ▼
         LLM text    Original
                    deterministic
                       draft
```

This allows the system to remain operational even when:

- The LLM times out
- The provider returns an error
- A response fails validation
- Rate limits are encountered

---

# 13. Evaluation

The solution was evaluated using the Magicpin LLM Judge.

### Final Evaluation

**36 / 50**

| Dimension | Result |
|---|---:|
| Specificity | 7 / 10 |
| Category Fit | 7 / 10 |
| Merchant Fit | 7 / 10 |
| Trigger Relevance / Decision Quality | 7 / 10 |
| Engagement / CTA | 6 / 10 |

### Notable Scenarios

**46 / 50 — Renewal**

The system correctly connected the renewal trigger to a timely retention action.

**45 / 50 — Supply Alert**

The system generated a highly relevant operational intervention while preserving the underlying facts.

**43 / 50 — Compliance**

The system prioritized a high-importance trigger and produced a clear action.

**40 / 50 — Kids Yoga**

The category-aware decision path produced a relevant recommendation.

---

# 14. Engineering Validation

The final implementation was validated using automated tests.

```text
60 / 60 unit tests                 PASS
11 / 11 safety / grounding cases   PASS
Gemini runtime verification        PASS
Provider failures                  0
Provider successes                 1 / 1
```

The test suite covers:

- Trigger prioritization
- Context handling
- Decision generation
- Category-specific behavior
- Consent
- Suppression
- Deduplication
- Numeric grounding
- LLM fallback
- Response validation

---

# 15. Technology Stack

### Backend

- Python
- Standard-library `ThreadingHTTPServer`
- JSON HTTP APIs

### Decision Engine

- Deterministic Python logic
- Versioned context
- Trigger prioritization
- Category-aware decisions
- Suppression / deduplication

### LLM

- Google Gemini
- Configurable model through environment variables

### Testing

- Python `unittest`
- End-to-end verification
- LLM runtime verification
- Grounding / hallucination checks

---

# 16. Project Structure

```text
magicpin-ai-challenge/
│
├── bot.py
│
├── engine/
│   ├── composition.py
│   ├── decisions.py
│   ├── gemini_provider.py
│   ├── nim_provider.py
│   └── nim_polish.py
│
├── tests/
│   └── test_engine.py
│
├── judge_simulator.py
├── verify_bot_e2e.py
├── verify_gemini_runtime.py
├── submission.jsonl
│
├── render.yaml
├── .env.example
└── README.md
```

---

# 17. Configuration

Create a `.env` file locally.

```env
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
LLM_API_KEY=<YOUR_API_KEY>

BOT_URL=http://127.0.0.1:8131
TEST_SCENARIO=full_evaluation
```

**Never commit `.env` or API keys to the repository.**

Use `.env.example` for public configuration documentation.

---

# 18. Local Run

Install the required dependencies if applicable, then start the bot:

```bash
python bot.py
```

The server runs on:

```text
http://127.0.0.1:8131
```

Verify health:

```bash
curl http://127.0.0.1:8131/healthz
```

Then run the evaluation:

```bash
python judge_simulator.py
```

---

# 19. Deployment

The repository includes a `render.yaml` deployment configuration.

The production deployment should provide the following environment variables:

```text
LLM_PROVIDER
LLM_MODEL
LLM_API_KEY
```

The API key should be configured through the deployment platform's secret/environment-variable system rather than committed to source control.

---

# 20. Design Trade-offs

### Why not use an LLM for everything?

An LLM-only architecture makes it difficult to guarantee:

- Consistent trigger prioritization
- Factual grounding
- Repetition control
- Consent enforcement
- Deterministic fallbacks

VERA therefore uses the LLM where it provides the most value:

> **Natural language generation and refinement.**

The business decision remains deterministic.

---

### Why deterministic fallback?

The system should still produce a valid action when:

```text
LLM unavailable
LLM timeout
LLM rate limited
Invalid LLM response
Unsupported factual claim
```

The deterministic draft becomes the fallback.

---

# 21. Key Takeaway

VERA is designed around a simple principle:

> **Decide deterministically. Communicate naturally. Validate everything.**

The architecture combines:

```text
State
+
Triggers
+
Deterministic Decisions
+
Category Intelligence
+
LLM Language Generation
+
Grounding
+
Suppression
+
Validation
```

to produce merchant-facing actions that are timely, relevant, actionable, and resilient to LLM failures.

---

## Final Status

**Submission-ready**

```text
Official Judge Score       36 / 50
Unit Tests                 60 / 60
Safety / Grounding        11 / 11
Gemini Runtime             PASS
Deployment Configuration   Ready
```

**Built for the Magicpin VERA AI Challenge.**