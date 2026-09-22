#!/usr/bin/env python3
"""List what content a new dump adds, with names resolved.

Answers the first question of any version bump: what actually arrived. New
avatars, weapons, artifacts, monsters, materials and quests, each with its real
name rather than a bare id.

Names are resolved against the tree you already run, not against the dump. That
sounds backwards and is worth stating plainly, because it is the single most
useful fact about these two lineages:

  the dump's EXCEL TABLES are in the same hash space as the base TextMap
  (its avatar names resolve ~83% there), while the dump's own TEXTMAP FILE is
  in a different space and resolves ~0% of anything either side asks it

So the dump's data grafts cleanly onto the base tree, and only its TextMap has
to be left behind. The dump's TextMap is still tried as a fallback for rows the
base cannot name. Nothing here writes to a resources tree.

New avatars usually need more than data: their ability names have to be in
GameConstants.DEFAULT_ABILITY_STRINGS or the character loads without its
passives. Anything found is listed under "abilities to review".

    python tools/content_report.py --dump YuanShenResources
    python tools/content_report.py --dump YuanShenResources --lang EN --markdown
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lunagc import load_json, repo_root, resolve_under_repo, row_id, save_json  # noqa: E402

# table -> (label, field holding the display-name hash)
TRACKED = {
    "AvatarExcelConfigData.json": ("avatars", "nameTextMapHash"),
    "WeaponExcelConfigData.json": ("weapons", "nameTextMapHash"),
    "ReliquaryExcelConfigData.json": ("artifacts", "nameTextMapHash"),
    "MonsterExcelConfigData.json": ("monsters", "nameTextMapHash"),
    "MaterialExcelConfigData.json": ("materials", "nameTextMapHash"),
    "MainQuestExcelConfigData.json": ("main quests", "titleTextMapHash"),
    "DungeonExcelConfigData.json": ("dungeons", "nameTextMapHash"),
    "NewActivityExcelConfigData.json": ("activities", "nameTextMapHash"),
    "AvatarSkillExcelConfigData.json": ("avatar skills", "nameTextMapHash"),
    "AvatarTalentExcelConfigData.json": ("constellations", "nameTextMapHash"),
}
AVATAR_TABLE = "AvatarExcelConfigData.json"


def ids_of(path: Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    try:
        rows = load_json(path)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(rows, list):
        return {}
    out = {}
    for row in rows:
        if isinstance(row, dict):
            rid = row_id(row)
            if rid is not None:
                out[rid] = row
    return out


def load_textmap(folder: Path, lang: str) -> dict[str, str]:
    for name in ("TextMap%s.json" % lang, "TextMap_Medium%s.json" % lang):
        p = folder / name
        if p.exists():
            try:
                data = load_json(p)
            except Exception:  # noqa: BLE001
                return {}
            return data if isinstance(data, dict) else {}
    return {}


# Plain-text names the dump carries inline. Genuinely new content has no
# TextMap entry anywhere yet - a 7.0 TextMap predates it and the dump's own is
# the wrong hash space - so the internal name is the only readable label there
# is, and "Avatar_Lantern_Girl" beats "(unnamed)" for working out what shipped.
INTERNAL_NAME_FIELDS = ("iconName", "monsterName", "jsonName", "name", "icon", "scriptType")


def name_of(row: dict, field: str, maps: list[dict]) -> str:
    h = row.get(field)
    if h is not None:
        key = str(h)
        for m in maps:
            hit = m.get(key)
            if hit:
                return hit
    for f in INTERNAL_NAME_FIELDS:
        v = row.get(f)
        if isinstance(v, str) and v:
            return v + "  (internal)"
    return ""


def ability_names(dump: Path, avatar_row: dict) -> list[str]:
    """Ability config names referenced by a new avatar, if the dump ships them."""
    internal = avatar_row.get("iconName") or avatar_row.get("bodyType") or ""
    stem = ""
    if isinstance(internal, str) and "_" in internal:
        stem = internal.rsplit("_", 1)[-1]
    if not stem:
        return []
    hits = []
    for folder in ("BinOutput/Avatar", "BinOutput/Ability/Temp/AvatarAbilities"):
        d = dump / folder
        if not d.is_dir():
            continue
        for p in d.rglob("*" + stem + "*.json"):
            hits.append(str(p.relative_to(dump)).replace("\\", "/"))
    return sorted(hits)[:6]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="resources")
    ap.add_argument("--dump", required=True)
    ap.add_argument("--lang", default="EN")
    ap.add_argument("--limit", type=int, default=30, help="rows listed per table")
    ap.add_argument("--markdown", action="store_true", help="emit a pasteable summary")
    ap.add_argument("--json", dest="json_out", default="tools/out/content-report.json")
    args = ap.parse_args(argv)

    root = repo_root()
    base = resolve_under_repo(args.base, root)
    dump = resolve_under_repo(args.dump, root)
    if not (dump / "ExcelBinOutput").is_dir():
        raise SystemExit("no ExcelBinOutput under " + str(dump))

    # Base first: it is the TextMap that shares a hash space with both trees'
    # excel tables. The dump's own is only a fallback for rows the base predates.
    maps = [m for m in (load_textmap(base / "TextMap", args.lang),
                        load_textmap(dump / "TextMap", args.lang)) if m]
    if not maps:
        print("note: no readable TextMap found - ids will be listed unnamed\n")

    report: dict[str, list] = {}
    lines: list[str] = []
    for table, (label, field) in TRACKED.items():
        old = ids_of(base / "ExcelBinOutput" / table)
        new = ids_of(dump / "ExcelBinOutput" / table)
        if not new:
            continue
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        entries = [{"id": i, "name": name_of(new[i], field, maps)} for i in added]
        report[label] = {"added": entries, "removed": removed,
                         "base_rows": len(old), "dump_rows": len(new)}
        if not added and not removed:
            continue
        lines.append("")
        lines.append("-- %s: +%d, -%d (%d -> %d) --"
                     % (label, len(added), len(removed), len(old), len(new)))
        named = [e for e in entries if e["name"]]
        show = (named or entries)[: args.limit]
        for e in show:
            lines.append("   %-12d %s" % (e["id"], e["name"] or "(unnamed)"))
        if len(entries) > len(show):
            lines.append("   ... and %d more" % (len(entries) - len(show)))

    print("\n".join(lines) if lines else "no tracked content changed")

    # New avatars are the ones that need code attention, not just data.
    avatars = report.get("avatars", {}).get("added", [])
    if avatars:
        print("\n-- abilities to review for new avatars --")
        new_rows = ids_of(dump / "ExcelBinOutput" / AVATAR_TABLE)
        for entry in avatars:
            row = new_rows.get(entry["id"], {})
            hits = ability_names(dump, row)
            print("   %-12d %-22s %s" % (entry["id"], entry["name"] or "(unnamed)",
                                         hits[0] if hits else "no ability config found in dump"))
            for h in hits[1:]:
                print("   %-35s %s" % ("", h))
        print("\n   Check each against GameConstants.DEFAULT_ABILITY_STRINGS; an avatar\n"
              "   whose passives are missing there loads but behaves wrongly.")

    out = resolve_under_repo(args.json_out, root)
    save_json(out, {"base": str(base), "dump": str(dump), "language": args.lang,
                    "content": report}, indent=2)
    print("\nreport -> " + str(out))

    if args.markdown:
        md = ["# Content added in this dump", ""]
        for label, data in report.items():
            if not data["added"]:
                continue
            md.append("## %s (+%d)" % (label, len(data["added"])))
            for e in data["added"][: args.limit]:
                md.append("- `%d` %s" % (e["id"], e["name"] or "(unnamed)"))
            md.append("")
        md_path = out.with_suffix(".md")
        md_path.write_text("\n".join(md), encoding="utf-8")
        print("markdown -> " + str(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
