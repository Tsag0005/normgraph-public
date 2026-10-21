"""Policy-only review contract and workspace preflight integration."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import io
import zipfile

from backend import public_server
from backend.tools import policy_review


FEATURE = """Feature: Privacy notices
@privacy
Scenario: Contact address
  Given a visitor reads the policy
  Then the policy states a privacy contact email
@privacy
Scenario: Delete records
  Given a visitor reads the policy
  Then the policy states that records are deleted on request
"""
DOCUMENT = "# Privacy Policy\nContact us at privacy@example.com.\nWe retain records."


def request(kind="review-preview", **overrides):
    return {"kind": kind, "featureText": FEATURE, "documentText": DOCUMENT,
            "documentName": "Example policy", "reviewModel": "gpt-6.1-sol",
            "reasoningEffort": "medium", **overrides}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.responses = self

    def create(self, **options):
        self.calls.append(options)
        verdict = ({"verdict": "PASS", "yellow_reason": None,
                    "explanation": "The policy gives a privacy contact email.",
                    "policy_evidence": [{"line": 2, "section": "Privacy Policy",
                                         "quote": "privacy@example.com"}]}
                   if len(self.calls) == 1 else
                   {"verdict": "YELLOW", "yellow_reason": "PARTIAL",
                    "explanation": "It discusses retention, but does not promise deletion on request.",
                    "policy_evidence": [{"line": 3, "section": "Privacy Policy",
                                         "quote": "We retain records."}]})
        return SimpleNamespace(status="completed", output_text=json.dumps(verdict),
                               usage=SimpleNamespace(input_tokens=500, output_tokens=100))


class ReviewTests(unittest.TestCase):
    def test_parser_keeps_each_scenario_and_then_step(self):
        cases = policy_review.parse_scenarios(FEATURE)
        self.assertEqual([case["name"] for case in cases], ["Contact address", "Delete records"])
        self.assertTrue(all(case["gherkin"].startswith("@privacy") for case in cases))
        self.assertEqual(cases[1]["assertions"],
                         ["Then the policy states that records are deleted on request"])

    def test_outline_is_rejected_until_examples_are_expanded(self):
        with self.assertRaisesRegex(ValueError, "Expand each example"):
            policy_review.parse_scenarios(
                "Feature: access\nScenario Outline: request\n  Then respond in <days> days\n"
                "Examples:\n  | days |\n  | 30 |")

    def test_high_reasoning_gets_a_larger_billed_output_allowance(self):
        medium = policy_review.preview(request())
        xhigh = policy_review.preview(request(reasoningEffort="xhigh"))
        self.assertGreater(xhigh["estimate"]["outputTokensAllowance"],
                           medium["estimate"]["outputTokensAllowance"])
        self.assertGreater(xhigh["estimate"]["estimatedMaximumUsd"],
                           medium["estimate"]["estimatedMaximumUsd"])

    def test_evidence_must_match_the_cited_line(self):
        value = {"verdict": "PASS", "yellow_reason": None, "explanation": "Contact is listed.",
                 "policy_evidence": [{"line": 3, "section": "Privacy Policy",
                                      "quote": "privacy@example.com"}]}
        self.assertIn("Quote not found at cited line 3",
                      policy_review.validate_verdict(value, DOCUMENT))

    def test_model_results_and_report_are_saved_without_storing_key(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            client = FakeClient()
            result = policy_review.run_review(request("review", maxCostUsd=5),
                                              directory, lambda *_: None, client=client)
            self.assertTrue(result["complete"])
            self.assertEqual([row["verdict"] for row in result["verdicts"]], ["PASS", "YELLOW"])
            self.assertEqual(result["verdicts"][1]["yellowReason"], "PARTIAL")
            self.assertEqual(result["verdicts"][0]["evidenceProblems"], [])
            self.assertEqual(result["actualUsage"]["inputTokens"], 1000)
            self.assertFalse(any("temperature" in call for call in client.calls))
            self.assertTrue(all(call["store"] is False for call in client.calls))
            self.assertIn("Delete records", (directory / "review.md").read_text(encoding="utf-8"))

    def test_spending_cap_stops_before_model_call(self):
        with tempfile.TemporaryDirectory() as folder:
            client = FakeClient()
            result = policy_review.run_review(request("review", maxCostUsd=0.0001),
                                              Path(folder), lambda *_: None, client=client)
            self.assertEqual(client.calls, [])
            self.assertFalse(result["complete"])
            self.assertEqual(result["verdicts"], [])

    def test_resume_reuses_paid_verdicts_and_finishes_with_higher_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prior_id = "20261021-000000-aaaaaaaa"
            first = root / prior_id
            first.mkdir()
            client = FakeClient()
            initial = policy_review.run_review(request("review", maxCostUsd=0.032),
                                               first, lambda *_: None, client=client)
            self.assertFalse(initial["complete"])
            self.assertEqual(len(initial["verdicts"]), 1)
            second = root / "20261021-000001-bbbbbbbb"
            second.mkdir()
            continued = policy_review.run_review(
                request("review", maxCostUsd=5, resumeJobId=prior_id),
                second, lambda *_: None, client=client)
            self.assertTrue(continued["complete"])
            self.assertEqual(len(continued["verdicts"]), 2)
            self.assertEqual(len(client.calls), 2)

    def test_public_worker_previews_without_key_or_corpus(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(public_server, "SCRATCH", Path(folder)), patch.object(public_server, "_python", return_value=sys.executable):
                public_server.STATES.clear()
                workspace = public_server._workspace("e" * 64)
                job = workspace.start("review-preview", request(), {"OPENAI_API_KEY": ""})
                for _ in range(100):
                    if workspace.jobs[job["id"]]["status"] not in public_server.core.ACTIVE:
                        break
                    time.sleep(0.1)
                finished = workspace.jobs[job["id"]]
                self.assertEqual(finished["status"], "succeeded", finished.get("error"))
                self.assertEqual(finished["result"]["estimate"]["scenarios"], 2)
                with zipfile.ZipFile(io.BytesIO(workspace.snapshot())) as archive:
                    saved = json.loads(archive.read(f"jobs/{job['id']}/result.json"))
                    self.assertEqual(saved["estimate"]["scenarios"], 2)
                with self.assertRaisesRegex(ValueError, "OpenAI API key"):
                    workspace.start("review", request("review", preflightId=job["id"], maxCostUsd=5),
                                    {"OPENAI_API_KEY": ""})


if __name__ == "__main__":
    unittest.main()
