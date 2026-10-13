"""Public workspace isolation, persistence, and local graph integration."""
from __future__ import annotations

import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from backend import public_server


class PublicWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.scratch = Path(self.temporary.name)
        patcher = patch.object(public_server, "SCRATCH", self.scratch)
        patcher.start()
        self.addCleanup(patcher.stop)
        public_server.STATES.clear()

    def test_local_preview_survives_browser_snapshot_transfer(self):
        first = public_server._workspace("a" * 64)
        second = public_server._workspace("b" * 64)
        self.assertNotEqual(first.path, second.path)
        corpus = next(item for item in first.corpora() if item["name"] == "app_schedule1")
        self.assertEqual(corpus["buildStatus"], "Local graph ready")
        supplied = {"OPENAI_API_KEY": "", "NEO4J_URI": "", "NEO4J_USERNAME": "",
                    "NEO4J_PASSWORD": "", "NEO4J_DATABASE": ""}
        job = first.start("preview", {"corpusId": corpus["id"], "provisions": ["6.1"],
                                      "graphBackend": "local"}, supplied)
        for _ in range(100):
            if first.jobs[job["id"]]["status"] not in public_server.core.ACTIVE:
                break
            time.sleep(0.1)
        finished = first.jobs[job["id"]]
        self.assertEqual(finished["status"], "succeeded", finished.get("error"))
        self.assertGreater(finished["result"]["items"][0]["details"]["norms_included"], 0)
        self.assertEqual(second.jobs, {})
        second.restore(first.snapshot())
        self.assertEqual(second.jobs[job["id"]]["status"], "succeeded")
        self.assertEqual(next(item for item in second.corpora() if item["name"] == "app_schedule1")["id"],
                         corpus["id"])

    def test_snapshot_rejects_path_traversal(self):
        workspace = public_server._workspace("c" * 64)
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../escape.txt", "bad")
        with self.assertRaisesRegex(ValueError, "invalid path"):
            workspace.restore(payload.getvalue())
        self.assertFalse((self.scratch / "escape.txt").exists())

    def test_secret_never_written_to_job_request(self):
        workspace = public_server._workspace("d" * 64)
        corpus = next(item for item in workspace.corpora() if item["name"] == "part_iiic")
        key = "sk-test-private-value"
        job = workspace.start("provisions", {"corpusId": corpus["id"], "graphBackend": "local"},
                              {"OPENAI_API_KEY": key})
        for _ in range(100):
            if workspace.jobs[job["id"]]["status"] not in public_server.core.ACTIVE:
                break
            time.sleep(0.1)
        self.assertEqual(workspace.jobs[job["id"]]["status"], "succeeded")
        self.assertNotIn(key, (workspace.jobs_dir / job["id"] / "request.json").read_text())
        self.assertNotIn(key, (workspace.jobs_dir / job["id"] / "job.json").read_text())
        (workspace.jobs_dir / job["id"] / "diagnostic.log").write_text("provider echoed " + key)
        workspace.transient_secrets["diagnostic"] = [key]
        self.assertNotIn(key.encode(), workspace.snapshot())


if __name__ == "__main__":
    unittest.main()
