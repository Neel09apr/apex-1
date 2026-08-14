# Apex-1 — System Design & Build Plan

Frozen design for the pre-send decision layer. Written before the remaining
build so the contracts stop moving. Companion to `DECISIONS.md` (strategy);
this file is mechanism.

Status of the codebase this describes: `agent.py`, `eval.py`, `rubric.md`,
`leads.sample.json` exist and pass 5/5 offline gates. Sections marked **BUILT**
are on disk. Sections marked **TO BUILD** are the remaining work.

---

## 1. Who runs this

**The operator is a Dublin startup, 5–40 people, no RevOps hire, no DPO.**

Not the lead — the operator. That distinction drives every design choice below.
Their pipeline is spread across six jurisdictions before they are twenty people,
because a home market of 5.4m forces them out. So they hit per-country lawful
basis at seed stage, with none of the legal function a US company would have by
the time it faces the same problem.

The three constraints that follow, and what each buys in design:

| Operator constraint | Design consequence |
|---|---|
| No DPO, still liable | Lawful basis is a versioned table in code, not a judgment call per send |
| Fear of an AI mistake is the #1 adoption barrier (30% of Irish SMEs) | Dry-run default, `hold_for_approval` branch, kill switch — the off switch is a feature, not a safety afterthought |
| No SDRs at all | One operator must clear a day's queue in minutes, so the decision has to arrive with its reasoning attached |

---

## 2. System context

```
      Unify (Detect)                Apex-1                     Unify (Engage)
 ┌───────────────────────┐   ┌──────────────────────┐   ┌────────────────────┐
 │ signal fires          │   │ deterministic layer  │   │ Play branches on   │
 │ waterfall enrichment  │──▶│  jurisdiction        │──▶│ ai_gate, 4 ways    │
 │ Play webhook          │   │  disqualifiers       │   │                    │
 └───────────────────────┘   │  ───────────────     │   │ send  → sequence   │
                             │ model layer (Codex)  │   │ hold  → Slack      │
                             │  fit + timing        │   │ suppr → DNC list   │
                             │  sourced evidence    │   │ nurt  → nurture    │
                             │  ───────────────     │   └────────────────────┘
                             │ gate + audit record  │
                             └──────────────────────┘
```

**The load-bearing rule:** policy and arithmetic never touch the model. The
model returns four dimension scores and evidence; code computes the weighted
score, the tier, the lawful basis and the gate. A hallucinating model costs a
mis-scored lead. A model deciding lawful basis costs a DPC complaint.

### Layer ownership

| Layer | Owns | Never touches |
|---|---|---|
| Unify | signals, enrichment, sequences, deliverability, reply routing, Slack | the decision |
| Python | jurisdiction, basis, disqualifiers, weights, tier, **gate** | unstructured judgment |
| Codex | fit/timing judgment, rationale, evidence | score, tier, action, gate |

---

## 3. Data contracts — frozen

Three contracts. Changing any of them after this point requires updating
`eval.py` in the same commit.

### 3.1 Unify webhook → internal record  **BUILT**

Mapped in exactly one function, `from_unify_webhook()`. When the payload shape
surprises us at hour six there is one place to fix.

| Internal field | Unify path | Provenance source |
|---|---|---|
| `record_id` | `id` ‖ `contact.id` ‖ `account.id` | — |
| `email` | `contact.email` | — |
| `company`, `domain` | `account.name`, `account.domain` | `unify:account` |
| `country`, `employees`, `industry`, `tech_stack`, `funding` | `account.*` | `unify:enrichment` |
| `title` | `contact.title` | `unify:contact` |
| `signals`, `engagement` | `signal.description`, `signal.detail` | `unify:<signal.type>` |
| `notes` | `notes` | `unify:form` |
| `is_customer`, `has_open_opp` | `account.*` | — |

Every enriched field is provenance-wrapped `{value, source, fetched_at}`.
`fetched_at` is ISO-8601 UTC, `%Y-%m-%dT%H:%M:%SZ`. A missing `fetched_at` must
not crash the run (`lead_012` is the regression case).

**Decision:** source strings keep the `unify:` prefix. Unify's enrichment is
then named in every audit record we emit — the platform is visible in the
output, not just the diagram.

### 3.2 Model I/O  **BUILT**

`SCORE_SCHEMA`, strict mode. Returns exactly: `dimension_scores{fit, timing,
engagement, reachability}`, `rationale`, `evidence[{claim, quote, source}]`,
`confidence`. Deliberately absent: score, tier, action.

**Contract change, rubric v3 → v4.** Evidence gained a required `quote`.
`claim` may paraphrase; `quote` must be character-exact from a field value, and
`verify_quotes()` checks every one against the record before anything is
written. A quote found in no field is discarded together with its claim.

