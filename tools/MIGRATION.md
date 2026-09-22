# 7.0.0 -> 7.1.0 migration runbook

Written 2026-09-17, before 7.1 dropped, so that release day is running tools
rather than writing them. Every tool here has been exercised against the 7.0.54
dump sitting in `YuanShenResources/`, so the commands below are known to work - what
is unknown is only what 7.1 itself will contain.

Run every step from the repo root.

---

## Before you start

Two things are worth knowing up front because they change how much work this is.

**7.0.5x resources are expected to work on 7.1.** Per the LunaGC owner, a 7.0.5x
resources tree runs on a 7.1 client. That means the server can be up on 7.1
*before* any resource work is done - the bump in Step 2 plus a rebuild may be
enough for a playable server. Everything from Step 4 onward is about getting 7.1
*content* in, not about getting the server to run. Sequence it that way if the
drop is time-pressured.

**`YuanShenResources/` is the dump this runbook reads.** It is a clone of
`gitlab.com/GuraFoundation/YuanShenResources`; the 7.1 migration was run against
`OSRELWin7.1.0`. It was called `7.1-Resources/` when this was written and that
folder is gone. Check the build string before trusting it - the mirror publishes
on release day, and `animegamedata2/` beside it is the **CN** line (`CNRELWin`),
which is not interchangeable. Step 4 starts with pulling it.

---

## Step 0 - snapshot 7.0 while it still works

Do this *before* anything changes. Every later comparison is against these files.

```
python tools/lgc.py resources manifest
python tools/lgc.py resources check --json tools/out/baseline-7.0.json
python tools/lgc.py opcodes audit  --json tools/out/opcodes-7.0.json
```

Known state of 7.0 as of writing, so a later run is comparable:

| check | 7.0 value |
|---|---|
| resources | 152 ok / 9 warn / 1 fail |
| opcodes | 2144 declared, 87 sentinels, 35 dead handlers |
| wire fields | 13 hardcoded, 5 match, 1 suspect, 7 without a proto |
| the 1 fail | `AvatarReplaceCostumeExcelConfigData` - pre-existing, see `notes/findings.md` |
| the 1 suspect | `HandlerPingReq.F_SEQ` reads `_cur_fps` - pre-existing, same file |

Capture the wire-field and opcode numbers too:

```
python tools/lgc.py opcodes field-numbers --json tools/out/field-numbers-7.0.json
```

Commit the branch before you touch anything:

```
git switch -c 7.1.0
```

---

## How long did last time take?

From this repo's own history, cross-referenced against when the 7.0 client
actually landed on this machine (`GenshinImpact.exe`, Aug 11 16:38):

| when | what |
|---|---|
| 08-11 16:38 | **7.0 client lands** |
| 08-11 18:37 | `feat(net): read 7.0 clients and name the packets nothing handles` - two hours later |
| 08-13 20:52 | `feat(net): de-obfuscated 7.0 protos and a derived opcode table` |
| 08-13 20:53 | `feat(net): complete the 7.0 login handshake` |
| 08-14 19:10 | `feat(net): name unhandled opcodes by their field numbers` |
| 08-15..20 | abyss, gacha, dailies, `regen the proto set for 7.0` |

**Release to working login: two days.** Work started the same afternoon the client
updated, so day zero was not idle - it was the no-dump bootstrap, and the protos
arrived on day two.

Two of those commits are now tools. `name the packets nothing handles` and `name
unhandled opcodes by their field numbers` are what `lgc.py opcodes recover` does, and
`de-obfuscated protos and a derived opcode table` is `lgc.py proto names` +
`lgc.py proto import` + `lgc.py opcodes gen`. So the two days were mostly spent on the
part that is now three commands - which is the reason to expect 7.1 to be faster
than 7.0 was, not slower.

Two things to notice. The 08-11 commit - naming "the packets nothing handles" -
is the log-based bootstrap that `lgc.py opcodes recover` now automates. And 08-13's
"de-obfuscated protos and a derived opcode table" is what `lgc.py proto names` +
`lgc.py proto import` + `lgc.py opcodes gen` now do in three commands.

So the tooling in this folder is a rebuild of the path that already worked,
which is the reason to expect 7.1 to go faster than 7.0 did rather than slower.

---

## Can this be done without a proto dump?

