#!/usr/bin/env python3
"""Measures each e2e project's time from CI job logs and prints `apps/web/e2e/shard-weights.json`.

    repo=boanlab/KloudChat run=<run id>
    for job in $(gh api "repos/$repo/actions/runs/$run/jobs" --paginate \
        -q '.jobs[] | select(.name | startswith("Web · e2e")) | .id'); do
      gh api "repos/$repo/actions/jobs/$job/logs"
    done | python3 .github/ci/e2e-weights.py > apps/web/e2e/shard-weights.json

A project's time is the gap from the line before its test (the previous test, or the
`Running N tests` line) to its test's line in the `list` reporter output, summed: setup,
fixtures and retries included. The shard split (`playwright.ci.config.ts`) only needs the
times relative to each other.
"""

import collections
import datetime
import json
import re
import sys

TEST = re.compile(r"(\d{4}-\d\d-\d\dT[\d:.]+)Z\s+(?:✓|✘|-|°)\s+\d+ \[([^\]]+)\]")
START = re.compile(r"(\d{4}-\d\d-\d\dT[\d:.]+)Z\s+Running \d+ tests? using")


def stamp(text: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(text[:26])


def main() -> None:
    spent: collections.Counter[str] = collections.Counter()
    last = None  # each job's log starts with its `Running N tests` line
    for line in sys.stdin:
        if started := START.search(line):
            last = stamp(started.group(1))
        elif test := TEST.search(line):
            at = stamp(test.group(1))
            if last is not None:
                spent[test.group(2)] += (at - last).total_seconds()
            last = at
    json.dump({name: round(spent[name], 1) for name in sorted(spent)}, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
