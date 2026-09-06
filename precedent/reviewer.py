import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import match
from config import DISPOSITIONS, REVIEW_CACHE_DIR
from corpus import Document

HEADING_PATTERN = re.compile(r"^\s*(\d{1,2})\.\s+([^\n].*?)\s*$")
ARTICLE_PATTERN = re.compile(
    r"^\s*(article|section)\s+([IVXLC\d]+(?:\.\d+)*)\s*[:.\-]*\s*(.*?)\s*$",
    re.IGNORECASE,
)
EXHIBIT_PATTERN = re.compile(
    r"^\s*(exhibit|schedule|appendix|schedules)\s+([A-Z0-9]+)\s*[:.\-]*\s*(.*?)\s*$",
    re.IGNORECASE,
)

# Bump when review logic changes so stale disk caches are not reused.
REVIEW_CODE_VERSION = "v4"

MAX_WORKERS = 4
TOPICS_PER_CLAUSE = 3

REVIEW_CLAUSE_SYSTEM = """You are a contracts counsel reviewing one clause of a counterparty draft against your firm's negotiation playbook.
Take exactly one disposition:
- "accept": the clause already matches the firm's standard position or an approved fallback whose conditions are satisfied on the face of the draft.
- "counter": the firm has language it can propose instead (quote or closely adapt the firm's own wording from the corpus in "proposed_language").
- "escalate": the clause is a never-accept position, is not covered by the playbook, or needs a human decision because a fallback's conditions cannot be verified from the draft.
Never hedge: commit to one disposition.
Every "citations" entry must be an exact file name from the corpus file list provided, spelled character for character.
Base the position only on the playbook and corpus, never on outside or market knowledge.
"confidence" is your 0-100 certainty in this call; use below 50 when the playbook coverage is thin or the clause is ambiguous.
Write in a plain, human voice: short sentences, no em dashes, no filler. Sound like a busy lawyer's memo, not a generated report.
Respond with a single JSON object and nothing else."""

REVIEW_CLAUSE_USER_TEMPLATE = """Corpus files available for citation (use these exact names):
{corpus_files}

Playbook topics relevant to this clause (these are the only topics you need):
{topics}

Clause to review:
{clause}

Return one JSON object of this exact shape:

{{
  "disposition": "accept" | "counter" | "escalate",
  "rationale": string,
  "proposed_language": string or null,
  "citations": [file names],
  "approval_note": string or null,
  "confidence": 0-100
}}

Rules:
- "counter" requires non-empty "proposed_language" drawn from the firm's own corpus wording.
- "approval_note" records who approved any fallback you rely on, or what threshold must be confirmed.
- A clause the topics do not cover is "escalate" with a rationale saying the corpus is silent."""

REVIEW_REPAIR_TEMPLATE = """Some clause reviews were rejected or are incomplete. Fix each one.

Rejected entries (missing rationale, unknown disposition, a counter with no
proposed language, or invented citations):
{broken}

Clauses they belong to (with the relevant playbook topics):
{context}

Corpus files available for citation:
{corpus_files}

Return a JSON object {{"clauses": [<same shape as before, one per clause above>]}}.
Keep the same clause order. Every entry needs a real citation and, for counters, proposed language."""

REVIEW_SUMMARY_SYSTEM = """You are a contracts counsel writing a one-paragraph handoff note.
Plain human voice, short sentences, no em dashes, no filler. One short paragraph, nothing else."""

REVIEW_SUMMARY_TEMPLATE = """The draft below was reviewed clause by clause against the firm's playbook.
Disposition counts: {counts}.
Per-clause results (clause: disposition - rationale):
{lines}

Write one short paragraph telling the reviewing lawyer where this draft sits
against the firm's positions: what is fine, what needs counters, and what must
be escalated."""


