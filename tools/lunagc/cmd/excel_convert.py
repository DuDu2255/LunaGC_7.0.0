#!/usr/bin/env python3
"""Build a LunaGC-style ExcelBinOutput from a raw client dump.

A raw dump is not a drop-in replacement for the tree the server runs on, and
copying one over `resources/` is the classic way to end up with monsters that
have zero stats. Three things differ:

  server-only fields   The dump is what the *client* reads. Fields only the
                       server uses - affix, critical, criticalHurt,
                       elementMastery, serverScript, securityLevel,
                       visionLevel, safetyCheck - are simply not in it. They
                       have to be carried over from the tree you already run.
  rotated field names  The dump's obfuscated symbols are reshuffled every
                       version, so its `NGILKOFNLKF` is not the one the Java
                       classes were taught. field_map.py works those out; this
                       applies them.
  value encoding       The dump writes 64-bit hashes as JSON strings and omits
                       every default-valued field. The tree writes hashes as
                       numbers.

So this grafts rather than replaces: the existing tree is the base, and the dump
supplies what is genuinely new. The default --mode add-only touches no row that
already exists, which is what makes the result safe to ship when a dump lands
hours before you want the server up. --mode merge additionally fills fields the
base row is missing, and still never overwrites a value the base already has.

Output always goes to a NEW folder. Nothing writes over `resources/`.

    python tools/excel_convert.py --dump YuanShenResources --out resources-7.1
    python tools/excel_convert.py --dump YuanShenResources --out resources-7.1 --mode merge
    python tools/excel_convert.py --dump YuanShenResources --out resources-7.1 --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lunagc import load_json, repo_root, resolve_under_repo, row_id, save_json  # noqa: E402

# Confidence tiers from field_map.py that are safe to rename on automatically.
TRUSTED = ("high", "medium", "name-identity")


def clone_file(src, dst, *, follow_symlinks=True):
    """Hardlink instead of copying where the filesystem allows it.

    The base tree is well over a gigabyte, most of it TextMap, and almost none
    of it changes. Hardlinking makes a build near-instant and near-free. It is
    only safe because save_json writes a temp file and renames over the target,
    which replaces the directory entry rather than the shared inode - a plain
    open("w") here would write straight through into `resources/`.
    """
    try:
        os.link(src, dst)
    except (OSError, NotImplementedError):
        shutil.copy2(src, dst, follow_symlinks=follow_symlinks)
    return dst


def numeric_string_to_int(value):
    """The dump writes 64-bit hashes as strings; the tree writes them as numbers."""
    if isinstance(value, str) and value.lstrip("-").isdigit():
        try:
            return int(value)
        except ValueError:
            return value
    if isinstance(value, list):
        return [numeric_string_to_int(v) for v in value]
    if isinstance(value, dict):
        return {k: numeric_string_to_int(v) for k, v in value.items()}
    return value


def convert_row(row: dict, rename: dict[str, str]) -> dict:
    """Rename the dump's field names to the ones the Java classes read."""
    out = {}
    for key, value in row.items():
        out[rename.get(key, key)] = numeric_string_to_int(value)
    return out


