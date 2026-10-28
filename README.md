# DDS2 Randomizer (数码恶魔传说2 天魔变 随机化工具)

A randomizer for the Chinese-patched PS2 release of *Shin Megami Tensei:
Digital Devil Saga 2* (`DDSAGA2.iso`, SLPM_657.95).

It reads the game ISO, randomizes several layers of the game data and writes
a new, playable ISO.  Every patched file keeps its original byte size, so the
`DDS3.DDT` index and the ISO9660 filesystem are never rebuilt.

## Quick start

```bash
# GUI (PySide2) - every layer has its own switch
python dds2_randomizer_gui.py        # or double-click run_gui.bat

# command line
python dds2_randomizer.py --iso DDSAGA2.iso --out DDS2_random.iso --seed 1234

# only some layers
python dds2_randomizer.py --only encounters --seed 42
python dds2_randomizer.py --only bosses    --seed 42
python dds2_randomizer.py --only text      --seed 42

# Mantra (曼斗罗) skills are randomized by default; these are the knobs
python dds2_randomizer.py --mantra-mode sets --seed 42
python dds2_randomizer.py --mantra-costs --seed 42
python dds2_randomizer.py --no-mantras --seed 42

# cosmetic: the protagonist's demon form (Varuna) becomes Black Varuna
python dds2_randomizer.py --black-varuna --seed 42

# let bosses and elite enemies answer the Analyse command with real data
python dds2_randomizer.py --reveal-enemies --seed 42

# self test (needs the game image for the parsing checks)
python selftest.py [path/to/DDSAGA2.iso]
python selftest_gui.py
```

The GUI settings map 1:1 onto `Config` and can be saved to / loaded from a
JSON file, which the command line front end accepts with `--config`.

## What gets randomized

Every layer is optional.

### 1. Normal (random) encounters
* The pool is every enemy id referenced by a **non-story** encounter record
  whose HP is sane; the pool is permuted into a 1:1 bijection, so enemy A
  becomes enemy B, C becomes D, and so on.
* The destination unit keeps the **appearance / name / model** it was assigned
  and takes the **level, HP, MP, the five stats and the skill list** of the
  enemy that used to stand in that slot - so a spawn point keeps its original
  difficulty and only the look changes.
* `--stats-mode copy` (default) takes the five stats verbatim, `scale` only
  matches their sum.
* Enemies in a **ban list** (`--ban-file`, `--ban`) are removed from the pool,
  and the encounter records that used them are rewritten to another encounter
  from the same block, so that spawn point still works.

### 2. Enemy AI follows the new skills
An enemy's AI is keyed by its id, so after the swap it still named its *old*
skills.  `/battle/AICALC.TBL` turns out to be two things:

* a **flat action table** - 395 records of 348 bytes, 36 actions of 8 bytes,
  `[u16 weight][u16 skill][u16 cond][u16 cond param]`.  Record `k` belongs to
  UNIT record `k + 128`, so each unit is re-pointed with **its own** skill map.
* two **FlowScript modules** (`FLW0`): the named battle AIs choose with
  `PUSHIS <skill>; COMM AI_ACT_SKILL`.  All 853 of those operands are re-pointed
  through the merged map.

Only those skill operands are touched.  `selftest.py` proves it byte by byte:
*"only skill ids touched (N changed, 0 stray)"*.

### 3. Boss battles (剧情战斗) — **shielded**
* Story battles are already shaken up through the ordinary enemy permutation,
  so the dedicated story-battle pass is **off by default** and the GUI no
  longer exposes it.  The code is intact; re-enable it with `--bosses`.
* It works by detecting story battles from the **battle event scripts**: every
  `/event/**/*.be` file stores its encounter record index at header offset
  `0x50` and repeats it in its `eNNN_<index>.BED` resource names.  Those 27
  records are the scripted battles (record 51 is the first boss,
  `[41, 40, 41]` = two Andras + one Vetala).
* When enabled it permutes those line-ups and rescales each enemy so its HP and
  stat sum match the average of the enemies that used to be there.
* A dedicated boss shuffle with its own swap / rescale controls is planned for
  later; see `docs/METHOD_ENEMY_SWAP.md` section 2.9.

