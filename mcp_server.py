"""Apex-1 as an MCP server — the same decision, asked instead of enforced.

    pip install "mcp[cli]" requests
    python mcp_server.py

In n8n Apex-1 is a gate: it sits IN the send path and nothing routes around it.
Here it is an advisor: a human or an agent asks, and nothing forces either to
call it or to obey the answer. That difference is real and worth saying out
loud — enforcement lives where the layer is in the path.

Deliberately no send tool. Apex-1 decides and never sends; that separation is
the product and it holds here exactly as it does in the CLI's dry-run default.

Calls the deployed service rather than importing agent.py, so an MCP client and
an n8n workflow are provably deciding through the same endpoint, with one set of
policy tables and no second copy to drift.
"""

import os

import requests
from mcp.server.mcpserver import MCPServer

APEX_URL = os.environ.get("APEX_URL", "https://apex-1-bi2t.onrender.com")
TIMEOUT = 120  # Render's free plan cold-starts in ~50s.

server = MCPServer(
    name="apex-1",
    instructions="Pre-send decision layer for outbound. Ask it before emailing "
                 "anyone: it answers send / hold_for_approval / suppress / "
                 "nurture per jurisdiction, with the evidence behind the call. "
                 "It never sends anything itself.",
)


@server.tool(
    description="Decide whether a lead may be contacted. Returns the gate "
                "(send / hold_for_approval / suppress / nurture), the fit tier, "
                "the lawful basis for its jurisdiction, and the verified "
                "evidence behind the judgment. Every quote is checked "
                "character-for-character against the lead record before it is "
                "returned; unverifiable quotes are dropped. Decides only — "
                "never sends, enrolls, or writes anywhere."
)
def decide_lead(lead: dict) -> dict:
    """`lead` uses the shape in leads.sample.json: provenance-carrying fields
    as {"value": ..., "source": ...}. The source labels are load-bearing —
    they are what the trust checks read."""
    r = requests.post(f"{APEX_URL}/decide", json=lead, timeout=TIMEOUT)
    if r.status_code != 200:
        return {"error": r.text[:400], "status": r.status_code}
    return r.json()


@server.tool(
    description="Look up the lawful basis for cold B2B outreach in a country, "
                "by ISO code. Instant, no model call, no cost. NOT LEGAL "
                "ADVICE — deliberately conservative, and every row needs a "
                "DPO's sign-off before a real send."
)
def check_jurisdiction(country_code: str) -> dict:
    """`unknown` means the country is not in the table — which is a different
    answer from `blocked`, and routes to a human rather than a permanent no."""
    import agent  # local import: this tool needs no network at all

    code = (country_code or "").strip().upper()
    basis = agent.OUTREACH_BASIS.get(code, "unknown")
    return {
        "country": code,
        "outreach_basis": basis,
        "gate_if_tier_a": agent.gate("A", basis),
        "gate_if_tier_c": agent.gate("C", basis),
        "note": "unknown means we have not decided, not that we decided no. "
                "Both fail closed; only unknown is recoverable."
        if basis == "unknown" else
        "Conservative default. Sources genuinely disagree on B2B legitimate "
        "interest in several EU markets." if basis == "consent_required" else "",
    }


@server.tool(
    description="Health of the decision service: which model and rubric "
                "version are configured, and whether it is answering at all."
)
def apex_status() -> dict:
    r = requests.get(f"{APEX_URL}/health", timeout=TIMEOUT)
    return {"url": APEX_URL, "status_code": r.status_code, **r.json()}


if __name__ == "__main__":
    server.run(transport="stdio")
