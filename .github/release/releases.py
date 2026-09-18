#!/usr/bin/env python3
"""Write RELEASES.json: every installable mls release and the mach it links (#288, #299).

mls links exactly one mach, so an editor extension that cannot list releases
reads this asset to find the newest mls whose mach a project accepts. A
version is listed only when its GitHub release carries the complete asset
set, so every listed version can be installed: a tag with no release, or a
release missing an archive, is left out. The mach version is
`dep/mach/mach.toml` at the dep/mach commit the release pins. Nothing here is
maintained by hand.

The format is frozen at 1.0: one JSON object mapping each mls version to its
mach version, newest first.

    releases.py <version being cut> <output path>

The release being cut is read from HEAD and listed unconditionally: the
workflow publishes it with the full set right after, and fails if any asset is
missing. Every other entry comes from a `vX.Y.Z` tag whose published release
has the set. The checkout needs its tags and the dep/mach submodule
initialized, `gh` needs a token, and commits a shallow submodule lacks are
fetched.
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
ARCHIVES = ("x86_64-linux.tar.gz", "aarch64-linux.tar.gz", "aarch64-darwin.tar.gz",
            "x86_64-darwin.tar.gz", "x86_64-windows.zip")


def run(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"releases: {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def git(*args: str, cwd: Path | None = None) -> str:
    return run("git", *args, cwd=cwd)


def asset_set(version: str) -> set[str]:
    return {f"mls-{version}-{archive}" for archive in ARCHIVES} | {"SHA256SUMS"}


def published_assets() -> dict[str, set[str]]:
    """The asset names of every published (not draft) release, by tag."""
    out = run("gh", "api", "--paginate", "repos/{owner}/{repo}/releases",
              "--jq", '.[] | select(.draft | not) | {tag: .tag_name, assets: [.assets[].name]}')
    releases: dict[str, set[str]] = {}
    for line in out.splitlines():
        if line.strip():
            item = json.loads(line)
            releases[item["tag"]] = set(item["assets"])
    return releases


def installable(releases: dict[str, set[str]]) -> dict[str, str]:
    """Tagged versions whose release carries the full asset set, with the reason
    each other tag is left out on stderr."""
    tags = [m for m in map(TAG.match, git("tag", "--list", "v*").split()) if m]
    listed: dict[str, str] = {}
    for m in tags:
        tag, version = m.group(0), m.group(0)[1:]
        assets = releases.get(tag)
        if assets is None:
            print(f"releases: {tag} has no published release; not listed", file=sys.stderr)
            continue
        missing = asset_set(version) - assets
        if missing:
            print(f"releases: {tag} lacks {', '.join(sorted(missing))}; not listed", file=sys.stderr)
            continue
        listed[version] = tag
    return listed


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

    releases = published_assets()
    refs = installable(releases)
    refs[current] = "HEAD"

    pins = {version: pinned(ref) for version, ref in refs.items()}
    fetch_missing(set(pins.values()))
    entries = {version: mach_version(commit) for version, commit in pins.items()}
    ordered = dict(sorted(entries.items(), key=lambda item: key(item[0]), reverse=True))

    # every listed version other than the one being cut is installable now
    for version in ordered:
        if version != current and asset_set(version) - releases.get("v" + version, set()):
            raise SystemExit(f"releases: {version} is listed without its full asset set")

    out.write_text(json.dumps(ordered) + "\n", encoding="utf-8")
    print(json.dumps(ordered, indent=2))


if __name__ == "__main__":
    main()
