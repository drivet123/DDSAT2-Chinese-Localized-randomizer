# Minimal /battle/ENCOUNT.TBL model (own reverse engineering).
#
# The file is two tables:
#   u32 table1_size (0xa000)
#   table1 : table1_size / 40 records of 40 bytes -- encounter groups.
#   table2 : (size - table1_size) / 12 records of 12 bytes -- separate.
#
# In table 1 the five u16 slots at byte offsets 10,12,14,16,18 hold the
# enemy ids (0 = empty).  An enemy id N maps to UNIT.TBL record N + 127
# (verified in-game 2026-09-12, see docs/FINDINGS_ENCOUNTER.md).
# Table 2 is left completely untouched.
import struct

RECORD = 40
SLOTS = (10, 12, 14, 16, 18)
DEFAULT_TABLE1 = 0xA000


class EncountRecord(object):
    def __init__(self, data, index):
        self.data = bytearray(data)
        self.index = index

    @property
    def enemy_ids(self):
        return [struct.unpack_from('<H', self.data, o)[0] for o in SLOTS]

    @enemy_ids.setter
    def enemy_ids(self, ids):
        ids = list(ids) + [0] * (5 - len(ids))
        for o, v in zip(SLOTS, ids[:5]):
            struct.pack_into('<H', self.data, o, max(0, min(0xFFFF, int(v))))

    @property
    def raw(self):
        return bytes(self.data)

    def __repr__(self):
        return '<Encount %d %s>' % (self.index, self.enemy_ids)


class EncountTable(object):
    def __init__(self, data):
        self.data = bytes(data)
        n = struct.unpack_from('<I', data, 0)[0] if len(data) >= 4 else 0
        if n <= 0 or n > len(data) - 40 or n % RECORD:
            n = min(DEFAULT_TABLE1, len(data))
            n -= n % RECORD
        self.table1_size = n
        self.tail = self.data[n:]
        self.records = [EncountRecord(self.data[i * RECORD:(i + 1) * RECORD], i)
                        for i in range(n // RECORD)]

    def all_enemy_ids(self):
        out = set()
        for r in self.records:
            for e in r.enemy_ids:
                if e and e != 0xFFFF:
                    out.add(e)
        return out

    def to_bytes(self):
        buf = bytearray()
        for r in self.records:
            buf += r.raw
        buf += self.tail
        return bytes(buf)