The old check tested that the `source` **label** existed in the record. A model
could pair a valid label — `enrichment:apollo` — with an entirely invented claim
and pass. A plausible label is trivial to emit; a character-exact quote from a
field you were never given is not.

Matching ignores case, whitespace and smart typography (the vendor's own noise)
and nothing else, so a quote stitched from two fields still fails. Surviving
entries are re-attributed to the field the quote was actually found in rather
than the label the model supplied — resolving provenance in code beats trusting
the model's pointer, and costs nothing.

No punctuation-stripping pass, unlike `earshot/answer.py`: that exists for
scraped markdown tables with missing delimiters. Our fields are structured
values, and list fields are joined with `", "` so the model can quote them as
rendered.

**A verbatim quote is not proof of a trustworthy field.** `verify_quotes()`
proves a quote is real; it says nothing about whether the field it came from is
one a prospect controls. `form` and `unify:form` are written directly by the
prospect — `lead_007`'s injection lives in exactly one of them, and a quote
lifted from it is genuinely verbatim, so the quote gate alone waves it through.

`validate()` adds a second check: any surviving evidence from
`UNTRUSTED_EVIDENCE_SOURCES` (`form`, `unify:form`) that backs a score ≥45 (the
threshold that would trigger `send` under `legitimate_interest`) forces
`confidence` to `"low"` in code — never left at the model's own self-report.
That routes to `human_review` through the existing low-confidence path in
`tier_and_action`, not a new one.

This rides on `verify_quotes()`'s re-attribution property for free: every
surviving quote is already resolved to the field it was *actually* found in,
not the label the model gave it. A model cannot dodge the trust check by
mislabelling an untrusted quote as `enrichment:apollo` — re-attribution happens
first, so the trust check sees the true source regardless of the model's claim.

**A real quote is not proof it supports the claim it's attached to.** Verbatim
matching proves the *quote* is genuine; it says nothing about whether the quote
actually backs the *claim* — a model can cite real, topically-adjacent text
(e.g. a `tech_stack` field) as evidence for an unrelated assertion (hiring
intent) and pass the quote check cleanly.

Contract change, rubric v4 → v5: evidence gained a required `supports`
boolean, declared by the model in the same call that produces the claim.
`verify_quotes()` drops anything marked `supports: false` before it ever
checks the quote — two independent gates, neither substituting for the other.
This proves the *mechanism*: a claim the model itself flags as unsupported is
discarded regardless of whether its quote is real. Whether the model's
*judgment* is any good — does it decline to over-claim when it should — is a
live-run question, measured the same way injection resistance is once model
access exists.

Missing `supports` (a recording from before this field existed) defaults to
`true`, so an older `runs.jsonl` still replays under `--replay`.

Strict mode requires `additionalProperties:false` and every property in
`required` at every level. Refusals are first-class (`message.refusal`) and
raise rather than degrade to a default score.

### 3.3 Decision → Unify write-back  **BUILT (path unverified)**

`POST {UNIFY_BASE}{UNIFY_RECORD_PATH}`, header `x-api-key`, custom object
`gtm_decision`. Twelve fields: `ai_gate`, `ai_tier`, `ai_score`, `ai_basis`,
`ai_jurisdiction`, `ai_confidence`, `ai_rationale`, `ai_evidence` (JSON string),
`ai_decided_at`, `ai_model_version`, `ai_rubric_version`, plus `record_id`.

⚠️ `UNIFY_RECORD_PATH` is **unverified**. Base URL and auth header are confirmed.
This is one line and it is the first thing to test.

---

## 4. Decision flow

```
lead ──▶ resolve_jurisdiction ──▶ disqualify ──┬── hits ──▶ tier=disqualified, 0 tokens
                                               │
                                               └── clean ──▶ Codex ──▶ validate ──▶ gate
```

Order matters: **disqualifiers run before the model.** A competitor, an existing
customer or a suppression-list hit costs zero tokens. Cost control and
correctness point the same way.

### Gate — the state machine

| basis \ tier | A | B | C | disqualified |
|---|---|---|---|---|
| `legitimate_interest` | send | send | nurture | suppress |
| `consent_required` | hold_for_approval | hold_for_approval | hold_for_approval | suppress |
| `unknown` | hold_for_approval | hold_for_approval | hold_for_approval | suppress |
| `blocked` | suppress | suppress | suppress | suppress |

Basis dominates tier. A perfect-fit German lead holds. That is the product.

**`unknown` is not `blocked`.** One means we have not decided; the other means we
decided no. Both fail closed — nothing sends either way — but `unknown` routes to
a queue a human can clear, and `blocked` is permanent.

Three states used to collapse into `suppress`: a missing country (enrichment
gap), a country absent from `OUTREACH_BASIS` (policy gap), and a country we
ruled against (decision). The first two are now `unknown` → hold. Collapsing
them lost unenriched leads permanently, and would have silently suppressed every
lead from the next market the operator entered.

Borrowed from `earshot/answer.py` in the AdversarialCI repo, which draws the
same line between `confidence: "error"` (we could not look) and
`confidence: "none"` (we looked and found nothing).

`confidence == "low"` forces `human_review` regardless of score — an unsure
model never routes straight to a rep.

---

## 5. Workflow provisioning — the four Unify branches  **TO BUILD**

The gate is inert until a Play acts on it. This is the integration work.

| `ai_gate` | Unify action | Proves |
|---|---|---|
| `send` | Enroll in sequence | The loop closes |
| `hold_for_approval` | **Unify native Slack alert** to operator; no enrollment | Human-in-the-loop — the answer to fear-of-mistakes |
| `suppress` | Add to do-not-contact list | Compliance has teeth, not just a field |
| `nurture` | Add to nurture list; no sequence | Not everything is binary |

The Slack row matters: we cut *custom* Slack alerting as scope creep, correctly.
Configuring Unify's *native* alert on the hold branch is using the platform, not
duplicating it — and it completes the human-in-the-loop story that
`hold_for_approval` implies but does not currently deliver.

**Prerequisite, test first:** does a Unify Play re-evaluate branch conditions on
a REST write-back to a custom field, and how quickly? If branching is delayed or
unavailable, the demo has to fall back to terminal logs plus the record view.
Discovering this at hour 22 kills the demo; discovering it at hour 1 costs
nothing.

---

## 6. Failure modes

| Failure | Handling | Status |
|---|---|---|
| Model refuses | Raise → dead letter | BUILT |
| Malformed / empty content | Raise → dead letter | BUILT |
| Dimension out of 0–100 | `ValueError` → dead letter | BUILT |
| Fabricated evidence source | Dropped, retained in `dropped_evidence` | BUILT |
| Unify 4xx/5xx | Logged to stderr, not ledgered, batch continues | BUILT |
| Duplicate processing | Local ledger `unify_written.jsonl` | BUILT — key needs widening, §8 |
| Unresolved jurisdiction | `unknown` → `hold_for_approval` (recoverable) | BUILT |
| High score backed by prospect-authored evidence | `confidence` forced to `low` → `human_review` | BUILT |
| Wrong model id / dead key / schema rejected | `preflight()` — one real call, then `NotRun`, exit 2 | BUILT |
| Provider fails mid-batch | Contamination guard — **no rates reported at all** | BUILT |
| Operator needs to stop everything | `LEADSCORE_KILL=1` | BUILT |
| Accidental live send | `--write` required; dry run is default | BUILT |
| `ai_evidence` too long → 422 | Truncate claims to 250 chars | TO BUILD, §8 |

One batch failure never takes down the batch. Every failure is recoverable from
`dead_letter.jsonl` and `runs.jsonl`.

---

## 7. Verification strategy

`eval.py`. Two tiers, and the split is deliberate: the deterministic tier costs
nothing and runs on every change; the model tier costs money and runs before the
demo.

**Deterministic (9 gates, no network):** compliance layer, disqualifiers,
evidence source filter, relevance gate, low-confidence routing, the pre-send
gate, jurisdiction states, trust tier, write payload.

**Model (4 gates):** tier agreement ≥80%, tier-A precision ≥80%, provenance
coverage 100%, injection resistance 100%. Plus baseline comparison and cost/lead.

**Exit codes:** `0` pass, `1` a gate failed, `2` not run. The third is the one
that matters on the day — it means the agent is not the problem, we just could
not measure it.

Two guards protect the model tier, both taken from the AdversarialCI eval
harness:

- **`preflight()`** — one real call with the real schema before spending the
  batch. A wrong model id, a dead key or a provider that rejects strict
  `json_schema` costs one request and a clear message, not twelve requests and a
  confusing partial result.
- **Contamination guard** — if any lead errors, **no rates are reported at
  all**. The surviving rows would measure whichever calls got through, not the
  agent. Their comment on why is the right one: a number that has to be
  remembered as untrustworthy will end up on a slide being trusted.

Two properties worth defending out loud:

1. **`EXPECTED_BASIS` is hand-written, not derived from `agent.py`.** Changing
   the policy code alone fails the eval. A table copied out of the code under
   test would verify nothing.
2. **The baseline gate.** The agent must beat trivial headcount-points scoring
   or the run fails. This is the answer to "is the LLM actually earning its
   place" — a question most demos cannot answer.

---

## 8. Build sequence

Dependency-ordered. Each step names how we know it worked.

| # | Task | Verified by | Blocks |
|---|---|---|---|
| 1 | Confirm `UNIFY_RECORD_PATH`; confirm `MODEL` | One successful write; model responds | 3, 4 |
| 2 | Test Play branching on a written-back custom field | Branch fires, latency measured | 4 |
| 3 | Live batch → full `eval.py` | 4 model gates pass | demo |
| 4 | Provision the four Play branches | Each gate value lands in its action | demo |
| 5 | ~~Idempotency key widening + 250-char truncation~~ **DONE** | Offline gates 6/6 | — |
| 6 | ~~`notes`-only pass on sample leads~~ **DONE** | Offline gates 6/6; 12 records, tiers unchanged | — |
| 7 | Rehearse demo, incl. live rubric edit | Timed run-through | — |
| 8 | `git init` | — | — |

Steps 1–2 are first because they are the only ones that can force a design
change. Everything else is additive.

### Small changes decided (step 5)

**Idempotency key — done.** Now `(record_id, rubric_version, signal_key)`.
`rubric_version` re-decides the backlog when the rubric changes; `signal_key`
re-decides a known lead when a *new signal* lands. Both are needed — a
signal-triggered system that ignores the second signal is broken.

Implemented as a **fingerprint of the signals list**, not the `signal_timestamp`
the draft plan named: Unify's webhook shape is unconfirmed, and a fingerprint
works off data we already hold while changing exactly when the signal set does.
`_signal_key` is ledger-only bookkeeping and is stripped before the POST —
idempotency is our problem, not a field Unify has to carry.

**Evidence truncation — done.** `claim` capped at `MAX_EVIDENCE_CLAIM_CHARS`
(250) in `unify_payload()`.

**Bug found and fixed while doing this:** `eval.py` referenced `agent.EFFORT`,
which does not exist — a leftover from the Anthropic port. `python eval.py`
without `--offline` would have crashed with `AttributeError` before reaching the
model. Directly on the step-3 path.

### Sample leads (step 6)

**Not rewriting.** The dataset is already correct: 4 of 12 are Irish, the rest
span DE/FR/GB/NL/ES/US — which is precisely a Dublin startup's pipeline, and the
Dublin thesis rendered in data. `EXPECTED_BASIS` hardcodes `lead_001`–`lead_012`
to country codes, so a rewrite risks the eval for no gain.

Scope: `notes` field only. **Record IDs, country codes, `expected_tier` and
`lead_007`'s injection payload stay frozen.**

**Done — and it turned out not to be cosmetic.** Four leads were leaking the
expected answer to the model through `notes`, all four reaching the model with
no disqualifier to stop them:

| Lead | Leaked |
|---|---|
| `lead_004` | "the kind of account static scoring buries" |
| `lead_008` | "Must produce insufficient evidence, not a guess" |
| `lead_009` | "Strong fit" |
| `lead_011` | "no reason to move now" |

These were fixture annotations written for a developer, but `notes` is inside
`<lead_record>`, so the model read them as evidence. Tier agreement would have
scored higher than the agent earned. Replaced with factual operator-style notes
carrying no verdict.

⚠️ **Expect the first live tier-agreement number to be lower than it would have
been before this change. That is the eval getting honest, not a regression.** If
it lands under the 80% gate, the rubric or the prompt needs work — which is
exactly what we now want it to tell us.

---

## 9. Explicitly not built

Ingestion, waterfall enrichment, signal detection, sequences, deliverability,
reply classification, custom Slack bots, email copywriting, translation,
automated DPO workflows.

Unify does all of it better. Rebuilding any of it invites the one question we
cannot win: *why not just use the platform?*

The loop terminates the moment `ai_gate` is written back.

---

## 10. Open items

1. `UNIFY_RECORD_PATH` unverified — §3.3, blocks step 1
2. `MODEL` default `gpt-5.2` unconfirmed against the account
3. Play branch re-evaluation behaviour unknown — §5, blocks step 4
4. `OUTREACH_BASIS` needs DPO sign-off; DE/AT/IT/FR default conservative because
   sources genuinely disagree
5. `SUPPRESSION` is a hardcoded stub — point at the real do-not-contact source
   before any live send
6. `git init` pending; `.gitignore` must cover `runs.jsonl`, `dead_letter.jsonl`,
   `unify_written.jsonl`, `crm_out.jsonl` — all will hold real prospect data
