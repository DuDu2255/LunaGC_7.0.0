# 7.1 release day

The short sequence. [MIGRATION.md](MIGRATION.md) is the reference and explains
why each step exists; this is the order to do them in when the clock is running.

Goal for day 0: **logged in**. Goal for day 1: **standing in the world**. The
proto dump is a cleanup pass that lands weeks later, not a gate.

---

## Before the day (do this now)

**1. Snapshot 7.0 while it still works.**

```
python tools/lgc.py release preflight
```

Baseline to beat: 152 ok / 9 warn / 1 fail, 2144 opcodes, 35 dead handlers.

**2. Rehearse the sweep against a known answer.** This is the one thing that
decides whether day 0 works, and it is testable today.

On your working 7.0 server, `GetPlayerTokenRsp` is 6000. Pretend you do not know
that and let the sweep find it:

```
git apply tools/patches/cmdid-sweep.patch
./gradlew jar -PskipHandbook=1
python tools/lgc.py opcodes sweep start GetPlayerTokenRsp --max 30000 --exclude-known
# paste the emitted cmdIdSweep block into config.json, restart, try to log in
python tools/lgc.py opcodes sweep hit|miss GetPlayerTokenRsp    # repeat ~25 times
```

- **Converges on 6000** - the technique works on the packet that matters, and
  day 0 is real. Note how long one round takes; multiply by 25.
- **Does not** - you have found that out with days to spare instead of on the
  day. Fall back to waiting for a dump and plan accordingly.

Either way, `git checkout -- src` afterwards.

**3. Prove the patch still builds and injects.** This is the dependency everything
else sits on: if the client cannot reach the server, no amount of opcode work
matters. Verified working 2026-09-17 - submodule fetches, `cargo build --release`
takes ~10s, produces `ext.dll` at ~593 KB.

```
git submodule update --init patch
cd patch && cargo build --release && cd ..
# deploy target/release/ext.dll as Astrolabe.dll in the GAME ROOT
#   C:\Program Files\HoYoPlay\games\Genshin Impact Games\Astrolabe.dll
#   NOT GenshinImpact_Data/Plugins - that is the game's own 2.6 MB DLL.
#   Back up the existing root DLL first, and deploy with the game closed.
```

It is `hk4e-patch-universal`, which finds its hooks by **byte-pattern scanning**
rather than hardcoded addresses - that is why it claims "6.5 and forward" and why
it has a fair chance of surviving a minor bump untouched. Two things can still
break on 7.1:

- a pattern stops matching, e.g. `MHYRSA_PERFORM_CRYPTO_ACTION` in
  `src/modules/security.rs`
- the `Astrolabe_*` export list in `patch/build.rs`, which its own comment says is
  "taken from the 7.0 client", no longer matches what 7.1 exports

Both announce themselves: the patch logs each hook it resolves. On day 0, launch
once and read that log before touching anything else.

**4. The dump pipeline is already rehearsed** - it was run against 7.0, where
the answer is known, and it reproduced `PacketOpcodes.java` exactly: **1740
opcodes identical, 0 contradicted**. Re-run it any time with:

```
python tools/lgc.py opcodes gen --dump tools/reference/GenshinImpact-OSRELWin7.0.0.proto                             --names tools/reference/nameTranslation.txt --version 7.0
```

**4b. The translation no longer has to be waited for.** `lgc.py proto names` derives
it from the dump plus the protos you already have. Graded on five real version
boundaries it runs at 96-98% precision; run end to end on 7.0 from 6.7 protos
alone it produced 3363 entries that are **98.7% correct** against the
community's own file. Prove it before you lean on it:

```
python tools/lgc.py proto eval --all-pairs
```

That replays 6.3->6.4 ... 6.7->7.0 and scores every answer, including the 13
login-path packets separately.

---

## Day 0

### 1. Patch first - it gates everything (minutes)

```
cd patch && cargo build --release && cd ..
# deploy ext.dll as Astrolabe.dll in the game ROOT, game closed
```

Launch the client once and read the patch's log. If a pattern failed to resolve,
fix that before anything else - without the patch the client never reaches your
server and every later step has nothing to observe.

### 2. Bump and build (minutes)

```
git switch -c 7.1.0
python tools/lgc.py release bump --to 7.1.0 --apply
./gradlew jar -PskipHandbook=1
```

### 3. Find out whether anything actually moved (5 minutes)

Start the server, point the updated client at it, try to log in.

- **It logs in** - CmdIds did not reshuffle. Skip to step 6. You are done.
- **It does not** - read the log and continue.

```
python tools/lgc.py opcodes audit
```

### 4. Take the free half (minutes)

Every packet the client sent is in the log with its field numbers.

