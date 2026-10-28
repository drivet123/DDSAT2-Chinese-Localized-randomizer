# /battle/AICALC.TBL - the enemy battle AI.
#
# The file holds two unrelated things, one after the other.  Everything below
# was derived from the retail file; see docs/METHOD_ENEMY_SWAP.md section 8.
#
# ---------------------------------------------------------------------------
# 1. the flat action table, file offset 0x10 .. 0x21a4c
#
#    395 records of 348 bytes:
#        +0   .. +51    per-record header.  Unused halves are filled with the
#                       constant 0x0808, which is what makes the record shape
#                       visible in a hex dump.
#        +52  .. +339   36 action entries of 8 bytes
#        +340 .. +347   per-record trailing field.  It is NOT an action: its
#                       second u16 sits in 1..600 far more often than its
#                       neighbours' (33 vs 1 and 0) and its first u16 is almost
#                       never 0, so treating it as a 37th action would corrupt
#                       it.
#
#    An action entry is
#        [u16 weight][u16 skill id][u16 cond_a][u16 cond_b]
#    so the skill id of action `j` lives at record_offset + 52 + 8*j + 2.
#    Weights observed: 0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 70, 100.  cond_a
#    holds fixed-point HP ratios (0x3333 = 0.2, 0x6666 = 0.4, 0x999A = 0.6,
#    0xCCCD = 0.8).  cond_b is a parameter for the condition, and its values
#    (128, 64, 20, 10, ...) overlap the skill id range - which is exactly why
#    the old "scan every u16 in the file" remap corrupted the table.
#
#    Record k belongs to UNIT.TBL record k + 128, i.e. to enemy id k + 1.
#    Evidence: matching the skill ids of each AI record against the five skill
#    slots of the UNIT records gives a mean coverage of 56.3% when the record
#    is paired with unit record k+128, and 4.7% for k+127 / k+129.  Coverage
#    is flat across k (53.8% for k=0..49, 63.3% for k=300..349), which also
#    confirms the 348-byte stride - 344/346/350/352 all fall to 8-30%.
#
# ---------------------------------------------------------------------------
# 2. two FlowScript modules (magic "FLW0") from 0x21a4c to the end of file
#
#    The first one holds the named battle AIs (ai_test, AI_KARMA_M,
#    AI_VETARA, AI_ABADON, AI_KAKUHEKI_* ...), the second the calc_*
#    helpers.  A FlowScript module is
#
#        +0   char[4]  "FLW0"
#        +4   u32      reserved
#        +8   u32      section count
#        +12  u32      field14
#        +16  u16[4]   reserved
#        +24  section descriptors, 16 bytes each:
#                 [u32 id][u32 unitSize][u32 unitCount][u32 startOffset]
#             id 0 = .label.proc, 1 = .label.jump, 2 = .text, 3 = .data.msg,
#             4 = .data.str.  startOffset is relative to the "FLW0" magic.
#
#    Instructions are u32 little endian `[u16 opcode][u16 operand]`; the
#    opcodes that carry a full u32 operand (0 PUSHI, 1 PUSHF, 10 JUMP,
#    11 CALL, 28 IF, 34 PUSHSTR) consume the following word as well.
#
#    A skill is chosen with
#        PUSHIS <skill id>      (opcode 29, operand u16)
#        COMM   51              AI_ACT_SKILL
#    Per the script compiler's argument order the pushed argument sits in the
#    word immediately before the COMM, and all 849 AI_ACT_SKILL call sites in
#    the retail file have PUSHIS there - no exceptions.  AI_ACT_SKILL_PARAM
#    (index 226) is the same shape and has 4 more call sites.
#
#    Only those 853 operands are touched.  A byte-level "any u16 that looks
#    like a skill" remap (the previous approach) rewrote 8583 words including
#    the string table and the label names, and the game no longer booted.

import struct

# --- flat action table -----------------------------------------------------
AI_TABLE_BASE = 0x10
AI_TABLE_RECORD = 348
AI_TABLE_COUNT = 395
AI_ACTION_OFFSET = 52
AI_ACTION_STRIDE = 8
AI_ACTION_SLOTS = 36

# UNIT.TBL record for flat-table record k
AI_UNIT_BASE = 128

# FlowScript
FLW0_MAGIC = b'FLW0'
FLW0_HEADER = 24
FLW0_DESC = 16
FLW0_SECTION_TEXT = 2
FLW0_SECTION_MSG = 3

OP_COMM = 8
OP_PUSHIS = 29
# library indices in the DDS FlowScript function list
AI_ACT_SKILL = 51
AI_ACT_SKILL_PARAM = 226
SKILL_COMMS = (AI_ACT_SKILL, AI_ACT_SKILL_PARAM)

MAX_SKILL_ID = 600


def flat_skill_offsets(data, index=None):
    """Byte offsets of every skill id in the flat action table.

    With `index` given, only that one record is returned.
    """
    out = []
    if index is None:
        indices = range(AI_TABLE_COUNT)
    else:
        indices = [index]
    for k in indices:
        if not (0 <= k < AI_TABLE_COUNT):
            continue
        base = AI_TABLE_BASE + k * AI_TABLE_RECORD
        if base + AI_TABLE_RECORD > len(data):
            continue
        for j in range(AI_ACTION_SLOTS):
            p = base + AI_ACTION_OFFSET + j * AI_ACTION_STRIDE
            s = struct.unpack_from('<H', data, p + 2)[0]
            if s:
                out.append((p + 2, s))
    return out


