"""Website-owned context rendering; select CORPUS_CONFIG before importing pipeline modules."""
from dataclasses import replace


def config_from_request(data):
    import corpus_config
    from kg_testgen.config import Config
    if data.get("contextMode", "spires") != "spires":
        raise ValueError("Production supports SPIRES only; evaluation arms are separate")
    mapping = {"llmModel": "llm_model", "temperature": "temperature", "maxOutputTokens": "max_output_tokens",
               "spiresHopDepth": "spires_hop_depth", "spiresInbound": "spires_include_inbound",
               "spiresExceptionsFirst": "spires_exceptions_first", "spiresProvisionText": "spires_include_provision_text",
               "allowUnaugmented": "allow_unaugmented"}
    values = {target: data[source] for source, target in mapping.items() if source in data}
    if data.get("spiresModalityFilter"):
        values["spires_modality_filter"] = data["spiresModalityFilter"]
    cfg = Config(**values, context_mode="spires", node_label=corpus_config.label("Provision"))
    cfg.validate()
    return cfg


def preview(data, pid, text):
    from kg_testgen.config import Secrets
    from kg_testgen.spires_query import build_context
    cfg = replace(config_from_request(data), provision_id=pid)
    import os
    if os.environ.get("GRAPH_BACKEND", "local") == "neo4j":
        from credentials import load_credentials
        from kg_testgen.neo4j_client import Neo4jClient
        credentials = load_credentials(None)
        if not all(credentials.get(key) for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")):
            raise ValueError("Neo4j credentials are required for a context preview")
        secrets = Secrets(neo4j_uri=credentials["NEO4J_URI"], neo4j_user=credentials["NEO4J_USERNAME"],
                          neo4j_password=credentials["NEO4J_PASSWORD"], neo4j_database=credentials.get("NEO4J_DATABASE") or "neo4j",
                          llm_api_key="")
        client_type = Neo4jClient
    else:
        from kg_testgen.local_graph import LocalGraphClient
        secrets = None
        client_type = LocalGraphClient
    with client_type(secrets, cfg) as client:
        context = build_context(client, text, cfg)
    return {"provision": pid, "text": context.text, "details": context.as_dict(),
            "summary": f"{context.norms_included} norms across {context.provisions_included} provisions"}
