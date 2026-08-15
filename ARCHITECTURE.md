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
   callers                        Apex-1                        outcomes
 ┌──────────────┐        ┌──────────────────────┐        ┌────────────────────┐
 │ Unify Play   │        │ deterministic layer  │        │ send  → sequence   │
 │  (webhook)   │        │  jurisdiction        │        │ hold  → a human    │
 │              │        │  disqualifiers       │        │ suppr → DNC list   │
 │ n8n / Zapier │───────▶│  ───────────────     │───────▶│ nurt  → nurture    │
 │  (HTTP)      │        │ model layer (Codex)  │ ai_gate│ ?     → fail closed│
 │              │        │  fit + timing        │        └────────────────────┘
 │ Claude/Codex │        │  sourced evidence    │
 │  (MCP)       │        │  ───────────────     │
 └──────────────┘        │ gate + audit record  │
                         └──────────────────────┘
                            decides, never sends
```

**The load-bearing rule:** policy and arithmetic never touch the model. The
model returns four dimension scores and evidence; code computes the weighted
score, the tier, the lawful basis and the gate. A hallucinating model costs a
mis-scored lead. A model deciding lawful basis costs a DPC complaint.

**The second rule, once there is more than one caller:** the decision layer
never sends. Every caller owns what happens with `ai_gate` — the CLI needs
`--write`, `server.py` has no write path at all, and an MCP client only ever
receives an answer. One decision, many consumers, no side effects.

### Layer ownership

| Layer | Owns | Never touches |
|---|---|---|
| Unify | signals, enrichment, sequences, deliverability, reply routing, Slack | the decision |
| Orchestrator (n8n) | routing on `ai_gate`, retries, the approval round-trip | what goes in the field |
| Python | jurisdiction, basis, disqualifiers, weights, tier, **gate** | unstructured judgment |
| Codex | fit/timing judgment, rationale, evidence | score, tier, action, gate |

The orchestrator row is the one that changed. Unify's own Play engine was
assumed to be the only consumer; in practice most GTM teams already run n8n
around Unify, and `server.py` makes the decision callable by any of them. That
turns "integrate with Unify" into "be one node in the workflow they already
have", which is a materially easier thing to sell.

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

### 3.3 Decision → Unify write-back  **BUILT, path confirmed 2026-08-14**

`POST {UNIFY_BASE}{UNIFY_RECORD_PATH}`, header `X-Api-Key`, custom object
`gtm_decision`, body `{"data": {...}}`. Twelve attributes inside `data`:
`source_record_id`, `ai_gate`, `ai_tier`, `ai_score`, `ai_basis`,
`ai_jurisdiction`, `ai_confidence`, `ai_rationale`, `ai_evidence` (JSON
string), `ai_decided_at`, `ai_model_version`, `ai_rubric_version`.

Confirmed against `docs.unifygtm.com/developers/api/data/{overview,records/create}.md`:
base URL, `X-Api-Key` header, and `POST /objects/{object_name}/records` all
match what was guessed. **The `data` wrapper did not** — the docs page's prose
example showed a flat body, but the `CreateRecordRequest` OpenAPI schema
requires the attributes nested under `data`. `write_to_unify()` and the
dry-run preview both wrap correctly now.

**Resolved.** `records/create.md` shows relationship attributes only for
standard objects (person → company); custom-object support is unconfirmed, and
unneeded here — the Play branches on `ai_gate` on this record directly, never
by traversing a link. Sent as a plain text attribute, named `source_record_id`
rather than `record_id` to avoid colliding with `gtm_decision`'s own
Unify-assigned record id.

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

### Confidence is the third axis  **FIXED 2026-08-14**

The table above is the `confidence != "low"` case. Low confidence holds, in
every jurisdiction, whatever the tier:

```
gate(tier, basis, confidence):
    blocked                        -> suppress          # decided no, permanent
    consent_required | unknown     -> hold_for_approval # law, or we cannot place it
    confidence == "low"            -> hold_for_approval # we do not trust our own answer
    tier in (A, B)                 -> send
    otherwise                      -> nurture
