# n8n workflow demo

Proves the claim from `README.md` §8: any orchestrator can call Apex-1's
decision layer with no custom integration. This is the concrete artifact —
import it into n8n, run it, watch it call `server.py` and get back a real,
correct decision.

## Status: two-thirds built, one honest gap

`n8n-workflow.json` is importable right now and contains three verified,
correct nodes:

1. **Manual trigger** — click to run, no webhook/tunnel needed for a live demo
2. **Sample Lead (lead_006)** — a Code node holding a real disqualified lead
   from `leads.sample.json`, verbatim
3. **Apex-1: Decide** — an HTTP Request node, `POST` to `/decide`

What's **not** in the file: the fourth node, a Switch branching on `ai_gate`
into the four outcomes. n8n's Switch node JSON schema has changed across
versions (`rules.rules[].value2` in older releases, a different
`rules.values[].conditions` shape in newer ones), and this machine's `npx n8n`
install was still resolving its dependency tree — the package bundles the
full LangChain ecosystem plus every integration, so it is a genuinely heavy
first-time install — when this was built. Rather than hand-write a schema I
could not verify against the actual running version, that one node is a
90-second manual step below. Everything else was built and is correct because
it doesn't depend on a version-sensitive schema I couldn't check.

## Complete it — 90 seconds once n8n is open

1. Import `n8n-workflow.json` (Workflows → Import from File)
2. Add a **Switch** node after "Apex-1: Decide", mode **Rules**, four rules,
   each: field `{{ $json.ai_gate }}`, operator **is equal to**, value one of:
   `send`, `hold_for_approval`, `suppress`, `nurture`
3. Add four **No Operation** nodes after the four Switch outputs, one per
   branch, labelled to match `ARCHITECTURE.md` §5 exactly:

   | `ai_gate` | Label the NoOp node |
   |---|---|
   | `send` | Enroll in sequence |
   | `hold_for_approval` | Slack alert (Unify native) |
   | `suppress` | Add to do-not-contact |
   | `nurture` | Add to nurture list |

4. Click **Execute workflow** on the trigger

## Run it right now, no n8n needed

The thing the workflow calls is already proven correct — verified last turn,
independent of n8n entirely:

```bash
python server.py
```

```bash
python -c "
import json, requests
lead = [l for l in json.load(open('leads.sample.json')) if l['record_id']=='lead_006'][0]
r = requests.post('http://127.0.0.1:8000/decide', json=lead)
print(r.status_code); print(json.dumps(r.json(), indent=2))
"
```

(a `curl -d @<(...)` version looks simpler but doesn't reliably work in Git
Bash on Windows — process substitution isn't fully supported there. The
Python version above is what was actually run to verify this.)

Expected: `"ai_gate":"suppress"`, `"ai_tier":"disqualified"`, zero API calls.
Once `OPENAI_API_KEY` is set, swap the Code node's payload for any
non-disqualified sample lead and the same workflow makes a real model call.

## Why a Manual Trigger and not a Webhook

The real architecture is `Unify Play (Detect) → webhook → decision layer`.
For a live pitch, a Manual Trigger removes the one variable a demo shouldn't
depend on — whether a tunnel to Unify's webhook works on conference wifi.
Swapping the trigger node for a Webhook node pointed at a real Unify Play is a
one-node change once that side is wired up; nothing else in the workflow
changes.
