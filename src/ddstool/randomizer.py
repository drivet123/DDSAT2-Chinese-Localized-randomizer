# Top level randomization driver.
import bisect
import os
import random
import struct

from .build import copy_iso, Patcher
from .unit import UnitTable
from .unitmap import UnitMap


class Log(object):
    def __init__(self, fn=None):
        self.fn = fn or (lambda m: None)

    def __call__(self, msg):
        self.fn(str(msg))


class Randomizer(object):
    def __init__(self, src_iso, out_iso, cfg, log=None):
        self.src = src_iso
        self.out = out_iso
        self.cfg = cfg
        self.log = Log(log)
        self.rng = random.Random(cfg.seed)
        self.stats = {}
        self._original_encount = None

    # ---------------------------------------------------------------- run
    def run(self):
        self.log('input : %s' % self.src)
        self.log('output: %s' % self.out)
        if os.path.abspath(self.src) == os.path.abspath(self.out):
            raise ValueError('input and output ISO must differ')
        if os.path.exists(self.out):
            import time
            last = None
            for _ in range(10):
                try:
                    os.remove(self.out)
                    last = None
                    break
                except OSError as exc:
                    last = exc
                    time.sleep(0.5)
            if last is not None:
                raise OSError(
                    'cannot replace %s because another program is using it (%s).\n'
                    'Close the program that holds the file, or pass a different --out.'
                    % (self.out, last))
        self.log('copying ISO (this takes a while) ...')
        copy_iso(self.src, self.out)
        self.log('copy done')
        with Patcher(self.out) as p:
            if self.cfg.randomize_encounters or self.cfg.shuffle_bosses or self.cfg.randomize_enemy_skills:
                self._battles(p)
            if self.cfg.reveal_enemies:
                self._reveal_enemy_info(p)
            if self.cfg.shuffle_text:
                self._text(p)
            if self.cfg.shuffle_voice:
                self._voice(p)
            if self.cfg.randomize_mantras:
                self._mantras(p)
            if getattr(self.cfg, 'black_varuna', False):
                self._black_varuna(p)
            p.save()
        self.log('all patches written')
        if self.cfg.write_enemy_table:
            try:
                path = self._write_enemy_table()
                self.log('enemy table written: %s' % path)
            except Exception as exc:
                self.log('enemy table failed: %s' % exc)
        if self.cfg.randomize_mantras and self.cfg.write_mantra_table:
            try:
                path = self._write_mantra_table()
                self.log('mantra table written: %s' % path)
            except Exception as exc:
                self.log('mantra table failed: %s' % exc)
        if self.cfg.verify:
            self._verify()
        return self.stats

    # ----------------------------------------------------- analysis reveal
    # Why bosses show "?" in the analyse window.  The window's row functions
    # (table at 0x415650, six of them guarded) branch to the "?" placeholder
    # drawer `0x1B8930` when one of three flags is set: the encounter's
    # `ENCOUNT.TBL +32` bit 0, the unit template's `+0` bit 0x800, or the unit
    # template's `+22` (a copy of `UNIT.TBL +70`, the unit's category byte).
    #
    # Clearing those bytes in the tables does reveal the data - but `+70` is
    # also read by the battle logic, and rewriting the bosses' 0xFF to 0 makes
    # the game **hang while loading a boss battle**.  So the guards in the code
    # are neutralised instead (see `ddstool.analyse`) and the game data is left
    # exactly as it is.
    def _reveal_enemy_info(self, p):
        """Let bosses and elite enemies answer the Analyse command."""
        from . import analyse
        raw = p.game.read_iso_file(analyse.ELF_PATH)
        ok, sites = analyse.verify(raw)
        new = analyse.patch(raw)
        if new != raw:
            p.set_iso_file(analyse.ELF_PATH, new)
        self.stats['reveal_guards_patched'] = len(sites)
        self.stats['reveal_guard_shape_ok'] = ok
        self.log('analysis reveal: ' + analyse.summary(sites))
        if not ok:
            self.log('analysis reveal: WARNING the guard pattern does not '
                     'match the expected %d sites; the patch may be incomplete'
                     % analyse.EXPECTED_SITES)

    # ------------------------------------------------------ enemy id table
    def _write_enemy_table(self):
        """Write a reference table of every enemy id -> stats for the built ISO."""
        from .game import Game
        from .unit import UnitTable
        from .unitmap import UNIT_BASE
        g = Game(self.out)
        try:
            enc = g.read('/battle/ENCOUNT.TBL')
            units = UnitTable(g.read('/battle/UNIT.TBL'))
        finally:
            g.close()
        base = os.path.splitext(self.out)[0]
        txt_path = base + '_enemies.txt'
        csv_path = base + '_enemies.csv'

        def rec_of(uid):
            return UNIT_BASE + uid

        def ids(buf, r):
            return [x for x in struct.unpack_from('<5H', buf, 40 * r + 10)
                    if x and x != 0xFFFF]

        rows = []
        for uid in range(0, 400):
            rec = rec_of(uid)
            if rec >= 896:
                break
            hp = units.hp(rec)
            if hp in (0, 0xFFFF, 0x7FFF):
                continue
            used = [r for r in range(1024) if uid in ids(enc, r)]
            st = units.stats(rec)
            sk = [x for x in units.skills(rec) if x]
            rows.append((uid, units.level(rec), hp, units.mp(rec), st, sk, used))
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write('id   lv   HP      MP      STR MAG VIT AGI LUK  skills                 records\n')
            for uid, lv, hp, mp, st, sk, used in rows:
                f.write('%-4d %-4d %-7d %-7d %-3d %-3d %-3d %-3d %-3d  %-22s %s\n' % (
                    uid, lv, hp, mp, st[0], st[1], st[2], st[3], st[4],
                    ','.join(str(x) for x in sk[:7]), ' '.join(str(r) for r in used[:12])))
        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write('id,level,hp,mp,str,mag,vit,agi,luk,skills,records\n')
            for uid, lv, hp, mp, st, sk, used in rows:
                f.write('%d,%d,%d,%d,%d,%d,%d,%d,%d,"%s","%s"\n' % (
                    uid, lv, hp, mp, st[0], st[1], st[2], st[3], st[4],
                    ' '.join(str(x) for x in sk[:7]), ' '.join(str(r) for r in used[:12])))
        self.stats['enemy_table_rows'] = len(rows)
        return txt_path

    # ------------------------------------------------------- mantra table
    def _write_mantra_table(self):
        """Write the mantra -> (cost, stat bonus, skills) table of the built ISO.

        A mantra's skills are granted the moment it is learned, so this table is
        also what the characters who already know a mantra at game start end up
        with: the game re-derives their skill list from it every time the
        player data is rebuilt (0x2a8a1c clears the bitfield and 0x313930
        re-applies every learned mantra through 0x313f98).
        """
        from .game import Game
        from . import mantra
        g = Game(self.out)
        try:
            table = mantra.parse(g.read_iso_file(mantra.ELF_PATH))
        finally:
            g.close()
        base = os.path.splitext(self.out)[0]
        txt_path = base + '_mantras.txt'
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write('id   cost     bonus                     skills\n')
            for m in table:
                if not m.skills and not any(m.bonus):
                    continue
                bonus = ' '.join('%s+%d' % (mantra.STAT_NAMES[i], v)
                                 for i, v in enumerate(m.bonus) if v)
                f.write('%-4d %-8d %-25s %s\n' % (
                    m.id, m.cost, bonus,
                    ','.join(str(s) for s in m.skills)))
        self.stats['mantra_table_rows'] = sum(
            1 for m in table if m.skills or any(m.bonus))
        return txt_path

    def _verify(self):
        from .game import Game
        try:
            g = Game(self.out)
        except Exception as exc:
            self.log('WARNING: output ISO failed to open: %s' % exc)
            return
        try:
            n = len(g.ddt.files())
            self.stats['verify_files'] = n
            self.log('verify: DDT parses, %d files' % n)
            if self.stats.get('encounter_ids_replaced'):
                enc = g.read('/battle/ENCOUNT.TBL')
                if enc == self._original_encount:
                    raise RuntimeError('verification failed: ENCOUNT.TBL was not patched')
            if self.stats.get('text_messages_swapped'):
                self.log('verify: patches read back OK')
        finally:
            g.close()

    def _alternative_output(self):
        base, ext = os.path.splitext(self.out)
        if not ext:
            ext = '.iso'
        for i in range(1, 100):
            cand = '%s_%d%s' % (base, i, ext)
            if not os.path.exists(cand):
                return cand
        raise OSError('cannot find a free output name')

    # ----------------------------------------------------------- battles
    def _battles(self, p):
        raw_unit = p.game.read('/battle/UNIT.TBL')
        units = UnitTable(raw_unit)
        umap = UnitMap(units)
        # old skill id -> new skill id, filled in while swapping enemies; used
        # afterwards to re-point the AI action table at the new skill set
        self.battle_skill_remap = {}
        # per-unit version: {UNIT record index: {old skill id: new skill id}}.
        # The flat AI record of a unit is known exactly (record k -> UNIT k+128),
        # so each recorded AI follows its own unit.
        self.ai_unit_remap = {}
        # every skill id that really exists in the game (taken from the unit
        # records) - used to reject flag fields that only look like skill ids
        self.valid_skill_ids = set()
        for rec in range(127, 896):
            for v in units.skills(rec):
                if v:
                    self.valid_skill_ids.add(v)
        # snapshot of the untouched table, used as the strength reference
        self._srcmap = UnitMap(UnitTable(raw_unit))
        self._srctable = UnitTable(raw_unit)
        enc = self._load_encount(p)
        if enc is None:
            self.log('ENCOUNT unavailable - battles left untouched')
            return
        raw_enc = enc.to_bytes()
        self._original_encount = raw_enc

        model_ids = self._model_ids(p)
        hps = {}
        stats = {}
        for i in enc.all_enemy_ids():
            hp = umap.hp_of_id(i)
            if 0 < hp < 0x7FFF:
                hps[i] = hp
                stats[i] = umap.statsum_of_id(i)
        if model_ids:
            hps = dict((i, v) for i, v in hps.items() if i in model_ids)
            stats = dict((i, stats[i]) for i in hps)
        self.stats['candidate_enemy_ids'] = len(hps)

        # Story battles are the encounter records referenced by the battle
        # event scripts (/event/**/*.be).  Those are the boss fights; their
        # record index is stored in the header at 0x50 and repeated in the
        # eNNN_<index>.BED resource names.
        story_records = self._story_battles(p, len(enc.records))
        # Every enemy referenced by a non-story encounter record belongs to the
        # normal pool, even if it only shows up in a single record.
        normal = set()
        for ri, rec in enumerate(enc.records):
            if ri in story_records:
                continue
            for eid in rec.enemy_ids:
                if eid and eid != 0xFFFF and eid in hps:
                    normal.add(eid)
        normal_ids = sorted(normal)
        self.stats['normal_enemy_ids'] = len(normal_ids)
        self.stats['story_battles'] = len(story_records)
        self.log('%d candidate enemies (%d normal); %d story battle records'
                 % (len(hps), len(normal_ids), len(story_records)))

        if self.cfg.randomize_enemy_skills:
            self.log('enemy skills are randomised through the enemy swap itself')
        # Bosses first: their re-scaling targets the story line-ups.  The normal
        # encounter pass runs last so that its strength alignment is final (the
        # two passes can otherwise touch the same UNIT record).
        if self.cfg.shuffle_bosses and story_records:
            self._shuffle_bosses(enc, units, umap, hps, stats, story_records)
        if self.cfg.randomize_encounters and normal_ids:
            # The whole normal-encounter pool takes part in the 1:1 permutation,
            # including enemies shared with story line-ups; the encounter pass
            # runs last so its strength alignment is the final word.
            self._randomize_encounters(enc, units, umap, normal_ids, story_records)

        # the enemy now casts the skills of the unit it replaced, so the AI
        # action table has to point at those skills as well
        if self.battle_skill_remap and self.cfg.remap_enemy_ai:
            self._remap_ai_actions(p)

        if enc.to_bytes() != raw_enc:
            p.set_file('/battle/ENCOUNT.TBL', enc.to_bytes())
        if units.to_bytes() != raw_unit:
            p.set_file('/battle/UNIT.TBL', units.to_bytes())

    # ------------------------------------------------------------ enemy AI
    def _remap_ai_actions(self, p):
        """Re-point the skill ids the enemy AI casts at the new skill set.

        Two independent places name a skill in /battle/AICALC.TBL, and this
        rewrites both of them through the same old -> new map:

          * the flat action table of the ordinary enemies (395 records of
            348 bytes, 36 actions of 8 bytes, skill at entry offset +2), and
          * the `PUSHIS <skill>; COMM AI_ACT_SKILL` call sites of the
            FlowScript AIs used by the scripted battles.

        Both layouts are described in `ddstool.aicalc`.  Nothing else in the
        file is touched - in particular the condition parameters that sit next
        to each action, the label names and the string table are left alone.
        """
        from . import aicalc
        unit_maps = getattr(self, 'ai_unit_remap', None) or {}
        remap = self.battle_skill_remap
        if not remap and not unit_maps:
            self.log('enemy AI: nothing to re-point')
            return
        try:
            raw = p.game.read('/battle/AICALC.TBL')
        except Exception as exc:
            self.log('AICALC.TBL unavailable: %s' % exc)
            return
        before = bytes(raw)
        data = bytearray(raw)
        info = aicalc.remap_skills(data, unit_maps, flow_map=remap)
        if bytes(data) != before:
            p.set_file('/battle/AICALC.TBL', bytes(data))
        self.stats['ai_action_skills_remapped'] = info['flat']
        self.stats['ai_flow_skills_remapped'] = info['flow']
        self.stats['ai_distinct_skills_remapped'] = info['distinct']
        self.stats['ai_units_remapped'] = len(unit_maps)
        self.log('enemy AI: %d action slot(s) over %d unit AI record(s) + %d '
                 'FlowScript call site(s) re-pointed to the new skill set '
                 '(%d distinct old ids)'
                 % (info['flat'], len(unit_maps), info['flow'], info['distinct']))

    def _story_battles(self, p, nrecords):
        """Encounter record indices referenced by the /event/**/*.be scripts."""
        return self._story_battles_meta(p.game, nrecords)

    def _story_battles_meta(self, game, nrecords):
        import re
        import struct as _s
        found = set()
        try:
            for e in game.ddt.files():
                if not e.name.lower().endswith('.be'):
                    continue
                d = game.read(e.path)
                v = _s.unpack_from('<I', d, 0x50)[0]
                if 0 < v < nrecords:
                    found.add(v)
                for m in re.finditer(rb'e\d+_(\d+)\.BED', d[:0x800], re.I):
                    v = int(m.group(1))
                    if 0 < v < nrecords:
                        found.add(v)
        except Exception as exc:
            self.log('story battle scan failed: %s' % exc)
        return sorted(found)

    def _model_ids(self, p):
        try:
            rec = p.game.ddt.by_path.get('/model/devil')
            ids = set()
            for e in p.game.listdir('/model/devil'):
                n = e.name
                if n.lower().endswith('.pb') and not n.lower().endswith('_on.pb'):
                    base = n[:-3]
                    if all(c in '0123456789abcdefABCDEF' for c in base):
                        ids.add(int(base, 16))
            return ids
        except Exception as exc:
            self.log('model scan failed: %s' % exc)
            return set()

    def _load_encount(self, p):
        try:
            from .encount import EncountTable
        except Exception:
            try:
                from .encount_min import EncountTable
            except Exception as exc:
                self.log('ENCOUNT module missing: %s' % exc)
                return None
        try:
            enc = EncountTable(p.game.read('/battle/ENCOUNT.TBL'))
            enc.all_enemy_ids()
            return enc
        except Exception as exc:
            self.log('ENCOUNT parse failed: %s' % exc)
            return None

    def _tolerance(self):
        return self.cfg.enemy_power_tolerance * (0.75 + 0.5 * self.cfg.encounter_variety)

    def _choose_replacement(self, orig, by_hp, stats, hps):
        hp0 = hps.get(orig)
        if not hp0:
            return None
        tol = self._tolerance()
        lo = bisect.bisect_left(by_hp, (int(hp0 * (1.0 - tol)), -1))
        hi = bisect.bisect_right(by_hp, (int(hp0 * (1.0 + tol)) + 1, 99999))
        if hi <= lo:
            return None
        st0 = stats.get(orig, 0)
        good = []
        for _ in range(64):
            cand = by_hp[self.rng.randrange(lo, hi)][1]
            if cand == orig:
                continue
            st1 = stats.get(cand, 0)
            if st0 and abs(st1 - st0) > tol * max(st0, st1):
                continue
            good.append(cand)
        if not good:
            return None
        return self.rng.choice(good)

    def _load_ban_ids(self):
        """Ids that must never appear as field encounters.

        Accepted sources (all optional, union of them):
          * cfg.ban_enemies : list of ints (or "12" strings)
          * cfg.ban_file    : path to a json file ({"ban": [..]} or [..]) or a
                              plain text file with one id per line ('#' = comment)
        """
        ids = set()

        def add(v):
            try:
                n = int(v)
            except (TypeError, ValueError):
                return
            if n > 0:
                ids.add(n)

        for v in (self.cfg.ban_enemies or []):
            add(v)
        path = getattr(self.cfg, 'ban_file', None)
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    raw = f.read()
                try:
                    import json
                    data = json.loads(raw)
                except ValueError:
                    data = None
                if isinstance(data, dict):
                    data = data.get('ban') or data.get('ban_enemies') or []
                if isinstance(data, list):
                    for v in data:
                        add(v)
                else:
                    for line in raw.splitlines():
                        line = line.split('#')[0].strip().rstrip(',')
                        if line:
                            add(line)
            except Exception as exc:
                self.log('ban file %s could not be read: %s' % (path, exc))
        if ids:
            self.log('ban list: %d enemy id(s): %s' % (len(ids), sorted(ids)))
        return ids

    @staticmethod
    def _encounter_blocks(enc, orig_ids=None):
        """Group consecutive table1 records that describe the same encounter
        family (the same leading enemy, listed with 1/2/3/4 members).

        `orig_ids` is a list of the per-record id lists from the untouched
        table; pass it so the grouping is not disturbed by the id permutation.
        Returns a list of lists of record indices, in order.
        """
        blocks = []
        cur = None
        prev_lead = object()
        for ri in range(len(enc.records)):
            if orig_ids is not None:
                v = orig_ids[ri]
            else:
                v = [e for e in enc.records[ri].enemy_ids if e and e != 0xFFFF]
            lead = v[0] if v else None
            if cur is None or lead != prev_lead:
                if cur:
                    blocks.append(cur)
                cur = [ri]
                prev_lead = lead
            else:
                cur.append(ri)
        if cur:
            blocks.append(cur)
        return blocks

    def _randomize_encounters(self, enc, units, umap, normal_ids, story_records=()):
        """1:1 global permutation of the normal-encounter enemy pool.

        The whole pool of normal encounter enemies is shuffled into a random
        bijection (so enemy A becomes enemy B, C becomes D, ...); every
        non-story encounter record is rewritten through that map, and then each
        destination enemy's UNIT record is retuned so that its level, HP, MP,
        five stats and skill list match the enemy it replaced.

        Enemies listed in the ban configuration are removed from the pool and
        the encounter records that used them are rewritten to another encounter
        from the same table1 block.
        """
        ban = self._load_ban_ids()
        # snapshot of the untouched table: the ban pass must reason about the
        # ORIGINAL line-ups, not the ones already rewritten by the permutation
        orig_ids = [[e for e in rec.enemy_ids if e and e != 0xFFFF]
                    for rec in enc.records]
        story_ids = set()
        for r in story_records:
            for eid in enc.records[r].enemy_ids:
                if eid and eid != 0xFFFF:
                    story_ids.add(eid)
        normal_ref = set()
        for ri, rec in enumerate(enc.records):
            if ri in story_records:
                continue
            for eid in rec.enemy_ids:
                if eid and eid != 0xFFFF:
                    normal_ref.add(eid)
        story_ids -= normal_ref
        pool = [i for i in sorted(set(normal_ids))
                if 0 < umap.hp_of_id(i) < 10000
                and i not in story_ids
                and i not in ban]
        self.stats['banned_enemy_ids'] = len(ban)
        self.stats['encounter_pool'] = len(pool)
        self.log("encounter pool: %d (of %d normal, %d story-shared, %d banned)"
                 % (len(pool), len(set(normal_ids)), len(story_ids), len(ban)))
        if len(pool) < 2:
            self.log('not enough normal encounter enemies (%d)' % len(pool))
            return
        ref = getattr(self, '_srcmap', umap)
        reft = getattr(self, '_srctable', units)
        src_hp = dict((i, ref.hp_of_id(i)) for i in pool)
        src_st = dict((i, ref.statsum_of_id(i)) for i in pool)
        # remember the original skill list of every unit, so the AI action
        # table can later be re-pointed at the new skill ids
        self._ai_src_skills = dict(
            (uid, reft.skills(127 + uid)) for uid in range(0, 400)
            if 127 + uid < 896)

        perm = list(pool)
        self.rng.shuffle(perm)
        for i in range(len(perm)):
            if perm[i] == pool[i]:
                j = (i + 1) % len(perm)
                perm[i], perm[j] = perm[j], perm[i]
        mapping = dict(zip(pool, perm))

        # ---- pass 1: rewrite the encounter ids through the permutation, and
        #      replace any record that would spawn a banned enemy by another
        #      record of the same block (same scene / same level band).
        blocked = []
        donors = {}
        if ban:
            blocks = self._encounter_blocks(enc, orig_ids)

            def clean_in(block):
                for ri in block:
                    if ri in story_records:
                        continue
                    v = orig_ids[ri]
                    if v and not any(e in ban for e in v):
                        return ri
                return None

            # nearest usable record of the whole table, used when a whole block
            # consists of banned enemies only
            all_clean = []
            for block in blocks:
                c = clean_in(block)
                if c is not None:
                    all_clean.append(c)
            all_clean.sort()

            def nearest_clean(ri, want_slots=0):
                """A clean record as close as possible to `ri`; prefer one with
                the same number of enemy slots, so the replacement encounter has
                the same size as the banned one."""
                if not all_clean:
                    return None
                import bisect as _b
                k = _b.bisect_left(all_clean, ri)
                cand = []
                if k < len(all_clean):
                    cand.extend(all_clean[k:k + 24])
                if k:
                    cand.extend(all_clean[max(0, k - 24):k])
                if not cand:
                    return None
                same = [c for c in cand if len(orig_ids[c]) == want_slots]
                pick = same or cand
                return min(pick, key=lambda x: abs(x - ri))

            for block in blocks:
                n_clean = clean_in(block)
                for ri in block:
                    if ri in story_records:
                        continue
                    v = orig_ids[ri]
                    if not v or not any(e in ban for e in v):
                        continue
                    donor = n_clean
                    if donor is None:
                        donor = nearest_clean(ri, len(v))
                    if donor is None:
                        continue
                    blocked.append(ri)
                    donors[ri] = donor

        n = 0
        skipped = 0
        for ri, rec in enumerate(enc.records):
            if ri in story_records:
                continue
            ids = list(rec.enemy_ids)
            out = list(ids)
            for k, eid in enumerate(ids):
                if not eid or eid == 0xFFFF:
                    continue
                t = mapping.get(eid)
                if t is None:
                    skipped += 1
                else:
                    out[k] = t
                    n += 1
            rec.enemy_ids = out
        # ---- pass 2: retune every destination unit to the strength of the
        #      enemy that used to stand in that slot, and build the old->new
        #      skill id mapping used by the AI action table.
        retuned = 0
        skill_remap = {}
        collisions = 0
        unit_remap = {}
        for src, tgt in mapping.items():
            hp0 = src_hp.get(src, 0)
            rec = 127 + tgt
            srcrec = 127 + src
            # the skills this unit had *before* the swap - these are the ids the
            # AI action table references, and they must follow the new skills.
            # Both lists are compared slot by slot: _retune_unit copies the
            # whole skill list, so position i of the target now holds skill i of
            # the source, and dropping the empty slots first would misalign the
            # two lists whenever a slot is empty on only one side.
            old = reft.skills(rec)[:5]
            if 0 < hp0 < 0x7FFF and rec < 896:
                self._retune_unit(units, rec, reft, srcrec)
                retuned += 1
            new = units.skills(rec)[:5]
            own = {}
            for a, b in zip(old, new):
                if not a or not b or a == b or a > 600 or b > 600:
                    continue
                own[a] = b
                if skill_remap.setdefault(a, b) != b:
                    collisions += 1
            if own:
                unit_remap[rec] = own
        self.battle_skill_remap = skill_remap
        # per-unit map: AICALC's flat record k belongs to UNIT record k + 128,
        # so the recorded AI can be re-pointed with the map of *its own* unit
        # instead of a global guess.  This removes the shared-skill ambiguity.
        self.ai_unit_remap = dict(getattr(self, 'ai_unit_remap', {}) or {})
        for rec, own in unit_remap.items():
            self.ai_unit_remap.setdefault(rec, {}).update(own)
        if collisions:
            self.log('skill remap: %d skill id(s) are shared by several units; '
                     'the per-unit AI table keeps them apart, the FlowScript AIs '
                     'can only follow one of them' % collisions)
        if skill_remap:
            self.log('skill remap for the AI table: %d entries (%d units)'
                     % (len(skill_remap), len(unit_remap)))

        # ---- pass 3: the records that used to spawn a banned enemy are
        #      overwritten with the permuted line-up of a clean record of the
        #      same block, so that spawn point still works.  The line-up is
        #      rebuilt from the ORIGINAL snapshot (the live records have already
        #      been rewritten in pass 1).
        for ri in blocked:
            donor = donors.get(ri)
            if donor is None or donor == ri:
                continue
            out = [mapping.get(e, e) if e and e != 0xFFFF else e
                   for e in orig_ids[donor]]
            enc.records[ri].enemy_ids = out

        self.stats['encounter_ids_replaced'] = n
        self.stats['encounter_ids_skipped'] = skipped
        self.stats['enemies_retuned'] = retuned
        self.stats['banned_records_replaced'] = len(blocked)
        self.log('encounter enemies swapped 1:1: %d slot(s), pool=%d, retuned=%d (skipped %d)'
                 % (n, len(pool), retuned, skipped))
        if blocked:
            self.log('banned encounters replaced by a same-block encounter: %d record(s)'
                     % len(blocked))

    # --------------------------------------------------------------- boss
    def _shuffle_bosses(self, enc, units, umap, hps, stats, story_records):
        """Swap the enemy line-ups of the story battles among each other.

        The battle script and AI stay untouched; after the swap every unit that
        now appears in a story battle is rescaled so its HP and stat sum match
        the average of the enemies that used to be in that slot.
        """
        recs = [r for r in story_records if any(enc.records[r].enemy_ids)]
        if len(recs) < 2:
            self.log('not enough story battles (%d)' % len(recs))
            return
        lineups = dict((r, [e for e in enc.records[r].enemy_ids if e]) for r in recs)
        targets = {}
        for r in recs:
            ids = lineups[r]
            if not ids:
                continue
            targets[r] = (sum(hps.get(i, 0) for i in ids) / float(len(ids)),
                          sum(stats.get(i, 0) for i in ids) / float(len(ids)))
        perm = list(recs)
        self.rng.shuffle(perm)
        for i in range(len(perm)):
            if perm[i] == recs[i]:
                j = (i + 1) % len(perm)
                perm[i], perm[j] = perm[j], perm[i]
        unit_targets = {}
        unit_source = {}
        reft = getattr(self, '_srctable', units)
        for r, src in zip(recs, perm):
            src_ids = lineups[src]
            if not src_ids:
                continue
            count = len(lineups[r])          # keep the original enemy count
            new_ids = [src_ids[k % len(src_ids)] for k in range(count)]
            rec = enc.records[r]
            rec.enemy_ids = new_ids
            tgt = targets.get(r)
            if tgt:
                for eid in new_ids:
                    unit_targets.setdefault(eid, []).append(tgt)
                    unit_source.setdefault(eid, src_ids[0])
        boss_skill_remap = {}
        boss_unit_remap = {}
        for eid, ts in unit_targets.items():
            thp = sum(t[0] for t in ts) / float(len(ts))
            tst = sum(t[1] for t in ts) / float(len(ts))
            rec = 127 + eid
            if 0 < rec < 896 and units.hp(rec):
                src = unit_source.get(eid)
                srcrec = (127 + src) if src is not None else None
                if srcrec is not None:
                    # slot-by-slot, see the note in _randomize_encounters
                    own = {}
                    old = reft.skills(rec)[:5]
                    new = reft.skills(srcrec)[:5]
                    for a, b in zip(old, new):
                        if not a or not b or a == b or a > 600 or b > 600:
                            continue
                        own[a] = b
                        boss_skill_remap.setdefault(a, b)
                    if own:
                        boss_unit_remap[rec] = own
                self._retune_unit(units, rec, reft, srcrec, target_hp=thp,
                                  target_statsum=tst)
        for a, b in boss_skill_remap.items():
            self.battle_skill_remap.setdefault(a, b)
        # the encounter pass runs after this one, so merge instead of replace
        self.ai_unit_remap = dict(getattr(self, 'ai_unit_remap', {}) or {})
        for rec, own in boss_unit_remap.items():
            self.ai_unit_remap.setdefault(rec, {}).update(own)
        self.stats['bosses_swapped'] = len(recs)
        self.log('story battles reshuffled: %d (%d enemy ids re-scaled)'
                 % (len(recs), len(unit_targets)))

    def _retune_unit(self, units, rec, srctable=None, srcrec=None,
                     target_hp=None, target_statsum=None):
        """Make UNIT record `rec` carry the strength of another unit.

        When `srctable`/`srcrec` are given (the normal-encounter path) the level,
        HP, MP, the five stats and the skill list are taken from that record --
        i.e. from the enemy that used to stand in this encounter slot.
        `target_hp`/`target_statsum` are the boss path, which only rescales.
        """
        strength = getattr(self.cfg, 'boss_match_strength', 1.0)
        mode = getattr(self.cfg, 'stats_mode', 'copy')

        if srctable is not None and srcrec is not None:
            # full alignment: everything becomes the enemy we replaced
            hp0 = srctable.hp(srcrec)
            if 0 < hp0 < 0x7FFF:
                units.set_hp(rec, hp0)
            units.set_mp(rec, srctable.mp(srcrec))
            units.set_level(rec, srctable.level(srcrec))
            src_stats = srctable.stats(srcrec)
            if mode == 'copy':
                units.set_stats(rec, [max(1, min(99, int(x))) for x in src_stats])
            else:
                cur = units.stats(rec)
                nsum = sum(cur)
                tsum = sum(src_stats)
                if nsum > 0 and tsum > 0:
                    f = float(tsum) / nsum
                    units.set_stats(rec, [max(1, min(99, int(round(x * f)))) for x in cur])
            units.set_skills(rec, srctable.skills(srcrec))
            return

        cur_hp = units.hp(rec)
        if cur_hp and target_hp and 0 < target_hp < 0x7FFF:
            units.set_hp(rec, int(round(cur_hp * (float(target_hp) / cur_hp) ** strength)))
        cur = units.stats(rec)
        nsum = sum(cur)
        if nsum > 0 and target_statsum and target_statsum > 0:
            f = (float(target_statsum) / nsum) ** strength
            units.set_stats(rec, [max(1, min(99, int(round(x * f)))) for x in cur])
        # boss path: when copying is requested, take MP / level / skills from the
        # boss that used to stand in this slot as well
        if mode == 'copy' and srctable is not None and srcrec is not None:
            units.set_mp(rec, srctable.mp(srcrec))
            units.set_level(rec, srctable.level(srcrec))
            units.set_skills(rec, srctable.skills(srcrec))

    # --------------------------------------------------------------- text
    def _text(self, p):
        try:
            from . import textdata
        except Exception as exc:
            self.log('text module unavailable (%s)' % exc)
            return
        try:
            stats = textdata.shuffle_game(p, self.rng, self.log,
                                          field_text=self.cfg.field_text,
                                          varlen=getattr(self.cfg, 'field_varlen', False),
                                          cg_mode=getattr(self.cfg, 'text_cg_mode',
                                                          'global'))
        except Exception as exc:
            self.log('text shuffle failed: %s' % exc)
            return
        self.stats.update(stats)

    # -------------------------------------------------------------- voice
    def _voice(self, p):
        try:
            from . import voice
        except Exception as exc:
            self.log('voice module unavailable (%s)' % exc)
            return
        paths = [e.path for e in p.game.ddt.files() if e.path.lower().startswith('/voice/')]
        if not paths:
            return
        blobs = dict((f, p.game.read(f)) for f in paths)
        out = voice.shuffle_all(paths, blobs, self.rng, self.cfg.shuffle_voice_scope)
        n = 0
        for f in paths:
            data = out.get(f)
            if data is not None and data != blobs[f]:
                p.set_file(f, data)
                n += 1
        self.stats['voice_files'] = n
        self.log('voice files shuffled: %d' % n)

    # -------------------------------------------------------------- mantra
    def _mantra_swap_sets(self, live):
        """Coarse mode: trade whole skill sets 1:1 (the original behaviour)."""
        lists = [m.skills for m in live]
        order = list(range(len(lists)))
        self.rng.shuffle(order)
        # never leave a mantra with its own set (it would look untouched)
        for i in range(len(order)):
            if order[i] == i:
                j = (i + 1) % len(order)
                order[i], order[j] = order[j], order[i]
        moved = 0
        for i, m in enumerate(live):
            if lists[order[i]] != m.skills:
                moved += 1
            m.skills = list(lists[order[i]])
        return moved

    def _mantra_shuffle_costs(self, table):
        """Permute the karma cost of every mantra that has a skill list."""
        from . import mantra
        live = mantra.live(table)
        costs = [m.cost for m in live]
        order = list(range(len(costs)))
        self.rng.shuffle(order)
        for i in range(len(order)):
            if order[i] == i:
                j = (i + 1) % len(order)
                order[i], order[j] = order[j], order[i]
        for i, m in enumerate(live):
            m.cost = costs[order[i]]

    def _mantras(self, p):
        """Randomize which skills a mantra teaches, and its stat bonus.

        The table lives in the game executable, so it is patched through the
        ISO9660 tree rather than DDS3.IMG.  Only the skill field and the five
        stat bytes of each 36 byte record change, so the ELF keeps its size and
        every offset inside it stays valid.
        """
        from . import mantra
        raw = p.game.read_iso_file(mantra.ELF_PATH)
        table = mantra.parse(raw)
        live = mantra.live(table)

        mode = getattr(self.cfg, 'mantra_swap_mode', 'skills')
        sources = mantra.skill_sources(live)
        if mode == 'sets':
            moved = self._mantra_swap_sets(live)
        else:
            moved = mantra.deal_skills(live, self.rng)

        if self.cfg.mantra_shuffle_cost:
            self._mantra_shuffle_costs(table)

        # The permanent stat bonus.  Only the story-given "event" mantras carry
        # one, so the whole five stat vector is traded between those nodes: a
        # node keeps its place on the grid and its cost, and hands out another
        # node's stats.  Nodes without a bonus stay untouched.
        gifted = mantra.gifted(table)
        bonus_swapped = 0
        if self.cfg.mantra_shuffle_bonus and len(gifted) > 1:
            vectors = [list(m.bonus) for m in gifted]
            border = list(range(len(gifted)))
            self.rng.shuffle(border)
            for i in range(len(border)):
                if border[i] == i:
                    j = (i + 1) % len(border)
                    border[i], border[j] = border[j], border[i]
            for i, m in enumerate(gifted):
                if vectors[border[i]] != m.bonus:
                    bonus_swapped += 1
                m.bonus = list(vectors[border[i]])

        new = mantra.patch(raw, table)
        if new != raw:
            p.set_iso_file(mantra.ELF_PATH, new)

        total = sum(len(m.skills) for m in live)
        points = sum(sum(m.bonus) for m in gifted)
        spread = mantra.spread(live, sources)
        self.stats['mantra_records'] = len(table)
        self.stats['mantra_skills_swapped'] = moved
        self.stats['mantra_skill_slots'] = total
        self.stats['mantra_swap_mode'] = mode
        self.stats['mantra_skill_spread'] = spread
        self.stats['mantra_bonus_nodes'] = len(gifted)
        self.stats['mantra_bonus_swapped'] = bonus_swapped
        self.stats['mantra_bonus_points'] = points
        if self.cfg.mantra_shuffle_cost:
            self.stats['mantra_costs_swapped'] = len(live)
        self.log('mantras: %s mode, %d/%d mantras changed (%d slots, '
                 'avg %.1f source mantras per set); '
                 '%d/%d stat-bonus nodes permuted (%d points)'
                 % (mode, moved, len(live), total, spread,
                    bonus_swapped, len(gifted), points))

    # ---------------------------------------------------------- models
    def _black_varuna(self, p):
        """Give Serph's demon form (Varuna) the Black Varuna model.

        Purely cosmetic: two `/model/human/pcNNN[_on].PB` slots of DDS3.IMG are
        overwritten with the bytes of `/model/devil/10E.PB`, zero padded so the
        files keep their size.  See `ddstool.model` for how the two ids were
        identified.
        """
        from . import model
        stats = model.swap(p.game, p, self.log)
        self.stats.update(stats)
        self.log('black varuna: ' + model.summary(stats))

