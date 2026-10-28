# Mapping between the enemy ids stored in /battle/ENCOUNT.TBL and the
# records of /battle/UNIT.TBL.
#
# Empirically (verified in-game 2026-09-12): enemy id N is unit record N + 127.
#   evidence: with every ENCOUNT record patched to id 37 the battle panel showed
#   UNIT record 164 = level 52 / HP 600 / MP 368, i.e. 37 + 127.
# The first 127 records of UNIT.TBL are a different (attribute) table.
# The unit records that are actually usable are those with a real HP value
# and a real stat block; the rest are placeholder/zero slots.
from .unit import UnitTable

UNIT_BASE = 127


class UnitMap(object):
    def __init__(self, units):
        self.units = units
        self.power = {}
        for rec in range(UNIT_BASE, 896):
            if self._usable(rec):
                self.power[rec] = units.power(rec)

    def _usable(self, rec):
        hp = self.units.hp(rec)
        if hp <= 0 or hp == 0xFFFF:
            return False
        st = self.units.stats(rec)
        if all(x == 0 for x in st):
            return False
        return True

    def record_for_id(self, eid):
        rec = eid + UNIT_BASE
        return rec if 0 <= rec < 896 else None

    def id_for_record(self, rec):
        return rec - UNIT_BASE

    def valid_ids(self):
        return sorted(self.id_for_record(r) for r in self.power)

    def valid_records(self):
        return sorted(self.power)

    def hp_of_id(self, eid):
        rec = self.record_for_id(eid)
        return self.units.hp(rec) if rec is not None else 0

    def statsum_of_id(self, eid):
        rec = self.record_for_id(eid)
        return sum(self.units.stats(rec)) if rec is not None else 0

    def power_of_id(self, eid):
        rec = self.record_for_id(eid)
        if rec is None:
            return 0.0
        return self.power.get(rec, 0.0)

    def ids_by_power(self):
        ids = self.valid_ids()
        ids.sort(key=lambda e: self.power_of_id(e))
        return ids
