# The two resource lineages, measured

Evidence behind the rules the tools enforce. Numbers here were measured on
2026-09-17 between `resources/` (LunaGC-Resources at 7.0) and `YuanShenResources/`
(YuanShenResources at `OSCBWin7.0.54`). Reproduce any of them with the tool named
beside it.

## What the two are

| | `resources/` | `YuanShenResources/` |
|---|---|---|
| origin | `github.com/capyb2222/LunaGC-Resources` | `gitlab.com/GuraFoundation/YuanShenResources` |
| shape | curated server tree | raw client dump |
| ExcelBinOutput | 128 files | 1935 files |
| BinOutput folders | 12 | ~130 |
| Scripts / ScriptSceneData / Server | yes | **no** |
| gitignored | yes (`/resources`) | no - see below |

The server reads 118 excel files and 12 BinOutput folders. The rest of a dump is
dead weight. `resource_manifest.py` derives that list from the Java source.

`YuanShenResources/` has no `Scripts/`, `ScriptSceneData/` or `Server/` at all. Those
have no source in a client dump and must keep coming from LunaGC-Resources.

## Rule 1 - never copy the dump's TextMap

`textmap_audit.py --compare YuanShenResources/TextMap`, on `nameTextMapHash` across
ten player-facing tables:

| table | hashes | current TextMap | dump's TextMap |
|---|---|---|---|
| ReliquaryExcelConfigData | 4352 | 93.80% | 0.00% |
| WeaponExcelConfigData | 281 | 93.24% | 0.00% |
| AvatarExcelConfigData | 165 | 83.64% | 0.00% |
| AvatarTalentExcelConfigData | 750 | 80.00% | 0.00% |
| MaterialExcelConfigData | 10404 | 71.20% | 0.00% |
| NpcExcelConfigData | 14060 | 55.87% | 0.01% |
| MonsterExcelConfigData | 3279 | 0.00% | 0.00% |
| GadgetExcelConfigData | 29870 | 0.00% | 0.01% |

Different hash space, not merely incomplete. The failure is silent - names render
blank and nothing logs.

Monster and Gadget at 0% on *both* sides is not a fault: those names were never in
TextMap. They drag the weighted average to 32%, which is why the per-table numbers
are what to read.

## Rule 2 - the dump's EXCEL tables are fine

This is the useful half, and it is easy to miss given Rule 1.

The dump's `nameTextMapHash` values resolve **138 of 167 (83%)** against the
*current* TextMap. So the excel tables on both sides share one hash space, and
only the dump's TextMap file is the outlier. That is what makes grafting work at
all - `excel_convert.py` can take rows straight from the dump and the existing
TextMap will name them.

The 17% that do not resolve are content newer than the 7.0 TextMap. New strings
have to come from LunaGC-Resources upstream, not from the dump.

## Rule 3 - server-only fields have to be carried over

The dump is what the *client* reads. `MonsterExcelConfigData` in the dump has 34
distinct keys against 62 in the server tree. Absent from the dump:

`affix`, `critical`, `criticalHurt`, `elementMastery`, `serverScript`,
`securityLevel`, `visionLevel`, `safetyCheck`, `canSwim`, `excludeWeathers`,
`hideNameInElementView`, `isAIHashCheck`, `isInvisibleReset`, `lodPatternName`,
`playType`, `radarHintID`, `skin`

Copy the dump over `resources/` and every monster loses these. This is the
"monsters with zeroed stats" failure. `excel_convert.py` grafts instead: existing
rows keep their values, and even `--mode merge` only fills keys that are absent.

## Rule 4 - obfuscated field names rotate every version

Same file, same rows, different symbols:

```
AnimalCodexExcelConfigData, id 584 rows in common
  resources/       ABOAGOHLBID  LICBOMOOAFI  LJMGOHDNFMO  MPNCFOBMIDN  OHCCAFLBIAJ
  YuanShenResources/   NDEAAFCAJBG  NAFICAKIEPK  JKCEPDGBLDI  HLPCNBJPNIF  PPLEAMKAAGP
```

