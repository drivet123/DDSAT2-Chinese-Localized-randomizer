#!/usr/bin/env python
# Self test: validates every parser round-trips and that the randomizer
# produces size-preserving output.  Run with:
#     python selftest.py [path/to/DDSAGA2.iso]
import os
import random
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'src'))

from ddstool.game import Game
from ddstool.unit import UnitTable
from ddstool.unitmap import UnitMap
from ddstool.encount_min import EncountTable
from ddstool import msgtbl, textdata, voice, aicalc
from ddstool.config import Config
from ddstool.randomizer import Randomizer


def main():
    iso = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'DDSAGA2.iso')
    ok = True

    def check(name, cond):
        print('%-46s %s' % (name, 'OK' if cond else 'FAIL'))
        return bool(cond)

    g = Game(iso)
    files = g.ddt.files()
    ok &= check('DDT parses (%d files)' % len(files), len(files) > 6000)
    ok &= check('DDT has no overlapping payloads', not g.ddt.overlapping())

    unit_raw = g.read('/battle/UNIT.TBL')
    units = UnitTable(unit_raw)
    ok &= check('UNIT.TBL round trip', units.to_bytes() == unit_raw)
    umap = UnitMap(units)
    ok &= check('unit map has enemies (%d)' % len(umap.valid_ids()), len(umap.valid_ids()) > 100)

    enc_raw = g.read('/battle/ENCOUNT.TBL')
    enc = EncountTable(enc_raw)
    ok &= check('ENCOUNT round trip', enc.to_bytes() == enc_raw)
    ok &= check('ENCOUNT groups (%d)' % len(enc.records), len(enc.records) == 1024)

    # MSG1 tables: parser must rebuild each file byte-identically
    for path, want in (('/event/e600/e601/e601_001/E601_001.PM1', 17),
                       ('/fld/f/f024/f024.bf', 143),
                       ('/battle/MSG.TBL', 1191)):
        blob = g.read(path)
        entries = msgtbl.parse(blob, path)
        ok &= check('MSG1 parse %-28s (%d)' % (os.path.basename(path), len(entries)),
                    len(entries) == want)
        buf = bytearray(blob)
        for e in entries:
            buf[e.slot_offset:e.slot_offset + e.size] = e.blob
        ok &= check('MSG1 rebuild %-28s' % os.path.basename(path), bytes(buf) == blob)

    # categories
    ok &= check('classify CG', textdata.classify('/event/e600/e601/e601_001/E601_001.PM1') == 'cg')
    ok &= check('classify ingame', textdata.classify('/fld/f/f024/f024.bf') == 'ingame')
    ok &= check('classify system', textdata.classify('/battle/MSG.TBL') is None)

    # in-memory patcher used by the battle dry run
    class FG(object):
        def read(self, path):
            return g.read(path)

        def read_iso_file(self, path):
            return g.read_iso_file(path)

        ddt = g.ddt
        listdir = g.listdir
        entry = g.entry
        # the movie-cutscene filter walks the ISO9660 tree to find /MOVIE*/*.IPS
        iso = g.iso

    class FP(object):
        def __init__(self):
            self.game = FG()
            self.written = {}

        def set_file(self, path, data):
            self.written[path] = data

        def set_iso_file(self, path, data):
            self.written[path] = data

    cfg = Config()
    cfg.seed = 1
    cfg.shuffle_text = False
    cfg.shuffle_voice = False
    # the story-battle shuffle is shielded by default; enable it here so its
    # code path stays covered for when it is reworked
    cfg.shuffle_bosses = True
    r = Randomizer('a', 'b', cfg, log=lambda m: None)
    fp = FP()
    r._battles(fp)
    ok &= check('randomizer patched ENCOUNT + UNIT',
                '/battle/ENCOUNT.TBL' in fp.written and '/battle/UNIT.TBL' in fp.written)
    ok &= check('UNIT size unchanged', len(fp.written['/battle/UNIT.TBL']) == len(unit_raw))
    ok &= check('ENCOUNT size unchanged', len(fp.written['/battle/ENCOUNT.TBL']) == len(enc_raw))
    ok &= check('encounter ids replaced (%d)' % r.stats.get('encounter_ids_replaced', 0),
                r.stats.get('encounter_ids_replaced', 0) > 100)
    ok &= check('story battles swapped (%d)' % r.stats.get('bosses_swapped', 0),
                r.stats.get('bosses_swapped', 0) > 4)
    e1 = EncountTable(fp.written['/battle/ENCOUNT.TBL'])
    ok &= check('first boss line-up changed',
                e1.records[51].enemy_ids != enc.records[51].enemy_ids)

    # enemy AI: only the skill ids of the flat action table and of the
    # FlowScript AI_ACT_SKILL call sites may change, nothing else
    ai_raw = g.read('/battle/AICALC.TBL')
    ok &= check('AICALC patched', '/battle/AICALC.TBL' in fp.written)
    if '/battle/AICALC.TBL' in fp.written:
        new_ai = fp.written['/battle/AICALC.TBL']
        ok &= check('AICALC size unchanged', len(new_ai) == len(ai_raw))
        ok &= check('AICALC flat table layout (395x348)',
                    aicalc.AI_TABLE_BASE + aicalc.AI_TABLE_COUNT * aicalc.AI_TABLE_RECORD
                    < len(ai_raw))
        allowed = set(o for o, _ in aicalc.flat_skill_offsets(ai_raw))
        allowed |= set(o for o, _ in aicalc.flw0_skill_offsets(ai_raw))
        ok &= check('AICALC skill operand sites (%d)' % len(allowed), len(allowed) > 2000)
        changed = set()
        for o in allowed:
            if ai_raw[o:o + 2] != new_ai[o:o + 2]:
                changed.add(o)
        stray = 0
        for o in range(0, len(ai_raw) - 1, 2):
            if ai_raw[o:o + 2] != new_ai[o:o + 2] and o not in allowed:
                stray += 1
        ok &= check('AICALC only skill ids touched (%d changed, %d stray)'
                    % (len(changed), stray), changed and stray == 0)
        # the flat table must follow each unit's OWN map, so that two units
        # sharing a skill id cannot drag the wrong replacement in
        flow_sites = dict(aicalc.flw0_skill_offsets(ai_raw))
        flat_site_unit = {}
        for rec, _m in r.ai_unit_remap.items():
            idx = aicalc.index_for_unit_record(rec)
            for o, _s in aicalc.flat_skill_offsets(ai_raw, idx):
                flat_site_unit[o] = rec
        bad_flat = bad_flow = 0
        for o in changed:
            old = struct.unpack_from('<H', ai_raw, o)[0]
            new = struct.unpack_from('<H', new_ai, o)[0]
            if o in flow_sites:
                expect = r.battle_skill_remap.get(old)
                if expect != new:
                    bad_flow += 1
            elif o in flat_site_unit:
                expect = r.ai_unit_remap[flat_site_unit[o]].get(old)
                if expect != new:
                    bad_flat += 1
            else:
                bad_flat += 1
        ok &= check('AICALC flat changes follow the per-unit map', bad_flat == 0)
        ok &= check('AICALC FlowScript changes follow the global map', bad_flow == 0)
        ok &= check('AICALC per-unit AI records (%d)'
                    % r.stats.get('ai_units_remapped', 0),
                    r.stats.get('ai_units_remapped', 0) > 50)
        ok &= check('AICALC FlowScript sites remapped (%d)'
                    % r.stats.get('ai_flow_skills_remapped', 0),
                    r.stats.get('ai_flow_skills_remapped', 0) > 0)

    # dialogue: every changed byte must stay inside a parsed MSG1 entry slot
    tstats = textdata.shuffle_game(fp, random.Random(7), log=None)
    ok &= check('dialogue: CG text runs swapped (%d/%d)'
                % (tstats['text_cg_swapped'], tstats['text_cg_fragments']),
                tstats['text_cg_swapped'] > tstats['text_cg_fragments'] * 0.7)
    ok &= check('dialogue: field text fragments swapped (%d/%d)'
                % (tstats['text_ingame_swapped'], tstats['text_ingame_fragments']),
                tstats['text_ingame_swapped'] > tstats['text_ingame_fragments'] * 0.8)
    ok &= check('dialogue: field swaps mostly cross file (%d/%d)'
                % (tstats['text_ingame_cross_file_pairs'], tstats['text_ingame_pairs']),
                tstats['text_ingame_cross_file_pairs'] > tstats['text_ingame_pairs'] * 0.8)

    # CG subtitle scope.  Reported in game: a randomized CG subtitle shows up
    # late, does not match its audio and some cutscenes lose subtitles - with
    # EQUAL lengths too, so it is not a length problem.  `text_cg_mode` lets the
    # user keep a cutscene's lines to itself ('file') or leave CG alone ('off').
    # One patcher is reused and cleared: each run keeps a copy of every rewritten
    # container, and four of them at once exhausts the 32 bit interpreter.
    cg_runs = {}
    for mode in ('off', 'file', 'global'):
        cg_fp = FP()
        cg_runs[mode] = textdata.shuffle_game(cg_fp, random.Random(7), log=None,
                                              cg_mode=mode)
        if mode == 'off':
            untouched = True
            for path, blob in cg_fp.written.items():
                if textdata.classify(path) == 'cg' and blob != g.read(path):
                    untouched = False
            ok &= check('cg mode off: every .PM1 is byte identical', untouched)
        cg_fp.written.clear()
        del cg_fp
    ok &= check('cg mode off: nothing swapped (%d)'
                % cg_runs['off']['text_cg_swapped'],
                cg_runs['off']['text_cg_swapped'] == 0)
    ok &= check('cg mode file: nothing crosses a cutscene (%d)'
                % cg_runs['file']['text_cg_cross_file_pairs'],
                cg_runs['file']['text_cg_cross_file_pairs'] == 0)
    ok &= check('cg mode file: still swaps inside one (%d/%d)'
                % (cg_runs['file']['text_cg_swapped'],
                   cg_runs['file']['text_cg_messages']),
                cg_runs['file']['text_cg_swapped'] > 0)
    ok &= check('cg mode global: swaps across cutscenes (%d)'
                % cg_runs['global']['text_cg_cross_file_pairs'],
                cg_runs['global']['text_cg_cross_file_pairs'] > 0)
    # ... and the option must actually survive the trip through the Randomizer.
    # A Config field that never reaches shuffle_game has silently turned a flag
    # into a no-op twice now (`--field-varlen`, `--cg-text`).
    for name, mode, want_off in (('off', 'off', True), ('file', 'file', False)):
        cg_cfg = Config()
        cg_cfg.shuffle_text = True
        cg_cfg.text_cg_mode = mode
        cg_cfg.field_text = False
        cg_r = Randomizer('a', 'b', cg_cfg, log=lambda m: None)
        cg_fp = FP()
        cg_r._text(cg_fp)
        cross = cg_r.stats.get('text_cg_cross_file_pairs', -1)
        swapped = cg_r.stats.get('text_cg_swapped', -1)
        ok &= check('cg mode %s reaches the shuffle (swapped %d, cross %d)'
                    % (name, swapped, cross),
                    swapped == 0 if want_off else (swapped > 0 and cross == 0))
        cg_fp.written.clear()
        del cg_fp, cg_r
    vl_cfg = Config()
    vl_cfg.shuffle_text = True
    vl_cfg.field_text = False
    vl_cfg.field_varlen = True
    vl_r = Randomizer('a', 'b', vl_cfg, log=lambda m: None)
    vl_fp = FP()
    vl_r._text(vl_fp)
    ok &= check('field_varlen reaches the shuffle (%d)'
                % vl_r.stats.get('text_ingame_varlen', -1),
                vl_r.stats.get('text_ingame_varlen', -1) > 0)
    vl_fp.written.clear()
    del vl_fp, vl_r
    stray = 0
    bad_size = 0
    for path, blob in fp.written.items():
        if textdata.classify(path) is None:
            continue          # ENCOUNT/UNIT/AICALC are checked above
        van = g.read(path)
        if len(van) != len(blob):
            bad_size += 1
            continue
        if van == blob:
            continue
        allowed = bytearray(len(van))
        for e in msgtbl.parse(van, path):
            for i in range(e.slot_offset, min(e.slot_offset + e.size, len(van))):
                allowed[i] = 1
        for i in range(len(van)):
            if van[i] != blob[i] and not allowed[i]:
                stray += 1
    ok &= check('dialogue: sizes preserved', bad_size == 0)
    ok &= check('dialogue: nothing changed outside a message slot (%d)' % stray, stray == 0)

    # The heart of the CG fix: a CG text carries cutscene specific control codes
    # (334 distinct leading codes over 93 files), so only the plain glyph runs may
    # travel.  Every control byte, the entry header and the line structure must
    # come out byte for byte identical.
    cg_ctl = cg_head = cg_lines = cg_bad = cg_pinned = 0
    for path, blob in fp.written.items():
        if textdata.classify(path) != 'cg':
            continue
        van = g.read(path)
        pa = msgtbl.parse(van, path)
        pb = msgtbl.parse(blob, path)
        if len(pa) != len(pb):
            cg_bad += 1
            continue
        for ea, eb in zip(pa, pb):
            la = struct.unpack_from('<H', ea.blob, 24)[0]
            lb = struct.unpack_from('<H', eb.blob, 24)[0]
            if la > 32 or lb > 32:
                continue
            if ea.blob[:28 + 4 * la] != eb.blob[:28 + 4 * lb]:
                cg_head += 1
            na = struct.unpack_from('<I', ea.blob, 28 + 4 * la)[0]
            nb = struct.unpack_from('<I', eb.blob, 28 + 4 * lb)[0]
            if na != nb:
                cg_lines += 1            # every run keeps its byte length
            ta = ea.blob[32 + 4 * la:32 + 4 * la + na]
            tb = eb.blob[32 + 4 * lb:32 + 4 * lb + nb]
            ma = textdata.field_mask(ta)
            mb = textdata.field_mask(tb)
            if bytes(c for c, m in zip(ta, ma) if m) != \
                    bytes(c for c, m in zip(tb, mb) if m):
                cg_ctl += 1
            # every byte the mask calls PINNED must come out identical.  This is
            # what catches a code operand read as a glyph: `f413 05 ff e6 08 f207`
            # used to be rewritten to `f413 05 ff 81 ff f207` and killed the
            # cutscene in game.
            for k in range(min(len(ta), len(tb))):
                if ma[k] and ta[k] != tb[k]:
                    cg_pinned += 1
                    break
    ok &= check('cg: pinned bytes are never rewritten (%d)' % cg_pinned,
                cg_pinned == 0)
    ok &= check('cg: every control byte is unchanged (%d)' % cg_ctl, cg_ctl == 0)
    ok &= check('cg: header and voice array unchanged (%d)' % cg_head, cg_head == 0)
    ok &= check('cg: every subtitle keeps its byte length (%d)' % cg_lines,
                cg_lines == 0)
    ok &= check('cg: entry count unchanged (%d)' % cg_bad, cg_bad == 0)

    # Variable length FIELD dialogue.  The plain text runs between the control
    # codes may trade places with runs of ANY length; the whole MSG1 block is
    # laid out again so the .bf - and the compressed .LB member - keep their
    # size.  Control codes must not move and every run stays whole glyphs.
    fp_v = FP()
    vstats = textdata.shuffle_game(fp_v, random.Random(11), log=None,
                                   field_text=True, varlen=True)
    ok &= check('field varlen: runs swapped (%d)'
                % vstats['text_ingame_swapped'], vstats['text_ingame_swapped'] > 1000)
    ok &= check('field varlen: blocks rebuilt (%d), fell back to equal length (%d)'
                % (vstats['text_ingame_blocks_rebuilt'],
                   vstats['text_ingame_blocks_fallback']),
                vstats['text_ingame_blocks_rebuilt'] > 20)
    ok &= check('field varlen: every field container was rewritten (%d)'
                % vstats['text_field_containers'],
                vstats['text_field_containers'] == 22 and not vstats['text_field_skipped'])
    ok &= check('field varlen: messages changed length (%d)'
                % vstats['text_ingame_varlen'], vstats['text_ingame_varlen'] > 500)
    v_stray = v_size = v_ctl = v_odd = 0
    v_over = 0
    v_wide = 0
    v_head = 0
    for path, blob in fp_v.written.items():
        van = g.read(path)
        if len(van) != len(blob):
            v_size += 1
            continue
        if not path.lower().endswith('.bf'):
            continue
        allowed = bytearray(len(van))
        for pos in msgtbl.find_blocks(van):
            end = min(pos + 4 + struct.unpack_from('<I', van, pos + 8)[0], len(van))
            # a block's declared size can overrun the section that follows it,
            # and the rebuild must stop at the FLW0 `.data.msg` section end
            lim = textdata._msg_section_limit(van, pos)
            if lim is not None:
                if end > lim:
                    v_over += 1
                end = min(end, lim)
            for i in range(pos, end):
                allowed[i] = 1
        for i in range(len(van)):
            if van[i] != blob[i] and not allowed[i]:
                v_stray += 1
                break
        for pos in msgtbl.find_blocks(van):
            old_ents = msgtbl._parse_block(van, pos, len(van))
            new_ents = msgtbl._parse_block(blob, pos, len(blob))
            if not old_ents or not new_ents or len(old_ents) != len(new_ents):
                v_ctl += 1
                continue
            for ea, eb in zip(old_ents, new_ents):
                la = struct.unpack_from('<H', ea.blob, 24)[0]
                lb_ = struct.unpack_from('<H', eb.blob, 24)[0]
                na = struct.unpack_from('<I', ea.blob, 28 + 4 * la)[0]
                nb = struct.unpack_from('<I', eb.blob, 28 + 4 * lb_)[0]
                ta = ea.blob[32 + 4 * la:32 + 4 * la + na]
                tb = eb.blob[32 + 4 * lb_:32 + 4 * lb_ + nb]
                ma = textdata.field_mask(ta)
                mb = textdata.field_mask(tb)
                # everything in front of the text EXCEPT the length field -
                # name, reserved, line count and the `u32` VOICE ARRAY, i.e.
                # `+0 .. +28+4*lines` - must be byte identical: moving the voice
                # array makes an entry fall back to its original line
                ha = ea.blob[:28 + 4 * la]
                hb = eb.blob[:28 + 4 * lb_]
                if ha != hb:
                    v_head += 1
                # the control bytes must form the same sequence; the text runs
                # between them are what changed, and they may change length
                if bytes(c for c, m in zip(ta, ma) if m) != \
                        bytes(c for c, m in zip(tb, mb) if m):
                    v_ctl += 1
                if len(textdata.field_fragments(ta)) != len(textdata.field_fragments(tb)):
                    v_ctl += 1
                for t in (ta, tb):
                    for _off, ln in textdata.field_fragments(t):
                        if ln % 2:
                            v_odd += 1
                # the box does not wrap, so no line may end up wider than the
                # widest line the retail data already shows
                for w in textdata.field_line_widths(tb):
                    if w > textdata.FIELD_MAX_LINE:
                        v_wide += 1
    ok &= check('field varlen: sizes preserved', v_size == 0)
    ok &= check('field varlen: nothing changed outside the .data.msg sections (%d)'
                % v_stray, v_stray == 0)
    ok &= check('field varlen: overrunning blocks are clamped (%d)' % v_over,
                v_over > 0)
    ok &= check('field varlen: no line is wider than %d cells (%d)'
                % (textdata.FIELD_MAX_LINE, v_wide), v_wide == 0)
    ok &= check('field varlen: the width cap actually bound (%d)'
                % vstats.get('text_ingame_lines_capped', 0),
                vstats.get('text_ingame_lines_capped', 0) > 0)
    ok &= check('field varlen: control runs and glyph split unchanged (%d)' % v_ctl,
                v_ctl == 0)
    ok &= check('field varlen: name/flags/voice array unchanged (%d)' % v_head,
                v_head == 0)
    ok &= check('field varlen: every text run is whole glyphs (%d)' % v_odd,
                v_odd == 0)
    # Why a different length is safe at all: the engine must delimit a message by
    # the `u32` length field rather than scanning for `00`, because the retail
    # data itself has entries whose declared text is followed by a non-zero byte
    # (the next entry's name).  A `00` scan would show garbage there.
    v_runon = 0
    for path, blob in fp_v.written.items():
        if not path.lower().endswith('.bf'):
            continue
        van = g.read(path)
        for pos in msgtbl.find_blocks(van):
            ents = msgtbl._parse_block(van, pos, len(van))
            for i, e in enumerate(ents):
                if i + 1 >= len(ents):
                    continue
                nl = struct.unpack_from('<H', van, e.slot_offset + 24)[0]
                if nl > 32:
                    continue
                loff = e.slot_offset + 28 + 4 * nl
                n = struct.unpack_from('<I', van, loff)[0]
                nxt = van[loff + 4 + n:loff + 4 + n + 1]
                if nxt and nxt != b'\x00':
                    v_runon += 1
    ok &= check('field varlen: retail text is length delimited (%d)' % v_runon,
                v_runon > 0)
    ok &= check('field varlen: containers rewritten (%d), none skipped'
                % vstats['text_field_containers'],
                vstats['text_field_containers'] > 0 and not vstats['text_field_skipped'])
    cg_bad = 0
    for path, blob in fp_v.written.items():
        if textdata.classify(path) != 'cg':
            continue
        van = g.read(path)
        allowed = bytearray(len(van))
        for e in msgtbl.parse(van, path):
            o = e.slot_offset
            lines = struct.unpack_from('<H', van, o + 24)[0]
            if lines > 32:
                continue
            loff = o + 28 + 4 * lines
            if van[loff:loff + 4] != blob[loff:loff + 4]:
                cg_bad += 1            # the length field must not move
            for i in range(o, min(o + e.size, len(van))):
                allowed[i] = 1
        for i in range(len(van)):
            if van[i] != blob[i] and not allowed[i]:
                cg_bad += 1
                break
    ok &= check('field varlen: CG subtitles untouched apart from equal length '
                'text (%d bad)' % cg_bad, cg_bad == 0)
    ok &= check('field_varlen: off by default', Config().field_varlen is False)

    # +28 must never move: swapping it made the game fall back to the original
    # subtitle, which is what made v24 look like it did nothing.  (v23 report)
    voicemoved = lengthmoved = 0
    for path, blob in fp.written.items():
        if textdata.classify(path) is None:
            continue
        van = g.read(path)
        for e in msgtbl.parse(van, path):
            o = e.slot_offset
            ln = struct.unpack_from('<H', van, o + 24)[0]
            if not (0 < ln <= 32) or o + 32 + 4 * ln > len(van):
                continue
            if van[o + 28:o + 28 + 4 * ln] != blob[o + 28:o + 28 + 4 * ln]:
                voicemoved += 1
            a = struct.unpack_from('<I', van, o + 28 + 4 * ln)[0]
            b = struct.unpack_from('<I', blob, o + 28 + 4 * ln)[0]
            if a != b:
                lengthmoved += 1
    ok &= check('dialogue: +28 array untouched (%d)' % voicemoved, voicemoved == 0)
    ok &= check('dialogue: length field untouched (%d)' % lengthmoved, lengthmoved == 0)

    # field messages: only the glyph bytes may move, every control code byte has
    # to stay exactly where it was (moving one into another scene crashed the
    # game - the codes point at things that field does not have)
    ctrl_moved = 0
    ctrl_checked = 0
    glyph_broken = 0
    for path, blob in fp.written.items():
        if textdata.classify(path) != 'ingame':
            continue
        van = g.read(path)
        for e in msgtbl.parse(van, path):
            lines = struct.unpack_from('<H', van, e.slot_offset + 24)[0]
            if lines > 32:
                continue
            length = struct.unpack_from('<I', van, e.slot_offset + 28 + 4 * lines)[0]
            off = e.slot_offset + 32 + 4 * lines
            if not (0 < length) or off + length > e.slot_offset + e.size:
                continue
            mask = textdata.field_mask(van[off:off + length])
            ctrl_checked += sum(mask)
            for i in range(length):
                if mask[i] and van[off + i] != blob[off + i]:
                    ctrl_moved += 1
            # the glyphs must still be two byte units in the same places
            if textdata.field_mask(blob[off:off + length]) != mask:
                glyph_broken += 1
    ok &= check('dialogue: field control codes untouched (%d of %d)'
                % (ctrl_moved, ctrl_checked), ctrl_moved == 0 and ctrl_checked > 1000)
    ok &= check('dialogue: field glyph pairing intact (%d broken)' % glyph_broken,
                glyph_broken == 0)


    # .LB member codec.  Every field container's BF member also exists as a
    # loose .bf, which is a known plain text oracle: the decoder must reproduce
    # it byte for byte.  Then a few members must survive compress -> decompress.
    from ddstool import lb as _lb, lbcmp, lbcmp_enc
    solved = failed = 0
    samples = []
    for e in g.ddt.files():
        if not (e.path.startswith('/fld/') and e.name.lower().endswith('.lb')):
            continue
        data = g.read(e.path)
        c = _lb.find_chunk(data, 'BF')
        if c is None:
            continue
        loose = e.path.rsplit('/', 1)[0] + '/' + e.name.split('_')[0] + '.bf'
        try:
            plain = g.read(loose)
        except Exception:
            continue
        if len(plain) != c.unpacked:
            continue
        src = data[c.data_offset:c.data_end]
        if lbcmp.decode(src, c.unpacked) == plain:
            solved += 1
            if len(samples) < 6:
                samples.append((e.path, plain))
        else:
            failed += 1
    ok &= check('LB codec: BF plain text oracle (%d solved, %d wrong)' % (solved, failed),
                failed == 0 and solved >= 20)
    for path, plain in samples:
        rt = lbcmp.decode(lbcmp_enc.compress(plain, max_chain=64), len(plain))
        ok &= check('LB codec: round trip %-20s' % os.path.basename(path), rt == plain)

    # the shuffled field scripts must reach the container the game reads, and
    # the container must keep its size with only the BF slot touched
    containers = [x for x in fp.written if x.lower().endswith('.lb')]
    ok &= check('dialogue: field containers rewritten (%d)' % len(containers),
                len(containers) >= 20)
    badc = 0
    for path in containers:
        van = g.read(path)
        new = fp.written[path]
        if len(van) != len(new):
            badc += 1
            continue
        # the layout must be untouched: the reader walks to the next member by
        # the 64 byte block the terminator landed in
        if [x.offset for x in _lb.chunks(van)] != [x.offset for x in _lb.chunks(new)]:
            badc += 1
            continue
        c = _lb.find_chunk(new, 'BF')
        try:
            plain = lbcmp.decode(new[c.data_offset:c.data_end], c.unpacked)
        except lbcmp.LbCodecError:
            badc += 1
            continue
        loose = (path.rsplit('/', 1)[0] + '/'
                 + path.rsplit('/', 1)[1].split('_')[0] + '.bf')
        if plain != fp.written.get(loose, g.read(loose)):
            badc += 1
        slot_lo = c.offset
        slot_hi = _lb.next_chunk_offset(van, _lb.find_chunk(van, 'BF'))
        for i in range(len(van)):
            if van[i] != new[i] and not (slot_lo <= i < slot_hi):
                badc += 1
                break
    ok &= check('dialogue: containers keep size and touch only BF', badc == 0)

    # the switch has to actually disable the container write back
    fp2 = FP()
    textdata.shuffle_game(fp2, random.Random(7), log=None, field_text=False)
    off = [x for x in fp2.written if x.lower().endswith('.lb')]
    ok &= check('dialogue: field_text=False writes no container (%d)' % len(off),
                not off)

    # Mantra (曼斗罗).  What each mantra teaches and the permanent stat bonus it
    # hands out live in the game executable (/SLPM_657.95, ISO9660 root) rather
    # than in DDS3.IMG, so they are patched through the ISO tree.  The default
    # 'skills' mode pools every skill and deals it back out (a mantra keeps its
    # number of skills but not its own skills), and the five-stat bonus vectors
    # are permuted among the 8 mantras that have one.  Only those fields of each
    # 36 byte record may change.
    from ddstool import mantra as _mantra
    elf = g.read_iso_file(_mantra.ELF_PATH)
    table = _mantra.parse(elf)
    live = _mantra.live(table)
    gifted = _mantra.gifted(table)
    slots = sum(len(m.skills) for m in live)
    points = sum(sum(m.bonus) for m in gifted)
    before = dict((m.id, list(m.skills)) for m in live)
    old_pool = sorted(s for m in live for s in m.skills)
    orig_sets = set(tuple(sorted(v)) for v in before.values())
    ok &= check('mantra table parses (%d records, %d teach skills)'
                % (len(table), len(live)),
                len(table) == _mantra.COUNT and len(live) > 50)
    ok &= check('mantra skill slots (%d)' % slots, slots > 200)
    ok &= check('mantra stat-bonus nodes (%d, %d points)' % (len(gifted), points),
                len(gifted) >= 8 and points > 0)
    ok &= check('mantra: only %d mantras teach nothing'
                % (len(table) - len(live)), len(table) - len(live) > 0)
    ok &= check('mantra table round trip', _mantra.patch(elf, table) == elf)
    ok &= check('mantra: on by default (verified in game)',
                Config().randomize_mantras is True
                and Config().mantra_swap_mode == 'skills')

    cfg_m = Config()
    cfg_m.seed = 99
    r_m = Randomizer('a', 'b', cfg_m, log=lambda m: None)
    fp_m = FP()
    r_m._mantras(fp_m)
    new_elf = fp_m.written.get(_mantra.ELF_PATH)
    ok &= check('mantra patched', new_elf is not None)
    if new_elf is not None:
        ok &= check('mantra: ELF size unchanged', len(new_elf) == len(elf))
        allowed = bytearray(len(elf))
        for m in table:
            base = _mantra.TABLE_OFFSET + m.id * _mantra.STRIDE
            for i in range(base + _mantra.BONUS_OFFSET,
                           base + _mantra.BONUS_OFFSET + _mantra.BONUS_STATS):
                allowed[i] = 1
            for i in range(base + _mantra.SKILL_OFFSET,
                           base + _mantra.SKILL_OFFSET + _mantra.SKILL_SLOTS * 2):
                allowed[i] = 1
        stray = sum(1 for i in range(len(elf))
                    if elf[i] != new_elf[i] and not allowed[i])
        ok &= check('mantra: only bonus + skill fields changed (%d stray)' % stray,
                    stray == 0)

        now = _mantra.parse(new_elf)
        new_live_ = _mantra.live(now)
        new_pool = sorted(s for m in now if m.skills for s in m.skills)
        ok &= check('mantra: the skill pool is only permuted', old_pool == new_pool)
        ok &= check('mantra: skill lists moved (%d/%d)'
                    % (r_m.stats.get('mantra_skills_swapped', 0), len(live)),
                    r_m.stats.get('mantra_skills_swapped', 0) > len(live) * 0.8)
        ok &= check('mantra: default mode is the skill pool (%s)'
                    % r_m.stats.get('mantra_swap_mode'),
                    r_m.stats.get('mantra_swap_mode') == 'skills')
        # every mantra keeps the number of skills it had ...
        ok &= check('mantra: skill counts per mantra preserved',
                    all(len(m.skills) == len(before[m.id]) for m in new_live_))
        # ... but none of them comes from the mantra itself any more
        keep = sum(1 for m in new_live_ if set(m.skills) & set(before[m.id]))
        ok &= check('mantra: no mantra keeps its own skills (%d)' % keep, keep == 0)
        # and the sets are genuinely mixed, not traded in one block
        same = sum(1 for m in new_live_ if tuple(sorted(m.skills)) in orig_sets)
        ok &= check('mantra: sets are mixed, not swapped whole (%d/%d unchanged)'
                    % (same, len(new_live_)), same < len(new_live_) * 0.2)
        ok &= check('mantra: skills come from several mantras (avg %.2f)'
                    % r_m.stats.get('mantra_skill_spread', 0.0),
                    r_m.stats.get('mantra_skill_spread', 0.0) > 1.5)

        old_vec = sorted(tuple(m.bonus) for m in table if any(m.bonus))
        new_vec = sorted(tuple(m.bonus) for m in now if any(m.bonus))
        ok &= check('mantra: the bonus vectors are only permuted', old_vec == new_vec)
        ok &= check('mantra: the bonus nodes did not move',
                    [m.id for m in _mantra.gifted(now)] == [m.id for m in gifted])
        ok &= check('mantra: bonuses shuffled (%d/%d)'
                    % (r_m.stats.get('mantra_bonus_swapped', 0), len(gifted)),
                    r_m.stats.get('mantra_bonus_swapped', 0) > len(gifted) * 0.8)
        ok &= check('mantra: bonus points preserved (%d)'
                    % sum(sum(m.bonus) for m in now), 
                    sum(sum(m.bonus) for m in now) == points)

        # the cost field is untouched unless the switch says otherwise
        costs_old = [m.cost for m in table]
        costs_new = [m.cost for m in now]
        ok &= check('mantra: costs untouched by default', costs_old == costs_new)

    # the older, coarser mode is still selectable and keeps the same invariants
    cfg_s = Config()
    cfg_s.seed = 5
    cfg_s.mantra_swap_mode = 'sets'
    r_s = Randomizer('a', 'b', cfg_s, log=lambda m: None)
    fp_s = FP()
    r_s._mantras(fp_s)
    elf_s = fp_s.written.get(_mantra.ELF_PATH)
    ok &= check('mantra sets mode: patched', elf_s is not None)
    if elf_s is not None:
        ok &= check('mantra sets mode: size unchanged', len(elf_s) == len(elf))
        tmp = _mantra.parse(elf_s)
        ok &= check('mantra sets mode: skill pool is only permuted',
                    sorted(s for m in tmp if m.skills for s in m.skills) == old_pool)
        tmp_live = _mantra.live(tmp)
        traded = sum(1 for m in tmp_live
                     if tuple(sorted(m.skills)) in orig_sets)
        ok &= check('mantra sets mode: sets really are traded whole (%d/%d)'
                    % (traded, len(tmp_live)), traded > len(tmp_live) * 0.5)

    # Character model swap (opt-in): Serph's demon form.  The party atma model
    # slots are `human/pcNNN[_on].PB` and entry 0x11 of the pointer tables in
    # SLPM_657.95 resolves to pc011 / pc011_on, whose body node is `varna_body`
    # (Varuna / 伐楼罗).
    #
    # A .PB model is a flat list of `[u32 type][u32 size][magic]` chunks, and
    # every Varuna model shares a **byte identical 199936 byte `TXP0` chunk
    # header and table** (3 x 256x256, format 19).  So the swap only replaces
    # that one chunk - the skeleton (`PIB0`), the mesh (`MD00`) and the
    # materials (`MT00`) stay exactly where they were, which is what keeps the
    # loader happy (copying a whole foreign model file hung the game).
    #
    # The "black" texture set comes from the e639 event model `kuro_varuna.PB`.
    # That model is not a legal playable-character model either, but a complete
    # copy of its texture block is embedded in `/model/devil/10E.PB`.
    from ddstool import model as _model
    donor = g.read(_model.DONOR)
    siro = g.read(_model.SIRO)
    plan = _model.plan(g)
    ok &= check('model: %d Varuna slot(s) found' % len(plan),
                len(plan) == len(_model.TARGETS))
    ok &= check('model: every slot has a matching donor texture chunk',
                bool(plan) and all(ok_ for _, _, _, _, ok_ in plan))
    ok &= check('model: all Varuna slots share the %d byte TXP0 chunk'
                % plan[0][3], all(size == plan[0][3] for _, _, _, size, _ in plan))
    ok &= check('model: the slots carry the Varuna body node',
                all(b'varna_body\x00' in g.read(p) for p, _, _, _, _ in plan))
    ok &= check('model: donor is the plain/black body (not varna_body01)',
                b'varna_body\x00' in donor and b'varna_body01' not in donor)
    ok &= check('model: the white Varuna model is varna_body01',
                b'varna_body01\x00' in siro)

    def _palette_means(chunk):
        """Mean RGB of the 256 entry palette of every TMX0 inside a TXP0."""
        out = []
        p = 0x40
        while p + 0x18 <= len(chunk):
            size = struct.unpack_from('<I', chunk, p + 4)[0]
            sub = chunk[p:p + size]
            if len(sub) < 0x18 or sub[8:12] != b'TMX0':
                break
            pal = sub[-1024:]
            n = float(len(pal) // 4)
            out.append(sum(sum(pal[k:k + 3]) for k in range(0, len(pal), 4))
                       / (3.0 * n))
            p += size
            while p % 0x40 and p < len(chunk) and chunk[p] == 0:
                p += 1
        return out

    _path0, _fsize0, _start0, _tsize0, _ = plan[0]
    _head0 = g.read(_path0)[_start0:_start0 + _model.TABLE_HEAD]
    _dstart, _dsize = _model._texture_chunk(donor, _tsize0, _head0)
    donor_chunk = donor[_dstart:_dstart + _tsize0]
    target_chunk = g.read(_path0)[_start0:_start0 + _tsize0]
    ok &= check('model: the donor chunk is a TXP0 of the same geometry',
                _dsize == _tsize0 and donor_chunk[:0x40] == target_chunk[:0x40])
    dm = _palette_means(donor_chunk)
    tm = _palette_means(target_chunk)
    ok &= check('model: the donor textures are the darker (black) set '
                '(%.0f vs %.0f)' % (sum(dm), sum(tm)),
                len(dm) == 3 and sum(dm) < sum(tm) * 0.8)

    pm1 = g.read('/event/e630/e639/e639_001/E639_001.PM1')
    names = [pm1[i * 32:(i + 1) * 32].split(b'\x00')[0].decode('latin1', 'replace')
             for i in range(20)]
    starts = []
    pos = pm1.find(b'PIB0')
    while pos != -1:
        starts.append(pos - 8)
        pos = pm1.find(b'PIB0', pos + 4)
    by_name = {}
    for i, name in enumerate(names):
        k = i - 8                      # the .PB names start at name slot 8
        if k < 0 or k >= len(starts):
            continue
        end = starts[k + 1] if k + 1 < len(starts) else len(pm1)
        by_name[name] = pm1[starts[k]:end]
    kuro = by_name.get('kuro_varuna.PB')
    ok &= check('model: e639 exposes kuro_varuna/siro_varna (%d models)'
                % len(by_name),
                kuro is not None and 'siro_varna.PB' in by_name)
    if kuro is not None:
        ok &= check('model: the donor chunk is the kuro_varuna texture block',
                    kuro[0x40:0x40 + _tsize0] == donor_chunk)

    ok &= check('model: the swap is off by default', Config().black_varuna is False)
    cfg_v = Config()
    cfg_v.black_varuna = True
    r_v = Randomizer('a', 'b', cfg_v, log=lambda m: None)
    fp_v = FP()
    r_v._black_varuna(fp_v)
    swapped = sorted(fp_v.written)
    ok &= check('model: both Varuna slots were rewritten (%s)'
                % ', '.join(swapped), swapped == sorted(_model.TARGETS))
    ok &= check('model: no other file was touched',
                not [p for p in swapped if p not in _model.TARGETS])
    bad = 0
    for path, fsize, start, tsize, _ok in plan:
        orig = g.read(path)
        blob = fp_v.written.get(path)
        if blob is None or len(blob) != fsize:
            bad += 1
            continue
        if blob[start:start + tsize] != donor_chunk:
            bad += 1            # the texture block did not land
            continue
        if blob[start:start + 0x40] != orig[start:start + 0x40]:
            bad += 1            # the TXP0 table must survive untouched
        elif (blob[:start] != orig[:start]
                or blob[start + tsize:] != orig[start + tsize:]):
            bad += 1            # anything outside TXP0 must be identical
    ok &= check('model: only the TXP0 chunk changed, size kept (%d bad)' % bad,
                bad == 0)
    ok &= check('model: stats report the swap (%d file(s), %d texture byte(s))'
                % (r_v.stats.get('model_files_swapped', 0),
                   r_v.stats.get('model_texture_bytes', 0)),
                r_v.stats.get('model_files_swapped') == len(_model.TARGETS)
                and r_v.stats.get('model_texture_bytes')
                == sum(t for _, _, _, t, _ in plan))

    # Analysis reveal.  The analyse window's row functions (table at 0x415650)
    # branch to the "?" placeholder drawer 0x1B8930 through thirteen
    # `bnel flag,zero,<placeholder>` guards.  Clearing the underlying data
    # bytes does reveal the values but breaks battle loading, so the guards in
    # SLPM_657.95 are neutralised instead: only the `rs` field of those words
    # changes, turning them into `bnel zero,zero,<same placeholder>`.
    from ddstool import analyse as _analyse
    elf_an = g.read_iso_file(_analyse.ELF_PATH)
    ok_an, sites_an = _analyse.verify(elf_an)
    ok &= check('reveal: %d analyse guard(s) found (shape %s)'
                % (len(sites_an), 'ok' if ok_an else 'BAD'), ok_an)
    patched_an = _analyse.patch(elf_an)
    ok &= check('reveal: ELF size unchanged', len(patched_an) == len(elf_an))
    stray = 0
    changed = 0
    allowed = bytearray(len(elf_an))
    for fo, _ in sites_an:
        for k in range(4):
            allowed[fo + k] = 1
    for fo, w in sites_an:
        new_w = struct.unpack_from('<I', patched_an, fo)[0]
        changed += 1
        if (new_w >> 26) != 21 or ((new_w >> 21) & 31) != 0 or ((new_w >> 16) & 31) != 0:
            stray += 1
        if (new_w & 0xFFFF) != (w & 0xFFFF):
            stray += 1
    for i in range(len(elf_an)):
        if elf_an[i] != patched_an[i] and not allowed[i]:
            stray += 1
    ok &= check('reveal: only the %d guard words changed (%d stray)' % (changed, stray),
                changed == _analyse.EXPECTED_SITES and stray == 0)

    cfg_r = Config()
    cfg_r.reveal_enemies = True
    cfg_r.shuffle_text = False
    cfg_r.shuffle_voice = False
    r_r = Randomizer('a', 'b', cfg_r, log=lambda m: None)
    fp_r = FP()
    r_r._reveal_enemy_info(fp_r)
    ok &= check('reveal: the ELF was the only file written (%s)'
                % ', '.join(sorted(fp_r.written)),
                sorted(fp_r.written) == [_analyse.ELF_PATH])
    ok &= check('reveal: game data untouched (UNIT/ENCOUNT not rewritten)',
                '/battle/UNIT.TBL' not in fp_r.written
                and '/battle/ENCOUNT.TBL' not in fp_r.written)
    ok &= check('reveal: stats report %d patched guard(s)'
                % r_r.stats.get('reveal_guards_patched', 0),
                r_r.stats.get('reveal_guards_patched') == _analyse.EXPECTED_SITES)
    ok &= check('reveal: off by default', Config().reveal_enemies is False)

    vpaths = [e.path for e in g.ddt.files() if e.path.startswith('/voice/')][:40]
    vblobs = dict((f, g.read(f)) for f in vpaths)
    vout = voice.shuffle_all(vpaths, vblobs, random.Random(2))
    ok &= check('voice shuffle keeps size',
                all(len(vout[f]) == len(vblobs[f]) for f in vout))
    g.close()
    print()
    print('SELFTEST', 'PASSED' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())