Partly, and the split matters more than a yes or no.

**This has already been done here, twice.** The repo says so itself.
`GameServerPacketHandler.handle` carries:

> This used to announce every opcode that arrived along with a dump of its
> fields, **which is how the 7.0 inbound map was recovered from a single client
> launch.**

and `ProtoSniff`:

> That is how the 6.7 handshake was mapped, and it is the only mapping method
> that needs nothing but a client willing to connect.

So "no dump means no migration" is not what the history shows. What the history
shows is a split:

| | without a dump | why |
|---|---|---|
| **inbound** CmdIds (Req/Notify the client sends) | recoverable | the client puts them on the wire; the server already logs every unhandled one with its field numbers |
| inbound **field numbers** | recoverable | `ProtoSniff` reads them straight out of the bytes |
| **resources** | already fine | 7.0.5x tables work on 7.1 per the owner |
| **outbound** CmdIds (Rsp/Notify the server sends) | not recoverable **from a capture** | they never appear in an inbound log. They DO come out of a binary dump - see below |

That last row looks like the end of the road, and it is not. A client will not
finish logging in until `GetPlayerTokenRsp` and `PlayerLoginRsp` go back under
the right numbers, and no capture will show you those. But *"the client silently
ignores a wrong one"* is not only the problem - it is the way out.

### The dump does not have to be complete

It does not even have to exist. 7.0 shipped with 87 names on sentinels and 35
dead handlers and was perfectly playable, so the target is not a full opcode
table - it is the path from "client connects" to "standing in the world".

```
python tools/lgc.py opcodes critical-path
```

Derived from the source: the state guards in `GameServerPacketHandler`, the
handshake order `PacketOpcodes.java` already lists, and what each gate handler
replies with. The answer is **25 opcodes out of 2143**:

| | count | how to get them |
|---|---|---|
| inbound | 10 | free - the client sends them, the log names them |
| outbound, tier 1 | 3 | `GetPlayerTokenRsp`, `PingRsp`, `PlayerLoginRsp` - authenticates the session |
| outbound, tier 2 | 12 | the enter-scene chain - loads the world |

Three sweeps to authenticate, twelve more to get into the world. At roughly 25
logins each that is a couple of hours for tier 1 and a day for tier 2 - against
two to three weeks of waiting for a dump.

Do them in tier order. Each one you find moves the client further, and that
progress is the signal for the next.

### The client is an oracle

This repo already used it. From the watermark commit:

> sweep takes a list of `cmdId:payloadField` candidates and sends the watermark
> under each in one login ... **That is how 1242 was found.** The client ignores
> a CmdId it does not know, so the wrong ones are inert.

Send a packet under many candidate numbers at once and the client either reacts
or does not. One bit per login - which is all binary search needs.

```
python tools/lgc.py opcodes sweep start GetPlayerTokenRsp --max 30000 --exclude-known
# paste the emitted list into the sweep config, start the server, try to log in
python tools/lgc.py opcodes sweep hit  GetPlayerTokenRsp    # client reacted
python tools/lgc.py opcodes sweep miss GetPlayerTokenRsp    # it did not
```

Measured on a simulated search of a 28264-wide space: **25 logins to pin one
number**, each round exactly one login with one yes/no answer. Only a handful of
outbound opcodes are needed to reach the world, so this is a day of work, not a
fortnight of waiting.

Two honest caveats. The technique is proven here for one packet, not for the
login pair. And the watermark config refuses to sweep 8191/9250 because a *Lua
payload* under a login opcode breaks login - sweeping the *correct* payload under
candidate numbers is the opposite case, but sweep one packet type at a time and
keep the payload the real one.

**So: day-one login without a dump is not guaranteed. Day-one preparation is.**
Everything above that line can be done the hour the client updates, which is most
of the work and all of the slow part.

### Getting a dump yourself

The descriptors are in `GameAssembly.dll`, and the chain to pull them out is:

    running game process
      -> Hiro420/Il2CppRuntimeDumper          (inject; dump.cs + DummyDll)
         or Il2CppInspectorRedux / Perfare/Il2CppDumper, IF you have decrypted
         metadata
      -> Hiro420/Il2CppProtoDescriptorDumper  (emulates the descriptor init
                                               codepaths in a custom x64 VM,
                                               recovers protobuf FileDescriptors)
      -> Hiro420/ProtoDescDump                (descriptors -> .proto files)

