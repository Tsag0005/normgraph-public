"""Diagnose an 'Incorrect API key provided' failure without revealing the key.

Prints only metadata -- length, prefix family, and whether stray characters got in.
Nothing here writes the key to disk or sends it anywhere except OpenAI's own auth check.
"""

import os
import sys

VAR = "OPENAI_KEY"          # EDC reads this name, not the conventional OPENAI_API_KEY

raw = os.environ.get(VAR)
if raw is None:
    print("{} is not set in this process at all.".format(VAR))
    sys.exit(1)

stripped = raw.strip().strip('"').strip("'")

print("variable        : {}".format(VAR))
print("length          : {}".format(len(raw)))
print("leading space   : {}".format(raw != raw.lstrip()))
print("trailing space  : {}".format(raw != raw.rstrip()))
print("wrapping quotes : {}".format(raw.strip() != stripped))
print("contains CR/LF  : {}".format("\n" in raw or "\r" in raw))

# Identify the key family from its prefix. This is the fastest way to catch the classic
# mistake of pasting a key from the wrong provider into an OpenAI variable.
families = [
    ("sk-ant-", "Anthropic  <-- WRONG PROVIDER, this is a Claude key, not an OpenAI one"),
    ("sk-proj-", "OpenAI project key"),
    ("sk-svcacct-", "OpenAI service-account key"),
    ("sk-admin-", "OpenAI admin key"),
    ("sk-", "OpenAI legacy user key"),
]
family = next((label for prefix, label in families if stripped.startswith(prefix)),
              "UNRECOGNISED -- does not start with 'sk-'")
print("key family      : {}".format(family))

if len(sys.argv) > 1 and sys.argv[1] == "--live":
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        pass
    import httpx

    print("\nCalling https://api.openai.com/v1/models ...")
    try:
        r = httpx.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": "Bearer " + stripped},
            timeout=30,
        )
        if r.status_code == 200:
            models = sorted(m["id"] for m in r.json().get("data", []))
            print("AUTH OK. {} models visible.".format(len(models)))
            print("gpt-4o-mini available: {}".format("gpt-4o-mini" in models))
        else:
            # Surface OpenAI's own wording -- it distinguishes a bad key from a key that
            # is valid but has no credit, which need completely different fixes.
            print("HTTP {}".format(r.status_code))
            print(r.text[:600])
    except Exception as e:
        print("request failed: {}: {}".format(type(e).__name__, e))