```

Order matters: `blocked` is checked first, so low confidence never rescues a
disqualified lead into a queue a human might approve out of.

**This was a real bug, found by running the injection lead end to end.**
`tier_and_action()` had always set `recommended_action = "human_review"` for low
confidence, and the docs claimed that "routes it to human review". It did not.
`recommended_action` is advisory; **the Play branches on `ai_gate`**, and the
gate never read confidence — so the lead sent, and the human review the field
promised never happened. Both trust checks in `validate()` were therefore
decorative. Now the gate reads confidence directly, which is the only place that
makes either check bite.

Two checks produce `confidence == "low"` in code, never from the model's
self-report:

| Check | Catches |
|---|---|
| Prospect-authored evidence at a send-grade score | A verbatim quote that is real but adversary-planted — `lead_007` |
| A send-grade score with **no surviving evidence** | A model that fabricated every quote, had them all dropped, and kept the score they were supposed to justify |

The second was the deeper hole. `verify_quotes()` drops bad evidence but nothing
re-checked the dimension scores it was meant to support, so a model citing
nothing real still reached tier A with an empty `ai_evidence` list. Verification
that the score can ignore is decoration.

---

## 5. Workflow provisioning — terminating the gate

The gate is inert until something acts on it. Two independent paths now do.

| `ai_gate` | Action | Proves |
|---|---|---|
| `send` | Enroll in sequence | The loop closes |
| `hold_for_approval` | Slack approval card; no enrollment | Human-in-the-loop — the answer to fear-of-mistakes |
| `suppress` | Add to do-not-contact list | Compliance has teeth, not just a field |
| `nurture` | Add to nurture list; no sequence | Not everything is binary |
| *unrecognised* | Fifth branch, holds | An unknown gate value must not vanish |

### 5.1 Via n8n  **BUILT, executed on n8n 2.22.6**

`n8n-workflow.json`. Switch node `typeVersion` 3.2, rules mode, one string
filter per gate value on `{{ $json.ai_gate }}`, plus `fallbackOutput: extra`.
Verified by import and execute: five leads, five branches, fallback empty.

This path does not depend on Unify's Play engine at all, which is why the
open question below stopped being a blocker.

### 5.2 Via a Unify Play  **STILL TO BUILD**

**Prerequisite, test first:** does a Unify Play re-evaluate branch conditions on
a REST write-back to a custom field, and how quickly? If branching is delayed or
unavailable, the demo falls back to the n8n path above — which is the whole
reason for building two.

---

### 5.3 The approval loop — where `hold_for_approval` terminates

`hold` is the only branch that ends in a person, so it is the only one with a
round-trip. Slack's native `sendAndWait` operation posts a card, pauses the
execution, and resumes on a button click.

```
hold_for_approval ─▶ Slack sendAndWait ─▶ switch on the answer ─┬─ approved  → sequence
                     (pauses execution)   reads data.approved   ├─ rejected  → DNC
                                                                └─ no answer → still held