Hiro is credited in `resources/README.md`, so this is from inside the same
ecosystem.

**Why the runtime dumper, and not the obvious one.** Perfare/Il2CppDumper is the
standard tool and it takes `GameAssembly.dll` + `global-metadata.dat`. Its README
says plainly: *"Sometimes games may obfuscate this file for content protection
purposes... Deobfuscating of such files is beyond the scope of this program."*
That is exactly the wall here - the metadata on disk is encrypted, so a static
dumper has nothing to read. A **runtime** dump does not care: by the time the
game is running, the metadata has been decrypted in memory. That is what
`Il2CppRuntimeDumper` is for, and why it is worth trying despite its author
calling it *"very bad... if you want something good, then don't use it."*
The same idea on rooted Android is `Zygisk-Il2CppDumper`. Use the
`codehasan/Zygisk-Il2CppDumper` fork - it is the modernised one (Gradle 9, NDK 28,
KernelSU/APatch, dynamic package targeting) and it states outright that it dumps
*"past encryption, obfuscation, and packing"*, which is the whole problem. Needs
Magisk v24+/KernelSU/APatch with Zygisk enabled, and the Android build of the
game. The protocol is the same as PC, so a dump from Android is usable for a PC
server.

Note it writes `dump.cs`, while the descriptor dumper wants script.json + dummy
DLLs. The usual bridge is to recover the *decrypted* metadata at runtime and then
run the static dumper on that properly. Expect some glue work here; this is the
least-trodden step in the chain.

So: try the static route first if you have decrypted metadata from anywhere, and
fall back to injection. Either way the output feeds the same descriptor dumper.

Caveats from the descriptor dumper's README, worth reading before budgeting time
around it: the descriptors "must not be stripped from their string literals",
and it has been tried on "very few binaries" with "results may vary". Treat it as
promising, not guaranteed.

`nitrog0d/ProtoDumper` is the wrong tool for this. It reads .NET assembly
metadata with Mono.Cecil, and it says outright that it "requires unobfuscated
assemblies". Genshin is IL2CPP and partly obfuscated, so it would need
Il2CppDumper output first and would still choke on the obfuscated half. The
Hiro420 chain supersedes it.

### Can you dump the PC client yourself? Checked - no

Measured on this machine's install (`game_version=7.0.0`, OS line) on 2026-09-17:

```
GenshinImpact_Data/Native/Data/Metadata/global-metadata.dat   80 MB
  first bytes: 4d 48 59 00   = "MHY" + a NUL
  a readable global-metadata.dat starts with af 1b b1 fa (0xFAB11BAF)
```

The metadata is encrypted with mihoyo's own container, which is precisely what
Il2CppDumper's README calls out of scope. There is also no separate
`UserAssembly.dll` - the IL2CPP code is packed inside the 430 MB
`GenshinImpact.exe`. So every *static* dumper fails at step one, on this install,
today. That is measured, not assumed.

Runtime dumping is what gets past it, and on PC that means working around
`HoYoKProtect.sys` (a kernel driver) and `mhypbase.dll`, both shipped in the game
folder. That is a different and much harder problem than proto extraction, and it
puts the account at risk.

The practical route is the **Android** build with a rooted device and
`codehasan/Zygisk-Il2CppDumper`, which reads the metadata after the game has
decrypted it in memory. Same protocol as PC, so the output is usable here.

So in order of effort:

1. **Obtain an OS dump.** Expect **2-3 weeks**, not days: the fast-moving dumps
   are CN beta (`CNCBWin`), and the `OSCBWin` set the global line needs lags well
   behind. Plan around that rather than waiting on it.
2. **Dump from rooted Android yourself.** Full independence, needs the hardware.
3. **Bootstrap without protos** - `lgc.py opcodes recover`, inbound only.

### Dress rehearsal, worth doing before release day

You have the OS 7.0 client and OS 7.0 protos, so a 7.0 OS dump is a test with a
known answer. Run the whole chain on it and check `lgc.py opcodes gen` reproduces the
`PacketOpcodes.java` already in the tree. If it does, release day is mechanical.

### If the dump comes out obfuscated

