# /battle/UNIT.TBL - unit (enemy / boss / party) records.
#
# Layout (empirically derived, see FINDINGS.md):
#   16-byte header, then 896 records of 76 bytes.
#   u16 skill[5]  @0..9    (0 = empty, ids up to 571)
#   u16 field12   @12    (NOT hp; verified against in-battle enemy stats)
#   u16 field14   @14
#   u16 field16   @16
#   u16 field18   @18
#   u8  model     @52
#   u8  level     @53
#   u16 hp        @54    (max/cur pair; copied verbatim into the battle unit)
#   u16 hp_cur    @56
#   u16 mp        @58
#   u16 mp_cur    @60
#   u8  stats[5]  @64..68  (max 99; placeholder default 28,28,28,23,14)
#   u8  kind      @69
#   u16 extra[2]  @72..75
import struct

RECORD = 76
HEADER = 16
COUNT = 896
PLACEHOLDER_STATS = (28, 28, 28, 23, 14)
PLACEHOLDER_HP = 0x7FFF


class UnitTable(object):
    def __init__(self, data):
        if len(data) != HEADER + RECORD * COUNT:
            raise ValueError('unexpected UNIT.TBL size %d' % len(data))
        self.data = bytearray(data)

    # ------------------------------------------------------------- access
    def record(self, i):
        return self.data[HEADER + i * RECORD:HEADER + (i + 1) * RECORD]

    def _u16(self, i, off):
        return struct.unpack_from('<H', self.data, HEADER + i * RECORD + off)[0]

    def _set_u16(self, i, off, v):
        struct.pack_into('<H', self.data, HEADER + i * RECORD + off, max(0, min(0xFFFF, int(v))))

    def hp(self, i):
        return self._u16(i, 54)

    def hp_cur(self, i):
        return self._u16(i, 56)

    def set_hp(self, i, v):
        # the record stores (max, current) as a pair; both must move together
        self._set_u16(i, 54, v)
        self._set_u16(i, 56, v)

    def mp(self, i):
        return self._u16(i, 58)

    def set_mp(self, i, v):
        self._set_u16(i, 58, v)
        self._set_u16(i, 60, v)

    def level(self, i):
        return self.data[HEADER + i * RECORD + 53]

    def set_level(self, i, v):
        self.data[HEADER + i * RECORD + 53] = max(0, min(255, int(v)))

    def stats(self, i):
        o = HEADER + i * RECORD + 64
        return list(self.data[o:o + 5])

    def set_stats(self, i, vals):
        o = HEADER + i * RECORD + 64
        for k, v in enumerate(vals):
            self.data[o + k] = max(0, min(255, int(v)))

    def skills(self, i):
        out = [self._u16(i, k * 2) for k in range(5)]
        out += [self._u16(i, 72), self._u16(i, 74)]
        return out

    def set_skills(self, i, vals):
        vals = list(vals) + [0] * (7 - len(vals))
        for k in range(5):
            self._set_u16(i, k * 2, vals[k])
        self._set_u16(i, 72, vals[5])
        self._set_u16(i, 74, vals[6])

    def is_placeholder(self, i):
        return self.hp(i) >= PLACEHOLDER_HP and tuple(self.stats(i)[:5]) == PLACEHOLDER_STATS

    def is_empty(self, i):
        return not any(self.record(i))

    # ------------------------------------------------------- whole record
    def swap(self, a, b):
        ra = HEADER + a * RECORD
        rb = HEADER + b * RECORD
        x = bytes(self.data[ra:ra + RECORD])
        y = bytes(self.data[rb:rb + RECORD])
        self.data[ra:ra + RECORD] = y
        self.data[rb:rb + RECORD] = x

    def get_record(self, i):
        o = HEADER + i * RECORD
        return bytes(self.data[o:o + RECORD])

    def put_record(self, i, blob):
        if len(blob) != RECORD:
            raise ValueError('record must be %d bytes' % RECORD)
        o = HEADER + i * RECORD
        self.data[o:o + RECORD] = blob

    def scale(self, i, hp_mult=1.0, stat_mult=1.0, mp_mult=1.0, max_stat=255):
        """Multiply HP / MP / the five stats, clamping to the field width."""
        if self.is_empty(i):
            return
        self.set_hp(i, int(round(self.hp(i) * hp_mult)))
        self.set_mp(i, int(round(self.mp(i) * mp_mult)))
        if stat_mult != 1.0:
            s = self.stats(i)
            s = [max(1, min(max_stat, int(round(v * stat_mult)))) for v in s]
            self.set_stats(i, s)

    def power(self, i):
        """Rough strength proxy used to match encounters of similar difficulty."""
        if self.is_empty(i) or self.is_placeholder(i):
            return 0.0
        s = self.stats(i)
        return self.hp(i) * 0.5 + sum(s) * 25.0 + self.mp(i) * 0.5

    def valid_units(self):
        return [i for i in range(COUNT) if not self.is_empty(i) and not self.is_placeholder(i)]

    def to_bytes(self):
        return bytes(self.data)