```

**Three outcomes, not two.** `data.approved` is `true` or `false` on a click and
**absent** when the 24h `limitWaitTime` expires. The timeout is the interesting
one, and it resolves to *still held* — the same `unknown != blocked` distinction
as §4, one layer out. Timing out into `suppress` would let silence permanently
kill a lead; timing out into `send` would make the entire layer decorative.

**The card carries the evidence.** Approving a name with no context is a rubber
stamp, not review, so the message renders score, basis, rationale and every
surviving quote *with its source label* — then asks the question that matches
the hold reason. `consent_required` is a legal question ("do we have a lawful
basis for this contact?"); low confidence is a quality one ("does this evidence
hold up?"). Rendering `lead_007` puts the injection's own words in the rationale
with the quote beneath it stamped `form`: the reviewer is looking at the attack.

**The node ships `disabled`.** An unconfigured Slack node is a workflow-level
issue in n8n — the *whole* workflow refuses to execute, including branches with
no Slack involvement. Disabled, it passes items through and held leads land in
"still held, nothing sent", which is the correct degraded state: no approver,
no send.

Volume ceiling: `sendAndWait` pauses one execution per held lead, so it is one
card per lead. Correct at this buyer's volume (5–15 holds/day); at 200/day it
needs a digest instead.

---

### 5.4 Deployment  **render.yaml WRITTEN, NEVER DEPLOYED**

n8n Cloud cannot reach `127.0.0.1`, and Slack's buttons call n8n back over the
public internet — which a laptop cannot receive without a tunnel. So the target
is n8n Cloud plus `server.py` on a real URL, and no tunnels anywhere.

`render.yaml` is a Blueprint deploy. `uvicorn server:app --host 0.0.0.0 --port
$PORT` is the start command rather than `python server.py`, because the
`__main__` block binds localhost — no code change, and the documented local
workflow is unaffected.

Three env vars carry meaning: `APEX_REPLAY` makes rehearsal mode a dashboard
toggle, `LEADSCORE_KILL` makes the off switch provable in one click, and
`OPENAI_API_KEY` is `sync: false` so it never enters the repo.

---

### 5.5 MCP server  **DESIGNED, NOT BUILT**

The same decision, reachable from Claude and Codex. Codex reads
`~/.codex/config.toml` and supports stdio and streamable-HTTP servers; that
config is shared with the ChatGPT desktop app and the IDE extension.

**In n8n, Apex-1 is a gate. In an MCP client, it is an advisor.** Worth stating
plainly, because the difference is real: in a workflow it sits *in* the send
path and nothing routes around it; in an MCP client a human or an agent *asks*,
and nothing forces either to call it or to obey the answer. Enforcement lives
where Apex-1 is in the path. Claiming otherwise would not survive a question.

Three tools, and deliberately **no send tool** — decides-never-sends has to hold
here exactly as it does in the CLI's dry-run default:

| Tool | Returns |
|---|---|
| `decide_lead` | The gate, tier, basis, evidence — the whole product |
| `check_jurisdiction` | Country in, lawful basis out. Zero tokens |
| `explain_decision` | A past decision from `runs.jsonl` by `record_id` |

The third is the audit thesis made interactive: *"why did we email this person
in March?"* answered in the tool the operator is already in. The first is where
the speed is — a batch of 40 leads triaged with sources in one turn, the agent
doing the fan-out.

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
| Send-grade score with every quote fabricated | No surviving evidence → `confidence` `low` → gate holds | BUILT |
| Unrecognised `ai_gate` reaches the orchestrator | Fifth Switch branch, holds — never silently dropped | BUILT |
| Nobody answers the approval card | 24h timeout resolves to *still held*, never send, never suppress | BUILT |
| High score backed by prospect-authored evidence | `confidence` forced to `low` → **gate holds**, §4 | BUILT |
| Wrong model id / dead key / schema rejected | `preflight()` — one real call, then `NotRun`, exit 2 | BUILT |
| Provider fails mid-batch | Contamination guard — **no rates reported at all** | BUILT |
| Operator needs to stop everything | `LEADSCORE_KILL=1` | BUILT |
| Accidental live send | `--write` required; dry run is default | BUILT |
| `ai_evidence` too long → 422 | Truncate claims to 250 chars | BUILT — `MAX_EVIDENCE_CLAIM_CHARS`, covered by the write-payload gate |

One batch failure never takes down the batch. Every failure is recoverable from
`dead_letter.jsonl` and `runs.jsonl`.

---

## 7. Verification strategy

`eval.py`. Two tiers, and the split is deliberate: the deterministic tier costs
nothing and runs on every change; the model tier costs money and runs before the
demo.

**Deterministic (10 gates, no network):** compliance layer, disqualifiers,
evidence source filter, relevance gate, low-confidence routing, the pre-send
gate, unevidenced score, jurisdiction states, trust tier, write payload.

The tenth gate exists because the ninth was not enough. `check_gate()` was
exhaustive over `(tier, basis)` and passed throughout — while `ai_gate: send`
was reachable from a fully compromised judgment, because confidence was not one
of its axes. An exhaustive table over the wrong axes is not exhaustive. Both new
checks were verified to *fail* against the pre-fix behaviour before being
counted as passing; a check that cannot fail verifies nothing.

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
| 1 | ~~Confirm `UNIFY_RECORD_PATH`~~ **DONE** (§3.3); ~~resolve `source_record_id`~~ **DONE**; confirm `MODEL` | One successful write; model responds | 3, 4 |
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
which does not exist — a leftover from an earlier provider's parameter set.
`python eval.py` without `--offline` would have crashed with `AttributeError`
before reaching the model. Directly on the step-3 path.

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

The loop terminates the moment `ai_gate` is written back — with one deliberate
exception. The approval round-trip (§5.3) is the only thing that reaches past the
gate, and only because `hold_for_approval` is meaningless without somewhere for
the human to answer. Even there the terminal nodes are NoOps: Apex-1 asks the
question and records the answer, and the enrollment itself stays Unify's job.

---

## 10. Open items

1. ~~`UNIFY_RECORD_PATH` unverified~~ **RESOLVED** — §3.3, including the
   `source_record_id` field naming
2. `MODEL` default `gpt-5.2` unconfirmed against the account
3. Play branch re-evaluation behaviour unknown — §5.2. **No longer blocking:**
   the n8n path (§5.1) terminates the gate without Unify's Play engine
4. `OUTREACH_BASIS` needs DPO sign-off; DE/AT/IT/FR default conservative because
   sources genuinely disagree
5. `SUPPRESSION` is a hardcoded stub — point at the real do-not-contact source
   before any live send
6. ~~`git init` pending~~ **RESOLVED** — repo live; `.gitignore` covers
   `runs.jsonl`, `dead_letter.jsonl`, `unify_written.jsonl`, `crm_out.jsonl`,
   `.env`, `*.log`, `.venv/`. `runs.adversarial.jsonl` is synthetic and
   committed on purpose
7. **No live model run has happened.** The 10 deterministic gates pass; the 4
   model gates are unmeasured. Everything verified end to end today ran against
   `runs.adversarial.jsonl`, which measures the structural defence — the part
   that does not depend on the model behaving — and nothing else
8. **The approval loop has never been clicked.** Node parameters were built
   against the installed n8n's own schema and the card was rendered from real
   `/decide` output, but no Slack workspace has been connected
9. **`render.yaml` has never been deployed.** Free plan cold-starts in ~50s —
   warm the URL before demoing
10. MCP server (§5.5) designed, not built