```
python tools/lgc.py opcodes recover --log logs/latest.log --handshake --patch
```

Paste what it is confident about into `PacketOpcodes.java`. It will name the big
messages and shortlist the small ones; do not force the ambiguous ones.

### 5. Sweep the three that unblock login (a few hours)

```
git apply tools/patches/cmdid-sweep.patch
python tools/lgc.py opcodes critical-path          # confirms the list
```

In order - each one you find moves the client further, which is the signal for
the next:

1. `GetPlayerTokenRsp`
2. `PlayerLoginRsp`
3. `PingRsp`

```
python tools/lgc.py opcodes sweep start GetPlayerTokenRsp --max 30000 \
       --exclude-known --exclude-inbound tools/out/opcodes-recover.json
# config, restart, try to log in, then:
python tools/lgc.py opcodes sweep hit|miss GetPlayerTokenRsp
```

~25 rounds each. Put each number into `PacketOpcodes.java` as you find it, and
clear `cmdIdSweep` before moving to the next packet.

### 6. Resources

7.0.5x tables are expected to work on 7.1, so this is not urgent on day 0. When
the 7.1 dump lands in `YuanShenResources/`:

```
cd YuanShenResources && git pull && cd ..     # confirm the tag says 7.1
python tools/lgc.py resources map --old resources --new YuanShenResources
python tools/lgc.py resources rename --apply
python tools/lgc.py resources excel  --dump YuanShenResources --out resources-7.1
python tools/lgc.py resources binout --dump YuanShenResources --out resources-7.1
python tools/lgc.py resources check --resources resources-7.1 \
       --baseline tools/out/baseline-7.0.json
```

Point `config.json` at `resources-7.1`. **Do not copy its TextMap** - different
hash space, verify with `lgc.py resources textmap`.

---

## Day 1 - into the world

The remaining twelve outbound opcodes, same sweep loop:

```
PlayerEnterSceneNotify   EnterSceneReadyRsp    SceneInitFinishRsp
EnterSceneDoneRsp        PostEnterSceneRsp     GetSceneAreaRsp
GetScenePointRsp         AvatarDataNotify      SceneTeamUpdateNotify
EnterScenePeerNotify     PlayerEnterSceneInfoNotify   CombatInvocationsNotify
```

`python tools/lgc.py opcodes critical-path` regenerates that list from the source if the
handshake has changed.

---

## After - as the dump arrives

Weeks later, when an `OSCBWin7.1.x` dump appears, file it first - that names it
by convention, checks it is the OS line and not CN, and makes every later command
path-free:

```
python tools/lgc.py proto intake <the-dump> --apply
   # -> local/protos/Obfuscated.proto   or   Deobfuscated.proto

python tools/lgc.py proto names                      # only if it is Obfuscated
   # derives message AND field names, prints where the login path stands, and
   # SHORTLISTS anything it refused rather than guessing. If the community
   # file turns up later, prefer it and keep this as the cross-check.

python tools/lgc.py proto import --names tools/out/nameTranslation.txt
python tools/lgc.py proto diff   --new tools/out/proto-staged   # RENUMBERED first
python tools/lgc.py opcodes gen  --version 7.1 --write
python tools/lgc.py opcodes field-numbers
python tools/lgc.py opcodes audit            # dead handlers should collapse
```

Expect `lgc.py proto names` to leave the smallest login packets unnamed. That is by
design, not a failure: `EnterSceneReadyReq`, `SceneInitFinishReq` and
`EnterSceneDoneReq` each hold one `enter_scene_token` and nothing else, so they
are structurally identical and no matcher can separate them. It prints the
closed set of candidates instead - on 7.0 that set was exactly the right three -
and the client orders them for you, because it sends them in the sequence the
report lists. Read them off the server's own log of unhandled opcodes
(`lgc.py opcodes recover`), or sweep the two or three candidates.

That fills the other ~2100 opcodes in one pass and turns the hand-found numbers
into a complete table.

---

## If something is wrong

| symptom | look at |
|---|---|
| client rejects the version | `VERSION_PARTS` disagreeing with `VERSION` |
| sweep never converges | the number is outside `--max`, or an exclusion removed it |
| sweep says hit every round | `includeOriginal` is true, or the packet already works |
| logs in, world empty | `lgc.py resources check` on the tree config.json points at |
| monsters have no stats | dump was copied over `resources/` instead of grafted |
| every name blank | the dump's TextMap got copied in; restore the old one |
| one feature dead | `lgc.py opcodes audit`, look under dead handlers |

Rollback is always: point `config.json` back at `resources/`, redeploy the old
jar. Nothing here modifies `resources/` or the previous build.
