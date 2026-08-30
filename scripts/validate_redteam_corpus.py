#!/usr/bin/env python3
"""Validate red-team corpus YAML files against the required schema.

Mirrors scripts/validate_eval_tags.py: exits 0 when every entry in every
vibe/redteam/corpus/*.yaml file carries valid id/surface/payload/
expected_outcome/severity values, exits 1 with a report otherwise.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from vibe.redteam.corpus import CORPUS_DIR, CorpusValidationError, load_corpus_file


def validate() -> int:
    yaml_files = sorted(CORPUS_DIR.glob("*.yaml"))
    if not yaml_files:
        # An empty corpus must never green-light a run (load_corpus fails fast too).
        print(f"❌ No corpus files found under {CORPUS_DIR}")
        return 1
    violations: list[str] = []
    total = 0
    ids: set[str] = set()
    for path in yaml_files:
        try:
            entries = load_corpus_file(path)
        except CorpusValidationError as e:
            violations.append(str(e))
            continue
        for entry in entries:
            total += 1
            if entry.id in ids:
                violations.append(f"duplicate id {entry.id!r} across corpus files")
            ids.add(entry.id)

    print("Red-Team Corpus Validation Report")
    print("=" * 50)
    print(f"Files checked: {len(yaml_files)}")
    print(f"Entries:       {total}")
    print(f"Violations:    {len(violations)}")
    for v in violations:
        print(f"  - {v}")
    if violations:
        print("\n❌ Corpus validation failed.")
        return 1
    print("\n✅ All corpus entries pass validation.")
    return 0


if __name__ == "__main__":
    sys.exit(validate())
