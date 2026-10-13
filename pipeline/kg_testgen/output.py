"""Outputs: the .feature file and the companion JSON run record.

The run record is what makes manual evaluation possible, so it is written even when a
later stage fails -- a run that died at the LLM call still tells you what was retrieved.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

FEATURE_FILENAME = "generated.feature"
RUN_RECORD_FILENAME = "run_record.json"
PROMPT_FILENAME = "prompt.txt"


def make_run_dir(base: Optional[str], provision_id: Optional[str]) -> str:
    if base:
        os.makedirs(base, exist_ok=True)
        return base
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = (provision_id or "section").replace(".", "-")
    path = os.path.join("runs", "%s_%s" % (stamp, slug))
    os.makedirs(path, exist_ok=True)
    return path


def write_feature(run_dir: str, gherkin: str) -> str:
    path = os.path.join(run_dir, FEATURE_FILENAME)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(gherkin.rstrip() + "\n")
    logger.info("Wrote %s", path)
    return path


def write_prompt(run_dir: str, prompt: str) -> str:
    path = os.path.join(run_dir, PROMPT_FILENAME)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(prompt)
    return path


def write_run_record(run_dir: str, record: Dict[str, Any]) -> str:
    path = os.path.join(run_dir, RUN_RECORD_FILENAME)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(record, fh, indent=2, ensure_ascii=False)
    logger.info("Wrote %s", path)
    return path


def new_record(config, input_path: Optional[str], section_text: str) -> Dict[str, Any]:
    return {
        "timestamp": _dt.datetime.now().astimezone().isoformat(),
        "status": "incomplete",
        "input_path": input_path,
        "input_section_text": section_text,
        "config": config.as_dict(),
        "schema": None,
        "seeding": None,
        "retrieval": None,
        "ranking": None,
        "serialisation": None,
        "prompt": None,
        "llm": None,
        "gherkin": None,
        "gherkin_structure_problems": None,
        "augmented": None,
        "error": None,
    }
