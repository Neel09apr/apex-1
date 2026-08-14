# ICP Rubric — v3

**Version:** 3 (bump on every change — it is written to the CRM and keyed for idempotency)
**Owner:** <your name>
**Last reviewed:** <date>

If you cannot write this down, the agent cannot apply it. Edit this file, not the prompt.

---

## What we sell to

B2B SaaS and services companies in EMEA selling to technical or operations buyers.
Deal size €15k–€150k ACV. Sales-assisted, 30–90 day cycles.

---

## Dimensions

The model scores each 0–100. The weighting arithmetic happens in code, not in the model.

### fit — 40%

| Score | Looks like |
|---|---|
| 80–100 | 50–500 employees, B2B software/SaaS/fintech/logistics-tech, EMEA HQ, has a RevOps or Sales Ops function |
| 50–79 | Right size, adjacent industry — or right industry at the edge of our size band (20–50, 500–1500) |
| 20–49 | Wrong size band but plausibly reachable; industry adjacent at best |
| 0–19 | Consumer, non-profit, reselling agency, or under 10 employees |

### timing — 30%
Is there a reason to move *now*? The dimension most scoring systems omit, and the reason reps ignore them.

| Score | Looks like |
|---|---|
| 80–100 | Hiring RevOps/Sales Ops/GTM Engineer; new CRO or VP Sales in the last 90 days; funding round in the last 6 months; announced expansion into a new market |
| 50–79 | Hiring any sales role; leadership change one level down; competitor incident or migration signal |
| 20–49 | General growth noise with no dated event |
| 0–19 | No timing signal at all |

### engagement — 20%
First-party behaviour. Your unfair advantage — do not skip it.

| Score | Looks like |
|---|---|
| 80–100 | Pricing page + docs in one session; demo request; 3+ visits in 14 days |
| 50–79 | Content download, webinar attendance, repeated opens + clicks |
| 20–49 | Single visit or single open |
| 0–19 | Nothing recorded |

### reachability — 10%

| Score | Looks like |
|---|---|
| 80–100 | Named contact, Director+ in Revenue/Sales/Ops, corporate email |
| 50–79 | Named contact, IC or unclear seniority |
| 20–49 | Generic inbox only (info@, hello@) |
| 0–19 | No route in |

---

## Weights

```
score = 0.40*fit + 0.30*timing + 0.20*engagement + 0.10*reachability
```

Lives in `agent.py` (`WEIGHTS`) and is applied **in code**. The model produces the four dimension
scores; the arithmetic is not its job. See Pro-Tip 5 in the guide.

---

## Tiers

| Tier | Score | Action |
|---|---|---|
| A | ≥ 70 | `route_to_ae` — Slack the AE within 5 minutes |
| B | 45–69 | `sequence_x` — nurture sequence, review in 30 days |
| C | < 45 | `nurture` — newsletter only |

**Calibration check:** if more than ~25% of a real batch lands in tier A, the rubric is a rubber
stamp and reps will learn to ignore it inside a week. Tighten the fit and timing bands — not the
threshold.

---

## Hard disqualifiers

Enforced in **code**, never by the model, and checked *before* the model is
called so a disqualified lead costs zero tokens. See `disqualify()` in
`agent.py`; the lists it reads are `COMPETITOR_DOMAINS`, `FREE_MAIL` and
`SUPPRESSION`.

- Domain on the competitor list
- Free-mail personal address as the only contact
- Already a customer (`is_customer: true`)
- Open opportunity already exists (`has_open_opp: true`)
- Email or domain on the suppression / do-not-contact list
- Jurisdiction could not be resolved

A disqualified lead gets `tier: "disqualified"` **without a model call** — cheaper, deterministic,
auditable.

---

## Confidence

The model sets this itself:

- `high` — 3+ enrichment fields present **and** at least one dated timing signal
- `medium` — partial enrichment, no dated signal
- `low` — 2 or fewer usable fields, or the evidence contradicts itself

`low` routes to the human review queue, never to an AE's calendar.

---

## Evidence

Every entry in `evidence[]` carries three parts:

| Part | Rule |
|---|---|
| `claim` | The model's own words. May paraphrase and summarise. |
| `quote` | Copied **character for character** from a field value in the record. |
| `source` | The source string of the field the quote came from. |

`verify_quotes()` in `agent.py` checks every quote against the record before anything is written.
A quote found in no field is discarded **together with its claim**. Matching ignores case,
whitespace and smart typography — the vendor's own noise — but nothing else; a quote assembled
from two separate fields does not match, and is dropped.

The `source` on a surviving entry is resolved from wherever the quote was actually found, not from
the label the model attached. Resolving provenance in code is more trustworthy than believing the
model's pointer, and it costs nothing.

Why a quote and not just a source: a plausible source string is trivial to emit. A character-exact
quote from a field you were never given is not.

**A verbatim quote is not proof of a trustworthy field.** `form`/`unify:form` fields are written by
the prospect — `lead_007`'s injection lives in one — so a genuine quote from them can still be
adversary-planted text. A score of 45+ resting on such evidence is forced to `confidence: "low"` in
code (see `validate()`), routing it to human review regardless of what the model reported.

---

## Change log

| Version | Change | Why |
|---|---|---|
| 4 | Evidence needs a verbatim `quote`, not just a `source` | A source label is trivial to fabricate; a character-exact quote is not |
| 3 | Added `timing` at 30%, dropped `fit` 60% → 40% | Reps ignored the old score; it was pure firmographics |
| 2 | Added reachability | Too many A-tiers with no route in |
| 1 | Initial | — |
