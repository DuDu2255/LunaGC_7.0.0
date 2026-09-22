#!/usr/bin/env python3
"""Check that a TextMap can actually resolve the hashes the server holds.

TextMaps are keyed by hash, so a wrong one is not an error - it is a silent
miss. Every lookup returns nothing and the game shows blank names. The two dump
lineages have historically used *different hash spaces*, so a TextMap that looks
perfectly healthy (60 MB, millions of entries, valid JSON) can resolve almost
none of the `nameTextMapHash` values sitting in the server's own excel tables.

This measures that directly: pull the nameTextMapHash values out of the tables
a player actually sees, then ask a candidate TextMap how many it can answer. It
needs no client and no running server, which makes it the one correctness check
available before release day.

Judge the result by comparison, not by an absolute bar. Rates are uneven for
honest reasons - on the current 7.0 tree Weapon and Reliquary sit around 93%
while Monster and Gadget are 0%, because most monster and gadget names were
never in TextMap to begin with. So the question worth asking is "does the new
TextMap do at least as well as the one we ship today", which is what --compare
answers.

    python tools/textmap_audit.py                                  # current tree
    python tools/textmap_audit.py --compare YuanShenResources/TextMap   # new vs current
    python tools/textmap_audit.py --compare YuanShenResources/TextMap --all-tables
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lunagc import load_json, repo_root, resolve_under_repo, save_json  # noqa: E402

# Tables whose names a player reads constantly. A regression here is visible in
# the first minute of play; a regression in ps5TitleTextMapHash is not.
CORE_TABLES = [
    "AvatarExcelConfigData.json",
    "WeaponExcelConfigData.json",
    "ReliquaryExcelConfigData.json",
    "MaterialExcelConfigData.json",
    "NpcExcelConfigData.json",
    "MonsterExcelConfigData.json",
    "GadgetExcelConfigData.json",
    "DungeonExcelConfigData.json",
    "AvatarSkillExcelConfigData.json",
    "AvatarTalentExcelConfigData.json",
]
HASH_FIELD = "nameTextMapHash"
REGRESSION = 0.05  # a drop bigger than this against the baseline is called out


def table_hashes(excel: Path, names: list[str], field: str) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for name in names:
        path = excel / name
        if not path.exists():
            continue
        try:
            rows = load_json(path)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(rows, list):
            continue
        found: set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = row.get(field)
            try:
                h = int(value)
            except (TypeError, ValueError):
                continue
            if h:
                found.add(h)
        if found:
            out[name] = found
    return out


def textmap_keys(path: Path) -> set[int]:
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object of hash -> string")
    keys: set[int] = set()
    for k in data:
        try:
            keys.add(int(k))
        except (TypeError, ValueError):
            continue
    return keys


def pick_textmap(folder: Path, lang: str) -> Path | None:
    for name in ("TextMap%s.json" % lang, "TextMap_Medium%s.json" % lang):
        p = folder / name
        if p.exists():
            return p
    rest = sorted(folder.glob("TextMap*.json"))
    return rest[0] if rest else None


def rates(per_table: dict[str, set[int]], keys: set[int]) -> dict[str, float]:
    return {n: len(hs & keys) / len(hs) for n, hs in per_table.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resources", default="resources",
                    help="tree whose excel tables supply the hashes to look up")
    ap.add_argument("--compare", default=None,
                    help="a candidate TextMap folder to test against the tree's own")
    ap.add_argument("--lang", default="EN")
    ap.add_argument("--all-tables", action="store_true",
                    help="every excel table, not just the player-facing ones")
    ap.add_argument("--field", default=HASH_FIELD)
    ap.add_argument("--json", dest="json_out", default="tools/out/textmap-audit.json")
    args = ap.parse_args(argv)

    root = repo_root()
    res = resolve_under_repo(args.resources, root)
    excel = res / "ExcelBinOutput"
    if not excel.is_dir():
        raise SystemExit("no ExcelBinOutput under " + str(res))

    names = sorted(p.name for p in excel.glob("*.json")) if args.all_tables else CORE_TABLES
    per_table = table_hashes(excel, names, args.field)
    if not per_table:
        raise SystemExit("no %s values found in %s" % (args.field, excel))

    base_dir = res / "TextMap"
    base_file = pick_textmap(base_dir, args.lang) if base_dir.is_dir() else None
    if base_file is None:
        raise SystemExit("no TextMap to use as a baseline under " + str(base_dir))
    base_keys = textmap_keys(base_file)
    base_rates = rates(per_table, base_keys)

    cand_file = cand_rates = None
    if args.compare:
        cand_dir = resolve_under_repo(args.compare, root)
        if not cand_dir.is_dir():
            raise SystemExit("no such TextMap folder: " + str(cand_dir))
        cand_file = pick_textmap(cand_dir, args.lang)
        if cand_file is None:
            raise SystemExit("no TextMap*.json in " + str(cand_dir))
        cand_rates = rates(per_table, textmap_keys(cand_file))

    print("field: %s   tables: %d   language: %s" % (args.field, len(per_table), args.lang))
    print("baseline : %s" % base_file)
    if cand_file:
        print("candidate: %s" % cand_file)
    print()
    header = "  %-38s %8s %9s" % ("table", "hashes", "baseline")
    if cand_rates:
        header += " %9s %8s" % ("candidate", "delta")
    print(header)

    regressions, report_rows = [], []
    for name in sorted(per_table, key=lambda n: -len(per_table[n])):
        n_hashes = len(per_table[name])
        b = base_rates[name]
        line = "  %-38s %8d %8.2f%%" % (name, n_hashes, 100 * b)
        row = {"table": name, "hashes": n_hashes, "baseline": round(b, 4)}
        if cand_rates:
            c = cand_rates[name]
            delta = c - b
            line += " %8.2f%% %+7.2f%%" % (100 * c, 100 * delta)
            row.update(candidate=round(c, 4), delta=round(delta, 4))
            if delta < -REGRESSION:
                regressions.append((name, b, c))
        print(line)
        report_rows.append(row)

    total = sum(len(h) for h in per_table.values())
    b_all = sum(len(h & base_keys) for h in per_table.values()) / total
    print("\n  %-38s %8d %8.2f%%" % ("ALL (weighted)", total, 100 * b_all))

    if cand_rates:
        cand_keys = textmap_keys(cand_file)
        c_all = sum(len(h & cand_keys) for h in per_table.values()) / total
        print("  %-38s %8s %8s %8.2f%% %+7.2f%%"
              % ("", "", "", 100 * c_all, 100 * (c_all - b_all)))
        print()
        if c_all < 0.05 and b_all >= 0.05:
            print("VERDICT: the candidate answers almost nothing the server asks it.\n"
                  "         This is the different-hash-space failure. Keep the TextMap\n"
                  "         folder you already have - do not copy this one in.")
        elif regressions:
            print("VERDICT: usable overall, but %d table(s) got worse:" % len(regressions))
            for name, b, c in regressions:
                print("         %-38s %.2f%% -> %.2f%%" % (name, 100 * b, 100 * c))
        elif c_all >= b_all:
            print("VERDICT: same hash space and no regression - safe to adopt.")
        else:
            print("VERDICT: same hash space, slightly behind the baseline overall.")

    out = resolve_under_repo(args.json_out, root)
    save_json(out, {
        "resources": str(res),
        "field": args.field,
        "baseline_file": str(base_file),
        "candidate_file": str(cand_file) if cand_file else None,
        "tables": report_rows,
        "weighted_baseline": round(b_all, 4),
        "weighted_candidate": round(c_all, 4) if cand_rates else None,
        "regressions": [{"table": n, "from": round(b, 4), "to": round(c, 4)}
                        for n, b, c in regressions],
    }, indent=2)
    print("\nreport -> " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
