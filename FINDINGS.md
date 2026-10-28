# DDS2 Randomizer — Reverse-engineering findings

Workspace: `D:\ps2\DDSAT2-rand-dsh`
Input ISO: `DDSAGA2.iso` (Chinese-patched Shin Megami Tensei: Digital Devil Saga 2, PS2, SLPM_657.95)

## 1. Disc layout (SOLVED)

Standard ISO9660, 2048-byte sectors.  Two game archives in the root:

| file | LBA | size |
|------|-----|------|
| DDS3.DDT | 2332 | 167233 |
| DDS3.IMG | 2414 | 1466599424 |

### DDS3.DDT — file index (SOLVED)

Breadth-first serialised tree of 12-byte little-endian records:

```
struct Entry {
    u32 name_offset;  // offset of NUL terminated name inside DDT (0 for root)
    u32 lba;          // file: sector offset inside DDS3.IMG (0x800 bytes)
                      // dir : offset of the children record array in DDT
    s32 size;         // file: byte size
                      // dir : negative child count (-count)
};
```

First record is the root.  Then the root children array, then for every node
of a level its name string followed by its children array (level by level).

Parsed with `src/ddstool/ddtimg.py`: **493 dirs, 6642 files, 0 overlaps**.

### DDS3.IMG — payload (SOLVED)

Plain concatenation of file payloads at `lba * 2048`.  Total payload
1 459 091 579 bytes, IMG 1 466 599 424 bytes → only **7.5 MB slack**.
=> patch files in place, keep size <= original.

Helper: `src/ddstool/game.py` (Game.read/write/listdir/glob).
`src/ddstool/iso9660.py` — ISO9660 reader/writer.

## 2. Battle tables (in /battle)

`/battle/UNIT.TBL` 68112 = **16-byte header + 896 records x 76 bytes**.
Strong 76-byte autocorrelation.  Records whose "stat" bytes are the
default placeholder (28,28,28,23,14) all live at record+64, confirming
base=16/stride=76.

Field hypotheses (little endian):
- bytes 0..9   : up to 5 skill ids (u16[0..4]), 0 = empty, max seen 571
- bytes 12..13 : HP (u16).  Placeholder units have 0xffff here.
- bytes 16..17 : MP (u16).
- bytes 64..68 : 5 stats (u8 each, max 99) - STR/MAG/VIT/AGI/LUK (order unknown)
- byte 69      : small type/flag
- bytes 72..75 : 2 more u16 ids (max 571)

Record index ranges (by content):
- 0..126   : no stats (resistance-like data, all fields 100/flags)
- 127..467 : real unit stat records (141 of them are placeholders)
- 468..510 : zero
- 511..895 : resistance-like (u16 value + u16 flag pairs, 100 = normal)

Values 0x80000082 / 0x80000000 / flags in the resistance records look like
element resistances (value 0/25/50/75/100/130/150, bit31 = absorb/repel).

`/battle/ENCOUNT.TBL` 112480 = 2812 x 40 bytes (exact, strong 40-byte
autocorrelation).  Records clearly contain small enemy ids, e.g.
rec116 u16 = [0,0,0,0,0,27,31,...], rec121 = [...54,53,54...].
First u32 of file = 0xa000 (40960 = 1024*40).  Some records contain byte
runs like 8 x 0x08.  FIELD SEMANTICS NOT YET SOLVED.

`/battle/SKILL.TBL` 39888 = 16 + 712 * 56 (strong 56-byte autocorrelation).
First u32 = 0x540 = 1344.

`/battle/AICALC.TBL` 375776, first u32 = 0x20a00; autocorr strongest ~348.
`/battle/VISUAL.TBL` 269600, autocorr ~20 (and 384).
`/battle/EFFECT.TBL` 27600, autocorr 32.
`/battle/MSG.TBL` 221984 — contains both 45-byte records (4-byte header +
41-byte text area, seen at start) and named records
(`[16-byte name][12 bytes][u32 offset][u32 len][8 control bytes][text][0x0a 0x00]`),
e.g. name "item_051" at 0x1dde4, "DEFCOM_07" at 0x3628c.
`/battle/SYSEFF.LB` 411520.

## 3. Scripts / text / voice

- `/event/eNNN/eNNN/scr/*.bf` : flow/event scripts, header "FLW0" magic,
  little-endian opcode stream.
- `/event/eNNN/eNNN/.../*.PM1` (up to 9 MB) / .PM2 / .PM3 : event data with
  lots of byte runs >= 0x81 (text).  Chinese patch uses a custom 2-byte glyph
  encoding — do not assume Shift-JIS.  Shuffle as opaque byte strings.
- `/voice/*.ADB` : 432 files, NANGxxxx.ADB naming.
- `/facility/data/mtrMantraBlockData.tbl` + `Tutorial` : Mantra grid data.
- `/facility/msg/mantra|shop|terminal/*` : Mantra/menu text.

## 4. Goal

Build `dds2_randomizer` that loads the ISO and writes a randomised mod ISO:
1. normal encounters: enemy types + skills randomised, strength preserved;
2. boss vs boss swap with stat/damage multipliers;
3. shuffle story dialogue + voice.