### 3a. Bosses become analysable (分析) — **verified in game**
Bosses and elite enemies normally answer the Analyse command with "?" instead
of HP / MP / skills.  The analyse window's row functions (table at `0x415650`)
branch to a "?" placeholder drawer (`0x1B8930`) through **thirteen**
`bnel flag,zero,<placeholder>` guards in `SLPM_657.95`; the flags they test are
the encounter's `ENCOUNT.TBL +32` bit 0, the unit template's `+0` bit 0x800 and
the unit's category byte (`UNIT.TBL +70`, `0xFF` on all 79 boss units).

Clearing those data bytes does reveal the values, but `+70` is read by the
battle logic too, and rewriting the bosses' `0xFF` to `0` **hangs the game
while loading a boss battle**.  So `--reveal-enemies` patches the code instead:
every guard's compare register becomes `$zero`, i.e. `bnel zero,zero,<same
target>` — never taken, delay slot still nullified, which is exactly the
"flag not set" path.  Only the `rs` field of those thirteen words changes;
`UNIT.TBL` and `ENCOUNT.TBL` are left untouched.

**Verified in game**: analysing a boss now shows real **level / HP / MP**.

**Known boundary**: the **skill row still shows "?"**.  That is not a hidden
flag - the row lists the unit record's skill slots and only 31 of the 79 boss
units carry any; a boss's actual actions come from the `AICALC.TBL` AI scripts,
which is a different thing.  So that row has no real value to show and is left
as it is.  See `docs/METHOD_ENEMY_SWAP.md` §7.7.

The GUI exposes it on the "敌人" tab, off by default.

### 3b. Mantra (曼斗罗) skill sets and stat bonuses — **on by default**
The Mantra system is split over two files, and only the second one holds the
skills and the stat bonuses:

* `/facility/data/mtrMantraBlockData.tbl` (inside `DDS3.IMG`) is the **hex grid
  only**: 176 records of 32 bytes - `flag(rank<<4|type)`, `id`, the two grid
  coordinates `A/B` and six neighbour links.  There are no skills in it: the
  section after the records is exactly the list of non-zero neighbour fields
  (456 entries = 2 x 228 links).
* The **cost, the permanent stat bonus and the skill list** live in the game
  executable, `SLPM_657.95`, which sits at the ISO9660 root and is *not* part of
  `DDS3.IMG`.  A table of 176 records of 36 bytes starts at file offset
  `0x301388`, indexed by the mantra id:

  ```
  +0x00 u32 cost
  +0x04 u8  bonus[5]     STR MAG VIT AGI LUK, one byte each
  +0x09 u8  0
  +0x0A u16 skills[8]    0-terminated
  +0x20 u16 group (grid column)
  ```

  Only the 8 story-given event mantras (145..152) carry a bonus - 24 points in
  total, e.g. 151 is "all five stats +1", 147 is "AGI+3".

`--mantras` (default mode `--mantra-mode skills`) pools every skill of every
teaching mantra, shuffles the pool, and deals it back out so that **each mantra
keeps the number of skills it had** but no longer teaches any of its own: 107
mantras, 267 skill slots, average ~2.5 original source mantras per new skill
list.  `--mantra-mode sets` is the older, coarser behaviour where whole skill
sets are traded 1:1 (so a mantra ends up with exactly another mantra's set).
Either way the total pool of skills is only permuted.  The five-stat bonus
vectors are permuted among the 8 nodes that have one, so the point total stays
at 24.  `--mantra-costs` also permutes the cost.

Only the bonus and skill fields change, so the ELF keeps its exact size and
every offset in it stays valid (self test: 495 bytes changed, 0 stray).
A character's learned skills are *derived* from the mantra table by the game
itself, so the characters that start with mantras already know follow the new
skills automatically: the new-game/chapter entry (`0x118598`) clears the player
data and then re-applies every learned mantra (`0x313930` -> `0x3137f8` ->
`0x313f98`).  Nothing in the image has to be kept in sync for that, and the
starting skill sets were confirmed to change correctly in game.

This layer is **on by default** (verified in game); use `--no-mantras` to turn it
off.  The GUI exposes it on the "文本与语音" tab, and a `<out>_mantras.txt`
reference table (id / cost / bonus / skills) is written next to the output ISO.

### 3c. Demon form model swap - Black Varuna (opt-in, cosmetic)
The protagonist (萨弗 / Serph) turns into **Varuna (伐楼罗)**.  That model is
`/model/human/pc011_on.PB` plus `/model/human/pc011.PB`: the party's atma slots
are indexed by the pointer tables at `SLPM_657.95` va `0x3C1BEC` (`pcNNN.PB`) and
va `0x3C2EAC` (`pcNNN_on.PB`), entry `0x11` resolves to `pc011`, and both files
name their body node **`varna_body`** with the model root `x101_root`.

With `--black-varuna` (GUI: the checkbox on the "文本与语音" tab, off by default)
the two models get the **Black Varuna textures**.  A `.PB` model is a flat list
of `[u32 type][u32 size][magic]` chunks (`PIB0` skeleton, `TXP0` textures,
`MD00` mesh, `MT00` materials, `END0`) padded to 0x40, and `pc011`,
`pc011_on` and the e639 event models `kuro_varuna` / `siro_varna` all carry a
**byte-identical 199936 byte `TXP0` chunk** (3 x 256x256, format 19).  So only
that one chunk is replaced, with the darker `kuro_varuna` set (palette mean 753
vs Varuna's 1080).  A byte-identical copy of it sits at `/model/devil/10E.PB`
offset `0x3B040` (and the white `siro_varna` set at `/model/devil/10F.PB`
`0x1D340`).

Skeleton, mesh, materials and every offset stay untouched, so the DDT index and
`DDS3.IMG` never move.  Copying the whole `/model/devil/10E.PB` file instead was
tried first and **hung the game** when the transformation model loaded - `10E`
is not a valid playable-character model.  Not verified in game yet; see
`docs/METHOD_ENEMY_SWAP.md` §13.

### 4. Dialogue - CG subtitles, equal-length swap by default
The subtitle text lives in the **`MSG1` table inside each movie file**:

```
/event/eNNN/eNNN/eNNN_NNN/E*_NNN.PM1      79 files, 1374 entries
```

`.PM1` is a `PMD1` container; the MSG1 table (chunk id 6) *is* the subtitle
store - there is no separate `.msg` chunk.  An entry is
`name[16] reserved[8] lines voice[lines] length text`.

**What the randomizer does - and only does:**

* every entry of every movie is put into **one pool** and grouped by **text
  byte length**; each group is shuffled and paired, so a line can come from any
  other cutscene (654 of 674 pairs cross files);
* **only the text payload moves, with an identical byte count.**

That means this is **an equal-length swap, not a full shuffle**: the ~9% of
lines whose byte length is unique in the whole pool keep their original text.
Coverage on seed 12345 is 1254 / 1374.

**Different text lengths: settled, and the answer differs per layer.**

*CG subtitles: same length, and only the words move.*  The container has room -
each entry's slot is `size - (32 + 4*lines)` bytes and the retail data leaves
10831 bytes (20.6% of all text) unused - but swapping subtitles of different
lengths (voice array untouched, global pairing) made them appear at the wrong
time and vanish, and some cutscenes lost their subtitles entirely, so **CG
subtitles always keep their byte length**.

A CG text is wrapped in **cutscene-specific control codes**:

```
e601_001   f208ffff f207                  . 0a f214 XX 01 00
e603_001   f208ffff f41305ffdb08 f207     . 0a f215 00 00 00
```

93 `.PM1` files use 334 distinct leading codes and 36 of them do not even share
one between their own entries, so swapping a **whole** text carried those codes
into a cutscene that does not know them - which is exactly the "late subtitles /
subtitles vanish" failure.  CG subtitles now follow the same rule as field
dialogue: only the plain glyph runs travel, every control byte stays where it was
and every run keeps its byte length, so each entry also keeps its exact line
structure.  `--cg-text` (GUI "CG 字幕范围") picks where a run may come from:
`global` (any cutscene, the default), `file` (the same cutscene only) or `off`
(leave `.PM1` byte-identical).

*The real bug, and the fix.*  Two subtleties cost several in-game failures:

1. `f208ffff f207` + `81ff` + words: that `81ff` is an entry-level **marker**, not
   text - 1373 of the 1379 CG entries carry it exactly once, always at the very
   start.  0x81 is a Shift-JIS lead byte, so the mask folds it into the first run.
   It survives by luck while only CG runs can be donors (they nearly all start
   with it); widening the donor pool to the field scripts deleted it.
2. **A code's operands can look exactly like glyphs.**  `f208ffff f413 05 ff e6 08
   f207` is a code whose operand pair `e6 08` sits in the lead-byte range, so it was
   cut out as a 2-byte "text run" and swapped - corrupting the control block into
   `f208ffff f413 05 ff 81 ff f207`.  That entry stopped displaying, and its voice
   (keyed to the subtitle) stopped with it, and **the whole cutscene stopped after
   that line** - the "subtitles and voice disappear part way through" report.

Both are fixed by refusing to swap any run shorter than `MIN_SWAP_RUN = 4` bytes:
code operands are short, real text is long.  Checking every pinned byte of every
CG entry against retail:

| build | CG texts changed | pinned bytes rewritten |
|---|---|---|
| before the fix | 1293 | **196** |
| now | 1261 | **0** |

Seed 11 swaps 1680 of 1766 runs in 877 pairs, with 0 control bytes, 0 lengths and
0 line shapes changed.  **Verified in game**: the words are scrambled while the
subtitles still appear and hold at the right time, keep the original number of
lines, and nothing disappears part way through.  The 225 runs no longer eligible
are all two bytes - code operands, or a single-glyph line.

*Randomizing the VOICE: not implemented.*  It was explored and dropped.  What was
established, and is worth keeping: a CG cutscene is one of two kinds - 44 are
pre-rendered movies (`/MOVIE`, `/MOVIE2`, `/MOVIE3`; the opening one is
`/MOVIE2/E601_001.IPS`) whose audio is inside the MPEG stream, and 49 run in
engine with one clip per spoken line at `/voice/<name[:8]>.ADB` (432 files, 11 KB
to 2 MB, almost all different sizes).  Nothing else names those clips - searching
every `.bf` script for the 24 entry names of `E639_001.PM1` finds **0** - so the
engine opens the clip from the entry's own 16-byte name field.  **Swapping those
names failed in game**: subtitles stopped resolving and the changed lines had no
audio, so an entry's name is the subtitle's own identity and must never be
touched.  Exchanging the clips' APCM payloads with `voice.shuffled_bytes` (which
keeps each file's header, stop/end markers and exact byte size) was built but
never verified on its own, and was rolled back with the rest.

*Field / NPC dialogue: yes, opt-in.*  `--field-varlen` (GUI "允许不同长度的场内
对话互换", off by default) lets the plain text runs between the control codes
trade places with runs of **any** length.  The whole MSG1 block is then laid out
again - entries contiguous, every table offset updated - which keeps the block's
declared size, so the `.bf`, the compressed `BF` member and the `.LB` all keep
their exact size and the compression layer is not involved at all.  Each block
has a text budget and runs may only grow while it lasts; blocks that still do not
fit fall back to the equal-length swap instead of being skipped.

What makes a different length safe at all is that the engine delimits a message
by the `u32` length field, not by a `00` terminator: of the 3220 retail field
entries, **880 are followed immediately by a non-zero byte** (the next entry's
name) and 228 have no gap at all - a `00` scan would read those as garbage, which
does not happen in game.  The rebuild updates that length field, so it is the one
thing that must move and it does.

The box does not wrap: a field message breaks lines with a `0a` control byte, and
across the 1908 field messages (6249 lines) the script authors never let a line
exceed **33 glyph cells** (97.9% are 20 or fewer, 99.5% are 24 or fewer, only
four lines reach 33).  A swap is therefore also capped so that no line passes 33
cells - the cap binds 4401 times on seed 11 and the widest line in the whole game
is still 33 afterwards.

A block's declared size can overrun the section that follows it: 27 blocks do,
always by two bytes (`.data.msg` claims to end after the `.data.str` section has
already started).  Rebuilding to the declared size therefore erased the first
bytes of the next section in two files; the block end is now clamped to the
`FLW0` section's real extent and the old text is only erased up to the end of the
original layout.  On seed 11 that swaps 4435 runs, rewrites 64 blocks (4 of them
falling back) and changes the length of 1812 messages, with 22 containers
byte-identical in size and nothing written outside a `.data.msg` section.
Not verified in game yet.

**What must not change** (each of these was forced by an in-game test):

| must stay | why |
|---|---|
| `u32 @+28` voice array | moving it makes the entry fall back to its ORIGINAL line |
| CG subtitle byte length | a different length mistimes the whole cutscene's subtitles |
| field control codes | they point at scene resources; moving one crashes the game |
| pairing inside one file | that only reorders a cutscene against itself, which looks like nothing happened |
| file sizes | the DDS3.DDT index and every following offset must not move |

The consequence of the first row is that **the audio no longer follows the
subtitle** - swapping the voice array kills the text change, and the meaning of
that array is still unknown.

**Field / NPC dialogue does not work at all yet.** `/fld/f/fNNN/fNNN.bf` is only
a loose copy; the game reads the field script from a compressed `BF` chunk
inside `/fld/f/fNNN/fNNN_000.LB`, and that codec has not been cracked.

System prompts (item get, locked doors, shop, quizzes, menus...) are detected
by entry name and excluded from the in-game pool.

See `docs/METHOD_ENEMY_SWAP.md` section 11 for the byte-level details, the
five build attempts and the list of open questions.

## Layout

```
dds2_randomizer.py          command line entry point
dds2_randomizer_gui.py      PySide2 GUI (per-layer switches)
run_gui.bat                 double-click launcher for the GUI
selftest.py                 parser / shuffler / AI self test
selftest_gui.py             GUI switches <-> Config self test
tools/enemy_info.py         enemy inspector (stats, records, recorded AI actions)
tools/p2s_diff.py           diff the EE RAM of two PCSX2 savestates (RE aid)
tools/make_release.py       freeze the tree into releases/<version> + zip
src/ddstool/
    iso9660.py   ISO9660 reader/writer
    ddtimg.py    DDS3.DDT tree parser
    game.py      Game facade (read/write/listdir/glob)
    build.py     ISO copy + in-place patcher
    config.py    options
    unit.py      UNIT.TBL parser (HP/MP/stats/skills)
    unitmap.py   enemy id <-> unit record mapping (UNIT_BASE = 127)
    encount_min.py ENCOUNT.TBL parser
    aicalc.py    AICALC.TBL: flat action table + FlowScript AI_ACT_SKILL sites
    msgtbl.py    MSG1 message table parser (exact slots)
    textdata.py  per-file, voice-binding dialogue shuffle
    voice.py     APCM .ADB parser + optional shuffle
    mantra.py    Mantra (曼斗罗) cost / stat bonus / skill list (in SLPM_657.95)
    model.py     character model slots + the opt-in Black Varuna swap
    randomizer.py driver
