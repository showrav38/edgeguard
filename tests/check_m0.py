"""Milestone 0 check: the repo skeleton is in place and the dataset is readable.

Uses only the Python standard library so it runs before any packages are installed.
Prints PASS or FAIL and exits with code 0 / 1.
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_DIRS = ["training", "models", "plc_sim", "edge_app", "dashboard", "mosquitto", "docs", "tests"]
REQUIRED_FILES = [".gitignore", "Makefile", "README.md", "requirements.txt", "docs/decisions.md"]
MAKE_TARGETS = ["train", "up", "down", "test", "demo"]
DATA_COLUMNS = [
    "Type", "Air temperature [K]", "Process temperature [K]", "Rotational speed [rpm]",
    "Torque [Nm]", "Tool wear [min]", "Machine failure", "TWF", "HDF", "PWF", "OSF", "RNF",
]


def main() -> int:
    errors = []

    for d in REQUIRED_DIRS:
        if not os.path.isdir(os.path.join(ROOT, d)):
            errors.append(f"missing directory: {d}/")
    for f in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(ROOT, f)):
            errors.append(f"missing file: {f}")

    # Makefile must define every required target ("name:" at line start).
    makefile = open(os.path.join(ROOT, "Makefile")).read() if os.path.isfile(os.path.join(ROOT, "Makefile")) else ""
    for t in MAKE_TARGETS:
        if f"\n{t}:" not in makefile:
            errors.append(f"Makefile target missing: {t}")

    # The dataset must never be committed.
    gitignore = open(os.path.join(ROOT, ".gitignore")).read() if os.path.isfile(os.path.join(ROOT, ".gitignore")) else ""
    if "data/" not in gitignore:
        errors.append(".gitignore does not exclude data/")

    # Dataset: exists, 10,000 rows, expected columns. utf-8-sig strips the BOM in this file.
    data_path = os.path.join(ROOT, "data", "ai4i2020.csv")
    if not os.path.isfile(data_path):
        errors.append("missing data/ai4i2020.csv")
    else:
        with open(data_path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
            missing_cols = [c for c in DATA_COLUMNS if c not in (reader.fieldnames or [])]
        if missing_cols:
            errors.append(f"dataset missing columns: {missing_cols}")
        if len(rows) != 10000:
            errors.append(f"dataset has {len(rows)} rows, expected 10000")
        else:
            n_fail = sum(r["Machine failure"] == "1" for r in rows)
            print(f"dataset: {len(rows)} rows, {n_fail} failures ({n_fail / len(rows):.1%})")

    for e in errors:
        print("  -", e)
    print("M0 check:", "PASS" if not errors else "FAIL")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
