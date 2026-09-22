#!/usr/bin/env python3
"""Flip every version-encoded surface in the repo from one version to the next.

There are only three places the server's own version actually lives, and they
have to agree - GameConstants.VERSION is what the client is told, VERSION_PARTS
is what version comparisons use, and build.gradle's version names the jar. A
mismatch between the first two is the kind of bug that only shows up as an
inscrutable client-side version check failure.

Everything else that mentions the version (the README, the javadoc in
PacketOpcodes) is prose. Those are reported for review rather than rewritten,
because a sentence like "7.0 reshuffles CmdIds completely" is a statement about
7.0 and stays true after the bump.

Dry run by default.

    python tools/version_bump.py --to 7.1.0
    python tools/version_bump.py --to 7.1.0 --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lunagc import repo_root  # noqa: E402

GAME_CONSTANTS = "src/main/java/emu/grasscutter/GameConstants.java"
BUILD_GRADLE = "build.gradle"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# Files where a version string is prose, not configuration.
PROSE = ("README.md", "docs/", "tools/", ".github/")


def edits_for(root: Path, new: str) -> list[tuple[Path, str, str, str]]:
    """(path, label, old_line, new_line) for each authoritative surface."""
    parts = new.split(".")
    out = []

    gc = root / GAME_CONSTANTS
    text = gc.read_text(encoding="utf-8")
    m = re.search(r'^(\s*public static String VERSION = ")([^"]+)(";)$', text, re.M)
    if m:
        out.append((gc, "GameConstants.VERSION", m.group(0),
                    m.group(1) + new + m.group(3)))
    m = re.search(r"^(\s*public static int\[\] VERSION_PARTS = \{)([^}]*)(\};)$", text, re.M)
    if m:
        out.append((gc, "GameConstants.VERSION_PARTS", m.group(0),
                    m.group(1) + ", ".join(parts) + m.group(3)))

    bg = root / BUILD_GRADLE
    text = bg.read_text(encoding="utf-8")
    m = re.search(r"^(version = ')([^']+)(')$", text, re.M)
    if m:
        out.append((bg, "build.gradle version", m.group(0), m.group(1) + new + m.group(3)))
    return out


def find_prose(root: Path, old_major_minor: str) -> list[tuple[str, int, str]]:
    """Other mentions of the old version, for a human to skim."""
    hits = []
    pattern = re.compile(r"\b" + re.escape(old_major_minor) + r"(?:\.\d+)?\b")
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in (".md", ".java", ".gradle", ".yml", ".cmd"):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        if rel.startswith(("resources/", "resources-7.1/", "YuanShenResources/", "animegamedata2/", "build/", ".git/", "src/generated/")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append((rel, i, line.strip()[:110]))
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", required=True, help="new version, e.g. 7.1.0")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show-prose", action="store_true",
                    help="list every other mention of the old version")
    args = ap.parse_args(argv)

    if not VERSION_RE.match(args.to):
        raise SystemExit("--to must look like 7.1.0")

    root = repo_root()
    gc_text = (root / GAME_CONSTANTS).read_text(encoding="utf-8")
    m = re.search(r'public static String VERSION = "([^"]+)"', gc_text)
    current = m.group(1) if m else "?"
    if current == args.to:
        print("already on " + args.to)
        return 0
    print("%s -> %s\n" % (current, args.to))

    edits = edits_for(root, args.to)
    if not edits:
        raise SystemExit("found no version declarations - has the layout changed?")

    by_file: dict[Path, list[tuple[str, str, str]]] = {}
    for path, label, old, new in edits:
        by_file.setdefault(path, []).append((label, old, new))

    for path, items in by_file.items():
        print("== " + str(path.relative_to(root)).replace("\\", "/"))
        for label, old, new in items:
            print("  %s" % label)
            print("  - " + old.strip())
            print("  + " + new.strip())
        if args.apply:
            text = path.read_text(encoding="utf-8")
            for _, old, new in items:
                text = text.replace(old, new, 1)
            path.write_text(text, encoding="utf-8")

    old_mm = ".".join(current.split(".")[:2])
    prose = find_prose(root, old_mm)
    print("\n%d other mention(s) of %s remain, in prose and comments." % (len(prose), old_mm))
    if args.show_prose:
        for rel, line_no, text in prose:
            print("  %s:%d  %s" % (rel, line_no, text))
    else:
        print("Re-run with --show-prose to list them; most should stay as they are.")

    print("\n%d edit(s)%s" % (len(edits), "" if args.apply else " - dry run, pass --apply to write"))
    if args.apply:
        print("\nNow rebuild: ./gradlew jar -PskipHandbook=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
