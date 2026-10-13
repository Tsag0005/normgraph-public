"""Run every check in this directory.

    python tests/run_all.py            # all of them
    python tests/run_all.py -q         # one line per file

None of these makes an API call. Two want a live Neo4j and skip cleanly without one, so
the suite is safe to run on a fresh checkout before any credentials exist.

Each test file is also runnable on its own and prints its own detail; this only collects
them so the whole thing is one command.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run every test file in tests/.")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="One line per file instead of per check.")
    args = ap.parse_args()

    files = sorted(f for f in glob.glob(os.path.join(HERE, "test_*.py")))
    if not files:
        print("No test files found in %s" % HERE, file=sys.stderr)
        return 2

    failed, total_checks = [], 0
    for path in files:
        name = os.path.basename(path)
        result = subprocess.run([sys.executable, path], cwd=ROOT,
                                capture_output=True, text=True)
        output = (result.stdout or "") + (result.stderr or "")
        summary = next((l for l in reversed(output.splitlines()) if l.strip()), "")
        if args.quiet:
            print("%-42s %s" % (name, summary.strip()))
        else:
            print("--- %s" % name)
            print(output.rstrip())
            print()
        # "12/13 passed" -- count what ran, so a file that silently ran nothing shows up.
        if "/" in summary and "passed" in summary:
            try:
                total_checks += int(summary.split("/")[1].split()[0])
            except (IndexError, ValueError):
                pass
        if result.returncode != 0:
            failed.append(name)

    print("=" * 72)
    if failed:
        print("FAILED: %s" % ", ".join(failed))
        return 1
    print("%d files, %d checks, all passed" % (len(files), total_checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