That is fine, and it is the case the rest of this kit was built for. A dump whose
every message is called `ABCDEFGHIJK` still carries field numbers, field types and
CmdIds, and **shape is close to a primary key**:

    all messages     70% have a unique structural signature
    >= 3 fields      95%
    >= 5 fields     100%
    >= 10 fields    100%

```
python tools/lgc.py proto eval --all-pairs                     # prove it first
python tools/lgc.py proto names --new <dumped-protos>          # derive the names
python tools/lgc.py proto import --dump <dumped-protos> --names tools/out/nameTranslation.txt
```

On a worst-case test - this repo's own protos with **every** name obfuscated but
field numbers held fixed - that recovered 1417 of 1737 with zero wrong.

**That test flatters it.** Across a real version bump the field numbers move too,
and the handlers here record it:

    F_KEY_ID          = 588;   // 6.7: 41
    F_CLIENT_RAND_KEY = 932;   // 6.7: 1475

The high scattered numbers are exactly what makes a big message identifiable, and
they are re-randomised every version. Tried against a genuinely different build
(CNCBWin7.0.50 against this repo's OS 7.0) it resolved 542 of 1737, with the
fuzzy half unverified.

So this closes the gap when the dump is the RIGHT version and only its names are
missing. It does not let a 7.0 proto set stand in for a 7.1 one. Read the
exact/fuzzy split it prints: a low `exact` count means the numbers moved and the
remainder is guesswork.

### Make sure the dump is the right build line

A dump is identified by its build, e.g. `CNCBWin7.0.50` or `OSCBWin7.0.54`.
`CNCBWin` is the China closed beta; `OSCBWin` is the global line this server
targets, and it is what `resources/` tracks. **They are not interchangeable.**
Checked on a real CN 7.0.50 dump, only 7.8% of its 4911 CmdIds appear in this
repo's OS 7.0 table - which is what two independent random assignments over the
same range would give you. Field numbers differ the same way.

Before spending time on any dump, confirm the build string in its header matches
the line your client is on.

### Do this first - it takes five minutes and may end the whole question

Before assuming anything reshuffled, find out whether it did:

```
# 1. bump the version, rebuild, start the server
python tools/lgc.py release bump --to 7.1.0 --apply
./gradlew jar -PskipHandbook=1

# 2. point the updated client at it and try to log in
# 3. then read the log
python tools/lgc.py opcodes audit
```

- **Client logs in** - CmdIds did not reshuffle. You are essentially done; go
  straight to Step 4 for content.
- **Client stalls, and the log is quiet** - the handshake opcodes are unchanged
  but something later is. Walk features one at a time.
- **Log fills with `arrived and nothing handles it`** - the table reshuffled.
  That log is the input to the next step.

Minor versions do not always reshuffle CmdIds. Spending five minutes to check
beats spending a day assuming.

### If it did reshuffle and no dump exists yet

```
python tools/lgc.py opcodes recover --log logs/latest.log --handshake --patch
```

It fingerprints each unhandled packet by its field numbers and matches against
`src/main/proto`. It only commits to a name when the message has 10+ declared
fields and a clear margin - which is where it is 92% accurate - and prints a
ranked shortlist for everything else rather than guessing. On a simulated 7.1
session it identified GetPlayerTokenReq and PlayerLoginReq with zero wrong
answers.

That gets the inbound side moving. For the outbound side you are waiting on a
dump, and the honest expectation is days, not hours. Meanwhile every resource
tool in this kit runs against the 7.1 tables the moment they land, so none of
that waiting is idle.

---

## Step 1 - the proto dump

This is the long pole, and the one input no tool here can produce: the 7.1
protos have to come from a dump of the 7.1 client, along with its
`nameTranslation.txt`.

### 1a - import the protos

```
# no nameTranslation.txt? derive one first:
python tools/lgc.py proto names --new <7.1-proto-dump>

python tools/lgc.py proto import --dump <7.1-proto-dump> --names <nameTranslation.txt>
```

It rewrites all 1816 files into this repo's conventions in one pass - message and
enum names, every reference in a field type, `import` lines, file names,
`java_package` / `java_outer_classname`, and the line endings the tree already
uses. Untranslated symbols are left alone on purpose; the repo already carries
394 of them and an invented name is worse than a symbol.

It stages to `tools/out/proto-staged/` so you can look before you leap. Verified
idempotent: re-importing the repo's own 1816 protos reproduces them byte for byte.

### 1b - see what moved

```
python tools/lgc.py proto diff --old src/main/proto --new tools/out/proto-staged
```

**`RENUMBERED, server-used` is the number that matters.** Protobuf carries field
numbers, not names, so a renumbered field does not throw - the generated class
treats the old number as unknown and every getter returns a default. The server
then runs on zeros. `HandlerPingReq` carries a comment about exactly this
happening in 7.0.

Work through that list before trusting anything. Then:

```
python tools/lgc.py proto import --dump <7.1-proto-dump> --names <nameTranslation.txt> --write
python tools/lgc.py opcodes field-numbers
```

`lgc.py opcodes field-numbers` checks the 13 hardcoded `F_*` wire offsets in handlers against
the freshly imported protos. Anything marked `!` or `!!` is reading a number that
no longer means what its name says. (7.0 already ships one: see
`notes/findings.md`.)

### 1c - CmdIds

CmdIds are reshuffled wholesale every version. A 7.0 number left in place for 7.1
does not error - it lands on some unrelated 7.1 opcode and routes that packet to
the wrong handler, where a state guard drops it silently. That is why every name
with no 7.1 entry gets a distinct negative sentinel instead.

```
python tools/lgc.py opcodes gen --dump <7.1-proto-dump> \
                            --names <nameTranslation.txt> \
                            --version 7.1 --prev 7.0
```

It prints to stdout so you can read it first. Add `--write` when it looks right.
It accepts all three shapes a dump uses to carry a CmdId (`enum CmdId { CMD_ID = N; }`,
`option (cmdid) = N;`, `// CmdId: N`), or `--map` with a plain JSON `{name: id}`.

If it reports *most CmdIds unchanged*, the dump is for the version you are already
on. Stop and get the right one.

Then:

```
python tools/lgc.py opcodes audit
```

Compare `dead handlers` against the 35 in Step 0. Every new dead handler is a
feature that will silently stop working. That number is the best single measure
of how complete the CmdId table is.

---

## Step 2 - bump the version

```
python tools/lgc.py release bump --to 7.1.0            # review
python tools/lgc.py release bump --to 7.1.0 --apply
./gradlew jar -PskipHandbook=1
```

Three surfaces, and they have to agree: `GameConstants.VERSION`,
`GameConstants.VERSION_PARTS`, `build.gradle`. Prose mentions of 7.0 are reported,
not rewritten - a comment explaining what 7.0 did stays true.

**At this point, try the server.** If 7.0.5x resources really do work on 7.1, you
have a running server here, and everything below is content work you can do
without pressure.

---

## Step 3 - patch and keys

- Rebuild the patch: `cd patch && cargo build --release`
- Deploy `patch/target/release/ext.dll` as `Astrolabe.dll` **in the game root**,
  not `GenshinImpact_Data/Plugins` - the README is wrong about this. The Plugins
  copy is the game's own 2.6 MB DLL; the patch is ~622 KB. Back up the existing
  root DLL first, and deploy with the game closed.
- `src/main/resources/keys/` only needs attention if 7.1 rotates the dispatch
  key or RSA keys. Nothing here can detect that offline - a failed login with a
  version mismatch is the signal.

---

## Step 4 - pull the real 7.1 resources

```
cd YuanShenResources && git pull && git log --oneline -1 && cd ..
```

Confirm the tag says 7.1 and not `OSCBWin7.0.x` before going further.

---

## Step 5 - work out what the dump renamed

The client obfuscates most excel field names into 11-letter symbols and reshuffles
them every version. The Java classes pin 117 of them by hand in
`@SerializedName(alternate = {...})`, and a stale one fails silently - Gson just
leaves the field at zero.

```
python tools/lgc.py resources map --old resources --new YuanShenResources
```

It matches columns by their *values*, not their names, so it does not care what
the symbols are called. Read two numbers from the output:

- **self-check** - it re-derives field names it already knows and reports how
  often it got them right. 96.5% on the 7.0.54 pair. If 7.1 comes out much lower,
  trust the individual answers less and review them by hand.
- **renames the code must learn** - 22 on the 7.0.54 pair.

Then teach the code, appending rather than replacing so the same jar still loads
the old tree:

```
python tools/lgc.py resources rename                  # review the diff
python tools/lgc.py resources rename --apply
./gradlew compileJava -PskipHandbook=1
```

Use `--min-confidence high` if the self-check came out poor.

---

## Step 6 - build the 7.1 resources tree

Two halves, into one folder:

```
python tools/lgc.py resources excel  --dump YuanShenResources --out resources-7.1
python tools/lgc.py resources binout --dump YuanShenResources --out resources-7.1
```

Neither writes over `resources/`. `excel_convert` clones the base tree
(hardlinked, so a gigabyte-plus tree builds in about 30 seconds) and grafts in
only what the dump genuinely adds; `binout_convert` then adds the BinOutput files
the base does not have. Both are add-only and touch no existing row or file,
which is what makes them safe to run on release day.

`binout_convert` will **skip** any subtree whose filenames do not correspond
between the two trees, and say so. On the 7.0.54 dump that is
`BinOutput/Monster` and `BinOutput/LevelDesign/Routes` - the base names monsters
`ConfigMonster_*.json` while the dump names them `<InternalName>_<id>.json`, so a
set difference would add 3427 files duplicating what is already there. Those keep
coming from LunaGC-Resources. Expect the same on 7.1.

Then validate, which is the whole point of Step 0:

```
python tools/lgc.py resources check --resources resources-7.1 \
                                --baseline tools/out/baseline-7.0.json
```

`REGRESSED`, `SHRANK` or `DROPPED` lines mean stop and look. `no change` plus the
new rows means the graft is clean.

Point `config.json` at `resources-7.1` rather than moving folders around. Rolling
back is then one line of config, and the jar still loads the old tree because
Step 5 only ever appended alternates.

### Do not copy the dump's TextMap

```
python tools/lgc.py resources textmap --compare YuanShenResources/TextMap
```

On the 7.0.54 dump this reports **0.00%** - the dump's TextMap is a different hash
space and resolves nothing the server asks it. The failure is silent: names just
render blank. Keep the TextMap you already have and take new strings from
LunaGC-Resources upstream instead.

The baseline's own weighted number is 32%, which looks alarming and is not: Monster
and Gadget names were never in TextMap at all and drag the average down. Judge by
the per-table deltas, not the absolute figure. Avatar 84%, Weapon 93%, Reliquary
94% are the ones that matter.

---

## Step 7 - new content

```
python tools/lgc.py resources content --dump YuanShenResources --markdown
```

New avatars need more than data. Their ability names have to be in
`GameConstants.DEFAULT_ABILITY_STRINGS` (and `DEFAULT_TEAM_ABILITY_STRINGS` where
the kit has a team passive), or the character loads and behaves wrongly. The
report locates each new avatar's `ConfigAbility_Avatar_*.json` in the dump so you
can read the ability names straight out of it.

Also regenerate the handbook once the tables are in place:

```
./gradlew generateHandbook
```

---

## Step 8 - final pass

```
python tools/lgc.py release preflight --dump YuanShenResources
```

Compare against Step 0. What you want to see:

- resources: no new `FAIL`
- opcodes: dead handlers at or below 35
- protos: `RENUMBERED, server-used` at 0 - every one handled
- wire fields: `suspect` at 0, or each one understood
- field map: every rename applied
- dump TextMap: still reported `DO NOT SHIP`, and you did not ship it

Then build, run, and log in.

---

## If it goes wrong

| symptom | first thing to check |
|---|---|
| client rejects the version | `GameConstants.VERSION_PARTS` disagreeing with `VERSION` |
| logs in, world is empty | `lgc.py resources check` on the tree `config.json` actually points at |
| monsters have no stats | the dump was copied over `resources/` instead of grafted - rebuild with `lgc.py resources excel` |
| every name is blank | the dump's TextMap got copied in; restore the old `TextMap/` |
| one feature silently dead | `lgc.py opcodes audit`, look for it under dead handlers |
| a field reads as 0 | `lgc.py resources map` for that table; the alias is stale |
| a packet parses but every value is 0 | `lgc.py proto diff` - the field was renumbered |
| login or ping misbehaves | `lgc.py opcodes field-numbers` - a hardcoded wire offset went stale |

Rollback is always: point `config.json` back at `resources/` and redeploy the
previous jar. Nothing in this runbook modifies `resources/` or the old jar.
