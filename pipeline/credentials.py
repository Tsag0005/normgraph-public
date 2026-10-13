"""Neo4j credentials, from one place, for everything that needs them.

Extracted from load_neo4j.py, which is otherwise the EDC comparison arm's CSV loader and
belongs with evaluation. This function does not: the SPIRES graph builder needs it, and
so does the workbench API, and both are the pipeline. One file doing both jobs was the
last thing preventing a clean split between what the project proposes and what measures
it.

Nothing here reads a schema, a corpus or a graph. It resolves four values and returns
them, and the password is never logged, printed or written anywhere.
"""

from __future__ import annotations

import io
import os

KEYS = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")


def load_credentials(path=None):
    """Neo4j credentials, from an Aura dump, a .env file, or the environment.

    Three sources, so there is a path that needs no PowerShell. `setup_env.ps1` reads the
    Aura file and dot-sources the values into a shell, which is convenient on Windows and
    unavailable anywhere else -- a .env is the portable equivalent and works the same for
    every consumer, because they all come through here.

    Precedence, weakest first: the Aura file, then .env, then the environment. Values are
    never echoed.
    """
    creds = {}
    try:
        from dotenv import load_dotenv
        load_dotenv()                      # populates os.environ; does not overwrite it
    except ImportError:
        pass
    if path:
        with io.open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                creds[key.strip()] = value.strip()
    for key in KEYS:
        if os.environ.get(key):          # environment wins over the file
            creds[key] = os.environ[key]
    return creds