The Java classes pin **117** such symbols by hand in
`@SerializedName(alternate = {...})`. Every one is stale the moment a version
lands, and a stale alias does not throw - Gson leaves the field at zero.

Names cannot be matched to names, so `field_map.py` matches columns by their
values across rows sharing a primary key. Two details make it work:

- It scores agreement on rows where the old column departs from *its own modal
  value*. Scoring on all rows makes every mostly-`false` column match every other
  mostly-`false` column.
- Fields whose name survived verbatim are settled first and removed from the
  candidate pool. Leaving them in let a rare boolean outscore a column's real
  owner, producing silent swaps - `mpPropID` and `combatBGMLevel` traded places in
  an early version of the matcher.

Self-check on the 7.0 -> 7.0.54 pair: **977 of 1012 (96.5%)** known field names
recovered from values alone. That figure is printed on every run and is the only
accuracy signal available without a client.

## Value encoding

Two more differences `lunagc/jsonio.py` normalises:

- The dump writes 64-bit hashes as JSON **strings**; the server tree writes them
  as **numbers**. `"13736895301956342668"` vs `13736895301956342668`.
- The dump is a proto3 JSON dump, so it **omits default-valued fields entirely**,
  including inside nested objects: an empty drop slot is `{}`, not
  `{"dropId": 0, "hpPercent": 0}`.

Gson tolerates the second (absent reads as 0). `excel_convert.py` fixes the first
when it grafts a row.

## Rule 5 - BinOutput only grafts where filenames correspond

The server reads 12 BinOutput subtrees. Most of them name files the same way in
both trees, so "files the base does not have" really is a list of new content:

| subtree | base | dump | base names found in dump |
|---|---|---|---|
| Ability/Temp | 1295 | 1319 | 100% |
| Avatar | 357 | 466 | 100% |
| Quest | 2917 | 4421 | 100% |
| Scene/Point | 1872 | 2378 | 100% |
| Talent/AvatarTalents | 126 | 129 | 100% |
| Gadget | 458 | 683 | 91% |
| **Monster** | 742 | 3427 | **0%** |
| **LevelDesign/Routes** | 385 | 443 | **0%** |

The two at 0% are not missing content, they are a different naming scheme:

```
base   BinOutput/Monster/ConfigAnimal_Alpaca_01.json
dump   BinOutput/Monster/Aahigaru_Male_01_25050301.json      <InternalName>_<id>

base   BinOutput/LevelDesign/Routes/0fe0d2d4.json            32-bit hash, hex
dump   BinOutput/LevelDesign/Routes/10000907580012788799.json 64-bit hash, decimal
```

A set difference there would add 3427 monster files duplicating what is already
present under other names. `binout_convert.py` measures the overlap and skips any
subtree below 50%, naming it in the output. Those keep coming from
LunaGC-Resources.

Two more subtrees have no counterpart in the dump at all and must be preserved:
`BinOutput/Scene/SceneNpcBorn`, and the single file
`BinOutput/AbilityGroup/AbilityGroup_Other_PlayerElementAbility.json`.

## Rule 6 - ability configs need no repair

Worth recording because it looks like it should. The base tree's ability configs
open with `"$type": "ConfigAbility"` and the dump's do not, which reads like a
missing polymorphic discriminator. It is not: `AbilityData` declares no `$type`
field, so Gson ignores it on the way in either way. The `$type` values that *do*
drive deserialisation, on modifier actions and mixins, are present in both (2561
`ByTargetGlobalValue`, 1860 `SetGlobalValue` and so on in the dump).

The only dump-side noise worth removing is `__unk_Q<digits>` keys, artefacts of
its own deobfuscation pass. `binout_convert.py` strips them.

## Housekeeping

`YuanShenResources/` is an embedded git clone that was **not** gitignored, so
`git add -A` would have swept it into the repo. Added to `.gitignore` alongside
the existing `YuanShenResources/` entry, which covers the same upstream under a
different folder name.
