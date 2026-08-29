import json
import tempfile
import threading
import unittest
from pathlib import Path

import httpx

import playbook as playbook_module
import reviewer as reviewer_module
from config import ROOT
from corpus import load_corpus
from reviewer import Reviewer, segment_clauses
from server import ThreadingHTTPServer, make_handler


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def complete_json(self, system, user, max_tokens=8000):
        self.calls += 1
        return self.responses.pop(0)


class CorpusTests(unittest.TestCase):
    def test_loads_all_document_kinds(self):
        documents = load_corpus(ROOT / "corpus")
        citations = {doc.citation for doc in documents}
        self.assertGreaterEqual(len(documents), 20)
        self.assertIn("template/Novaric_MSA_standard_form.txt", citations)
        self.assertIn("deals/Bluepine_MSA_executed_2022.pdf", citations)
        self.assertIn("policies/clause_matrix_2023.xlsx", citations)
        self.assertIn("redlines/Bluepine_MSA_v4 (counterparty draft).txt", citations)

    def test_pdf_and_xlsx_are_readable_text(self):
        documents = {doc.citation: doc.text for doc in load_corpus(ROOT / "corpus")}
        self.assertIn("Bluepine Logistics (Executed)", documents["deals/Bluepine_MSA_executed_2022.pdf"])
        self.assertIn("Pricing Commitments", documents["policies/clause_matrix_2023.xlsx"])


class SegmentationTests(unittest.TestCase):
    def test_splits_numbered_clauses(self):
        draft = (ROOT / "inbound" / "Marchetti_MSA_draft.txt").read_text(encoding="utf-8")
        clauses = segment_clauses(draft)
        identifiers = [clause["clause"] for clause in clauses]
        self.assertIn("3. FEES AND PAYMENT", identifiers)
        self.assertIn("16. LIQUIDATED DAMAGES", identifiers)
        self.assertIn("17. GENERAL", identifiers)
        for clause in clauses:
            self.assertTrue(clause["text"].strip())

    def test_unstructured_fallback(self):
        clauses = segment_clauses("No headings here at all.")
        self.assertEqual(len(clauses), 1)
        self.assertEqual(clauses[0]["clause"], "Contract")



class ReviewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reviewer_module.REVIEW_CACHE_DIR = Path(self.tmp.name)
        self.documents = load_corpus(ROOT / "corpus")
        self.draft = (ROOT / "inbound" / "Marchetti_MSA_draft.txt").read_text(encoding="utf-8")

    def _reviewer(self, responses):
        return Reviewer(FakeLLM(responses), self.documents, {"topics": []}, "testfp")

    def test_invalid_entries_are_repaired(self):
        first_pass = {
            "summary": "mixed",
            "clauses": [
                {
                    "clause": "3. FEES AND PAYMENT",
                    "disposition": "accept",
                    "rationale": "matches standard net-30 terms",
                    "citations": ["template/Novaric_MSA_standard_form.txt", "made_up_file.txt"],
                    "proposed_language": None,
                    "approval_note": None,
                },
                {
                    "clause": "4. PRICING COMMITMENTS",
                    "disposition": "maybe",
                    "rationale": "unclear",
                    "citations": [],
                    "proposed_language": None,
                    "approval_note": None,
                },
            ],
        }
        repair_pass = {
            "summary": "fixed",
            "clauses": [
                {
                    "clause": "4. PRICING COMMITMENTS",
                    "disposition": "escalate",
                    "rationale": "most-favored-nation pricing is never accepted",
                    "citations": ["memos/memo_2025_Veylan_declined.txt"],
                    "proposed_language": None,
                    "approval_note": "decline engagement (partner)",
                }
            ],
        }
        reviewer = self._reviewer([first_pass, repair_pass])
        review = reviewer.review(self.draft)
        by_clause = {entry["clause"]: entry for entry in review["clauses"]}
        self.assertEqual(by_clause["4. PRICING COMMITMENTS"]["disposition"], "escalate")
        self.assertEqual(
            by_clause["4. PRICING COMMITMENTS"]["citations"], ["memos/memo_2025_Veylan_declined.txt"]
        )
        self.assertEqual(by_clause["3. FEES AND PAYMENT"]["citations"], ["template/Novaric_MSA_standard_form.txt"])

    def test_every_clause_gets_a_disposition(self):
        partial = {
            "summary": "partial",
            "clauses": [
                {
                    "clause": "3. FEES AND PAYMENT",
                    "disposition": "accept",
                    "rationale": "standard",
                    "citations": [],
                    "proposed_language": None,
                    "approval_note": None,
                }
            ],
        }
        review = self._reviewer([partial]).review(self.draft)
        clauses = segment_clauses(self.draft)
        self.assertEqual(len(review["clauses"]), len(clauses))
        self.assertEqual(sum(review["overall_counts"].values()), len(review["clauses"]))
        self.assertTrue(
            all(entry["disposition"] in ("accept", "counter", "escalate") for entry in review["clauses"])
        )

    def test_review_is_cached_by_contract(self):
        payload = {
            "summary": "s",
            "clauses": [
                {
                    "clause": "3. FEES AND PAYMENT",
                    "disposition": "accept",
                    "rationale": "standard",
                    "citations": ["template/Novaric_MSA_standard_form.txt"],
                    "proposed_language": None,
                    "approval_note": None,
                }
            ],
        }
        reviewer = self._reviewer([payload])
        first = reviewer.review(self.draft)
        second = reviewer.review(self.draft)
        self.assertEqual(first, second)
        self.assertEqual(reviewer.llm.calls, 1)