releases/                    frozen snapshots (source + MANIFEST.json + zip)
```

## Safety

* The input ISO is never modified; a fresh output ISO is written (~4.4 GB).
* Every patched file keeps its original byte size.
* If the requested output path is locked by another program the tool now
  stops with a clear error instead of silently writing a different file, and
  it re-reads the output to confirm the patches landed.
* `--no-ai-remap` disables the AI re-pointing on its own, leaving everything
  else in place.

## Produced output

`DDS2_random_v26.iso` (seed `12345`): 939 normal encounter slots re-pointed
over a 163-enemy pool, 27 story-battle records left untouched (the dedicated
boss pass is shielded), 242 AI action slots across 78 unit AI records plus 395
FlowScript call sites re-pointed with **0 stray bytes**, **1254/1374 CG
subtitles swapped by equal length across 79 movie files**, system text
untouched, 0 layout changes.

Open items: field / NPC dialogue (blocked on the `.LB` chunk codec), CG voice
sync, and the remaining 9% of CG lines that have no equal-length partner.

## Documentation

* `docs/METHOD_ENEMY_SWAP.md` - the algorithm, the verified constants, the
  AICALC.TBL layout and the ISO-wide FlowScript scan (Chinese)
* `FINDINGS.md` - raw reverse-engineering notes (English)
* `docs/REPORT.md` - format specification + confidence levels (Chinese)
* `docs/USAGE_zh.md` - 中文使用说明