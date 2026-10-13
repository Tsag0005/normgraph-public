"""Configuration for the KG query + test-case generation pipeline.

Every tunable named in the requirements lives here so retrieval variations can be
compared without editing code. Secrets are never stored on the dataclass: they are read
from the environment at point of use (see `secrets_from_env`).
"""

from __future__ import annotations

import argparse
import dataclasses
import os
from dataclasses import dataclass, field
from typing import Optional

# Secrets are read from these environment variables only, never hardcoded.
ENV_NEO4J_URI = "NEO4J_URI"
ENV_NEO4J_USER = "NEO4J_USERNAME"
ENV_NEO4J_PASSWORD = "NEO4J_PASSWORD"
ENV_NEO4J_DATABASE = "NEO4J_DATABASE"
# The vendored EDC code hardcodes OPENAI_KEY; the conventional name is OPENAI_API_KEY.
# Both are accepted, and OPENAI_KEY is populated internally so EDC's client works.
ENV_LLM_KEY_PRIMARY = "OPENAI_API_KEY"
ENV_LLM_KEY_FALLBACK = "OPENAI_KEY"


class ConfigError(RuntimeError):
    """Raised for missing or contradictory configuration. Always fatal, never swallowed."""


@dataclass
class Config:
    # --- Seeding -----------------------------------------------------------------
    seeding_method: str = "llm"
    """'llm' uses the vendored EDC EntityExtractor. 'vector' is an unimplemented stub."""

    fuzzy_threshold: float = 0.85
    """Minimum similarity ratio for the fuzzy matching tier. 0-1."""

    provision_anchor: bool = True
    """Seed on the input's own :Provision node in addition to extracted entities."""

    provision_id: Optional[str] = None
    """Explicit subclause id, e.g. '6.1'. Auto-detected from the input text if omitted."""

    # --- Retrieval ---------------------------------------------------------------
    entity_hop_depth: int = 2
    provision_hop_depth: int = 3
    """Asymmetric by seed type. Provision nodes are near-leaves -- 59 of 77 have degree
    1 -- so they need an extra hop to reach anything, while entity seeds already touch
    28-40% of the graph at depth 2."""

    max_nodes: int = 120
    max_relationships: int = 200
    max_relationships_per_node: int = 25

    node_label: str = "AppNode"
    """Validated against live schema introspection rather than trusted blindly."""

    # --- Ranking -----------------------------------------------------------------
    ranking_key: str = "hop_weight_degree"
    """'hop_weight_degree' (POC) or 'hop_degree'. Embedding and centrality are stubs."""

    # --- Serialisation -----------------------------------------------------------
    serialisation_format: str = "triples"
    """'triples' (default) or 'json' (stubbed)."""

    self_provision_policy: str = "sole"
    """How to drop triples asserted by the input subclause. Applied at serialisation,
    NOT at traversal: the walk must be free to pass through those edges or a provision
    seed is severed at hop 1 and contributes nothing.

    'sole' (default) drops a triple only when the input subclause is its ONLY asserting
    provision, i.e. it genuinely just restates the verbatim text. 'any' drops a triple if
    the input subclause appears anywhere in its provision list -- stricter, but it also
    discards facts asserted by many provisions, which are the strongest cross-section
    context available. 'none' disables the filter."""

    provision_text_scope: str = "cross-referenced"
    """Whether retrieved provisions contribute their statutory text, and which.

    'cross-referenced' (default) includes text only for provisions the target clause
        actually points at (REFERS_TO). Highly selective: APP 6.1 pulls 6.2 and 6.3.
    'retrieved'        includes text for every provision named in a retrieved edge's
        provenance. Measured at ~9,800 tokens for APP 6.1 -- 86% of the whole corpus,
        which collapses this arm into full-corpus and stops it being a comparison.
    'none'             triples only; the behaviour before provision text existed.
    """

    include_triples: bool = True
    """Whether the serialised KG triples reach the prompt at all.

    Setting this false, with provision_text_scope='cross-referenced', isolates the
    graph's own contribution: the run keeps identical seeding, retrieval and provision
    text, and differs only in whether ~3,000 tokens of triples sit beside ~400 tokens of
    statutory text. If removing them improves the output, the triples are costing more
    than they add."""

    provision_text_variant: str = "raw"
    """'raw' (verbatim, citable) or 'normalised' (the corpus document EDC saw)."""

    context_token_budget: int = 3000

    # --- LLM ---------------------------------------------------------------------
    llm_model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_output_tokens: int = 4000

    # --- Behaviour ---------------------------------------------------------------
    context_mode: str = "kg"
    """Which supplementary context the prompt carries -- i.e. the experimental arm.

    'kg'          retrieve a subgraph from the APP graph and serialise it as triples
    'rp1'         select and order the whole RP1 background graph (see rp1_query)
    'spires'      walk the SPIRES norm graph and render norms (see spires_query)
    'none'        no supplementary context at all; the section text alone
    'full-corpus' paste the entire legislation corpus in verbatim

    'none' and 'full-corpus' never touch the database. Distinct from allow_unaugmented,
    which only degrades to no-context when KG retrieval fails."""

    # --- SPIRES arm --------------------------------------------------------------
    spires_hop_depth: int = 1
    """How far to follow SP_REFERS_TO / SP_DISAPPLIES from the target provision.

    1 is the direct dependency set -- what the clause points at and what points at it.
    2 adds the dependencies of those, which for APP 6.1 pulls in the permitted general
    and health situations behind 6.2(c) and (d). Beyond 2 the Schedule's reference
    network reaches most of itself and the arm degenerates into full-corpus."""

    spires_include_inbound: bool = True
    """Follow reference edges backwards as well as forwards.

    Outbound answers "what does this clause depend on"; inbound answers "what defeats
    it". Subclause 2.1 read alone states an unqualified anonymity right and is
    misleading until 2.2 -- which points AT it -- is in view. Outbound-only retrieval
    can never find 2.2 from 2.1, which is why this defaults on."""

    spires_modality_filter: Optional[str] = None
    """None keeps every norm; 'deontic' keeps only OBLIGATION/PROHIBITION/PERMISSION
    (plus APPLICABILITY, which carries the content of exceptions used elsewhere)."""

    spires_include_provision_text: bool = False
    """Append the verbatim text of the provisions the walk reached.

    The hybrid. `kg-text-only` -- statutory text with the triples withheld -- is the best
    arm measured so far, and the norm structure is what the flat triples failed to be. So
    the obvious combination is both: the structure says which rule is which and where the
    carve-outs are, the text says exactly what each one says.

    Text is read from the :AppNode graph, which is where enrich_graph.py stored it; the
    SPIRES graph holds structure only. Only reached provisions contribute -- the target's
    own text is already Block 2, and repeating it would waste budget."""

    spires_exceptions_first: bool = True
    """Render each norm's exceptions above its conditions.

    The measured gap across every arm is boundary coverage -- six elements no
    configuration ever reached were all negative or structural. Ordering exceptions
    first is a direct intervention on that, and cheap to reverse if it does not help."""

    block3_format: str = "default"
    """Which phrasing of the block 3 header to use: 'default', 'terse' or 'neutral'.

    Only for the format-sensitivity measurement. The non-default variants strip the
    arm-specific framing, so an arm run under them is not the arm as designed -- it is a
    probe of how much the framing was contributing."""

    corpus_path: Optional[str] = None
    """Corpus file for context_mode='full-corpus'. Defaults to the APP corpus."""

    run_label: Optional[str] = None
    """Free-text label recorded in the run record, for grouping experiment cells."""

    allow_unaugmented: bool = False
    """When false (default), zero seed matches or empty retrieval is fatal. The
    requirements forbid silently falling through to an unaugmented LLM call; this flag
    makes the fallthrough explicit and deliberate, and is recorded in the run record."""

    output_dir: Optional[str] = None
    """Defaults to a timestamped run folder under ./runs."""

    log_level: str = "INFO"

    def validate(self) -> None:
        if self.seeding_method not in ("llm", "vector"):
            raise ConfigError("seeding_method must be 'llm' or 'vector' (got %r)" % self.seeding_method)
        if self.seeding_method == "vector":
            raise ConfigError(
                "Vector seeding is an unimplemented extension point: node embeddings do "
                "not exist in the graph. See seeding.seed_by_vector_similarity.")
        if self.context_mode not in ("kg", "rp1", "spires", "none", "full-corpus"):
            raise ConfigError(
                "context_mode must be 'kg', 'rp1', 'spires', 'none' or 'full-corpus'")
        if self.spires_modality_filter not in (None, "deontic"):
            raise ConfigError("spires_modality_filter must be None or 'deontic'")
        if self.block3_format not in ("default", "terse", "neutral"):
            raise ConfigError("block3_format must be 'default', 'terse' or 'neutral'")
        if self.spires_hop_depth < 1:
            raise ConfigError("spires_hop_depth must be at least 1")
        if self.serialisation_format not in ("triples", "json"):
            raise ConfigError("serialisation_format must be 'triples' or 'json'")
        if self.provision_text_scope not in ("cross-referenced", "retrieved", "none"):
            raise ConfigError(
                "provision_text_scope must be 'cross-referenced', 'retrieved' or 'none'")
        if self.provision_text_variant not in ("raw", "normalised"):
            raise ConfigError("provision_text_variant must be 'raw' or 'normalised'")
        if self.self_provision_policy not in ("sole", "any", "none"):
            raise ConfigError("self_provision_policy must be 'sole', 'any' or 'none'")
        if self.ranking_key not in ("hop_weight_degree", "hop_degree"):
            raise ConfigError("ranking_key must be 'hop_weight_degree' or 'hop_degree'")
        if not 0.0 <= self.fuzzy_threshold <= 1.0:
            raise ConfigError("fuzzy_threshold must be between 0 and 1")
        for name in ("entity_hop_depth", "provision_hop_depth"):
            if getattr(self, name) < 1:
                raise ConfigError("%s must be >= 1" % name)
        for name in ("max_nodes", "max_relationships", "max_relationships_per_node",
                     "context_token_budget"):
            if getattr(self, name) < 1:
                raise ConfigError("%s must be >= 1" % name)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class Secrets:
    """Held apart from Config so the run record can serialise Config safely."""
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    llm_api_key: str