def index_by(rows, keys: list[str]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = None
        for k in keys:
            if k in row:
                try:
                    rid = int(row[k])
                except (TypeError, ValueError):
                    rid = None
                break
        if rid is None:
            rid = row_id(row)
        if rid is not None and rid not in out:
            out[rid] = row
    return out


def rename_map(file_map: dict) -> dict[str, str]:
    """new-dump name -> the name the base tree and Java use."""
    out: dict[str, str] = {}
    for old_name, m in (file_map or {}).get("mappings", {}).items():
        if m.get("confidence") in TRUSTED and m.get("new") != old_name:
            out[m["new"]] = old_name
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="resources", help="tree the server runs today")
    ap.add_argument("--dump", required=True, help="the new client dump")
    ap.add_argument("--out", required=True, help="new folder to build (must not be the base)")
    ap.add_argument("--map", default="tools/out/field-map.json")
    ap.add_argument("--mode", choices=["add-only", "merge"], default="add-only")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = repo_root()
    base = resolve_under_repo(args.base, root)
    dump = resolve_under_repo(args.dump, root)
    out = resolve_under_repo(args.out, root)
    if out.resolve() == base.resolve():
        raise SystemExit("--out must not be the base tree; it is never written in place")
    for p in (base / "ExcelBinOutput", dump / "ExcelBinOutput"):
        if not p.is_dir():
            raise SystemExit("no ExcelBinOutput under " + str(p.parent))

    manifest_path = root / "tools" / "out" / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit("manifest missing - run: python tools/resource_manifest.py")
    manifest = load_json(manifest_path)

    map_path = resolve_under_repo(args.map, root)
    field_map = load_json(map_path).get("files", {}) if map_path.exists() else {}
    if not field_map:
        print("note: no field map at %s - running without rename support.\n"
              "      Run tools/field_map.py first if the dump is a new version." % map_path)

    if not args.dry_run:
        if out.exists():
            raise SystemExit("%s already exists - remove it or pick another --out" % out)
        print("cloning base tree -> %s" % out)
        shutil.copytree(base, out, ignore=shutil.ignore_patterns(".git", "*.tmp"),
                        copy_function=clone_file)

    rows_added = files_touched = 0
    summary = []
    for name, spec in sorted(manifest["excel_files"].items()):
        base_path = base / "ExcelBinOutput" / name
        dump_path = dump / "ExcelBinOutput" / name
        if not base_path.exists() or not dump_path.exists():
            continue
        try:
            base_rows = load_json(base_path)
            dump_rows = load_json(dump_path)
        except Exception as e:  # noqa: BLE001
            print("  skip %-46s unreadable (%s)" % (name, e))
            continue
        if not isinstance(base_rows, list) or not isinstance(dump_rows, list):
            continue

        keys = spec.get("primary_keys") or []
        rename = rename_map(field_map.get(name))
        base_idx = index_by(base_rows, keys)
        dump_idx = index_by(dump_rows, keys)

        new_ids = sorted(set(dump_idx) - set(base_idx))
        added = [convert_row(dump_idx[i], rename) for i in new_ids]
        filled = 0

        result = list(base_rows)
        if args.mode == "merge":
            by_id = {}
            for row in result:
                rid = row_id(row) if not keys else None
                if keys:
                    for k in keys:
                        if k in row:
                            try:
                                rid = int(row[k])
                            except (TypeError, ValueError):
                                rid = None
                            break
                    if rid is None:
                        rid = row_id(row)
                if rid is not None:
                    by_id.setdefault(rid, row)
            for rid, row in by_id.items():
                src = dump_idx.get(rid)
                if not src:
                    continue
                for key, value in convert_row(src, rename).items():
                    # Never overwrite. A value already in the base tree may be a
                    # server-only field the dump cannot know about.
                    if key not in row:
                        row[key] = value
                        filled += 1

        result.extend(added)
        if added or filled:
            files_touched += 1
            rows_added += len(added)
            summary.append((name, len(base_rows), len(added), filled, len(rename)))
        if not args.dry_run and (added or filled):
            save_json(out / "ExcelBinOutput" / name, result)

    print("\n  %-46s %8s %7s %7s %7s" % ("table", "base", "+rows", "+fields", "renames"))
    for name, nbase, nadd, nfill, nren in summary:
        print("  %-46s %8d %7d %7d %7d" % (name, nbase, nadd, nfill, nren))
    print("\n%d table(s) changed, %d row(s) added%s"
          % (files_touched, rows_added, "  [dry run, nothing written]" if args.dry_run else ""))
    if not args.dry_run:
        print("built -> %s" % out)
        print("\nNext: python tools/check_resources.py --resources %s \\\n"
              "        --baseline tools/out/baseline-7.0.json" % args.out)
    print("\nTextMap was not touched. The dump's TextMap is a different hash space;\n"
          "verify with tools/textmap_audit.py before ever swapping it in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
