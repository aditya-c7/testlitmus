import hashlib
import json
import re
from pathlib import Path

from config import DISPOSITIONS, REVIEW_CACHE_DIR
from corpus import Document

HEADING_PATTERN = re.compile(r"^\s*(\d{1,2})\.\s+([^\n].*?)\s*$")

REVIEW_SYSTEM = """You are a contracts counsel reviewing a counterparty draft against your firm's negotiation playbook.
For every clause of the draft you must take exactly one disposition:
- "accept": the clause already matches the firm's standard position or an approved fallback whose conditions are satisfied on the face of the draft.
- "counter": the firm has language it can propose instead — quote or closely adapt the firm's own wording from the corpus in "proposed_language".
- "escalate": the clause is a never-accept position, is not covered by the playbook, or needs a human decision because a fallback's conditions cannot be verified from the draft.
Never hedge: pick one disposition per clause and commit to it.
Every "citations" entry must be an exact file name from the corpus file list provided, spelled character for character.
Base every position only on the playbook and corpus — never on outside or market knowledge.
Where the playbook records conflicts, follow the recorded resolution and mention the conflict in the rationale.
Respond with a single JSON object and nothing else."""

REVIEW_USER_TEMPLATE = """Corpus files available for citation (use these exact names):
{corpus_files}

Firm negotiation playbook:
{playbook}

Contract draft, pre-split into clauses:
{clauses}

Review the draft. Return one JSON object of this exact shape:

{{
  "summary": string,
  "clauses": [
    {{
      "clause": string,
      "disposition": "accept" | "counter" | "escalate",
      "rationale": string,
      "proposed_language": string or null,
      "citations": [file names],
      "approval_note": string or null
    }}
  ]
}}

Rules:
- "clause" must identify the clause as numbered and titled in the draft (for example "8. LIMITATION OF LIABILITY").
- Include an entry for every clause given, in order, and do not invent clauses.
- "counter" requires non-empty "proposed_language" drawn from the firm's own corpus wording.
- "approval_note" records who approved any fallback you rely on, when the playbook says so, or what threshold must be confirmed.
- A clause the playbook does not cover is "escalate" with a rationale saying the corpus is silent.
- "summary" is one short paragraph telling the reviewing lawyer where this draft sits against the firm's positions."""


def segment_clauses(text: str) -> list[dict]:
    lines = text.splitlines()
    headings = [
        (index, match.group(1), match.group(2))
        for index, line in enumerate(lines)
        if (match := HEADING_PATTERN.match(line)) and _is_heading(match.group(2))
    ]
    if len(headings) < 2:
        return [{"clause": "Contract", "text": text.strip()}]
    clauses = []
    if headings[0][0] > 0:
        preamble = "\n".join(lines[: headings[0][0]]).strip()
        if preamble:
            clauses.append({"clause": "Preamble", "text": preamble})
    for position, (start, number, title) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        clauses.append({"clause": f"{number}. {title}", "text": body or title})
    return clauses


def _is_heading(title: str) -> bool:
    title = title.strip()
    return 0 < len(title) <= 70 and not title.endswith((".", ";", ",", ":", ")")) and "%" not in title


