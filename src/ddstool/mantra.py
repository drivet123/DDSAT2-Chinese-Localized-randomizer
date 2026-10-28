# DDS2 Mantra data.
#
# The Mantra system is split over two files, and they are easy to confuse:
#
# 1. /facility/data/mtrMantraBlockData.tbl   (inside DDS3.IMG, read via Game)
#    The hex *grid*.  176 records of 32 bytes:
#       +0x00 u16 flag        (rank << 4) | type        type 1..4, rank 0..3,
#                             bit 8 marks the 8 event mantras
#       +0x02 u16 id          index into the ELF table below
#       +0x04 u16 A           col * 10 + 5 * (row & 1)  (odd-r hex layout)
#       +0x06 u16 B           row * 10
#       +0x08 u32 neighbour[6]  record id * 32, 0 = none.  Opposite directions
#                             are (0,3) (1,4) (2,5); the links are symmetric.
#    Everything after the records is a pointer list into those six fields.
#    Verified in the ELF: 0x26c8d8 looks a node up by (A / 10, B / 10); the
#    neighbour fields are followed as record pointers and never hold skills.
#
# 2. /SLPM_657.95                            (ISO9660 ROOT - not in DDS3.IMG)
#    The per mantras *cost*, *stat bonus* and *skills*.  A table of 176 records
#    of 36 bytes at file offset 0x301388, indexed by the mantra id:
#       +0x00 u32 cost
#       +0x04 u8  bonus[5]    STR, MAG, VIT, AGI, LUK - the permanent stat
#                             bonus the mantra hands out when it is learned
#       +0x09 u8  0
#       +0x0A u16 skills[8]   0 terminated - what the mantra teaches
#       +0x1A u16 0
#       +0x1C u16 0 / 1       (set on the unused 50-karma filler records)
#       +0x20 u16 group       (the mantra's column on the grid, 1..7)
#       +0x22 u16 1
#    Only 8 records carry a bonus: the story-given "event" mantras 145..152
#    (flag & 0x100 in the block data).  Each boosts one stat by 1..3, and
#    mantra 151 boosts all five by 1 - so the five bytes really are the five
#    stats, one per byte.  Every other record has five zeroes there.
#    Verified in the ELF:
#       0x313dc0  return *(u32 *)(0x400388 + id * 36)          -> the cost
#       0x313f98  walk the 0-terminated list at 0x400392 + id * 36 and set
#                 the matching bits in the player skill bitfield -> the skills
#       0x314b00  same list, at most 8 entries
#
# Only the ids that exist in the block data carry a skill list: 1..120 (id 96
# is unused) and the 8 event mantras 145..152, whose lists are empty.
import struct

ELF_PATH = '/SLPM_657.95'
TABLE_OFFSET = 0x301388
STRIDE = 36
COUNT = 176
COST_OFFSET = 0x00
BONUS_OFFSET = 0x04
BONUS_STATS = 5             # STR MAG VIT AGI LUK, one u8 each
SKILL_OFFSET = 0x0A
SKILL_SLOTS = 8
GROUP_OFFSET = 0x20
TAIL_OFFSET = 0x22
MAX_SKILL_ID = 671          # /battle/MSG.TBL carries skill_000 .. skill_671
STAT_NAMES = ('STR', 'MAG', 'VIT', 'AGI', 'LUK')


class Mantra(object):
    __slots__ = ('id', 'cost', 'bonus', 'skills', 'group', 'tail', 'raw')

    def __init__(self, mid, cost, bonus, skills, group, tail, raw):
        self.id = mid
        self.cost = cost
        self.bonus = list(bonus)
        self.skills = list(skills)
        self.group = group
        self.tail = tail
        self.raw = raw

    def __repr__(self):
        b = ','.join('%s+%d' % (STAT_NAMES[i], v)
                     for i, v in enumerate(self.bonus) if v)
        return '<Mantra %d cost=%d skills=%s%s>' % (
            self.id, self.cost, self.skills, (' ' + b) if b else '')


def parse(blob):
    """Return the list of Mantra records held in |blob| (the whole ELF)."""
    if len(blob) < TABLE_OFFSET + COUNT * STRIDE:
        raise ValueError('SLPM_657.95 is too short for the mantra table')
    out = []
    for mid in range(COUNT):
        o = TABLE_OFFSET + mid * STRIDE
        raw = bytes(blob[o:o + STRIDE])
        cost = struct.unpack_from('<I', blob, o + COST_OFFSET)[0]
        bonus = list(blob[o + BONUS_OFFSET:o + BONUS_OFFSET + BONUS_STATS])
        skills = []
        for i in range(SKILL_SLOTS):
            v = struct.unpack_from('<H', blob, o + SKILL_OFFSET + 2 * i)[0]
            if v == 0:
                break
            skills.append(v)
        group = struct.unpack_from('<H', blob, o + GROUP_OFFSET)[0]
        tail = struct.unpack_from('<H', blob, o + TAIL_OFFSET)[0]
        out.append(Mantra(mid, cost, bonus, skills, group, tail, raw))
    return out


