"""The bundled GDPR graph must work through the public site's free preview."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from backend import public_server


class DemoAssetsTests(unittest.TestCase):
    def test_gdpr_article_13_local_preview_without_api_key(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(public_server, "SCRATCH", Path(folder)), patch.object(
                public_server, "_python", return_value=sys.executable
            ):
                public_server.STATES.clear()
                workspace = public_server._workspace("f" * 64)
                corpus = next(item for item in workspace.corpora()
                              if item.get("name") == "gdpr_article13_demo")
                self.assertEqual(corpus["errors"], [])
                self.assertEqual(corpus["buildStatus"], "Local graph ready")
                check = workspace.start("graph-status", {
                    "corpusId": corpus["id"], "graphBackend": "local",
                }, {"OPENAI_API_KEY": ""})
                for _ in range(100):
                    if workspace.jobs[check["id"]]["status"] not in public_server.core.ACTIVE:
                        break
                    time.sleep(0.1)
                checked = workspace.jobs[check["id"]]
                self.assertEqual(checked["status"], "succeeded", checked.get("error"))
                self.assertGreater(checked["result"]["provisions"], 0)
                job = workspace.start("preview", {
                    "corpusId": corpus["id"], "provisions": ["13(2)"],
                    "graphBackend": "local",
                }, {"OPENAI_API_KEY": ""})
                for _ in range(100):
                    if workspace.jobs[job["id"]]["status"] not in public_server.core.ACTIVE:
                        break
                    time.sleep(0.1)
                finished = workspace.jobs[job["id"]]
                self.assertEqual(finished["status"], "succeeded", finished.get("error"))
                item = finished["result"]["items"][0]
                self.assertEqual(item["provision"], "13(2)")
                self.assertGreater(item["details"]["norms_included"], 0)
                self.assertTrue(item["text"].strip())


if __name__ == "__main__":
    unittest.main()
