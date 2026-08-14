# Decisions — Apex-1 (Track A)

Everything settled so far that is not visible in the code. Read this first,
then `ARCHITECTURE.md` for system design, data contracts and the build sequence.

**Name:** Apex-1 — EMEA Pre-Send Decision Layer. Not "Revenue Governance": a
title with governance in it primes the room for a compliance tool, which is a
cost centre. Governance is the *how*.

## The buyer

**A Dublin startup, 5–40 people, no RevOps hire, no DPO** — the operator running
Apex-1, not the lead in it.

The reframe that makes this early-stage relevant: **an Irish startup is
multi-jurisdiction at ten employees.** A US startup has 330m people domestically
before lawful basis ever crosses its mind. An Irish startup has 5.4m and is
selling into Germany, France and the UK by month six. Thirty markets is not an
enterprise-scale problem in Dublin — it is a seed-stage problem.

The research (guide §2, problem 5) concluded this buyer will not buy an
autonomous agent. They will buy *"a narrow, auditable, human-in-the-loop agent
with a visible off switch."* We built exactly that — and the off switch (dry-run
default, `hold_for_approval`, `LEADSCORE_KILL`) is currently our strongest asset
for this audience and completely absent from the demo. **Show it.**

| Their daily reality | What answers it |
|---|---|
| No DPO, no legal budget, still liable | `OUTREACH_BASIS` — the judgment they cannot afford to hire, versioned, applied per lead |
| Fear of an AI mistake — the #1 barrier, 30% of Irish SMEs | Dry run by default; hold queue; kill switch |
| No SDRs at all — 27% cite skills as the barrier | "One operator" is their staffing reality, not an aspiration |
| Enterprise buyers demand provenance they cannot produce | `ai_evidence` with source and fetch time |

## Stack

| Layer | Tool | Owns |
|---|---|---|
| Reasoning / orchestration | **Codex (OpenAI)** | Fit + timing judgment on unstructured evidence, sourced rationale |
| GTM platform | **UnifyGTM** | Signals, waterfall enrichment, sequences, deliverability, reply routing, Slack |
| Plain code | — | Jurisdiction, lawful basis, disqualifiers, weights, the gate |

The third row is load-bearing: policy and arithmetic never touch the model.

## Track: A, reframed

**Not "lead scoring."** Unify already scores against ICP rules in its Qualify stage — competing with it invites the "why not just Play branching?" objection.

**Call it the pre-send decision layer:** the single go / hold / suppress call plus the audit record, made between Unify's Qualify and Engage. A Play branch can route on a field; it cannot decide what goes in the field or defend it six months later.

**Track B (content engine) rejected** — no call transcripts available, and generating market copy without them produces fluent guessing a marketer spots in thirty seconds. Revisit only if ≥10 real transcripts across ≥2 markets appear.

**Track C (data trust) rejected** — batch CRM audit; would have used none of Unify's real-time surface.

## Architecture

```
Unify Play (Detect) --webhook--> agent.py (Qualify judgment + lawful basis)
                                        |
                                 Unify API write-back
                                 (ai_gate, ai_tier, ai_basis, ai_evidence)
                                        |
                          Play branches on ai_gate -> Personalize -> Engage
```

`ai_gate` ∈ `send | hold_for_approval | suppress | nurture`. This is the product.

## The pitch

**"Unify showed one person can run this loop. We show one person can run it across EMEA."**

Unify's proof point is Perplexity: $1.7M pipeline, 75+ opportunities, 3 months, no BDR, one operator. That proof is single-market. It does not survive 30 buying cultures and contested consent regimes — which is Dublin's default problem, not its edge case.

**Lead with speed, land with defensibility.** Do NOT pitch this as a compliance tool — that is a cost centre and the room's energy dies. Pitch coverage and speed; the compliance layer is *how*, not *what*.

**The opening, for a Dublin room:**

> Irish startups sell abroad from day one — 5 million people at home means Germany by month six. But your outbound stack thinks EMEA is one market, you don't have a DPO, and 30% of Irish SMEs say the thing stopping them using AI is fear of it making a mistake.
>
> So we didn't build an autonomous agent. We built the decision layer that sits in front of one — it decides go, hold or suppress on every lead, per jurisdiction, and shows its work. Unify runs the loop. We make it safe to run in thirty countries with one person and no legal team.

**The close** is the last line `agent.py` prints: `N held for approval, M suppressed — sends a US-configured Play would have made`. Countable from your own logs, and the one number Unify alone does not produce.

**Supporting stats:** MIT — contact within 5 min = 21× more likely to qualify vs 30 min. Unify — "recency is part of the mechanism, not a footnote." Industry — AI outbound took volume 1,150→7,400/rep while replies fell 4.7%→2.9%, and 47% of AI SDR deployments hit a domain-reputation wall inside 90 days.

**Expected objection:** "Isn't this just Play branching?" Concede the routing half openly — volunteering the boundary is worth more than any feature.

## Open items

1. ~~**`UNIFY_RECORD_PATH` in agent.py is unverified.**~~ **RESOLVED 2026-08-14** — confirmed against `docs.unifygtm.com`: base URL, `X-Api-Key` header, and `POST /objects/{object_name}/records` all match. The check also found a real bug (write body needed a `{"data": {...}}` wrapper, now fixed) and a naming risk (the lead identifier is sent as `source_record_id`, not `record_id`, to avoid colliding with `gtm_decision`'s own Unify-assigned record id). Full detail in `ARCHITECTURE.md` §3.3.
2. **`MODEL` defaults to `gpt-5.2`** via `OPENAI_MODEL`. Confirm against the account's model list.
3. **`git init` pending** — deliberately not run. When it happens, `.gitignore` must cover `runs.jsonl`, `dead_letter.jsonl`, `unify_written.jsonl`, `crm_out.jsonl` — they will hold real prospect data after the first live run.
4. **`OUTREACH_BASIS` needs a DPO sign-off.** DE/AT/IT/FR default to `consent_required`; sources genuinely disagree. `eval.py` asserts against a hand-written table, so changing the code alone fails the eval — deliberate.
5. **`SUPPRESSION` is a hardcoded stub.** Point it at the real do-not-contact source before any live send.
6. **Play branch re-evaluation is unknown.** Does a Unify Play re-check branch conditions when a custom field is written back over REST, and how fast? Blocks the four-branch workflow. Test in hour one — finding out at hour 22 kills the demo.

Judging note: the criterion is depth of UnifyGTM and Codex usage. Our
architecture is deliberately minimal on Unify's surface, which is correct
engineering but reads as shallow integration. The fix is not more features — it
is **closing the loop**: `ai_gate` must terminate in four distinct native Unify
actions (`ARCHITECTURE.md` §5), and the Codex story leads with the eval harness,
not the prompt.

## Status

`python eval.py --offline` — 5/5 deterministic gates pass. No live model run has happened yet; no output files exist.
