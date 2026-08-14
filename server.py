"""HTTP wrapper around agent.py -- makes the decision layer callable from any
orchestrator (n8n, Zapier, Make, a Unify Play's own webhook action) without a
custom integration built per tool. Many GTM teams already run n8n as their
outbound orchestration layer; this is the thing n8n's HTTP Request node calls.

Decides only. Never writes to Unify or anywhere else -- the caller (an n8n
workflow, a Play) owns what happens with ai_gate. Same separation the CLI
already has between a dry run and --write.

No new decision logic lives here. Every route is a thin call into the
functions agent.py already has and eval.py already tests -- resolve_jurisdiction,
disqualify, score_lead, unify_payload. If the decision is ever wrong, the bug
is in agent.py, not here.

    pip install fastapi uvicorn
    export OPENAI_API_KEY=...
    python server.py
    open http://127.0.0.1:8000/docs      # interactive API docs, no extra code

Routes:
    GET  /health
    POST /decide               <lead>                internal lead shape (leads.sample.json)
    POST /decide/unify-webhook <unify webhook body>   raw Unify Play webhook payload

A lead that hits a disqualifier (see agent.disqualify) never touches the
model or needs OPENAI_API_KEY -- so /decide is testable right now, with no
keys, using any disqualified sample lead (e.g. lead_003, lead_006, lead_010).
"""

import os
from typing import Any

from fastapi import FastAPI, HTTPException

import agent

app = FastAPI(
    title="Apex-1",
    description="Pre-send decision layer for signal-based outbound. "
                 "Decides send / hold_for_approval / suppress / nurture. "
                 "Never sends anything itself.",
)

_rubric = None
_client = None


def _load_rubric():
    global _rubric
    if _rubric is None:
        with open("rubric.md", encoding="utf-8") as f:
            _rubric = f.read()
    return _rubric


def _get_client():
    """Constructed on first real need, same discipline as agent.py: a
    disqualified lead never imports or touches openai at all."""
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI()
    return _client


@app.get("/health")
def health():
    return {"status": "ok", "model": agent.MODEL, "rubric_version": agent.RUBRIC_VERSION}


def _decide(lead: dict) -> dict:
    if os.environ.get("LEADSCORE_KILL") == "1":
        raise HTTPException(503, "LEADSCORE_KILL=1 -- refusing to decide.")
    if not lead.get("record_id"):
        raise HTTPException(422, "lead is missing 'record_id'")

    # Cheap, pure, safe to call again inside score_lead: decides whether this
    # request needs the model at all, so a disqualified lead never pays for
    # (or requires a key for) an OpenAI client it was never going to use.
    needs_model = not agent.disqualify(lead)
    client = None
    if needs_model:
        try:
            client = _get_client()
        except Exception as exc:
            raise HTTPException(503, f"model unavailable: {exc}") from exc

    try:
        row = agent.score_lead(client, lead, _load_rubric())
    except Exception as exc:
        raise HTTPException(502, f"{lead['record_id']}: {exc}") from exc

    payload = agent.unify_payload(row)
    # Native list, not the JSON-string encoding unify_payload() uses -- that
    # string-encoding exists only because Unify's custom field is text, not
    # because nesting is undesirable. An HTTP JSON consumer wants the list.
    payload["ai_evidence"] = row["evidence"]
    return payload


@app.post("/decide")
def decide(lead: dict[str, Any]):
    """Body: a lead in the internal shape -- see leads.sample.json for
    examples. Deliberately untyped (not a pydantic model of every field):
    the schema lives in agent.py, and duplicating it here in a second,
    parallel definition is exactly the kind of drift this project has spent
    the whole build avoiding."""
    return _decide(lead)


@app.post("/decide/unify-webhook")
def decide_from_webhook(payload: dict[str, Any]):
    """Body: a raw Unify Play webhook payload. Mapped through the same
    from_unify_webhook() the CLI's --unify-webhook flag uses -- one place
    that knows the Unify shape, same as everywhere else in this project."""
    return _decide(agent.from_unify_webhook(payload))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
