"""Exercise the public Docker workbench through its HTTP entry point."""
from __future__ import annotations

from http.cookiejar import CookieJar
from io import BytesIO
import json
import os
import time
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zipfile import ZipFile


BASE = os.environ.get("NORMGRAPH_URL", "http://127.0.0.1:18080").rstrip("/")
client = build_opener(HTTPCookieProcessor(CookieJar()))


def call(path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(BASE + path, data=data,
                      headers={"Content-Type": "application/json"} if data else {})
    return client.open(request, timeout=30)


def result(path: str, body: dict | None = None):
    with call(path, body) as response:
        return json.load(response)


def wait_for_job(job_id: str):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        job = result(f"/api/jobs/{job_id}")
        if job["status"] not in {"queued", "running", "cancelling"}:
            assert job["status"] == "succeeded", job.get("error") or job.get("result")
            return job["result"]
        time.sleep(1)
    raise TimeoutError(f"Job {job_id} did not finish")


result("/api/workspace/open", {"token": "a" * 64})
corpus = next(item for item in result("/api/corpora")
              if item.get("name") == "gdpr_article13_demo")
assert corpus["buildStatus"] == "Local graph ready", corpus
status = result("/api/jobs/graph-status", {
    "corpusId": corpus["id"], "graphBackend": "local",
})
assert wait_for_job(status["id"])["provisions"] > 0
preview = result("/api/jobs/preview", {
    "corpusId": corpus["id"], "graphBackend": "local", "provisions": ["13(2)"],
})
item = wait_for_job(preview["id"])["items"][0]
assert item["details"]["norms_included"] > 0, item
review_preview = result("/api/jobs/review-preview", {
    "featureText": "Feature: Privacy\nScenario: Contact\n  Then the policy lists a contact\n"
                   "Scenario: Erasure\n  Then the policy explains erasure\n",
    "selectedScenarioIndices": [2],
    "documentText": "# Privacy policy\nContact us by email.",
    "documentName": "Smoke policy",
    "reviewModel": "gpt-6.1-sol",
    "reasoningEffort": "medium",
})
review_result = wait_for_job(review_preview["id"])
assert review_result["estimate"]["scenarios"] == 1, review_result
assert review_result["selectedScenarioIndices"] == [2], review_result
with call("/api/workspace/snapshot") as response:
    assert "attachment" in response.headers["Content-Disposition"]
    with ZipFile(BytesIO(response.read())) as archive:
        assert archive.testzip() is None
        assert any(name.endswith("job.json") for name in archive.namelist())
print("Published workbench: GDPR graph, selected review estimate, and ZIP export passed")
