import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, r"c:/Users/shash/OneDrive/Desktop/test/precedent")

import config
import corpus
import playbook as playbook_module
import reviewer as reviewer_module
from reviewer import Reviewer

documents = corpus.load_corpus(config.CORPUS_DIR)
playbook, fingerprint = playbook_module.load_or_build(documents, None)
tmp = tempfile.TemporaryDirectory()
reviewer_module.REVIEW_CACHE_DIR = Path(tmp.name)

from llm import LLMClient
base_url, api_key = config.credentials()
client = LLMClient(base_url, api_key)


class _LLM:
    def complete_json(self, system, user, max_tokens=8000):
        return client.complete_json(system, user, max_tokens=max_tokens)


reviewer = Reviewer(_LLM(), documents, playbook, fingerprint)
contract = (config.ROOT / "inbound" / "Marchetti_MSA_draft.txt").read_text(encoding="utf-8")
started = time.time()
review = reviewer.review(contract)
print(f"marchetti final: {time.time() - started:.0f}s", flush=True)
empty = [entry["clause"] for entry in review["clauses"] if not entry["citations"]]
bad = [c for entry in review["clauses"] for c in entry["citations"] if c not in {d.citation for d in documents}]
print(f"empty_citations={empty} invalid_citations={bad}", flush=True)
for entry in review["clauses"]:
    lang = " +language" if entry.get("proposed_language") else ""
    print(f"  {entry['clause']}: {entry['disposition']}{lang} [{', '.join(entry['citations'])}]", flush=True)
print(f"determinism_identical={reviewer.review(contract) == review}", flush=True)
print("DONE", flush=True)