"""Offline stand-in for the LLM: scores a proposal from keyword evidence.

Used only when RFP_FAKE_LLM=1 (demos, Playwright E2E, CI). It is deliberately simple and
deterministic; it quotes real lines from the document so evidence verification passes.
It is NOT a substitute for the model and is clearly labelled as demo mode in the UI.
"""

from __future__ import annotations

import re
from typing import Any

from core.tools.fake_provider import criterion_ids_from_schema

# (keywords, base score, score per distinct keyword found)
_RULES: dict[str, tuple[list[str], float, float]] = {
    "technical": (
        ["architecture", "microservice", "scalab", "api", "connector", "sandbox", "load test",
         "requests per second", "sap", "oracle", "event-driven", "horizontal", "gateway",
         "postgresql", "contract test", "multi-tenant", "modular", "workflow engine"], 3, 0.4),
    "implementation": (
        ["milestone", "phase", "raci", "risk register", "acceptance", "hypercare", "governance",
         "dry-run", "rehearsal", "user acceptance", "steering", "weekly", "communication plan",
         "go-live", "owner", "mitigation", "change-control"], 3, 0.45),
    "commercial": (
        ["fixed", "itemis", "assumption", "total", "optional", "included", "exclusive of taxes",
         "usd", "contingency", "term", "price"], 2, 0.4),
    "security": (
        ["soc 2", "iso 27001", "aes-256", "tls 1.3", "multi-factor", "audit log", "penetration",
         "gdpr", "customer-managed keys", "encrypt", "immutable", "single sign-on", "role-based",
         "certif"], 1, 0.6),
    "support": (
        ["24x7", "sla", "service credits", "customer success", "reference", "delivered",
         "hypercare", "severity", "account manager", "named", "onboarded", "similar",
         "monthly service", "experience", "track record", "implementations", "contacted"], 1, 0.5),
}
_GENERIC = (sorted({k for kws, _, _ in _RULES.values() for k in kws}), 2, 0.3)
_CRITERION_LINE = re.compile(r"- criterion_id (\d+): (.+?) \(score 0-(\d+)\)")


def _rule_for(name: str) -> tuple[list[str], float, float]:
    lowered = name.lower()
    for key, rule in _RULES.items():
        if key in lowered or (key == "security" and "compliance" in lowered) \
                or (key == "commercial" and "pric" in lowered) \
                or (key == "support" and "experience" in lowered):
            return rule
    return _GENERIC


def _document(user: str) -> str:
    start, end = user.find("<document>"), user.rfind("</document>")
    return user[start + len("<document>") : end] if start != -1 and end != -1 else user


def _evidence(document: str, keyword: str) -> str:
    for line in document.splitlines():
        if keyword in line.lower():
            return line.strip()[:300]
    return ""


_FACT_LINE = re.compile(r"- criterion_id (\d+): (.+?)(?: \(expected unit: (.+?)\))?$", re.MULTILINE)
_TOTAL_ROW = re.compile(r"^total\s*\|\s*([\d,]+(?:\.\d+)?)\s*$", re.IGNORECASE | re.MULTILINE)
_WEEKS = re.compile(r"(\d+)[- ]weeks?\b", re.IGNORECASE)


def _facts(user: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Offline fact extraction for formula criteria: total price rows and week counts."""
    document = _document(user)
    out = []
    for cid, label, unit in _FACT_LINE.findall(user):
        lowered, value, evidence = label.lower(), None, ""
        if any(w in lowered for w in ("price", "cost", "fee")):
            m = _TOTAL_ROW.search(document)
            if m:
                value, evidence = float(m.group(1).replace(",", "")), m.group(0).strip()
        elif any(w in lowered for w in ("week", "time", "duration", "deliver", "schedule")):
            m2 = _WEEKS.search(document)
            if m2:
                value = float(m2.group(1))
                line = next((ln for ln in document.splitlines() if m2.group(0) in ln), "")
                evidence = line.strip()[:300]
        out.append({"criterion_id": int(cid), "found": value is not None, "value": value,
                    "unit": unit, "evidence": evidence})
    return {"facts": out}


def demo_responder(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    if "facts" in schema.get("properties", {}):
        return _facts(user, schema)
    document = _document(user)
    lowered = document.lower()
    wanted = {int(i): (n, int(m)) for i, n, m in _CRITERION_LINE.findall(user)}
    results = []
    for cid in criterion_ids_from_schema(schema):
        name, max_score = wanted.get(cid, (f"Criterion {cid}", 10))
        keywords, base, step = _rule_for(name)
        found = [k for k in keywords if k in lowered]
        score = round((base + step * len(found)) * max_score / 10)
        score = max(0, min(max_score, score))
        evidence = _evidence(document, found[0]) if found else ""
        results.append({
            "criterion_id": cid,
            "evidence": evidence,
            "justification": (f"Found {len(found)} relevant points ({', '.join(found[:3])})."
                              if found else "The document does not address this criterion."),
            "score": score,
            "addressed": bool(found),
            "confidence": "medium" if len(found) >= 3 else "low",
        })
    risks = [] if "risk" in lowered else ["No risk plan is described."]
    if "reference" not in lowered:
        risks.append("No references are provided.")
    return {"criteria": results, "risks": risks,
            "overall_summary": "Offline demo evaluation based on keyword evidence."}