def segment_clauses(text: str) -> list[dict]:
    """Split a draft into clauses using numbered, article/section, or exhibit headings."""
    lines = text.splitlines()
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        label = _match_heading(line)
        if label is not None:
            headings.append((index, label))
    if len(headings) >= 2:
        clauses = []
        if headings[0][0] > 0:
            preamble = "\n".join(lines[: headings[0][0]]).strip()
            if preamble:
                clauses.append({"clause": "Preamble", "text": preamble})
        for position, (start, label) in enumerate(headings):
            end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
            body = "\n".join(lines[start + 1 : end]).strip()
            clauses.append({"clause": label, "text": body or label})
        return clauses
    # Fallback for single-line drafts (e.g. ".... 1. Fees ... 2. Law ..."):
    # split inline on " N. Title." boundaries so smoke tests still get >1 clause.
    inline = _split_inline_clauses(text)
    if len(inline) >= 2:
        return inline
    if len(headings) == 1:
        start, label = headings[0]
        preamble = "\n".join(lines[:start]).strip()
        clauses = []
        if preamble:
            clauses.append({"clause": "Preamble", "text": preamble})
        body = "\n".join(lines[start + 1 :]).strip()
        clauses.append({"clause": label, "text": body or label})
        return clauses
    return [{"clause": "Contract", "text": text.strip()}]


def _match_heading(line: str) -> str | None:
    match = HEADING_PATTERN.match(line)
    if match and _is_heading(match.group(2)):
        return f"{match.group(1)}. {match.group(2).strip()}"
    match = ARTICLE_PATTERN.match(line)
    if match:
        kind, number, title = match.group(1).capitalize(), match.group(2).upper(), match.group(3).strip()
        title = title.rstrip(".:;-").strip()
        if title and _is_heading(title):
            return f"{kind} {number} {title}"
        if not title and number:
            return f"{kind} {number}"
    match = EXHIBIT_PATTERN.match(line)
    if match:
        kind, number, title = match.group(1).capitalize(), match.group(2).upper(), match.group(3).strip()
        title = title.rstrip(".:;-").strip()
        label = f"{kind} {number}"
        if title and _is_heading(title):
            label += f" {title}"
        return label
    return None


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


def _call_llm_json(llm, system: str, user: str, max_tokens: int):
    """Call the LLM for JSON, with or without usage tracking."""
    fn = getattr(llm, "complete_json_with_usage", None)
    if callable(fn):
        return fn(system, user, max_tokens=max_tokens)
    zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return llm.complete_json(system, user, max_tokens=max_tokens), zero


