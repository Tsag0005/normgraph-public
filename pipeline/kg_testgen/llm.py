"""LLM client. One call per input section -- no agent loop, no iterative retrieval."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from .config import Config, Secrets

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4


class LLMError(RuntimeError):
    """The model could not be reached or returned nothing usable."""


@dataclass
class LLMResponse:
    text: str
    model: str
    attempts: int
    finish_reason: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_source: str = "unavailable"
    """'api' when the provider reported usage, 'unavailable' when it did not.

    Recorded rather than assumed, because a run whose cost was reconstructed from the
    stored text and one whose cost was reported by the provider are not the same
    measurement and a results table should not present them as though they were."""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict:
        return {"model": self.model, "attempts": self.attempts,
                "finish_reason": self.finish_reason,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "usage_source": self.usage_source,
                "text": self.text}


def request_additional_context(*_args, **_kwargs):
    """EXTENSION POINT -- not implemented.

    Multi-turn retrieval in which the model asks for more of the graph and the pipeline
    runs a second query. The POC is deliberately a single call so that each run is
    reproducible for manual evaluation.
    """
    raise NotImplementedError("Iterative/multi-turn retrieval is not implemented.")


def generate(prompt: str, config: Config, secrets: Secrets) -> LLMResponse:
    from openai import OpenAI

    client = OpenAI(api_key=secrets.llm_api_key)
    last_error = None

    # Accumulated across attempts, not taken from the last one. A response that arrives
    # and is then rejected -- an empty completion, below -- was still billed, and a run
    # that reports only the attempt that succeeded understates what it cost.
    billed_prompt = billed_completion = 0
    usage_source = "unavailable"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            completion = client.chat.completions.create(
                model=config.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=config.temperature,
                max_tokens=config.max_output_tokens,
            )
            # Before the empty check, so a rejected response still counts.
            usage = getattr(completion, "usage", None)
            if usage is not None:
                billed_prompt += getattr(usage, "prompt_tokens", 0) or 0
                billed_completion += getattr(usage, "completion_tokens", 0) or 0
                usage_source = "api"

            choice = completion.choices[0]
            text = (choice.message.content or "").strip()
            if not text:
                raise LLMError("model returned an empty response")
            logger.info("LLM call succeeded on attempt %d (finish_reason=%s, %d+%d tokens)",
                        attempt, choice.finish_reason, billed_prompt, billed_completion)
            if choice.finish_reason == "length":
                logger.warning("Response hit the %d-token output cap and may be truncated; "
                               "raise --max-output-tokens", config.max_output_tokens)
            return LLMResponse(text=text, model=config.llm_model, attempts=attempt,
                               finish_reason=choice.finish_reason,
                               prompt_tokens=billed_prompt,
                               completion_tokens=billed_completion,
                               usage_source=usage_source)
        except Exception as exc:  # noqa: BLE001 - retry transient, surface the rest
            last_error = exc
            logger.warning("LLM call failed (attempt %d/%d): %s", attempt, MAX_ATTEMPTS, exc)
            if attempt == MAX_ATTEMPTS:
                break
            time.sleep(2 * attempt)

    raise LLMError("LLM call failed after %d attempts: %s" % (MAX_ATTEMPTS, last_error))


_FENCE_RE = re.compile(r"^\s*```(?:gherkin|cucumber|feature)?\s*\n(.*?)\n\s*```\s*$",
                       re.DOTALL | re.IGNORECASE)


def clean_gherkin(text: str) -> str:
    """Strip a markdown fence if the model added one despite instructions."""
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text.strip()


def check_gherkin_structure(text: str) -> list:
    """Lightweight structural validation.

    Not a full Gherkin parse -- enough to catch the failure modes that would waste a
    manual evaluation pass: no Feature, no Scenario, missing step keywords, untagged
    scenarios.
    """
    problems = []
    lines = [l.strip() for l in text.splitlines()]

    if not any(l.startswith("Feature:") for l in lines):
        problems.append("no 'Feature:' line")

    feature_count = sum(1 for l in lines if l.startswith("Feature:"))
    if feature_count > 1:
        problems.append("%d 'Feature:' lines (expected exactly 1)" % feature_count)

    scenario_idx = [i for i, l in enumerate(lines) if l.startswith(("Scenario:", "Scenario Outline:"))]
    if not scenario_idx:
        problems.append("no 'Scenario:' blocks")

    untagged = 0
    for i in scenario_idx:
        preceding = next((lines[j] for j in range(i - 1, -1, -1)
                          if lines[j] and not lines[j].startswith("#")), "")
        if not preceding.startswith("@"):
            untagged += 1
    if untagged:
        problems.append("%d scenario(s) without a preceding @tag" % untagged)

    for keyword in ("Given", "When", "Then"):
        if not any(l.startswith(keyword) for l in lines):
            problems.append("no '%s' steps" % keyword)

    return problems
