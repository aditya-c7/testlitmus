"""Local lexical ranking (BM25) over playbook topics and corpus passages.

Stdlib only. Used to (a) send each clause only its relevant playbook topics
instead of the whole playbook, and (b) pull evidence excerpts for citations.
"""
import math
import re

STOPWORDS = frozenset(
    """
    a an the and or of to in on for with by from as at is are was were be been
    being it its this that these those their there here which who whom shall
    will may must should can could would any each such other than then into
    under over between through during per within without within including
    not no nor but if then else when where how what all both few more most
    ou our your his her they them he she we you me my mine your yours our ours
    """.split()
)


def tokenize(text: str) -> list[str]:
    return [
        tok
        for tok in re.findall(r"[a-z0-9]+", (text or "").lower())
        if tok not in STOPWORDS and len(tok) > 1
    ]


class BM25:
    """Minimal BM25 over a fixed list of documents."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = [tokenize(d) for d in docs]
        self.N = max(len(self.docs), 1)
        self.avgdl = sum(len(d) for d in self.docs) / self.N if self.docs else 0.0
        df: dict[str, int] = {}
        for tokens in self.docs:
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1
        self.idf = {
            term: math.log(1 + (self.N - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def scores(self, query: str) -> list[float]:
        qterms = tokenize(query)
        out = []
        for tokens in self.docs:
            if not tokens:
                out.append(0.0)
                continue
            tf: dict[str, int] = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            dl = len(tokens)
            norm = 1 - self.b + self.b * (dl / self.avgdl if self.avgdl else 1.0)
            total = 0.0
            for term in qterms:
                if term not in tf:
                    continue
                idf = self.idf.get(term, 0.0)
                total += idf * (tf[term] * (self.k1 + 1)) / (tf[term] + self.k1 * norm)
            out.append(total)
        return out


def topic_text(topic: dict) -> str:
    parts = [
        str(topic.get("topic", "")),
        str(topic.get("standard_position", "")),
        str(topic.get("standard_language", "")),
        str(topic.get("notes", "")),
    ]
    for key in ("fallbacks", "never_accept"):
        for item in topic.get(key) or []:
            parts.append(str((item or {}).get("position", "")))
            parts.append(str((item or {}).get("conditions", "")))
    return "\n".join(p for p in parts if p)


def rank_topics(playbook: dict, identifier: str, text: str, top_n: int = 3):
    """Return [(topic, score)] best-first, dropping zero scores."""
    topics = [t for t in playbook.get("topics", []) if isinstance(t, dict)]
    if not topics:
        return []
    index = BM25([topic_text(t) for t in topics])
    query = f"{identifier}\n{identifier}\n{text}"
    scored = sorted(
        ((t, s) for t, s in zip(topics, index.scores(query)) if s > 0),
        key=lambda pair: pair[1],
        reverse=True,
    )
    # Identifier-name matches are strong signal; keep them even on ties.
    ident = identifier.lower()
    boosted = []
    for topic, score in scored:
        name = str(topic.get("topic", "")).lower()
        name_tokens = [w for w in re.findall(r"[a-z0-9]+", name) if len(w) > 3]
        if name_tokens and any(tok in ident for tok in name_tokens):
            score += 2.0
        boosted.append((topic, score))
    boosted.sort(key=lambda pair: pair[1], reverse=True)
    return boosted[:top_n]


def split_passages(text: str, target: int = 600) -> list[str]:
    """Split a document into paragraph-ish passages, merging tiny ones."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if not paras:
        chunk = (text or "").strip()
        return [chunk] if chunk else []
    merged: list[str] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + len(para) + 2 <= target * 2 and len(buf) < target:
            buf += "\n" + para
        else:
            if buf:
                merged.append(buf)
            buf = para
    if buf:
        merged.append(buf)
    return merged


def excerpt(passage: str, limit: int = 400) -> str:
    text = " ".join((passage or "").split())
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit // 2 else limit].rstrip() + " [...]"


def rank_evidence(documents, query: str, preferred: list[str] | None = None, top_n: int = 3):
    """Return [{citation, excerpt}] best-first for a query string.

    Passages from `preferred` citations (e.g. a topic's own evidence files)
    are searched first; the whole corpus backs them up.
    """
    preferred = [c for c in (preferred or []) if c]
    by_citation = {d.citation: d for d in documents}
    ordered_cites = preferred + [c for c in sorted(by_citation) if c not in preferred]
    pool: list[tuple[str, str]] = []
    for cite in ordered_cites:
        doc = by_citation.get(cite)
        if doc is None:
            continue
        for passage in split_passages(doc.text):
            pool.append((cite, passage))
    if not pool:
        return []
    index = BM25([p for _, p in pool])
    scores = index.scores(query)
    ranked = sorted(zip(pool, scores), key=lambda ps: ps[1], reverse=True)
    seen: set[str] = set()
    out = []
    for (cite, passage), score in ranked:
        if score <= 0 or cite in seen:
            continue
        seen.add(cite)
        out.append({"citation": cite, "excerpt": excerpt(passage)})
        if len(out) >= top_n:
            break
    return out
