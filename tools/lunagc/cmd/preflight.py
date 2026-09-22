#!/usr/bin/env python3
"""Run every read-only check and print one readiness summary.

Two uses:

  before the drop   `python tools/preflight.py` records where 7.0 stands. The
                    numbers it prints are the baseline every later run is
                    compared against, so a regression is obvious instead of
                    being one line in a thousand-line log.

  after the drop    `python tools/preflight.py --dump YuanShenResources` adds the
                    dump-facing checks: what content arrived, which fields were
                    renamed, and whether the dump's TextMap can be used (it
                    could not for 7.0.5x, and almost certainly cannot for 7.1).

Nothing here writes to a resources tree or to src/. It is safe to run at any
time, including on a server that is up.

    python tools/preflight.py
    python tools/preflight.py --dump YuanShenResources
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lunagc import load_json, repo_root, resolve_under_repo  # noqa: E402

TOOLS = Path(__file__).resolve().parent


def run(script: str, *args: str) -> tuple[int, str]:
    cmd = [sys.executable, str(TOOLS / script), *args]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    took = time.time() - started
    label = script.replace(".py", "")
    mark = "ok  " if proc.returncode == 0 else "FAIL"
    print("  [%s] %-22s %5.1fs" % (mark, label, took))
    if proc.returncode != 0 and proc.stderr.strip():
        for line in proc.stderr.strip().splitlines()[-4:]:
            print("         " + line)
    return proc.returncode, proc.stdout


def out_json(root: Path, name: str) -> dict:
    p = root / "tools" / "out" / name
    try:
        return load_json(p) if p.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resources", default="resources")
    ap.add_argument("--dump", default=None, help="a new client dump to assess as well")
    ap.add_argument("--baseline", default="tools/out/baseline-7.0.json")
    ap.add_argument("--proto", default="src/main/proto")
    ap.add_argument("--proto-dump", default=None,
                    help="a new version's .proto folder to diff against")
    ap.add_argument("--names", default=None, help="nameTranslation.txt for --proto-dump")
    args = ap.parse_args(argv)

    root = repo_root()
    print("LunaGC migration preflight")
    print("repo      : %s" % root)
    print("resources : %s" % resolve_under_repo(args.resources, root))
    if args.dump:
        print("dump      : %s" % resolve_under_repo(args.dump, root))
    print()

    failures = []
    run("resource_manifest.py")
    baseline = resolve_under_repo(args.baseline, root)
    check_args = ["--resources", args.resources, "--json", "tools/out/check-latest.json"]
    if baseline.exists():
        check_args += ["--baseline", args.baseline]
    run("check_resources.py", *check_args)
    run("opcodes_audit.py", "--json", "tools/out/opcodes-latest.json")
    run("field_numbers.py", "--proto", args.proto)

    if args.proto_dump:
        run("proto_diff.py", "--old", args.proto, "--new", args.proto_dump,
            *(["--names", args.names] if args.names else []))

    if args.dump:
        run("field_map.py", "--old", args.resources, "--new", args.dump)
        run("textmap_audit.py", "--resources", args.resources,
            "--compare", str(Path(args.dump) / "TextMap"))
        run("content_report.py", "--base", args.resources, "--dump", args.dump)
        run("excel_convert.py", "--base", args.resources, "--dump", args.dump,
            "--out", "tools/out/_dryrun", "--dry-run")
        run("binout_convert.py", "--base", args.resources, "--dump", args.dump,
            "--out", "tools/out/_dryrun", "--dry-run")

    check = out_json(root, "check-latest.json")
    opcodes = out_json(root, "opcodes-latest.json")
    fmap = out_json(root, "field-map.json") if args.dump else {}
    tmap = out_json(root, "textmap-audit.json") if args.dump else {}
    content = out_json(root, "content-report.json") if args.dump else {}
    fnums = out_json(root, "field-numbers.json")
    pdiff = out_json(root, "proto-diff.json") if args.proto_dump else {}
    binout = out_json(root, "binout-convert.json") if args.dump else {}

    print("\n" + "=" * 62)
    print("READINESS")
    print("=" * 62)

    s = check.get("summary", {})
    line = "resources    %d ok / %d warn / %d FAIL" % (
        s.get("ok", 0), s.get("warn", 0), s.get("fail", 0))
    # A raw failure count is not the signal - the 7.0 tree already has one, and
    # calling it a blocker every run trains you to ignore the word. What matters
    # is whether anything got worse than the snapshot taken before the bump.
    if baseline.exists():
        base = out_json(root, Path(args.baseline).name)
        was = {e.get("file") or e.get("path")
               for sec in ("excel", "trees", "textmap") for e in base.get(sec, [])
               if e.get("status") == "fail"}
        now = {e.get("file") or e.get("path")
               for sec in ("excel", "trees", "textmap") for e in check.get(sec, [])
               if e.get("status") == "fail"}
        new_fails = sorted(now - was)
        line += "   (%d pre-existing, %d NEW)" % (len(now & was), len(new_fails))
        if new_fails:
            failures.append("new failing tables: " + ", ".join(new_fails[:5]))
    elif s.get("fail"):
        failures.append("resources tree has failing tables and no baseline to compare against")
    print(line)
    dead = len(opcodes.get("dead_handlers", {}))
    print("opcodes      %d declared, %d sentinels, %d dead handlers"
          % (opcodes.get("declared", 0), len(opcodes.get("sentinels", [])), dead))

    fc = fnums.get("counts", {})
    bad_nums = fc.get("ABSENT", 0) + fc.get("MISMATCH", 0)
    print("wire fields  %d hardcoded, %d match, %d suspect, %d without a proto"
          % (len(fnums.get("constants", [])), fc.get("match", 0), bad_nums,
             fc.get("no-proto", 0)))
    if bad_nums:
        # Not a blocker: 7.0 already ships one of these (HandlerPingReq's F_SEQ
        # reads _cur_fps). Blocking on a known, pre-existing defect every run is
        # how a readiness check stops being read. It is called out instead.
        print("             ^ see tools/out/field-numbers.json - a suspect number "
              "reads the wrong field")

    if pdiff:
        pc = pdiff.get("counts", {})
        print("protos       %d changed, %d RENUMBERED in server-used messages, %d gone"
              % (pc.get("changed", 0), pc.get("renumbered_used", 0), pc.get("gone", 0)))
        if pc.get("renumbered_used"):
            failures.append("%d server-used message(s) have renumbered fields"
                            % pc["renumbered_used"])

    if args.dump:
        sc = fmap.get("self_check", {})
        acc = sc.get("accuracy")
        renames = fmap.get("renames_the_code_must_learn", [])
        print("field map    %d rename(s) to teach the code; matcher self-check %s"
              % (len(renames), ("%.1f%%" % (100 * acc)) if acc is not None else "n/a"))
        wc = tmap.get("weighted_candidate")
        wb = tmap.get("weighted_baseline")
        if wc is not None and wb is not None:
            usable = "USABLE" if wc >= wb * 0.95 else "DO NOT SHIP"
            print("dump TextMap %.2f%% vs baseline %.2f%%  -> %s"
                  % (100 * wc, 100 * wb, usable))
        added = content.get("content", {})
        bits = ["%s +%d" % (k, len(v.get("added", []))) for k, v in added.items()
                if v.get("added")]
        if bits:
            print("new content  " + ", ".join(bits[:6]))
        if binout:
            skipped = binout.get("skipped_mismatched_naming", [])
            print("binout       %d file(s) graftable, %d subtree(s) skipped%s"
                  % (binout.get("files_added", 0), len(skipped),
                     " (" + ", ".join(s.split("/")[-1] for s in skipped) + ")" if skipped else ""))

    print()
    if failures:
        for f in failures:
            print("blocker: " + f)
    else:
        print("no blockers from the read-only checks")
    print("\nFull reports in tools/out/. Runbook: tools/MIGRATION.md")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
