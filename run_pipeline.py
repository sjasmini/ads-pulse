#!/usr/bin/env python3
"""Run the whole pipeline: python3 run_pipeline.py daily|weekly [--allow-stale]

Required steps stop the run (non-zero exit). Optional analysis steps never block the email:
if one fails, its previous report is kept and a warning is printed. Prints "PIPELINE OK" at the end."""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
STEPS = [  # (script, args, required, report it writes)
    ("ingest.py", [], True, "ingest.json"),
    ("process_full.py", [], True, "full.json"),
    ("process_placement.py", [], False, "placement.json"),
    ("detect_changes.py", [], False, "changes.json"),
    ("process_funnel.py", [], False, "funnel.json"),
    ("build_report.py", [], True, "report.json"),
    ("render.py", ["MODE"], True, None),
]


def main():
    mode = next((a for a in sys.argv[1:] if a in ("daily", "weekly")), None)
    if not mode:
        sys.exit("usage: run_pipeline.py daily|weekly [--allow-stale]")
    warnings = []
    for script, args, required, report in STEPS:
        args = [mode if a == "MODE" else a for a in args]
        rpath = os.path.join(ROOT, "reports", report) if report else None
        backup = rpath + ".prev" if rpath and os.path.exists(rpath) else None
        if backup:
            shutil.copy2(rpath, backup)
        res = subprocess.run([sys.executable, os.path.join(ROOT, script)] + args, cwd=ROOT)
        if res.returncode != 0:
            if required:
                print(f"PIPELINE FAILED at {script} (exit {res.returncode})", file=sys.stderr)
                sys.exit(res.returncode or 1)
            if backup:
                shutil.copy2(backup, rpath)
            warnings.append(f"WARNING: optional step {script} failed; kept its previous report")
            print(warnings[-1], file=sys.stderr)
        if backup and os.path.exists(backup):
            os.remove(backup)
        if script == "ingest.py":
            import json
            ing = json.load(open(os.path.join(ROOT, "reports", "ingest.json")))
            if not ing["fresh"] and "--allow-stale" not in sys.argv:
                print(f"PIPELINE FAILED: newest data date {ing['data_date']} is not yesterday "
                      f"({ing['expected_date']})", file=sys.stderr)
                sys.exit(5)
    for w in warnings:
        print(w)
    print("PIPELINE OK" + (f" ({len(warnings)} warning(s))" if warnings else ""))


if __name__ == "__main__":
    main()