class PlaybookTests(unittest.TestCase):
    def test_build_persists_and_reuses(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        playbook_module.PLAYBOOK_DIR = Path(tmp.name)
        generated = {
            "firm": "Test Firm",
            "topics": [
                {
                    "topic": "Fees and Payment",
                    "standard_position": "net-30",
                    "standard_language": "within thirty (30) days",
                    "fallbacks": [
                        {
                            "position": "net-45",
                            "conditions": "procurement policy requires it",
                            "approved_by": "Partner - M. Lindqvist",
                            "evidence": ["policies/approvals_log.csv", "ghost.txt"],
                        }
                    ],
                    "never_accept": [],
                    "escalation": {"who": "partner", "when": "any deviation"},
                    "conflicts": [],
                    "notes": "",
                }
            ],
        }
        llm = FakeLLM([generated])
        documents = load_corpus(ROOT / "corpus")
        first, _ = playbook_module.load_or_build(documents, llm)
        self.assertEqual(first["topics"][0]["fallbacks"][0]["evidence"], ["policies/approvals_log.csv"])
        markdown = (Path(tmp.name) / "PLAYBOOK.md").read_text(encoding="utf-8")
        self.assertIn("Fees and Payment", markdown)
        self.assertIn("Partner - M. Lindqvist", markdown)
        second, _ = playbook_module.load_or_build(documents, llm)
        self.assertEqual(second["topics"][0]["topic"], "Fees and Payment")
        self.assertEqual(llm.calls, 1)


class FakeService:
    def health(self):
        return {"status": "ready"}

    def handle_review(self, contract_text):
        return {"summary": "ok", "overall_counts": {"accept": 1, "counter": 0, "escalate": 0}, "clauses": []}


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FakeService()))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_health_endpoint(self):
        response = httpx.get(f"http://127.0.0.1:{self.port}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    def test_review_endpoint(self):
        response = httpx.post(
            f"http://127.0.0.1:{self.port}/api/review", json={"contract": "1. Fees. Pay us."}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], "ok")

    def test_review_rejects_bad_input(self):
        self.assertEqual(httpx.post(f"http://127.0.0.1:{self.port}/api/review", json={}).status_code, 400)
        self.assertEqual(
            httpx.post(
                f"http://127.0.0.1:{self.port}/api/review",
                content="not json",
                headers={"Content-Type": "application/json"},
            ).status_code,
            400,
        )

    def test_unknown_paths_404(self):
        self.assertEqual(httpx.get(f"http://127.0.0.1:{self.port}/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()

