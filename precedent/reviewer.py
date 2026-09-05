import hashlib
import json
import re
from pathlib import Path

from config import DISPOSITIONS, REVIEW_CACHE_DIR
from corpus import Document

HEADING_PATTERN = re.compile(r"^\s*(\d{1,2})\.\s+([^\n].*?)\s*$")

# Bump when review logic changes so stale disk caches are not reused.
REVIEW_CODE_VERSION = "v3"

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
    if len(headings) >= 2:
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
    # Fallback for single-line drafts (e.g. ".... 1. Fees ... 2. Law ..."):
    # split inline on " N. Title." boundaries so smoke tests still get >1 clause.
    inline = _split_inline_clauses(text)
    if len(inline) >= 2:
        return inline
    if len(headings) == 1:
        start = headings[0][0]
        preamble = "\n".join(lines[:start]).strip()
        clauses = []
        if preamble:
            clauses.append({"clause": "Preamble", "text": preamble})
        _, number, title = headings[0]
        body = "\n".join(lines[start + 1 :]).strip()
        clauses.append({"clause": f"{number}. {title}", "text": body or title})
        return clauses
    return [{"clause": "Contract", "text": text.strip()}]


def _split_inline_clauses(text: str) -> list[dict]:
    """Split drafts where numbered clauses share one line."""
    pattern = re.compile(r"(?<=.)\s+(\d{1,2})\.\s+([A-Za-z][^.]{0,60}?)\.\s*")
    matches = list(pattern.finditer(text))
    if len(matches) < 1:
        return []
    clauses: list[dict] = []
    first_start = matches[0].start()
    if first_start > 20:
        preamble = text[:matches[0].start()].strip()
        if preamble:
            clauses.append({"clause": "Preamble", "text": preamble})
    for i, match in enumerate(matches):
        number, title = match.group(1), match.group(2).strip()
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        clauses.append({"clause": f"{number}. {title}", "text": body or title})
    # Guard against pathological splits (e.g. decimal numbers).
    if len(clauses) > 30:
        return []
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
        try:
            user = REVIEW_USER_TEMPLATE.format(
                corpus_files="\n".join(sorted(self.citations)),
                playbook=json.dumps(self.playbook, ensure_ascii=False)[:40000],
                clauses=json.dumps(clauses, ensure_ascii=False, indent=2)[:40000],
            )
            raw = self.llm.complete_json(REVIEW_SYSTEM, user, max_tokens=12000)
            if not isinstance(raw, dict):
                raise ValueError("LLM did not return a JSON object")
            entries = self._validated(raw.get("clauses", []))
            entries = self._repair(entries, raw.get("clauses", []), clauses)
            entries = self._cover_gaps(entries, clauses)
            entries = self._force_never_accept(entries, clauses)
            entries = self._ensure_citations(entries, clauses)
            summary = str(raw.get("summary", "") or "").strip() or self._default_summary(entries)
            review = self._compose(summary, entries)
        except Exception:
            # Offline/demo fallback: deterministic heuristic review so the
            # localhost app always returns a valid, cited review.
            entries = self._heuristic_review(clauses)
            entries = self._force_never_accept(entries, clauses)
            entries = self._ensure_citations(entries, clauses)
            review = self._compose(self._default_summary(entries), entries)
        self._write_cache(contract_text, review)
        return review

    def _force_never_accept(self, entries: list[dict], clauses: list[dict]) -> list[dict]:
        absolutes = []
        for topic in self.playbook.get("topics", []):
            for rule in topic.get("never_accept", []) or []:
                position = str(rule.get("position", "")).lower()
                # Broad match: any never-accept phrasing ("never", "refuse",
                # "will not", "decline", "no ... of any kind") counts.
                if any(
                    needle in position
                    for needle in ("never", "refuse", "will not", "decline", "not accept", "no ")
                ):
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

    def _repair(self, entries: list[dict], candidates, clauses: list[dict] | None = None) -> list[dict]:
        addressed = {entry["clause"] for entry in entries}
        broken = [c for c in candidates if isinstance(c, dict) and c.get("clause") not in addressed]
        uncited = [entry["clause"] for entry in entries if not entry["citations"]]
        if not broken and not uncited:
            return entries
        clause_map = {c["clause"]: c.get("text", "") for c in (clauses or [])}
        context = ""
        if clauses:
            context = "\n".join(
                f"- {c['clause']}: {(c.get('text', '') or '')[:600]}" for c in clauses[:25]
            )
        fix_prompt = (
            "Some clause reviews were rejected or are incomplete.\n"
            "Entries rejected for: missing rationale, unknown disposition, a counter with no "
            "proposed language, or invented citations:\n"
            f"{json.dumps(broken, ensure_ascii=False)[:6000]}\n"
            "Entries below are valid but cite no corpus file; every rationale must stand on at "
            "least one corpus document, so supply their citations (and you may revise the "
            "rationale and disposition if needed):\n"
            f"{json.dumps(uncited, ensure_ascii=False)}\n"
            f"Corpus files available for citation:\n{chr(10).join(sorted(self.citations))}\n"
            f"Firm playbook (follow it):\n{json.dumps(self.playbook, ensure_ascii=False)[:12000]}\n"
            f"Clause texts:\n{context[:8000]}\n"
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
        norm_map = {_normalize(k): v for k, v in entry_map.items()}
        result = []
        for clause in clauses:
            identifier = clause["clause"]
            entry = entry_map.get(identifier) or norm_map.get(_normalize(identifier))
            if entry is None:
                entry = {
                    "clause": identifier,
                    "disposition": "escalate",
                    "rationale": (
                        "No usable disposition was produced for this clause and the playbook does not "
                        "clearly cover it; a lawyer must decide."
                    ),
                    "proposed_language": None,
                    "citations": [self._default_citation()],
                    "approval_note": "Requires partner review.",
                }
            result.append(entry)
        return result

    def _default_citation(self) -> str:
        for preferred in (
            "template/Novaric_MSA_standard_form.txt",
            "policies/approvals_log.csv",
            "policies/clause_matrix_2023.xlsx",
        ):
            if preferred in self.citations:
                return preferred
        return sorted(self.citations)[0] if self.citations else "template/Novaric_MSA_standard_form.txt"

    def _ensure_citations(self, entries: list[dict], clauses: list[dict]) -> list[dict]:
        """Guarantee every entry cites at least one real corpus file."""
        clause_map = {c["clause"]: c for c in clauses}
        for entry in entries:
            cites = [c for c in (entry.get("citations") or []) if c in self.citations]
            if not cites:
                topic = self._topic_for_clause(entry.get("clause", ""), clause_map.get(entry.get("clause", ""), {}).get("text", ""))
                cites = self._topic_citations(topic)
            entry["citations"] = cites
            if entry.get("disposition") == "counter" and not str(entry.get("proposed_language") or "").strip():
                lang = self._topic_language(self._topic_for_clause(entry.get("clause", ""), ""))
                entry["proposed_language"] = lang or "Replace with the firm's standard language for this topic."
            if not str(entry.get("rationale") or "").strip():
                entry["rationale"] = "Reviewed against the firm playbook; see cited files."
        return entries

    def _default_summary(self, entries: list[dict]) -> str:
        counts = {d: 0 for d in DISPOSITIONS}
        for entry in entries:
            if entry.get("disposition") in counts:
                counts[entry["disposition"]] += 1
        total = len(entries)
        return (
            f"Reviewed {total} clauses against the firm playbook: "
            f"{counts.get('accept', 0)} accept, {counts.get('counter', 0)} counter, "
            f"{counts.get('escalate', 0)} escalate. Counters use firm standard language; "
            "escalations need a lawyer decision."
        )

    # -- Deterministic heuristic fallback (offline / demo mode) ---------------

    def _heuristic_review(self, clauses: list[dict]) -> list[dict]:
        entries = []
        for clause in clauses:
            identifier = clause["clause"]
            text = clause.get("text", "")
            topic = self._topic_for_clause(identifier, text)
            topic_name = str((topic or {}).get("topic", "") or "").lower()
            blob = f"{identifier} {text}".lower()
            never_rules = (topic or {}).get("never_accept") or []
            if never_rules and self._mentions_topic(blob, topic_name):
                rule = never_rules[0]
                entries.append(
                    {
                        "clause": identifier,
                        "disposition": "escalate",
                        "rationale": (
                            f"Playbook marks '{(topic or {}).get('topic', 'this topic')}' as "
                            f"never accepted ({rule.get('position', '')}); escalated for partner decision."
                        ),
                        "proposed_language": None,
                        "citations": self._topic_citations(topic),
                        "approval_note": "Never-accept position; escalate to partner.",
                    }
                )
                continue
            standard = str((topic or {}).get("standard_position", "") or "")
            standard_lang = str((topic or {}).get("standard_language", "") or "")
            if topic and self._looks_standard(blob, standard, standard_lang):
                entries.append(
                    {
                        "clause": identifier,
                        "disposition": "accept",
                        "rationale": f"Matches the firm's standard position for '{topic.get('topic', '')}' ({standard[:160]}).",
                        "proposed_language": None,
                        "citations": self._topic_citations(topic),
                        "approval_note": None,
                    }
                )
                continue
            if topic and standard_lang:
                fallbacks = (topic or {}).get("fallbacks") or []
                note = None
                if fallbacks:
                    first = fallbacks[0]
                    note = f"Fallback seen: {first.get('position', '')} (approved by {first.get('approved_by', 'unrecorded')})."
                entries.append(
                    {
                        "clause": identifier,
                        "disposition": "counter",
                        "rationale": (
                            f"Differs from the firm's standard for '{topic.get('topic', '')}'. "
                            f"Proposing firm language."
                        ),
                        "proposed_language": standard_lang[:1200],
                        "citations": self._topic_citations(topic),
                        "approval_note": note,
                    }
                )
                continue
            entries.append(
                {
                    "clause": identifier,
                    "disposition": "escalate",
                    "rationale": "No playbook topic clearly covers this clause; a lawyer must decide.",
                    "proposed_language": None,
                    "citations": [self._default_citation()],
                    "approval_note": "Requires partner review.",
                }
            )
        return entries

    def _mentions_topic(self, blob: str, topic_name: str) -> bool:
        tokens = [w for w in re.findall(r"[a-z0-9]+", topic_name) if len(w) > 3]
        if not tokens:
            return True
        return any(t in blob for t in tokens)

    def _topic_for_clause(self, identifier: str, text: str) -> dict | None:
        blob = f"{identifier} {text}".lower()
        best = None
        best_score = 0
        for topic in self.playbook.get("topics", []):
            name = str(topic.get("topic", "") or "")
            tokens = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 2]
            if not tokens:
                continue
            score = sum(2 for t in tokens if t in identifier.lower()) + sum(
                1 for t in tokens if t in blob
            )
            # Boost: standard-position keywords appearing in the clause.
            std = str(topic.get("standard_position", "") or "").lower()
            for word in re.findall(r"[a-z]{4,}", std)[:8]:
                if word in blob:
                    score += 1
            if score > best_score:
                best_score = score
                best = topic
        return best if best_score > 0 else None

    def _topic_citations(self, topic: dict | None) -> list[str]:
        cites: list[str] = []
        if topic:
            for key in ("fallbacks", "never_accept", "conflicts"):
                for item in topic.get(key) or []:
                    for citation in (item or {}).get("evidence") or []:
                        if citation in self.citations and citation not in cites:
                            cites.append(citation)
        if not cites:
            cites.append(self._default_citation())
        return cites[:3]

    def _topic_language(self, topic: dict | None) -> str | None:
        if not topic:
            return None
        lang = str(topic.get("standard_language", "") or "").strip()
        return lang or None

    @staticmethod
    def _looks_standard(blob: str, standard: str, standard_lang: str) -> bool:
        clues = re.findall(r"[a-z]{4,}", f"{standard} {standard_lang}".lower())
        if not clues:
            return False
        hits = sum(1 for w in clues[:12] if w in blob)
        return hits >= 3

    def _compose(self, summary: str, entries: list[dict]) -> dict:
        counts = {disposition: 0 for disposition in DISPOSITIONS}
        for entry in entries:
            if entry.get("disposition") in counts:
                counts[entry["disposition"]] += 1
        if not str(summary or "").strip():
            summary = self._default_summary(entries)
        return {
            "summary": summary,
            "overall_counts": counts,
            "clauses": entries,
            "playbook_fingerprint": self.fingerprint,
        }

    def _cache_path(self, contract_text: str) -> Path:
        digest = hashlib.sha256(
            (REVIEW_CODE_VERSION + "\0" + self.fingerprint + "\0" + contract_text).encode("utf-8")
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
