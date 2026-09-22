#!/usr/bin/env python3
"""Recover rotated obfuscated field names by matching values, not names.

The single biggest mechanical cost of a version bump. The client obfuscates most
excel field names into 11-letter symbols, and those symbols are reshuffled every
game version: AnimalCodex's `ABOAGOHLBID` in one dump is `NDEAAFCAJBG` in the
next. The Java data classes pin 117 of them by hand via

    @SerializedName(value = "flycloakId", alternate = {"BBDGOAOMCMB", ...})

so every one of those is stale the moment a new version lands, and a stale alias
fails silently: Gson just leaves the field at zero.

Names cannot be matched to names, so this matches *data*. For rows that appear in
both dumps under the same primary key, a field in the old dump and a field in the
new dump are the same field if their values agree row after row. Agreement alone
is not enough - a column that is `false` 97% of the time agrees with every other
mostly-false column - so a candidate has to clear three bars:

  informative agreement  values agree on rows where the OLD value is non-default,
                         which is what actually distinguishes two sparse columns
  lift                   agreement must beat what the two columns' own value
                         distributions would produce by chance
  mutual best            old->new and new->old have to pick each other

Clear-named fields are left in the candidate pool on purpose: they map to
themselves, so the rate at which this tool recovers `describeId -> describeId`
is a direct, client-free measure of how much to trust the obfuscated answers it
gives in the same run. That self-check is printed every time and written into
the output as `self_check`.

    python tools/field_map.py --old resources --new YuanShenResources
    python tools/field_map.py --old resources --new YuanShenResources --file MonsterExcelConfigData.json -v
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lunagc import load_json, repo_root, resolve_under_repo, row_id, save_json, normalise  # noqa: E402


# Tuned against the 7.0 -> 7.0.5x pair, where the clear-name self-check gives
# ground truth. Raising MIN_INFORMATIVE trades recall for precision; a wrong
# alias is far more expensive than a missing one, since a missing one is loud
# (the field reports unmatched) and a wrong one is silent (Gson reads garbage).
MIN_COMMON_ROWS = 8
MIN_INFORMATIVE = 5
HIGH_AGREE = 0.97
OK_AGREE = 0.85
MIN_LIFT = 1.15
MIN_MARGIN = 0.05




def index_rows(path: Path, keys: list[str]) -> dict[int, dict]:
    """Index a table by primary key, trying the manifest key then id/Id/ID."""
    try:
        data = load_json(path)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, list):
        return {}
    out: dict[int, dict] = {}
    for row in data:
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


def hashable(v):
    """A comparable, hashable stand-in for any JSON value."""
    if isinstance(v, (list, dict)):
        return repr(v)
    return v


def column(rows: dict[int, dict], ids: list[int], field: str) -> list:
    """One field's values across `ids`, normalised and made hashable.

    Absent keys come back as None, which is how a proto3 dump spells a default,
    so an omitted field and an explicit zero still compare as the same column.
    """
    return [hashable(normalise(rows[i].get(field))) for i in ids]


def chance_agreement(a: list, b: list) -> float:
    """P(two independent draws from these columns agree) - the null model."""
    ca, cb = Counter(a), Counter(b)
    n = len(a)
    if not n:
        return 1.0
    return sum(ca[v] * cb.get(v, 0) for v in ca) / (n * n)


def score_pair(a: list, b: list) -> tuple[float, float, int, float]:
    """(informative agreement, overall agreement, informative rows, lift).

    "Informative" means rows where the old column does NOT hold its own modal
    value. Excluding only proto defaults is not enough: a column that is `1` on
    97% of rows agrees with every other mostly-`1` column just as thoroughly as
    a mostly-`false` one does. Scoring on the rows where a column departs from
    its own mode is what separates two sparse columns from each other.
    """
    n = len(a)
    if not n:
        return 0.0, 0.0, 0, 0.0
    agree = sum(1 for x, y in zip(a, b) if x == y)
    overall = agree / n
    mode = Counter(a).most_common(1)[0][0]
    inf_idx = [i for i, x in enumerate(a) if x != mode]
    if inf_idx:
        inf_agree = sum(1 for i in inf_idx if a[i] == b[i]) / len(inf_idx)
    else:
        inf_agree = 0.0
    baseline = chance_agreement(a, b)
    lift = overall / baseline if baseline > 0 else float("inf")
    return inf_agree, overall, len(inf_idx), lift


def confidence(inf: float, overall: float, support: int, lift: float, margin: float) -> str:
    if support < MIN_INFORMATIVE:
        return "insufficient-data"
    if lift < MIN_LIFT:
        return "rejected-no-lift"
    if inf >= HIGH_AGREE and overall >= HIGH_AGREE and margin >= MIN_MARGIN:
        return "high"
    if inf >= OK_AGREE and margin >= MIN_MARGIN:
        return "medium"
    if inf >= OK_AGREE:
        return "low-ambiguous"
    return "rejected-low-agreement"


def map_file(old_path: Path, new_path: Path, keys: list[str]) -> dict | None:
    old = index_rows(old_path, keys)
    new = index_rows(new_path, keys)
    ids = sorted(set(old) & set(new))
    if len(ids) < MIN_COMMON_ROWS:
        return {
            "common_rows": len(ids),
            "skipped": "fewer than %d rows share a primary key" % MIN_COMMON_ROWS,
            "mappings": {},
        }
    old_fields = sorted({k for i in ids for k in old[i]})
    new_fields = sorted({k for i in ids for k in new[i]})
    old_cols = {f: column(old, ids, f) for f in old_fields}
    new_cols = {f: column(new, ids, f) for f in new_fields}

    # A name that survived the version bump verbatim is the strongest evidence
    # there is, so those pairs are settled first and taken out of the pool.
    # Leaving them in lets a rare boolean like isInvisibleReset outscore the
    # real owner of a column that happens to look similar, and the resulting
    # swaps are silent. Only fields with no surviving name get value-matched,
    # and they compete over a pool nobody has claimed.
    identity = [f for f in old_fields if f in new_cols]
    unclaimed_new = [f for f in new_fields if f not in set(identity)]
    to_match = [f for f in old_fields if f not in set(identity)]

    mappings: dict[str, dict] = {}
    for f in identity:
        inf, overall, support, lift = score_pair(old_cols[f], new_cols[f])
        mappings[f] = {
            "new": f,
            "confidence": "name-identity",
            "informative_agreement": round(inf, 4),
            "overall_agreement": round(overall, 4),
            "informative_rows": support,
            "lift": round(lift, 2) if lift != float("inf") else None,
            "margin": None,
            "mutual_best": True,
            "runner_up": None,
        }

    if to_match and unclaimed_new:
        matrix = {
            x: sorted(
                ((score_pair(old_cols[x], new_cols[y]), y) for y in unclaimed_new),
                key=lambda t: (t[0][0], t[0][1], t[0][3]),
                reverse=True,
            )
            for x in to_match
        }
        rev_best: dict[str, str] = {}
        for y in unclaimed_new:
            best_x, best_s = None, None
            for x in to_match:
                s = score_pair(new_cols[y], old_cols[x])
                if best_s is None or (s[0], s[1]) > (best_s[0], best_s[1]):
                    best_x, best_s = x, s
            rev_best[y] = best_x
        for x, ranked in matrix.items():
            (inf, overall, support, lift), y = ranked[0]
            margin = inf - ranked[1][0][0] if len(ranked) > 1 else 1.0
            conf = confidence(inf, overall, support, lift, margin)
            mutual = rev_best.get(y) == x
            if not mutual and conf in ("high", "medium"):
                conf = "low-ambiguous"
            mappings[x] = {
                "new": y,
                "confidence": conf,
                "informative_agreement": round(inf, 4),
                "overall_agreement": round(overall, 4),
                "informative_rows": support,
                "lift": round(lift, 2) if lift != float("inf") else None,
                "margin": round(margin, 4),
                "mutual_best": mutual,
                "runner_up": ranked[1][1] if len(ranked) > 1 else None,
            }

    return {
        "common_rows": len(ids),
        "identity_fields": len(identity),
        "value_matched": len(to_match),
        "mappings": mappings,
        "self_check": identity_probe(old_cols, new_cols, identity),
    }


def identity_probe(old_cols: dict, new_cols: dict, identity: list[str]) -> dict:
    """Would value matching alone have recovered the names we already know?

    Every field whose name survived the bump is a labelled example. Re-matching
    it against the *whole* new pool - identity candidates included - measures
    the matcher on data where the answer is known, which is the only accuracy
    signal available without a client to test against.
    """
    total = hit = 0
    misses = []
    for x in identity:
        ranked = sorted(
            ((score_pair(old_cols[x], new_cols[y]), y) for y in new_cols),
            key=lambda t: (t[0][0], t[0][1], t[0][3]),
            reverse=True,
        )
        if not ranked or ranked[0][0][2] < MIN_INFORMATIVE:
            continue
        total += 1
        if ranked[0][1] == x:
            hit += 1
        else:
            misses.append("%s -> %s (%.3f)" % (x, ranked[0][1], ranked[0][0][0]))
    return {"tested": total, "recovered": hit, "misses": misses[:10]}


def self_check(results: dict) -> dict:
    """Aggregate the per-file identity probes into one accuracy figure."""
    total = hit = 0
    misses = []
    for fname, res in results.items():
        sc = res.get("self_check") or {}
        total += sc.get("tested", 0)
        hit += sc.get("recovered", 0)
        misses.extend("%s: %s" % (fname, m) for m in sc.get("misses", []))
    return {
        "clear_names_tested": total,
        "mapped_to_themselves": hit,
        "accuracy": round(hit / total, 4) if total else None,
        "misses": misses[:40],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", default="resources", help="baseline tree the server runs on today")
    ap.add_argument("--new", default="YuanShenResources", help="the new dump")
    ap.add_argument("--file", default=None, help="limit to one ExcelBinOutput file")
    ap.add_argument("--out", default="tools/out/field-map.json")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every mapping")
    args = ap.parse_args(argv)

    root = repo_root()
    old_root = resolve_under_repo(args.old, root) / "ExcelBinOutput"
    new_root = resolve_under_repo(args.new, root) / "ExcelBinOutput"
    for p in (old_root, new_root):
        if not p.is_dir():
            raise SystemExit("no ExcelBinOutput under " + str(p.parent))

    manifest_path = root / "tools" / "out" / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else {"excel_files": {}}
    wanted = set(manifest["excel_files"]) or {p.name for p in old_root.glob("*.json")}
    if args.file:
        wanted = {args.file}

    results: dict[str, dict] = {}
    for name in sorted(wanted):
        op, np_ = old_root / name, new_root / name
        if not (op.exists() and np_.exists()):
            continue
        keys = manifest["excel_files"].get(name, {}).get("primary_keys") or []
        res = map_file(op, np_, keys)
        if res:
            results[name] = res

    # The actionable question is not "where did alias X go" but "for each field
    # the Java code reads, what is it called in the new dump, and does the code
    # already accept that name". Anything answered "no" is a silent zero at runtime.
    fields_by_class = manifest.get("fields_by_class", {})
    accepted: dict[str, set[str]] = {}
    for name, spec in manifest["excel_files"].items():
        names: set[str] = set()
        for cls in spec["classes"]:
            info = fields_by_class.get(cls, {})
            names.update(info.get("clear", []))
            for alias_list in info.get("obfuscated_aliases", {}).values():
                names.update(alias_list)
        accepted[name] = names

    renames: list[dict] = []
    for fname, res in results.items():
        known = accepted.get(fname, set())
        for old_name, m in res.get("mappings", {}).items():
            if m["confidence"] not in ("high", "medium", "name-identity"):
                continue
            if m["new"] == old_name or m["new"] in known:
                continue
            if old_name not in known:
                continue  # the code never read this field anyway
            renames.append({
                "file": fname,
                "reads": old_name,
                "now_called": m["new"],
                "confidence": m["confidence"],
                "informative_agreement": m["informative_agreement"],
                "classes": manifest["excel_files"].get(fname, {}).get("classes", []),
            })
    renames.sort(key=lambda r: (r["file"], r["reads"]))

    tiers = Counter(
        m["confidence"]
        for res in results.values()
        for m in res.get("mappings", {}).values()
    )
    report = {
        "_generated_by": "tools/field_map.py",
        "old": str(old_root.parent),
        "new": str(new_root.parent),
        "files_compared": len(results),
        "confidence_tiers": dict(tiers),
        "self_check": self_check(results),
        "renames_the_code_must_learn": renames,
        "files": results,
    }
    out = resolve_under_repo(args.out, root)
    save_json(out, report, indent=2)

    sc = report["self_check"]
    print("compared %d files (%s -> %s)" % (len(results), old_root.parent.name, new_root.parent.name))
    print("confidence: " + ", ".join("%s=%d" % kv for kv in sorted(tiers.items())))
    print("self-check: %d/%d clear names mapped to themselves (%s)" % (
        sc["mapped_to_themselves"], sc["clear_names_tested"],
        ("%.1f%%" % (100 * sc["accuracy"])) if sc["accuracy"] is not None else "n/a"))
    if renames:
        print("\n%d field(s) the server reads were renamed in the new dump:" % len(renames))
        for r in renames:
            print("   %-46s %-22s -> %-22s (%s)" % (
                r["file"], r["reads"], r["now_called"], r["confidence"]))
        print("\nAdd each as a @SerializedName alternate before running on this dump.")
    else:
        print("no renames detected among the fields the server reads")

    if args.verbose:
        for fname, res in sorted(results.items()):
            ms = res.get("mappings", {})
            interesting = {k: v for k, v in ms.items()
                           if is_obfuscated(k) or v["new"] != k}
            if not interesting:
                continue
            print("\n-- %s (%d common rows) --" % (fname, res["common_rows"]))
            for k, v in sorted(interesting.items()):
                print("   %-14s -> %-14s %-16s inf=%.3f lift=%s" % (
                    k, v["new"], v["confidence"], v["informative_agreement"],
                    v["lift"] if v["lift"] is not None else "inf"))

    print("\nreport -> " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