def secrets_from_env() -> Secrets:
    """Read every secret from the environment. Fails loudly and names what is missing."""
    missing = []
    uri = os.environ.get(ENV_NEO4J_URI)
    user = os.environ.get(ENV_NEO4J_USER)
    password = os.environ.get(ENV_NEO4J_PASSWORD)
    if os.environ.get("GRAPH_BACKEND", "local") == "neo4j":
        for name, value in ((ENV_NEO4J_URI, uri), (ENV_NEO4J_USER, user), (ENV_NEO4J_PASSWORD, password)):
            if not value:
                missing.append(name)

    llm_key = os.environ.get(ENV_LLM_KEY_PRIMARY) or os.environ.get(ENV_LLM_KEY_FALLBACK)
    if not llm_key:
        missing.append("%s (or %s)" % (ENV_LLM_KEY_PRIMARY, ENV_LLM_KEY_FALLBACK))

    if missing:
        raise ConfigError(
            "Missing required environment variables: %s\n"
            "Copy .env.example to .env and fill it in, or export them in your shell."
            % ", ".join(missing))

    # The vendored EDC client reads OPENAI_KEY directly; populate it so that code path
    # works regardless of which name the user set.
    os.environ[ENV_LLM_KEY_FALLBACK] = llm_key

    return Secrets(
        neo4j_uri=uri or "",
        neo4j_user=user or "",
        neo4j_password=password or "",
        neo4j_database=os.environ.get(ENV_NEO4J_DATABASE) or "neo4j",
        llm_api_key=llm_key,
    )


