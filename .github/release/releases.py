#!/usr/bin/env python3
"""Write RELEASES.json: every mls release and the mach version it links (#288).

mls links exactly one mach, so an editor extension that cannot list releases
reads this asset to find the newest mls whose mach a project accepts. Each
entry is derived from git alone: the mach version is `dep/mach/mach.toml` at
the dep/mach commit the release pins. Nothing here is maintained by hand.

The format is frozen at 1.0: one JSON object mapping each mls version to its
mach version, newest first.

    releases.py <version being cut> <output path>

The release being cut is read from HEAD, whether or not its tag exists yet,
and every `vX.Y.Z` tag supplies the rest. The checkout needs its tags and the
dep/mach submodule initialized; commits a shallow submodule lacks are fetched.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
VERSION = re.compile(r'^version = "([^"]+)"$', re.M)
SUBMODULE = "dep/mach"


def git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"releases: git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def pinned(ref: str) -> str:
    fields = git("ls-tree", ref, SUBMODULE).split()
    if len(fields) < 3 or fields[1] != "commit":
        raise SystemExit(f"releases: {ref} pins no {SUBMODULE}")
    return fields[2]


def fetch_missing(commits: set[str]) -> None:
    sub = Path(SUBMODULE)
    missing = sorted(
        c for c in commits
        if subprocess.run(("git", "cat-file", "-e", f"{c}^{{commit}}"), cwd=sub, capture_output=True).returncode != 0
    )
    # one fetch: successive shallow fetches race on git's shallow file
    if missing:
        git("fetch", "--quiet", "--no-tags", "--depth=1", "origin", *missing, cwd=sub)


def mach_version(commit: str) -> str:
    found = VERSION.search(git("show", f"{commit}:mach.toml", cwd=Path(SUBMODULE)))
    if found is None:
        raise SystemExit(f"releases: {SUBMODULE}@{commit} has no mach.toml version")
    return found.group(1)


def key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    current, out = sys.argv[1], Path(sys.argv[2])
    if TAG.match("v" + current) is None:
        raise SystemExit(f"releases: {current} is not a release version")

    refs = {m.group(0)[1:]: m.group(0) for m in map(TAG.match, git("tag", "--list", "v*").split()) if m}
    refs[current] = "HEAD"

    pins = {version: pinned(ref) for version, ref in refs.items()}
    fetch_missing(set(pins.values()))
    entries = {version: mach_version(commit) for version, commit in pins.items()}
    ordered = dict(sorted(entries.items(), key=lambda item: key(item[0]), reverse=True))
    out.write_text(json.dumps(ordered) + "\n", encoding="utf-8")
    print(json.dumps(ordered, indent=2))


if __name__ == "__main__":
    main()