def flat_weight_offsets(data, index):
    """(weight_offset, skill_offset, weight, skill) of one flat AI record."""
    out = []
    base = AI_TABLE_BASE + index * AI_TABLE_RECORD
    if not (0 <= index < AI_TABLE_COUNT) or base + AI_TABLE_RECORD > len(data):
        return out
    for j in range(AI_ACTION_SLOTS):
        p = base + AI_ACTION_OFFSET + j * AI_ACTION_STRIDE
        w, s = struct.unpack_from('<HH', data, p)
        out.append((p, p + 2, w, s))
    return out


def unit_record_for_index(k):
    """UNIT.TBL record that uses flat AI action record `k`."""
    return k + AI_UNIT_BASE


def index_for_unit_record(rec):
    return rec - AI_UNIT_BASE


def _sections(data, flw):
    """(id, unit_size, unit_count, start) for each section of one FLW0."""
    if data[flw:flw + 4] != FLW0_MAGIC:
        return None
    count = struct.unpack_from('<I', data, flw + 8)[0]
    if not (1 <= count <= 32):
        return None
    out = []
    for i in range(count):
        o = flw + FLW0_HEADER + i * FLW0_DESC
        if o + FLW0_DESC > len(data):
            return None
        sid, usize, ucount, start = struct.unpack_from('<IIII', data, o)
        if start >= len(data) - flw:
            return None
        out.append((sid, usize, ucount, start))
    return out


def flw0_modules(data):
    """Offsets of every FlowScript module in the file."""
    out = []
    pos = data.find(FLW0_MAGIC)
    while pos != -1:
        if _sections(data, pos):
            out.append(pos)
        pos = data.find(FLW0_MAGIC, pos + 4)
    return out


def flw0_skill_offsets(data):
    """Byte offsets of the `PUSHIS <skill>` operand of every AI_ACT_SKILL*."""
    out = []
    for flw in flw0_modules(data):
        secs = _sections(data, flw)
        text = None
        for sid, usize, ucount, start in secs:
            if sid == FLW0_SECTION_TEXT and usize == 4:
                text = (flw + start, ucount)
                break
        if text is None:
            continue
        base, words = text
        if base + words * 4 > len(data):
            words = max(0, (len(data) - base) // 4)
        for u in range(1, words):
            w = struct.unpack_from('<I', data, base + u * 4)[0]
            if (w & 0xFFFF) != OP_COMM:
                continue
            if (w >> 16) not in SKILL_COMMS:
                continue
            prev = struct.unpack_from('<I', data, base + (u - 1) * 4)[0]
            if (prev & 0xFFFF) != OP_PUSHIS:
                continue
            s = prev >> 16
            if s:
                out.append((base + (u - 1) * 4 + 2, s))
    return out


def _clean(mapping):
    out = {}
    for a, b in (mapping or {}).items():
        try:
            a, b = int(a), int(b)
        except (TypeError, ValueError):
            continue
        if 0 < a <= MAX_SKILL_ID and 0 < b <= MAX_SKILL_ID and a != b:
            out[a] = b
    return out


def remap_flat_by_unit(data, unit_maps):
    """Re-point the flat action table, one unit at a time.

    `unit_maps` maps a UNIT.TBL record index to that unit's own
    `{old skill id: new skill id}` map.  Because the flat record for a unit is
    known exactly (record `k` belongs to UNIT record `k + 128`), each unit is
    re-pointed with its *own* map - a skill id that two units happen to share
    can no longer drag the wrong replacement in.

    Returns (changed, seen_old_ids, unmapped_old_ids).
    """
    changed = 0
    seen = set()
    unmapped = set()
    for rec, mapping in sorted(unit_maps.items()):
        m = _clean(mapping)
        index = index_for_unit_record(rec)
        if not (0 <= index < AI_TABLE_COUNT):
            continue
        for off, old in flat_skill_offsets(data, index):
            if not (0 < old <= MAX_SKILL_ID):
                continue
            if old not in m:
                unmapped.add(old)
                continue
            seen.add(old)
            new = m[old]
            if new != old:
                struct.pack_into('<H', data, off, new)
                changed += 1
    return changed, seen, unmapped


def remap_flow_skills(data, remap):
    """Re-point the `PUSHIS` operand of every AI_ACT_SKILL* in the FLW0 modules.

    The FlowScript AIs cannot be attributed to a single unit, so this one uses
    the merged global map.  Only the 853 operands are touched.

    Returns (changed, seen_old_ids, unmapped_old_ids).
    """
    m = _clean(remap)
    changed = 0
    seen = set()
    unmapped = set()
    for off, old in flw0_skill_offsets(data):
        if not (0 < old <= MAX_SKILL_ID):
            continue
        if old not in m:
            unmapped.add(old)
            continue
        seen.add(old)
        new = m[old]
        if new != old:
            struct.pack_into('<H', data, off, new)
            changed += 1
    return changed, seen, unmapped


def remap_skills(data, unit_maps, flow_map=None):
    """Both halves at once.  `flow_map` defaults to the union of unit_maps."""
    if flow_map is None:
        flow_map = {}
        for mapping in unit_maps.values():
            for a, b in (mapping or {}).items():
                flow_map.setdefault(a, b)
    flat, seen_f, unmapped_f = remap_flat_by_unit(data, unit_maps)
    flow, seen_w, unmapped_w = remap_flow_skills(data, flow_map)
    return {'flat': flat, 'flow': flow,
            'distinct': len(seen_f | seen_w),
            'unmapped': unmapped_f | unmapped_w}
