import hashlib
import json
import time

from config import PLAYBOOK_DIR
from corpus import Document

PLAYBOOK_SYSTEM = """You are a senior contracts counsel distilling a law firm's negotiation playbook from its own working files.
Derive every position strictly from the documents provided. Never import outside or market knowledge.
What the firm actually signed in executed agreements, and what its approvers actually approved, is the strongest evidence of what it accepts.
When documents disagree, prefer the most recent evidence and actual deal conduct over older memos and matrices, and record the disagreement in "conflicts".
A fallback is real only if an executed agreement used it or the approvals log shows it was approved; record the approver names exactly as the files give them.
Never-accept positions come from memos, rejected approvals, or a consistent pattern of refusal.
Every "evidence" entry must be the exact file name as given in the FILE headers, spelled character for character.
Respond with a single JSON object and nothing else."""

PLAYBOOK_USER_TEMPLATE = """Below are all working files of one firm's contract practice, one per FILE header.

{documents}

Build the firm's negotiation playbook. For each clause topic that these files speak to (cover every topic the standard form or any draft or executed agreement addresses, including any topic that appears in the files under any name), produce an entry of this exact shape:

{{
  "firm": string,
  "topics": [
    {{
      "topic": string,
      "standard_position": string,
      "standard_language": string,
      "fallbacks": [
        {{"position": string, "conditions": string, "approved_by": string, "evidence": [file names]}}
      ],
      "never_accept": [
        {{"position": string, "evidence": [file names]}}
      ],
      "escalation": {{"who": string, "when": string}},
      "conflicts": [
        {{"issue": string, "resolution": string, "evidence": [file names]}}
      ],
      "notes": string
    }}
  ]
}}

Rules:
- standard_position and standard_language come from the firm's standard form; if the form is silent, say so and use the files that fill the gap.
- fallbacks: include only positions the firm demonstrably conceded in executed deals or approved in the approvals log; carry over any conditions and thresholds (deal size, account type, required sign-off) exactly.
- never_accept: positions the files show the firm refuses in any form.
- escalation: who signs off on deviations for this topic, and when escalation is required.
- conflicts: where files disagree (including stale guidance contradicted by later deals or approvals), describe both sides and how you resolved them.
- Every list in "evidence" must contain only exact file names from the FILE headers, and every claim must be traceable to them.
- If a topic appears in drafts or memos but the standard form is silent, still include it.

Return only the JSON object."""


def corpus_fingerprint(documents: list[Document]) -> str:
    digest = hashlib.sha256()
    for doc in documents:
        digest.update(doc.citation.encode("utf-8"))
        digest.update(hashlib.sha256(doc.text.encode("utf-8")).digest())
    return digest.hexdigest()[:16]


def load_or_build(documents: list[Document], llm) -> tuple[dict, str]:
    fingerprint = corpus_fingerprint(documents)
    existing = _read_existing(fingerprint)
    if existing is not None:
        return existing, fingerprint
    if llm is None:
        stale = _read_stale()
        if stale is not None:
            return stale, stale.get("fingerprint", fingerprint)
        raise RuntimeError(
            "no playbook for current corpus and no LLM available; "
            "run with AI credentials once or keep the committed playbook.json"
        )
    try:
        playbook = build(documents, llm)
    except Exception:
        stale = _read_stale()
        if stale is not None:
            return stale, stale.get("fingerprint", fingerprint)
        raise
    persist(playbook, fingerprint)
    return playbook, fingerprint


def _truncate_for_prompt(documents: list[Document], budget: int = 60000) -> list[Document]:
    """Cap total prompt chars so a large corpus can't blow the context window.

    Priority: template > memos > policies > deals > redlines, then alphabetical.
    """
    def priority(doc: Document) -> tuple[int, str]:
        order = {"template": 0, "memos": 1, "policies": 2, "deals": 3, "redlines": 4}
        return (order.get(doc.category, 9), doc.citation)

    ordered = sorted(documents, key=priority)
    kept: list[Document] = []
    used = 0
    per_file_cap = 8000
    for doc in ordered:
        text = doc.text[:per_file_cap]
        if used + len(text) > budget and kept:
            remaining = budget - used
            if remaining > 1000:
                kept.append(Document(citation=doc.citation, category=doc.category, text=text[:remaining]))
            break
        kept.append(Document(citation=doc.citation, category=doc.category, text=text))
        used += len(text)
    return kept


def build(documents: list[Document], llm) -> dict:
    scoped = _truncate_for_prompt(documents)
    rendered = "\n\n".join(
        f"===== FILE: {doc.citation} ({doc.category}) =====\n{doc.text}" for doc in scoped
    )
    user = PLAYBOOK_USER_TEMPLATE.format(documents=rendered)
    playbook = llm.complete_json(PLAYBOOK_SYSTEM, user, max_tokens=12000)
    if not isinstance(playbook, dict) or not playbook.get("topics"):
        raise RuntimeError("playbook generation did not return usable topics")
    citations = {doc.citation for doc in documents}
    for topic in playbook["topics"]:
        topic["fallbacks"] = [
            {**fallback, "evidence": _keep_known(fallback.get("evidence"), citations)}
            for fallback in topic.get("fallbacks", [])
        ]
        topic["never_accept"] = [
            {**rule, "evidence": _keep_known(rule.get("evidence"), citations)}
            for rule in topic.get("never_accept", [])
        ]
        topic["conflicts"] = [
            {**conflict, "evidence": _keep_known(conflict.get("evidence"), citations)}
            for conflict in topic.get("conflicts", [])
        ]
    return playbook


