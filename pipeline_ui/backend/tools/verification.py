"""Resumable verification policy owned by the website, not the pipeline."""
from pathlib import Path
import json
import os
import subprocess

from corpora import ROOT, read_yaml


def verify_missing(clauses, corpus, argv, emit, sentence_parser):
    entries = []
    output = Path(corpus.SPIRES_OUTPUT_DIR)
    report_path = output / "verification-report.json"

    def record(pid, status, message):
        entries.append({"provision": pid, "status": status, "message": message})
        report_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        emit("verify", f"{pid}: {status}. {message}")

    for pid in clauses:
        raw = output / corpus.provision_filename(pid, ".yaml")
        cleaned = output / corpus.provision_filename(pid, ".clean.yaml")
        verified = output / corpus.provision_filename(pid, ".verified.yaml")
        if verified.exists():
            record(pid, "reused", "Existing verified artifact retained.")
            continue
        try:
            clean = read_yaml(cleaned)
            if not isinstance(clean.get("norms"), list):
                raise ValueError("Cleaned artifact has no valid norms list")
            if not raw.exists():
                record(pid, "skipped", "Raw extraction unavailable; using cleaned norms without exception recovery.")
                continue
            document = read_yaml(raw)
            extracted = document.get("extracted_object")
            if not isinstance(extracted, dict):
                raise ValueError("Raw extraction has no structured extracted_object")
            norms = extracted.get("norms") or []
            if not isinstance(norms, list) or any(not isinstance(norm, dict) for norm in norms):
                raise ValueError("Raw extraction has a malformed norms list")
            if not norms and not clean["norms"]:
                record(pid, "skipped", "No structured norms to verify. This may be a heading-only item or an extraction omission; "
                       "review the source. No API call made and no verified artifact created.")
                continue
            sentences = sentence_parser(document.get("raw_completion_output"))
            if len(sentences) != len(norms) or not norms:
                record(pid, "skipped", f"Cannot pair {len(sentences)} raw sentences with {len(norms)} structured norms. "
                       "Using cleaned norms without exception recovery; review this extraction. No API call made.")
                continue
            command = [*argv, "--glob", str(raw)]
            emit("command", subprocess.list2cmdline(command))
            checked = subprocess.run(command, cwd=ROOT, env=dict(os.environ), capture_output=True,
                                     text=True, encoding="utf-8", errors="replace")
            report = checked.stdout + checked.stderr
            print(report, flush=True)
            if checked.returncode or "verify failed:" in report or "SKIPPED -" in report or not verified.exists():
                raise ValueError("Verifier failed or produced incomplete output; see the command log")
            verified_document = read_yaml(verified)
            if not isinstance(verified_document.get("norms"), list):
                raise ValueError("Verifier produced a malformed artifact")
            record(pid, "verified", "Exception recovery completed.")
        except Exception as exc:
            # It did not exist before this attempt, so an incomplete result must
            # not look reusable next time. Original/raw/cleaned files stay intact.
            verified.unlink(missing_ok=True)
            record(pid, "failed", f"{exc}. Cleaned output retained; other provisions will continue.")
    return entries
