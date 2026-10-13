"""Route TLS verification through the OS certificate store, at interpreter startup.

Kept at the repository root and COPIED into .venv-ontogpt/Lib/site-packages by
bootstrap.ps1. It used to exist only inside that venv, which is gitignored -- so a fresh
checkout lost it, and the failure it prevents is a CERTIFICATE_VERIFY_FAILED on every
ontogpt call with nothing to say why.

Route TLS verification through the OS certificate store.

The local TLS-inspecting proxy presents a certificate that certifi does not trust, so
every HTTPS call from this venv fails with CERTIFICATE_VERIFY_FAILED -- including
tiktoken's encoding download and every OpenAI request. `run.py` solves this by calling
truststore at the top of the script, but the `ontogpt` console script has no such hook,
so the injection has to happen at interpreter startup instead. Python imports
sitecustomize automatically, which covers the CLI and any `python -m` invocation alike.
"""

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 - never let this break interpreter startup
    pass