class Reviewer:
    def __init__(self, llm, documents: list[Document], playbook: dict, fingerprint: str):
        self.llm = llm
        self.citations = {doc.citation for doc in documents}
        self.playbook = playbook
        self.fingerprint = fingerprint

    def review(self, contract_text: str) -> dict:
        cached = self._read_cache(contract_text)
        if cached is not None:
            return cached
        clauses = segment_clauses(contract_text)
        user = REVIEW_USER_TEMPLATE.format(
            corpus_files="\n".join(sorted(self.citations)),
            playbook=json.dumps(self.playbook, ensure_ascii=False),
            clauses=json.dumps(clauses, ensure_ascii=False, indent=2),
        )
        raw = self.llm.complete_json(REVIEW_SYSTEM, user, max_tokens=12000)
        entries = self._validated(raw.get("clauses", []))
        entries = self._repair(entries, raw.get("clauses", []))
        entries = self._cover_gaps(entries, clauses)
        entries = self._force_never_accept(entries, clauses)
        review = self._compose(raw.get("summary", ""), entries)
        self._write_cache(contract_text, review)
        return review

    def _force_never_accept(self, entries: list[dict], clauses: list[dict]) -> list[dict]:
        absolutes = []
        for topic in self.playbook.get("topics", []):
            for rule in topic.get("never_accept", []) or []:
                position = str(rule.get("position", "")).lower()
                if "never" in position and ("any form" in position or "any kind" in position):
                    tokens = [w for w in re.findall(r"[a-z0-9]+", str(topic.get("topic", "")).lower()) if len(w) > 2]
                    if tokens:
                        absolutes.append((tokens, rule, [topic.get("topic", "")]))
        if not absolutes:
            return entries
        clause_map = {clause["clause"]: clause for clause in clauses}
        for entry in entries:
            clause_text = clause_map.get(entry["clause"], {}).get("text", "").lower()
            clause_id = entry["clause"].lower()
            for tokens, rule, topic_names in absolutes:
                matched = any(token in clause_id for token in tokens)
                if not matched:
                    matched = any(token in clause_text for token in tokens)
                if not matched:
                    matched = any(name.lower() in clause_id for name in topic_names)
                if matched and entry["disposition"] != "escalate":
                    entry["disposition"] = "escalate"
                    entry["proposed_language"] = None
                    existing = list(dict.fromkeys(entry.get("citations") or []))
                    for citation in rule.get("evidence", []):
                        if isinstance(citation, str) and citation in self.citations and citation not in existing:
                            existing.append(citation)
                    entry["citations"] = existing
                    entry["approval_note"] = "Never-accept position; escalate to partner before any concession."
                    entry["rationale"] = (
                        f"The playbook marks this topic as never accepted in any form "
                        f"(\"{rule.get('position', '')}\"), so no counter is offered. "
                    ) + (entry.get("rationale") or "")
                if matched:
                    break
        return entries

    def _validated(self, candidates) -> list[dict]:
        valid = []
        for entry in candidates if isinstance(candidates, list) else []:
            if not isinstance(entry, dict):
                continue
            disposition = str(entry.get("disposition", "")).strip().lower()
            if disposition not in DISPOSITIONS or not str(entry.get("rationale", "")).strip():
                continue
            if disposition == "counter" and not str(entry.get("proposed_language") or "").strip():
                continue
            valid.append(
                {
                    "clause": str(entry.get("clause", "")),
                    "disposition": disposition,
                    "rationale": str(entry.get("rationale", "")).strip(),
                    "proposed_language": entry.get("proposed_language"),
                    "citations": [c for c in entry.get("citations", []) if c in self.citations],
                    "approval_note": entry.get("approval_note"),
                }
            )
        return valid

    def _repair(self, entries: list[dict], candidates) -> list[dict]:
        addressed = {entry["clause"] for entry in entries}
        broken = [c for c in candidates if isinstance(c, dict) and c.get("clause") not in addressed]
        uncited = [entry["clause"] for entry in entries if not entry["citations"]]
        if not broken and not uncited:
            return entries
        fix_prompt = (
            "Some clause reviews were rejected or are incomplete.\n"
            "Entries rejected for: missing rationale, unknown disposition, a counter with no "
            "proposed language, or invented citations:\n"
            f"{json.dumps(broken, ensure_ascii=False)}\n"
            "Entries below are valid but cite no corpus file; every rationale must stand on at "
            "least one corpus document, so supply their citations (and you may revise the "
            "rationale and disposition if needed):\n"
            f"{json.dumps(uncited, ensure_ascii=False)}\n"
            f"Corpus files available for citation:\n{chr(10).join(sorted(self.citations))}\n"
            "Return the corrected entries in the same JSON shape; keep the same clauses."
        )
        try:
            fixed = self.llm.complete_json(REVIEW_SYSTEM, fix_prompt, max_tokens=6000)
        except Exception:
            return entries
        fixed_clauses = fixed if isinstance(fixed, list) else fixed.get("clauses", []) if isinstance(fixed, dict) else []
        merged = {entry["clause"]: entry for entry in entries}
        for entry in self._validated(fixed_clauses):
            if entry["clause"] not in merged or not merged[entry["clause"]]["citations"]:
                merged[entry["clause"]] = entry
        return list(merged.values())

    def _cover_gaps(self, entries: list[dict], clauses: list[dict]) -> list[dict]:
        entry_map = {entry["clause"]: entry for entry in entries}
        result = []
        for clause in clauses:
            identifier = clause["clause"]
            entry = entry_map.get(identifier) or entry_map.get(_normalize(identifier))
            if entry is None:
                entry = {
                    "clause": identifier,
                    "disposition": "escalate",
                    "rationale": (
                        "No usable disposition was produced for this clause and the playbook does not "
                        "clearly cover it; a lawyer must decide."
                    ),
                    "proposed_language": None,
                    "citations": [],
                    "approval_note": None,
                }
            result.append(entry)
        return result

    def _compose(self, summary: str, entries: list[dict]) -> dict:
        counts = {disposition: 0 for disposition in DISPOSITIONS}
        for entry in entries:
            counts[entry["disposition"]] += 1
        return {
            "summary": summary,
            "overall_counts": counts,
            "clauses": entries,
            "playbook_fingerprint": self.fingerprint,
        }

    def _cache_path(self, contract_text: str) -> Path:
        digest = hashlib.sha256(
            (self.fingerprint + "\0" + contract_text).encode("utf-8")
        ).hexdigest()
        REVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return REVIEW_CACHE_DIR / f"{digest}.json"

    def _read_cache(self, contract_text: str) -> dict | None:
        path = self._cache_path(contract_text)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_cache(self, contract_text: str, review: dict) -> None:
        try:
            self._cache_path(contract_text).write_text(
                json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass


def _normalize(identifier: str) -> str:
    return re.sub(r"\s+", " ", identifier).strip().lower()
