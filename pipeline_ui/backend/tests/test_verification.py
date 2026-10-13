"""Verification regressions; every model call is mocked."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml
from backend.tests.test_server import LocalFiles
import verification


class VerificationTests(LocalFiles):
    def setUp(self):
        super().setUp()
        self.corpus = SimpleNamespace(SPIRES_OUTPUT_DIR=str(self.root),
                                     provision_filename=lambda pid, suffix: pid + suffix)

    def artifact(self, pid, norms):
        (self.root / f"{pid}.yaml").write_text(yaml.safe_dump({
            "extracted_object": {"norms": norms}, "raw_completion_output": "raw response"}), encoding="utf-8")
        (self.root / f"{pid}.clean.yaml").write_text(yaml.safe_dump({"provision_id": pid, "norms": norms}), encoding="utf-8")

    def run_verification(self, pids, parser):
        return verification.verify_missing(pids, self.corpus, ["python", "verify_exceptions.py"], Mock(), parser)

    def test_heading_only_72_skips_without_a_call_and_next_provision_is_verified(self):
        self.artifact("72", [])
        self.artifact("72(2)", [{"action": "make determination"}])
        def success(*args, **kwargs):
            (self.root / "72(2).verified.yaml").write_text("norms: []", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="completed", stderr="")
        with patch.object(verification.subprocess, "run", side_effect=success) as calls:
            entries = self.run_verification(["72", "72(2)"], Mock(return_value=["sentence"]))
        self.assertEqual([x["status"] for x in entries], ["skipped", "verified"])
        self.assertEqual(calls.call_count, 1)
        self.assertFalse((self.root / "72.verified.yaml").exists())
        self.assertTrue((self.root / "72.clean.yaml").exists())
        self.assertEqual(json.loads((self.root / "verification-report.json").read_text()), entries)

    def test_unpairable_nonempty_norms_preserve_cleaned_output_without_spending(self):
        self.artifact("73", [{"action": "act"}])
        before = (self.root / "73.clean.yaml").read_bytes()
        with patch.object(verification.subprocess, "run") as calls:
            entries = self.run_verification(["73"], Mock(return_value=["one", "two"]))
        calls.assert_not_called()
        self.assertEqual(entries[0]["status"], "skipped")
        self.assertIn("Cannot pair 2", entries[0]["message"])
        self.assertEqual(before, (self.root / "73.clean.yaml").read_bytes())

    def test_real_failure_discards_partial_verification_and_continues(self):
        self.artifact("73", [{"action": "act"}])
        self.artifact("74", [{"action": "act"}])
        def response(command, **kwargs):
            pid = "73" if command[-1].endswith("73.yaml") else "74"
            (self.root / f"{pid}.verified.yaml").write_text("norms: []")
            return SimpleNamespace(returncode=0, stdout="verify failed: rate limit" if pid == "73" else "ok", stderr="")
        with patch.object(verification.subprocess, "run", side_effect=response):
            entries = self.run_verification(["73", "74"], Mock(return_value=["one"]))
        self.assertEqual([x["status"] for x in entries], ["failed", "verified"])
        self.assertFalse((self.root / "73.verified.yaml").exists())
        self.assertTrue((self.root / "73.yaml").exists())
        self.assertTrue((self.root / "74.verified.yaml").exists())
        with patch.object(verification.subprocess, "run") as calls:
            entries = self.run_verification(["74"], Mock())
        calls.assert_not_called()
        self.assertEqual(entries[0]["status"], "reused")

    def test_malformed_raw_is_failed_not_a_heading_skip(self):
        self.artifact("75", [])
        (self.root / "75.yaml").write_text("extracted_object: broken")
        with patch.object(verification.subprocess, "run") as calls:
            entries = self.run_verification(["75"], Mock())
        calls.assert_not_called()
        self.assertEqual(entries[0]["status"], "failed")
