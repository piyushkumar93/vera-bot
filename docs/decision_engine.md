# VERA — deterministic decision engine

## Approach

`compose(category, merchant, trigger, customer?)` is a pure, rule-based decision
pipeline. No LLM, no network, no randomness: the same four contexts always yield
the same message, which keeps results reproducible under the judge's post-hoc
context injection.

```
context validation → normalization → signal extraction
   → eligibility & consent → suppression → trigger priority
   → action selection → message → CTA → send-as → rationale → validation
```

Design rules that drive every rule in `engine/`:

- **Say only what context contains.** Numbers, prices, dates, competitor names,
  offers, and citations are copied from the supplied objects. When a fact is
  absent the message says so or stays silent — it is never estimated.
- **Never hardcode a merchant, trigger ID, or test case.** Behaviour is keyed on
  trigger *kind* and on the shape of the payload, so unseen kinds and unseen
  merchants take a sensible generic path instead of failing.
- **Restraint is a feature.** A tick with nothing worth sending returns
  `{"actions": []}`; at most one message per merchant per tick.
- **Consent is a hard gate.** Customer-scoped sends require a matching customer
  whose recorded opt-in scope covers the trigger's purpose. Missing consent is
  denial.

## Tradeoffs

- **Determinism over fluency.** Wording is templated, so it is less nuanced than
  a sampled model, but it never invents a fact and never changes under replay.
- **Restraint over coverage.** Triggers whose payload cannot be grounded (for
  example a stub `perf_dip` with no figure and no matching snapshot movement) are
  described honestly as unquantified rather than given an invented magnitude.
- **One message per merchant per tick.** Simultaneous events queue rather than
  stack, which trades short-term reach for anti-spam.

## What extra context would help most

1. A stable `slot`/`time` on appointment and refill triggers, so reminders can
   name a real booked time instead of asking for one.
2. A per-merchant outcome signal (did the last Vera message get a reply?) to
   steer topic choice more sharply than `engagement` tags allow.
3. Consent scopes expressed as a purpose enum rather than free-text strings, so
   integrations can share one stable set of purpose values.

## Layout

| Path | Purpose |
|---|---|
| `bot.py` | stdlib HTTP service exposing five required `/v1/*` endpoints plus optional teardown |
| `engine/composition.py` | trigger strategies, CTA, send-as, rationale |
| `engine/signals.py` | context accessors, number/date helpers, consent gate |
| `engine/context_store.py` | versioned context storage |
| `engine/state.py` | suppression, conversation and opt-out state |
| `engine/conversation.py` | reply routing (auto-reply, opt-out, intent) |
| `engine/constants.py` | centralised thresholds and phrase sets |
| `conversation_handlers.py` | optional replay wrapper for multi-turn checks |
| `run_judge_checks.py` | official operational scenarios without LLM scoring |
| `generate_submission.py` | regenerates `submission.jsonl` from the expanded pairs |
| `docs/decision_engine.md` | engineering log and rule rationale |

## Running

```bash
python bot.py --port 8080
python -m unittest discover -s tests
python run_judge_checks.py --port 8080
python generate_submission.py
```

---

# Engineering log

## Context versioning

Contexts are stored by `(scope, context_id)`. Payloads must be objects and
versions positive integers; only a strictly higher version replaces the prior
record. Equal and older versions return `409 stale_version` with the current
version, matching the supplied API examples. `/v1/teardown` clears the in-memory
context and runtime state.

## Trigger priority

Ranking is a stable tuple, not insertion order: a safety class (supply alerts,
regulation changes, recall alerts) is ordered before the caller-supplied
`urgency` (1–5), then the trigger ID breaks ties. So a safety event always
outranks a merely urgent one, and equal-urgency triggers always resolve the same
way. One message is emitted per merchant per tick, with a 20-action cap, so
simultaneous events queue instead of stacking. Unknown kinds fall through to a
generic strategy that quotes only plain-text `title` / `topic` / `event` /
`metric_or_topic` fields.

## Signal interpretation

Trigger payload facts lead; the merchant snapshot is the fallback, never the
override. A `delta_pct` renders as a percentage ratio when `|value| <= 1` and as
an already-whole percentage otherwise, so both `0.18` and `18` read as `18%`.