def patch(blob, mantras):
    """Write the bonus and skill fields back into a copy of |blob|.

    Only those two fields change, and every field keeps its width, so the ELF
    keeps its size, its offsets and the ISO layout.
    """
    buf = bytearray(blob)
    for m in mantras:
        base = TABLE_OFFSET + m.id * STRIDE
        if len(m.skills) > SKILL_SLOTS:
            raise ValueError('mantra %d has too many skills' % m.id)
        if len(m.bonus) != BONUS_STATS:
            raise ValueError('mantra %d has a bad bonus vector' % m.id)
        for i, v in enumerate(m.bonus):
            if not 0 <= v <= 255:
                raise ValueError('mantra %d bonus out of range' % m.id)
        o = base + BONUS_OFFSET
        buf[o:o + BONUS_STATS] = bytes(bytearray(m.bonus))
        o = base + SKILL_OFFSET
        field = len(m.skills) * 2
        buf[o:o + SKILL_SLOTS * 2] = b''.join(
            struct.pack('<H', s) for s in m.skills) + b'\x00' * (SKILL_SLOTS * 2 - field)
    return bytes(buf)


def live(mantras):
    """The mantras that actually teach something."""
    return [m for m in mantras if m.skills]


def gifted(mantras):
    """The mantras that hand out a permanent stat bonus."""
    return [m for m in mantras if any(m.bonus)]


def deal_skills(mantras, rng):
    """Put every skill of every teaching mantra into one pool, then deal it back.

    Each mantra keeps the *number* of skills it had, so its weight on the grid
    (and its cost) stays meaningful, but the skills themselves are drawn from
    the whole grid instead of being traded as a block: a mantra that used to
    teach two skills now teaches two skills that between them can come from two
    unrelated mantras.

    The pool is only permuted, so the total multiset of skills is unchanged, and
    a repair pass makes sure no mantra gets one of its own skills back.  Returns
    the number of mantras whose skill list changed.
    """
    counts = [len(m.skills) for m in mantras]
    pool = []
    for i, m in enumerate(mantras):
        for s in m.skills:
            pool.append((s, i))          # (skill id, where it came from)
    rng.shuffle(pool)

    deal = []
    pos = 0
    for n in counts:
        deal.append(pool[pos:pos + n])
        pos += n
    _avoid_own_skills(deal)

    moved = 0
    for i, m in enumerate(mantras):
        new = [s for s, _src in deal[i]]
        if new != m.skills:
            moved += 1
        m.skills = new
    return moved


def _avoid_own_skills(deal):
    """Swap away the entries a mantra would get straight back from itself.

    Walks the offending entries and trades each with an entry elsewhere that is
    foreign to both sides.  Best effort: with ~250 skills over ~100 mantras the
    first pass almost always clears everything, and the loop is capped so a
    pathological layout cannot hang.
    """
    n = len(deal)
    for _ in range(8):
        bad = [(i, k) for i in range(n) for k in range(len(deal[i]))
               if deal[i][k][1] == i]
        if not bad:
            return
        for i, k in bad:
            if deal[i][k][1] != i:       # an earlier swap already fixed it
                continue
            swapped = False
            for j in range(n):
                if j == i:
                    continue
                for l in range(len(deal[j])):
                    src = deal[j][l][1]
                    if src == j or src == i:
                        continue         # would leave j or i with its own
                    deal[i][k], deal[j][l] = deal[j][l], deal[i][k]
                    swapped = True
                    break
                if swapped:
                    break


def skill_sources(mantras):
    """skill id -> the set of mantra ids that teach it (call before shuffling)."""
    where = {}
    for m in mantras:
        for s in m.skills:
            where.setdefault(s, set()).add(m.id)
    return where


def spread(mantras, sources):
    """Average number of distinct original mantras behind one skill list.

    ~1.0 means every list came from a single other mantra (the coarse 'sets'
    mode: the source set is the donor plus, for its own leftover skills, itself);
    a large value means the skills really are mixed across the grid.
    """
    if not mantras:
        return 0.0
    total = 0.0
    for m in mantras:
        srcs = set()
        for s in m.skills:
            srcs |= sources.get(s, set())
        total += len(srcs)
    return total / float(len(mantras))


def summary(mantras):
    live_ms = live(mantras)
    gift_ms = gifted(mantras)
    return {
        'mantras': len(mantras),
        'mantras_with_skills': len(live_ms),
        'mantra_skills': sum(len(m.skills) for m in live_ms),
        'mantras_with_bonus': len(gift_ms),
        'mantra_bonus_points': sum(sum(m.bonus) for m in gift_ms),
    }