def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """Expose every tunable as a CLI override. Defaults are None so unset flags do not
    clobber the dataclass defaults."""
    g = parser.add_argument_group("retrieval")
    g.add_argument("--entity-hop-depth", type=int)
    g.add_argument("--provision-hop-depth", type=int)
    g.add_argument("--max-nodes", type=int)
    g.add_argument("--max-relationships", type=int)
    g.add_argument("--max-relationships-per-node", type=int)
    g.add_argument("--node-label")
    g.add_argument("--provision-anchor", dest="provision_anchor", action="store_true", default=None)
    g.add_argument("--no-provision-anchor", dest="provision_anchor", action="store_false", default=None)
    g.add_argument("--provision-id", help="Subclause id, e.g. 6.1. Auto-detected if omitted.")

    g = parser.add_argument_group("seeding")
    g.add_argument("--seeding-method", choices=["llm", "vector"])
    g.add_argument("--fuzzy-threshold", type=float)

    g = parser.add_argument_group("ranking and serialisation")
    g.add_argument("--ranking-key", choices=["hop_weight_degree", "hop_degree"])
    g.add_argument("--serialisation-format", choices=["triples", "json"])
    g.add_argument("--context-token-budget", type=int)
    g.add_argument("--provision-text-scope",
                   choices=["cross-referenced", "retrieved", "none"],
                   help="Which retrieved provisions contribute their statutory text. "
                        "Default 'cross-referenced' -- only clauses the target points at.")
    g.add_argument("--provision-text-variant", choices=["raw", "normalised"])
    g.add_argument("--no-triples", dest="include_triples", action="store_false",
                   default=None,
                   help="Omit the serialised KG triples from the prompt, keeping "
                        "provision text. Isolates what the triples contribute.")
    g.add_argument("--self-provision-policy", choices=["sole", "any", "none"],
                   help="Which triples asserted by the input subclause to drop from the "
                        "context. 'sole' (default) drops only those the input subclause "
                        "alone asserts; 'any' drops any that mention it; 'none' keeps all.")

    g = parser.add_argument_group("llm")
    g.add_argument("--llm-model")
    g.add_argument("--temperature", type=float)
    g.add_argument("--max-output-tokens", type=int)

    g = parser.add_argument_group("behaviour")
    g.add_argument("--context-mode",
                   choices=["spires"],
                   help="How supplementary context is built. The pipeline builds one: "
                        "'spires' walks the norm graph. The comparison arms -- none, "
                        "full-corpus, kg and rp1 -- live in evaluation/arms/ and are run "
                        "with `python -m evaluation.run_experiment`.")
    g.add_argument("--corpus-path",
                   help="Corpus file for the evaluation full-corpus arm. Unused by "
                        "the pipeline, which builds only the spires context.")
    g.add_argument("--block3-format", choices=["default", "terse", "neutral"],
                   help="Phrasing of the block 3 header. Non-default variants strip the "
                        "arm-specific framing and exist only to measure how much of a "
                        "score is the context and how much is the wording around it.")

    s = parser.add_argument_group("SPIRES arm (--context-mode spires)")
    s.add_argument("--spires-hop-depth", type=int,
                   help="Hops to follow along SP_REFERS_TO / SP_DISAPPLIES.")
    s.add_argument("--no-spires-inbound", dest="spires_include_inbound",
                   action="store_false", default=None,
                   help="Follow reference edges forwards only. Off by default: inbound "
                        "edges are what find the provision that DEFEATS the target, and "
                        "2.1 is misleading until 2.2 -- which points at it -- is in view.")
    s.add_argument("--spires-modality-filter", choices=["deontic"],
                   help="Keep only obligations, prohibitions and permissions (plus "
                        "APPLICABILITY, which carries the content of exceptions used "
                        "elsewhere). Default keeps every norm.")
    s.add_argument("--spires-provision-text", dest="spires_include_provision_text",
                   action="store_true", default=None,
                   help="Append the verbatim text of each provision the walk reached. "
                        "This is the spires-hybrid arm.")
    s.add_argument("--no-spires-exceptions-first", dest="spires_exceptions_first",
                   action="store_false", default=None,
                   help="Render conditions above exceptions. Exceptions come first by "
                        "default, aimed at the boundary cases every arm was missing.")
    g.add_argument("--run-label", help="Label recorded in the run record, for grouping cells.")
    g.add_argument("--allow-unaugmented", action="store_true", default=None,
                   help="Proceed with an unaugmented LLM call when retrieval yields "
                        "nothing. Off by default: the pipeline fails loudly instead.")
    g.add_argument("--output-dir")
    g.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"])


def config_from_args(args: argparse.Namespace) -> Config:
    """Build a Config from defaults, overridden by any flag the user actually passed."""
    overrides = {
        f.name: getattr(args, f.name)
        for f in dataclasses.fields(Config)
        if getattr(args, f.name, None) is not None
    }
    cfg = Config(**overrides)
    cfg.validate()
    return cfg