def persist(playbook: dict, fingerprint: str) -> None:
    PLAYBOOK_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"fingerprint": fingerprint, "derived_at": time.strftime("%Y-%m-%d %H:%M:%S"), **playbook}
    (PLAYBOOK_DIR / "playbook.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (PLAYBOOK_DIR / "PLAYBOOK.md").write_text(render_markdown(payload), encoding="utf-8")


def _read_existing(fingerprint: str) -> dict | None:
    path = PLAYBOOK_DIR / "playbook.json"
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        return stored if stored.get("fingerprint") == fingerprint else None
    except (OSError, ValueError):
        return None


def _read_stale() -> dict | None:
    """Return the committed playbook even if the fingerprint differs.

    Used as a last resort so the service (and localhost demo) can boot
    without credentials or when the LLM call fails.
    """
    path = PLAYBOOK_DIR / "playbook.json"
    if not path.exists():
        return None
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        return stored if isinstance(stored, dict) and stored.get("topics") else None
    except (OSError, ValueError):
        return None


def _keep_known(citations, known: set[str]) -> list[str]:
    if not isinstance(citations, list):
        return []
    return [c for c in citations if isinstance(c, str) and c in known]



def render_markdown(playbook: dict) -> str:
    return "\n".join(_render_markdown_lines(playbook))


def _render_markdown_lines(playbook: dict) -> list[str]:
    lines = [
        f"# Negotiation Playbook — {playbook.get('firm', 'Firm')}",
        "",
        f"Derived from the corpus at `{playbook.get('derived_at', '')}` (fingerprint `{playbook.get('fingerprint', '')}`).",
        "",
    ]
    for topic in playbook.get("topics", []):
        _render_topic(lines, topic)
    return lines


def _render_topic(lines: list[str], topic: dict) -> None:
    wash = _wash_topic(topic)
    lines.append(f"## {wash.get('topic', 'Unnamed topic')}")
    lines.append("")
    lines.append(f"**Standard position:** {wash.get('standard_position', '')}")
    language = wash.get("standard_language", "")
    if language:
        lines.append("")
        lines.append(f"**Standard language:** {language}")
    fallbacks = wash.get("fallbacks") or []
    if fallbacks:
        lines.append("")
        lines.append("**Approved fallbacks:**")
        for fallback in fallbacks:
            approver = fallback.get("approved_by") or "unrecorded"
            conditions = fallback.get("conditions") or "none stated"
            lines.append(
                f"- {fallback.get('position', '')} — conditions: {conditions}; approved by: {approver} "
                f"(evidence: {', '.join(fallback.get('evidence', []))})"
            )
    never = wash.get("never_accept") or []
    if never:
        lines.append("")
        lines.append("**Never accept:**")
        for rule in never:
            lines.append(f"- {rule.get('position', '')} (evidence: {', '.join(rule.get('evidence', []))})")
    escalation = wash.get("escalation") or {}
    if escalation:
        lines.append("")
        lines.append(f"**Escalation:** {escalation.get('who', '')} — {escalation.get('when', '')}")
    conflicts = wash.get("conflicts") or []
    if conflicts:
        lines.append("")
        lines.append("**Conflicts in the files:**")
        for conflict in conflicts:
            lines.append(
                f"- {conflict.get('issue', '')} → {conflict.get('resolution', '')} "
                f"(evidence: {', '.join(conflict.get('evidence', []))})"
            )
    notes = wash.get("notes", "")
    if notes:
        lines.append("")
        lines.append(f"**Notes:** {notes}")
    lines.append("")


def _wash_topic(topic: dict) -> dict:
    cleaned = {}
    for key, value in topic.items():
        if key == "fallbacks":
            cleaned[key] = [
                {"position": f.get("position", ""), "conditions": f.get("conditions", ""),
                 "approved_by": f.get("approved_by", ""), "evidence": f.get("evidence", [])}
                for f in value if isinstance(f, dict)
            ]
        elif key == "never_accept":
            cleaned[key] = [
                {"position": r.get("position", ""), "evidence": r.get("evidence", [])}
                for r in value if isinstance(r, dict)
            ]
        elif key == "conflicts":
            cleaned[key] = [
                {"issue": c.get("issue", ""), "resolution": c.get("resolution", ""),
                 "evidence": c.get("evidence", [])}
                for c in value if isinstance(c, dict)
            ]
        elif key == "escalation":
            cleaned[key] = _if_dict(value)
        else:
            cleaned[key] = value
    return cleaned


def _if_dict(value):
    return value if isinstance(value, dict) else {}