Three cases drove specific decisions:

- **Stub payloads.** Generator-expanded triggers carry
  `{"placeholder": true, "metric_or_topic": <kind>}` with no metric or figure.
  Emitting "the trigger flags a decline in performance" is uninformative, so the
  engine falls back to the merchant's own `delta_7d` via `movement_sentence`. If
  the snapshot shows no matching movement either, the message says the trigger
  quotes no figure rather than inventing a magnitude.
- **Conflicting signals.** A `perf_dip` trigger against a snapshot showing
  growth is reported as a conflict with the real totals, not resolved silently
  in either direction.
- **Digest lookups.** An explicit `top_item_id` that does not resolve returns
  nothing. Substituting a different digest item would turn stale context into a
  false citation, so the research claim is dropped instead.

Category knowledge is used as *category knowledge*: `_catalog_offer` supplies a
service+price pattern from the category pack and is phrased as a suggestion, never
as the merchant's own live offer. Only offers with `status: active` are described
as the merchant's.

## Suppression design

Output `suppression_key` is the trigger's own key, or `trigger:{id}` when absent.
Internal dedupe is namespaced `merchant|customer|suppression_key`, because a
category-level key such as `research:dentists:2026-W17` is shared across
merchants and must not silence the other recipients. A body-level guard also
blocks re-sending an identical body to the same recipient, and conversation IDs
are never reused. A sent trigger is not re-sent until teardown.

## Conversation state

`/v1/reply` routes repeated canned auto-replies (wait, then end on the third
repetition), explicit opt-outs (end and suppress that recipient), topic-specific
declines (end only the current conversation), deferrals (wait a day), clear
commitments (move to the next step without another qualifying question),
out-of-scope requests, and questions (ask what needs clarification rather than
guessing). Duplicate text pauses to avoid loops. For planning triggers, the
latest merchant and Vera turns determine whether to advance an unanswered
question or follow up on an outline Vera already sent; the earlier outline is
clearly framed as a proposal from conversation history.

## Customer and consent

Customer scope requires a customer context whose `merchant_id` matches, an
eligible state, a recorded opt-in date, a usable channel, and no explicit
`reminder_opt_in: false`. The recorded consent scopes must exactly match one of
the allowed purposes for that trigger (recall, appointment, refill, win-back,
trial/program, or bridal). Missing or unknown scopes are denied; consent is never
inferred from the customer merely existing.

This is why several canonical pairs are *composed* but not *sent*: their
customers consented to `promotional_offers` only, which does not cover an
appointment reminder. The composition is still previewable, but the send gate
holds the line.

## Category handling

Categories are read from context, never branched by slug. Salutation comes from
identity plus the category voice: a professional title is added when the slug or
business name implies one, and never doubled when the supplied name already
carries it (dentists get "Dr. Meera", not "Meera" and not "Dr. Dr. Meera").
Unknown categories degrade to neutral operator language with no invented
category facts.

## Important thresholds

- `SAFETY_PRIORITY` — supply/regulation/recall alerts outrank all other classes.
- `AUTO_REPLY_PHRASES` — one repetition waits 4h, the second 24h, the third ends.
- Opt-out, decline, defer, question, and commitment phrase sets live in
  `engine/conversation.py`.

## Time handling

Seasonal grounding uses the `now` supplied on the tick, never wall-clock time, so
composition stays a pure function of its inputs. Month ranges expand from written
form (`Apr-Jun`, and `Nov-Feb` which wraps the year boundary); a beat is used only
when it actually covers the current month, since applying another window's note
would misstate the season.

## Known limitations

- Wording is templated; it is serviceable but less varied than a sampled model.
- Consent scope matching uses exact lower-case set intersection. An unrecognized
  alias is denied rather than guessed.
- Appointment and refill triggers often lack a concrete slot, so the message asks
  for a time rather than naming a booked one.
- The rationale is a faithful summary of the decision path rather than a
  per-trigger argument; it will not enumerate every fact used.
- No per-merchant outcome feedback loop, so topic rotation is driven by trigger
  kind alone.