class Reviewer:
    def __init__(self, llm, documents: list[Document], playbook: dict, fingerprint: str,
                 model: str = "unknown", max_workers: int = MAX_WORKERS):
        self.llm = llm
        self.documents = list(documents)
        self.citations = {doc.citation for doc in documents}
        self.playbook = playbook
        self.fingerprint = fingerprint
        self.model = model
        self.max_workers = max(1, max_workers)
        self._usage_lock = threading.Lock()
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    # -- main pipeline ------------------------------------------------------

    def review(self, contract_text: str) -> dict:
        result = None
        for kind, payload in self.review_iter(contract_text):
            if kind == "done":
                result = payload
        assert result is not None
        return result

    def review_iter(self, contract_text: str):
        """Yield ("clause", {"index", "entry", "total"}) live, then ("done", review).

        Lets the UI paint clause cards as they finish instead of waiting for
        the whole review. Fully deterministic given the same LLM outputs.
        """
        cached = self._read_cache(contract_text)
        if cached is not None:
            clauses = cached.get("clauses", [])
            for i, entry in enumerate(clauses):
                yield ("clause", {"index": i, "entry": entry, "total": len(clauses)})
            yield ("done", cached)
            return
        clauses = segment_clauses(contract_text)
        self._reset_usage()
        matched = [match.rank_topics(self.playbook, c["clause"], c.get("text", ""), TOPICS_PER_CLAUSE) for c in clauses]

        entries: list[dict | None] = [None] * len(clauses)
        raws: list = [None] * len(clauses)
        finalized: set[int] = set()

        def emit(i: int) -> None:
            if entries[i] is not None and i not in finalized:
                entries[i] = self._finalize([entries[i]], clauses)[0]
                finalized.add(i)

        if len(clauses) == 1 or self.max_workers == 1:
            order = range(len(clauses))
            for i in order:
                entries[i], raws[i] = self._review_single(clauses[i], [t for t, _ in matched[i]])
                if entries[i] is not None:
                    emit(i)
                    yield ("clause", {"index": i, "entry": entries[i], "total": len(clauses)})
        else:
            from concurrent.futures import as_completed

            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(clauses))) as pool:
                future_to_index = {
                    pool.submit(self._review_single, clauses[i], [t for t, _ in matched[i]]): i
                    for i in range(len(clauses))
                }
                for future in as_completed(future_to_index):
                    i = future_to_index[future]
                    try:
                        entries[i], raws[i] = future.result()
                    except Exception:
                        entries[i], raws[i] = None, None
                    if entries[i] is not None:
                        emit(i)
                        yield ("clause", {"index": i, "entry": entries[i], "total": len(clauses)})

        failed_idx = [i for i, e in enumerate(entries) if e is None]
        if failed_idx:
            repaired = self._repair_batch(
                [(clauses[i], raws[i], [t for t, _ in matched[i]]) for i in failed_idx]
            )
            for i, entry in zip(failed_idx, repaired):
                entries[i] = entry
                emit(i)
                yield ("clause", {"index": i, "entry": entry, "total": len(clauses)})
        # Any clause still without an entry gets the deterministic heuristic.
        for i, clause in enumerate(clauses):
            if entries[i] is None:
                entries[i] = self._heuristic_entry(clause, [t for t, _ in matched[i]])
                emit(i)
                yield ("clause", {"index": i, "entry": entries[i], "total": len(clauses)})

        ordered = self._cover_gaps([e for e in entries if e is not None], clauses)
        # Gap-filled entries are new; finalize just those.
        for entry in ordered:
            try:
                idx = next(i for i, c in enumerate(clauses) if c["clause"] == entry["clause"])
            except StopIteration:
                idx = -1
            if idx not in finalized:
                self._finalize([entry], clauses)
                finalized.add(idx)
        texts = {c["clause"]: (c.get("text", "") or "")[:2000] for c in clauses}
        for entry in ordered:
            entry.setdefault("text", texts.get(entry.get("clause", ""), ""))
        summary = self._summarize(ordered)
        review = self._compose(summary, ordered)
        self._write_cache(contract_text, review)
        yield ("done", review)

    def _finalize(self, entries: list[dict], clauses: list[dict]) -> list[dict]:
        entries = self._force_never_accept(entries, clauses)
        entries = self._ensure_citations(entries, clauses)
        entries = self._attach_evidence(entries, clauses)
        entries = self._attach_risk(entries)
        return entries

    def _review_single(self, clause: dict, topics: list[dict]):
        """One LLM call for one clause. Returns (entry|None, raw|None)."""
        user = REVIEW_CLAUSE_USER_TEMPLATE.format(
            corpus_files="\n".join(sorted(self.citations)),
            topics=json.dumps(topics or {"note": "no playbook topic matches"}, ensure_ascii=False)[:9000],
            clause=json.dumps({"clause": clause["clause"], "text": clause.get("text", "")[:4000]}, ensure_ascii=False),
        )
        try:
            raw, usage = _call_llm_json(self.llm, REVIEW_CLAUSE_SYSTEM, user, max_tokens=2500)
            self._add_usage(usage)
        except Exception:
            return None, None
        if not isinstance(raw, dict):
            return None, raw
        entry = self._validated_one(clause["clause"], raw)
        return (entry, None) if entry else (None, raw)

    # -- guards ---------------------------------------------------------------

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
                    entry["confidence"] = min(int(entry.get("confidence") or 90), 95)
                if matched:
                    break
        return entries

    def _validated_one(self, identifier: str, raw) -> dict | None:
        if not isinstance(raw, dict):
            return None
        disposition = str(raw.get("disposition", "")).strip().lower()
        if disposition not in DISPOSITIONS or not str(raw.get("rationale", "")).strip():
            return None
        if disposition == "counter" and not str(raw.get("proposed_language") or "").strip():
            return None
        return {
            "clause": identifier,
            "disposition": disposition,
            "rationale": str(raw.get("rationale", "")).strip(),
            "proposed_language": raw.get("proposed_language"),
            "citations": [c for c in raw.get("citations", []) if c in self.citations],
            "approval_note": raw.get("approval_note"),
            "confidence": _clamp_confidence(raw.get("confidence")),
        }

    def _validated(self, candidates) -> list[dict]:
        # Kept for backward compatibility with existing tests/callers.
        valid = []
        for entry in candidates if isinstance(candidates, list) else []:
            if not isinstance(entry, dict):
                continue
            one = self._validated_one(str(entry.get("clause", "")), entry)
            if one is not None:
                valid.append(one)
        return valid

    def _repair(self, entries: list[dict], candidates, clauses: list[dict] | None = None) -> list[dict]:
        # Backward-compatible wrapper around the batched repair.
        addressed = {entry["clause"] for entry in entries}
        broken = [c for c in candidates if isinstance(c, dict) and c.get("clause") not in addressed]
        uncited = [entry["clause"] for entry in entries if not entry["citations"]]
        if not broken and not uncited:
            return entries
        clause_map = {c["clause"]: c for c in (clauses or [])}
        jobs = []
        for raw in broken:
            identifier = str(raw.get("clause", ""))
            clause = clause_map.get(identifier, {"clause": identifier, "text": ""})
            topics = [t for t, _ in match.rank_topics(self.playbook, identifier, clause.get("text", ""), TOPICS_PER_CLAUSE)]
            jobs.append((clause, raw, topics))
        for entry in entries:
            if entry["clause"] in uncited:
                clause = clause_map.get(entry["clause"], {"clause": entry["clause"], "text": ""})
                topics = [t for t, _ in match.rank_topics(self.playbook, entry["clause"], clause.get("text", ""), TOPICS_PER_CLAUSE)]
                jobs.append((clause, entry, topics))
        fixed = self._repair_batch(jobs)
        merged = {entry["clause"]: entry for entry in entries}
        for entry in fixed:
            if entry["clause"] not in merged or not merged[entry["clause"]]["citations"]:
                merged[entry["clause"]] = entry
        return list(merged.values())

    def _repair_batch(self, jobs: list[tuple[dict, object, list[dict]]]) -> list[dict]:
        """One LLM call fixing every broken clause. Returns validated entries."""
        if not jobs:
            return []
        items = []
        for clause, raw, topics in jobs:
            items.append({
                "clause": clause["clause"],
                "text": (clause.get("text", "") or "")[:1500],
                "rejected_output": raw,
                "relevant_topics": topics,
            })
        user = REVIEW_REPAIR_TEMPLATE.format(
            broken=json.dumps(items, ensure_ascii=False)[:12000],
            context=json.dumps(items, ensure_ascii=False)[:12000],
            corpus_files="\n".join(sorted(self.citations)),
        )
        try:
            fixed, usage = _call_llm_json(self.llm, REVIEW_CLAUSE_SYSTEM, user, max_tokens=6000)
            self._add_usage(usage)
        except Exception:
            return []
        fixed_clauses = fixed if isinstance(fixed, list) else fixed.get("clauses", []) if isinstance(fixed, dict) else []
        by_clause = {c["clause"]: c for c in (clause for clause, _, _ in jobs)}
        out = []
        for raw in fixed_clauses if isinstance(fixed_clauses, list) else []:
            if not isinstance(raw, dict):
                continue
            identifier = str(raw.get("clause", ""))
            if identifier not in by_clause and _normalize(identifier) not in {_normalize(k) for k in by_clause}:
                continue
            entry = self._validated_one(identifier, raw)
            if entry is not None:
                out.append(entry)
        # Align repaired entries back to the requested clause identifiers.
        wanted = [clause["clause"] for clause, _, _ in jobs]
        aligned = []
        pool = {e["clause"]: e for e in out}
        norm_pool = {_normalize(k): v for k, v in pool.items()}
        for identifier in wanted:
            entry = pool.get(identifier) or norm_pool.get(_normalize(identifier))
            if entry is not None:
                entry["clause"] = identifier
                aligned.append(entry)
        return aligned

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
                    "confidence": 40,
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
                ranked = match.rank_topics(
                    self.playbook, entry.get("clause", ""),
                    clause_map.get(entry.get("clause", ""), {}).get("text", ""),
                    TOPICS_PER_CLAUSE,
                )
                topic = ranked[0][0] if ranked else None
                cites = self._topic_citations(topic)
            entry["citations"] = cites
            if entry.get("disposition") == "counter" and not str(entry.get("proposed_language") or "").strip():
                ranked = match.rank_topics(self.playbook, entry.get("clause", ""), "", 1)
                lang = self._topic_language(ranked[0][0]) if ranked else None
                entry["proposed_language"] = lang or "Replace with the firm's standard language for this topic."
            if not str(entry.get("rationale") or "").strip():
                entry["rationale"] = "Reviewed against the firm playbook; see cited files."
            if entry.get("confidence") is None:
                entry["confidence"] = 60
        return entries

    def _attach_evidence(self, entries: list[dict], clauses: list[dict]) -> list[dict]:
        """Attach {citation, excerpt} evidence quotes for each entry's citations."""
        clause_map = {c["clause"]: c for c in clauses}
        for entry in entries:
            docs = [d for d in self.documents if d.citation in (entry.get("citations") or [])]
            if not docs:
                docs = list(self.documents)
            query = f"{entry.get('clause', '')} {clause_map.get(entry.get('clause', ''), {}).get('text', '')}"[:2000]
            entry["evidence"] = match.rank_evidence(docs, query, top_n=3)
        return entries

    def _attach_risk(self, entries: list[dict]) -> list[dict]:
        for entry in entries:
            entry["risk"] = self._risk_for(entry)
        return entries

    def _risk_for(self, entry: dict) -> str:
        disposition = entry.get("disposition")
        confidence = entry.get("confidence")
        try:
            confidence = int(confidence)
        except (TypeError, ValueError):
            confidence = 70
        if disposition == "escalate":
            level = 2
        elif disposition == "counter":
            level = 1
            topic_names = " ".join(
                str(t.get("topic", "")) for t in self.playbook.get("topics", [])
                if (t.get("never_accept") or [])
            ).lower()
            blob = f"{entry.get('clause', '')} {entry.get('rationale', '')}".lower()
            if any(tok in blob for tok in re.findall(r"[a-z]{4,}", topic_names)):
                level = 2
        else:
            level = 0
        if confidence < 50 and level < 2:
            level += 1
        return ("low", "medium", "high")[level]

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

    def _summarize(self, entries: list[dict]) -> str:
        counts = {d: sum(1 for e in entries if e.get("disposition") == d) for d in DISPOSITIONS}
        lines = "\n".join(
            f"- {e['clause']}: {e['disposition']} (risk {e.get('risk', '?')}) - {(e.get('rationale', '') or '')[:160]}"
            for e in entries[:30]
        )
        user = REVIEW_SUMMARY_TEMPLATE.format(
            counts=", ".join(f"{v} {k}" for k, v in counts.items()), lines=lines
        )
        try:
            text, usage = _call_llm_json(self.llm, REVIEW_SUMMARY_SYSTEM, user, max_tokens=600)
            self._add_usage(usage)
            if isinstance(text, dict):
                text = text.get("summary", "") or ""
            if isinstance(text, str) and text.strip():
                return text.strip()
        except Exception:
            pass
        return self._default_summary(entries)

    # -- Deterministic heuristic fallback (offline / demo mode) ---------------

    def _heuristic_entry(self, clause: dict, topics: list[dict]) -> dict:
        identifier = clause["clause"]
        text = clause.get("text", "")
        topic = topics[0] if topics else None
        topic_name = str((topic or {}).get("topic", "") or "").lower()
        blob = f"{identifier} {text}".lower()
        never_rules = (topic or {}).get("never_accept") or []
        if never_rules and self._mentions_topic(blob, topic_name):
            rule = never_rules[0]
            return {
                "clause": identifier,
                "disposition": "escalate",
                "rationale": (
                    f"Playbook marks '{(topic or {}).get('topic', 'this topic')}' as "
                    f"never accepted ({rule.get('position', '')}); escalated for partner decision."
                ),
                "proposed_language": None,
                "citations": self._topic_citations(topic),
                "approval_note": "Never-accept position; escalate to partner.",
                "confidence": 85,
            }
        standard = str((topic or {}).get("standard_position", "") or "")
        standard_lang = str((topic or {}).get("standard_language", "") or "")
        if topic and self._looks_standard(blob, standard, standard_lang):
            return {
                "clause": identifier,
                "disposition": "accept",
                "rationale": f"Matches the firm's standard position for '{topic.get('topic', '')}' ({standard[:160]}).",
                "proposed_language": None,
                "citations": self._topic_citations(topic),
                "approval_note": None,
                "confidence": 70,
            }
        if topic and standard_lang:
            fallbacks = (topic or {}).get("fallbacks") or []
            note = None
            if fallbacks:
                first = fallbacks[0]
                note = f"Fallback seen: {first.get('position', '')} (approved by {first.get('approved_by', 'unrecorded')})."
            return {
                "clause": identifier,
                "disposition": "counter",
                "rationale": (
                    f"Differs from the firm's standard for '{topic.get('topic', '')}'. "
                    f"Proposing firm language."
                ),
                "proposed_language": standard_lang[:1200],
                "citations": self._topic_citations(topic),
                "approval_note": note,
                "confidence": 60,
            }
        return {
            "clause": identifier,
            "disposition": "escalate",
            "rationale": "No playbook topic clearly covers this clause; a lawyer must decide.",
            "proposed_language": None,
            "citations": [self._default_citation()],
            "approval_note": "Requires partner review.",
            "confidence": 40,
        }

    def _heuristic_review(self, clauses: list[dict]) -> list[dict]:
        # Kept for backward compatibility with existing tests/callers.
        out = []
        for clause in clauses:
            ranked = match.rank_topics(self.playbook, clause["clause"], clause.get("text", ""), TOPICS_PER_CLAUSE)
            out.append(self._heuristic_entry(clause, [t for t, _ in ranked]))
        return out

    def _mentions_topic(self, blob: str, topic_name: str) -> bool:
        tokens = [w for w in re.findall(r"[a-z0-9]+", topic_name) if len(w) > 3]
        if not tokens:
            return True
        return any(t in blob for t in tokens)

    def _topic_for_clause(self, identifier: str, text: str) -> dict | None:
        # Kept for backward compatibility; BM25-backed.
        ranked = match.rank_topics(self.playbook, identifier, text, 1)
        return ranked[0][0] if ranked else None

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
        risks = {"low": 0, "medium": 0, "high": 0}
        for entry in entries:
            if entry.get("disposition") in counts:
                counts[entry["disposition"]] += 1
            if entry.get("risk") in risks:
                risks[entry["risk"]] += 1
        if not str(summary or "").strip():
            summary = self._default_summary(entries)
        with self._usage_lock:
            usage = dict(self._usage)
        return {
            "summary": summary,
            "overall_counts": counts,
            "risk_counts": risks,
            "clauses": entries,
            "playbook_fingerprint": self.fingerprint,
            "model": self.model,
            "usage": usage,
        }

    # -- usage + cache ----------------------------------------------------------

    def _reset_usage(self) -> None:
        with self._usage_lock:
            self._usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    def _add_usage(self, usage) -> None:
        if not isinstance(usage, dict):
            return
        with self._usage_lock:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                try:
                    self._usage[key] += int(usage.get(key) or 0)
                except (TypeError, ValueError):
                    pass
            self._usage["calls"] += 1

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


def _clamp_confidence(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 60
    return max(0, min(100, number))


def _normalize(identifier: str) -> str:
    return re.sub(r"\s+", " ", identifier).strip().lower()
