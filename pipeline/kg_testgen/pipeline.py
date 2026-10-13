"""End-to-end orchestration: section text in, Gherkin out.

One context mode is built here: `spires`, which walks the SPIRES norm graph. The
comparison arms -- none, full-corpus, the EDC triple graph, the RP1 concept graph and the
format probes -- live in `evaluation/arms/` and reach this function through
`external_context`. That direction is the whole point: evaluation may import the
pipeline, the pipeline must never import evaluation, and
`tests/test_pipeline_isolation.py` fails if it ever does.

`ExternalContext` is therefore the only seam. It carries a block of text, the header to
put above it, whatever belongs in the run record, and optionally the provision id the arm
resolved -- everything the removed inline arms used to set directly.

Failure policy: a context failure is fatal unless --allow-unaugmented is set, and the run
record is written either way so a failed run is still diagnosable.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from . import llm as llm_module
from . import output, prompting
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import corpus_config as _corpus_config  # noqa: E402

from .config import Config, Secrets
from .errors import ContextError, RetrievalError
from .neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)

@dataclass
class ExternalContext:
    """Context built outside the pipeline, by one of the evaluation arms.

    `text=None` means an arm deliberately supplies no context -- the `none` control --
    which is not the same as an empty string and is recorded as unaugmented.
    """
    text: Optional[str]
    header: Optional[str] = None
    record: Optional[dict] = None
    provision_id: Optional[str] = None


@dataclass
class PipelineResult:
    run_dir: str
    feature_path: Optional[str]
    record_path: str
    gherkin: Optional[str]
    augmented: bool
    structure_problems: list


def _generate_and_write(record, section_text, context_block, config, secrets,
                        provision_id=None, block3_header=None):
    """Prompt, generate, and write outputs. Shared by the spires and external paths."""
    if provision_id is None:
        provision_id = config.provision_id or _corpus_config.detect_provision_id(section_text)

    augmented = context_block is not None
    record["augmented"] = augmented
    prompt = prompting.build_prompt(section_text, context_block, provision_id,
                                    context_mode=config.context_mode,
                                    block3_format=config.block3_format,
                                    block3_header=block3_header)
    record["prompt"] = prompt
    # Which instruction text produced this. Runs recorded before the contract could vary
    # have no such field, and are all the one contract frozen in tests/fixtures/.
    record["prompt_contract"] = prompting.CURRENT.fingerprint

    logger.info("Calling %s (temperature %.1f)%s", config.llm_model, config.temperature,
                "" if augmented else " WITHOUT KG context")
    response = llm_module.generate(prompt, config, secrets)
    record["llm"] = response.as_dict()

    gherkin = llm_module.clean_gherkin(response.text)
    problems = llm_module.check_gherkin_structure(gherkin)
    record["gherkin"] = gherkin
    record["gherkin_structure_problems"] = problems
    record["status"] = "ok"
    if problems:
        logger.warning("Generated Gherkin has structural problems: %s", "; ".join(problems))

    run_dir = output.make_run_dir(config.output_dir, provision_id)
    feature_path = output.write_feature(run_dir, gherkin)
    output.write_prompt(run_dir, prompt)
    record_path = output.write_run_record(run_dir, record)
    return PipelineResult(run_dir=run_dir, feature_path=feature_path,
                          record_path=record_path, gherkin=gherkin,
                          augmented=augmented, structure_problems=problems)


def run(section_text: str, config: Config, secrets: Secrets,
        input_path: Optional[str] = None,
        external_context: Optional[ExternalContext] = None) -> PipelineResult:
    """Generate Gherkin for one section.

    Two ways context arrives. Normally the pipeline builds it: `--context-mode spires`
    walks the norm graph, and that is the only mode this function knows how to build.
    Alternatively an evaluation arm builds its own and passes it in, which is how the
    controls and the EDC and RP1 arms still run without the pipeline importing them.
    """
    record = output.new_record(config, input_path, section_text)
    run_dir = None

    try:
        # Supplied context short-circuits everything, including the database: the
        # `none` and `full-corpus` arms never needed a connection and should not open one.
        if external_context is not None:
            if external_context.record:
                record.update(external_context.record)
            return _generate_and_write(record, section_text, external_context.text,
                                       config, secrets,
                                       provision_id=external_context.provision_id,
                                       block3_header=external_context.header)

        if config.context_mode != "spires":
            raise ContextError(
                "context_mode=%r is not built by the pipeline. 'spires' is the only mode "
                "it builds; none, full-corpus, kg and rp1 are comparison arms in "
                "evaluation/arms/ and reach run() through external_context. Use "
                "`python -m evaluation.run_experiment` to run them."
                % config.context_mode)

        if os.environ.get("GRAPH_BACKEND", "local") == "neo4j":
            client_type = Neo4jClient
        else:
            from .local_graph import LocalGraphClient
            client_type = LocalGraphClient
        with client_type(secrets, config) as client:
            record["schema"] = client.schema.as_dict()
            try:
                from . import spires_query
                spires = spires_query.build_context(client, section_text, config)
                record["spires_context"] = spires.as_dict()
                context_block = spires.text
                provision_id = spires.target_provision
            except ContextError as exc:
                if not config.allow_unaugmented:
                    record["error"] = str(exc)
                    record["status"] = "failed"
                    run_dir = output.make_run_dir(config.output_dir, None)
                    output.write_run_record(run_dir, record)
                    raise
                logger.warning("Proceeding without KG augmentation "
                               "(--allow-unaugmented): %s", exc)
                record["error"] = "non-fatal (allow_unaugmented): %s" % exc
                context_block, provision_id = None, None

            return _generate_and_write(record, section_text, context_block, config,
                                       secrets, provision_id=provision_id)

    except ContextError:
        raise
    except Exception as exc:  # noqa: BLE001 - always leave a diagnosable record
        record["error"] = "%s: %s" % (type(exc).__name__, exc)
        record["status"] = "failed"
        if run_dir is None:
            run_dir = output.make_run_dir(config.output_dir, None)
        output.write_run_record(run_dir, record)
        raise
