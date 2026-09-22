#!/usr/bin/env python3
"""Graft a dump's BinOutput into a LunaGC-style tree.

excel_convert.py handles the tables; this handles the other half of the resource
tree - abilities, avatars, talents, quests, scenes, gadgets, monsters. The server
reads 12 BinOutput subtrees out of the ~130 a dump ships, and only those are
touched.

Add-only, always. An existing file is never overwritten, because the base tree's
copies carry hand-fixes that no dump knows about. Only files the base does not
have at all are brought across, which is what picks up a new version's characters
and quests without disturbing anything that already works.

Two things it does NOT need to do, both checked rather than assumed:

  $type        The base tree's ability configs carry `"$type": "ConfigAbility"`
               at the top level and the dump's do not. That turns out not to
               matter: AbilityData declares no $type field, so Gson ignores it on
               the way in. The nested $type discriminators that DO matter, on
               modifier actions and mixins, are present in both.
  hash values  Left as the dump writes them. Gson coerces a quoted number into a
               numeric field, and rewriting types here would risk mangling fields
               that are genuinely strings.

What it does clean up is `__unk_Q<digits>` keys, which are artefacts of the dump's
own deobfuscation pass and carry no data.

    python tools/binout_convert.py --dump YuanShenResources --out resources-7.1
    python tools/binout_convert.py --dump YuanShenResources --out resources-7.1 --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lunagc import load_json, repo_root, resolve_under_repo, save_json  # noqa: E402

UNKNOWN_KEY = re.compile(r"^__unk_[A-Za-z]?\d+$")
# Below this share of base filenames appearing in the dump, the two trees are
# not naming the same things and a set difference is not a list of new content.
MIN_OVERLAP = 0.5


def server_subtrees(manifest: dict) -> list[str]:
    """The BinOutput paths the Java source actually addresses, deepest first."""
    out = set()
    for lit in manifest.get("tree_paths", {}):
        rel = lit.rstrip("/")
        if not rel.startswith("BinOutput"):
            continue
        if rel == "BinOutput":
            continue
        out.add(rel)
    # Collapse a path that is already covered by a shorter one.
    kept = []
    for p in sorted(out):
        if not any(p != q and p.startswith(q + "/") for q in out):
            kept.append(p)
    return kept


def strip_unknown(value):
    """Drop __unk_* keys the dump's deobfuscator invents."""
    if isinstance(value, dict):
        return {k: strip_unknown(v) for k, v in value.items() if not UNKNOWN_KEY.match(k)}
    if isinstance(value, list):
        return [strip_unknown(v) for v in value]
    return value


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="resources")
    ap.add_argument("--dump", required=True)
    ap.add_argument("--out", required=True,
                    help="tree to add into; usually the one excel_convert.py built")
    ap.add_argument("--keep-unknown", action="store_true",
                    help="keep __unk_* keys instead of stripping them")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = repo_root()
    base = resolve_under_repo(args.base, root)
    dump = resolve_under_repo(args.dump, root)
    out = resolve_under_repo(args.out, root)
    if out.resolve() == base.resolve():
        raise SystemExit("--out must not be the base tree")
    if not args.dry_run and not out.is_dir():
        raise SystemExit("%s does not exist - run excel_convert.py first so both\n"
                         "halves of the tree end up in one place" % out)

    manifest_path = root / "tools" / "out" / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit("manifest missing - run: python tools/resource_manifest.py")
    subtrees = server_subtrees(load_json(manifest_path))
    if not subtrees:
        raise SystemExit("manifest lists no BinOutput paths")

    print("%d BinOutput subtree(s) the server reads\n" % len(subtrees))
    print("  %-40s %7s %7s %7s  %s" % ("subtree", "base", "dump", "+added", "note"))

    total_added = total_cleaned = 0
    rows, skipped = [], []
    for rel in subtrees:
        base_dir, dump_dir = base / rel, dump / rel

        # Some manifest entries name a single file, not a folder.
        if base_dir.is_file() or dump_dir.is_file() or rel.endswith(".json"):
            present = base_dir.is_file()
            note = "single file; " + ("kept from base" if present else "missing from base")
            print("  %-40s %7s %7s %7d  %s" % (rel, "file", "-", 0, note))
            rows.append({"subtree": rel, "kind": "file", "added": 0, "note": note})
            continue

        have = {p.relative_to(base_dir).as_posix()
                for p in base_dir.rglob("*.json")} if base_dir.is_dir() else set()
        if not dump_dir.is_dir():
            note = "absent from dump - keeps coming from the base tree"
            print("  %-40s %7d %7s %7d  %s" % (rel, len(have), "-", 0, note))
            rows.append({"subtree": rel, "base": len(have), "dump": None, "added": 0,
                         "note": note})
            continue

        theirs = {p.relative_to(dump_dir).as_posix() for p in dump_dir.rglob("*.json")}
        new = sorted(theirs - have)

        # If almost none of the base's filenames appear in the dump, the two are
        # not naming the same things and "files the base lacks" is meaningless.
        # BinOutput/Monster is the clear case: the base holds ConfigMonster_*.json
        # while the dump holds <InternalName>_<id>.json, so a graft would add
        # 3427 files that duplicate what is already there under other names.
        # LevelDesign/Routes is the same story in a different hash width.
        overlap = len(have & theirs) / len(have) if have else 1.0
        if have and overlap < MIN_OVERLAP:
            note = "SKIPPED - only %.0f%% of base names appear in the dump" % (100 * overlap)
            print("  %-40s %7d %7d %7d  %s" % (rel, len(have), len(theirs), 0, note))
            rows.append({"subtree": rel, "base": len(have), "dump": len(theirs),
                         "added": 0, "name_overlap": round(overlap, 3), "note": note})
            skipped.append(rel)
            continue

        cleaned = 0
        for name in new:
            if args.dry_run:
                continue
            src = dump_dir / name
            try:
                data = load_json(src)
            except Exception:  # noqa: BLE001
                continue
            if not args.keep_unknown:
                before = repr(data).count("__unk_")
                data = strip_unknown(data)
                cleaned += before
            save_json(out / rel / name, data)
        total_added += len(new)
        total_cleaned += cleaned
        print("  %-40s %7d %7d %7d  %s"
              % (rel, len(have), len(theirs), len(new),
                 "%.0f%% name overlap" % (100 * overlap)))
        rows.append({"subtree": rel, "base": len(have), "dump": len(theirs),
                     "added": len(new), "name_overlap": round(overlap, 3),
                     "unknown_keys_stripped": cleaned})

    print("\n%d file(s) added%s" % (total_added,
                                    "  [dry run, nothing written]" if args.dry_run else ""))
    if total_cleaned:
        print("%d __unk_* key(s) stripped" % total_cleaned)
    if skipped:
        print("\n%d subtree(s) skipped for mismatched naming:" % len(skipped))
        for rel in skipped:
            print("  " + rel)
        print("  These keep coming from the base tree. Grafting them needs a name\n"
              "  mapping, not a set difference - see tools/notes/resource-lineages.md.")
    if not args.dry_run:
        print("built into %s" % out)
        print("\nNext: python tools/check_resources.py --resources %s \\\n"
              "        --baseline tools/out/baseline-7.0.json" % args.out)

    save_json(resolve_under_repo("tools/out/binout-convert.json", root),
              {"base": str(base), "dump": str(dump), "out": str(out),
               "dry_run": args.dry_run, "subtrees": rows,
               "skipped_mismatched_naming": skipped,
               "files_added": total_added}, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
