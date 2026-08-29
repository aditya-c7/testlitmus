import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"c:/Users/shash/OneDrive/Desktop/test/precedent")

import config
import corpus
import playbook as playbook_module
from reviewer import Reviewer

documents = corpus.load_corpus(config.CORPUS_DIR)
playbook, fingerprint = playbook_module.load_or_build(documents, None)

from llm import LLMClient
base_url, api_key = config.credentials()
client = LLMClient(base_url, api_key)
print(f"model: {client.model}", flush=True)


class _LLM:
    def complete_json(self, system, user, max_tokens=8000):
        return client.complete_json(system, user, max_tokens=max_tokens)


reviewer = Reviewer(_LLM(), documents, playbook, fingerprint)
for name in ["Windrow_MSA_draft.txt", "Marchetti_MSA_draft.txt"]:
    contract = (config.ROOT / "inbound" / name).read_text(encoding="utf-8")
    started = time.time()
    review = reviewer.review(contract)
    print(f"=== {name}: {time.time() - started:.0f}s", flush=True)
    empty = [entry["clause"] for entry in review["clauses"] if not entry["citations"]]
    bad = [entry["citations"] for entry in review["clauses"] for c in entry["citations"] if c not in {d.citation for d in documents}]
    dispositions_ok = all(entry["disposition"] in ("accept", "counter", "escalate") for entry in review["clauses"])
    counters_ok = all(entry.get("proposed_language") for entry in review["clauses"] if entry["disposition"] == "counter")
    print(f"  clauses={len(review['clauses'])} dispositions_ok={dispositions_ok} counters_have_language={counters_ok}", flush=True)
    print(f"  empty_citations={empty} invalid_citations={bad}", flush=True)
    for entry in review["clauses"]:
        lang = " +language" if entry.get("proposed_language") else ""
        print(f"  {entry['clause']}: {entry['disposition']}{lang} [{', '.join(entry['citations'])}]", flush=True)
    (config.ROOT / f"_final_{name}.json").write_text(json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8")

second = reviewer.review((config.ROOT / "inbound" / "Windrow_MSA_draft.txt").read_text(encoding="utf-8"))
first = json.loads((config.ROOT / "_final_Windrow_MSA_draft.txt.json").read_text(encoding="utf-8"))
print(f"determinism_identical={second == first}", flush=True)
print("DONE", flush=True)
