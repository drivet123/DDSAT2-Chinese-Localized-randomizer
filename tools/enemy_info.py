#!/usr/bin/env python
"""Enemy inspector / ban-list helper for the DDS2 randomizer.

The randomizer never decides by itself which enemies to ban - you do.  This
tool exists so you can look up which id is which enemy, then write those ids
into a ban file that the randomizer reads.

Usage
-----
    # show one enemy in detail
    python tools/enemy_info.py 76

    # show several
    python tools/enemy_info.py 76 41 27

    # dump the whole table (id, level, HP, MP, stats, #encounter records)
    python tools/enemy_info.py --all

    # filter: only enemies whose model/type byte is X, or with HP in range
    python tools/enemy_info.py --all --hp 1 999
    python tools/enemy_info.py --all --model 4

    # write a starter ban file with the ids you picked
    python tools/enemy_info.py --write-ban bans/enemies.json 120 121 122

Notes
-----
* enemy id N lives in UNIT.TBL record N + 127 (verified in-game).
* "records" lists the ENCOUNT.TBL table1 records that reference the id.
* Skill entries are shown as raw id numbers (no name table is decoded yet);
  two enemies with the same skill ids cast the same things.
* The detailed view also prints the enemy's recorded AI action list from
  AICALC.TBL.  Flat action record k belongs to UNIT record k + 128, so enemy
  id N uses record N - 1.  Entries whose skill is not one of the five skill
  slots are marked with '*' - the randomizer never re-points those.
* UNIT record +72/+74 is most likely a drop-item pair, not skills: it adds
  almost nothing to the AI-skill correlation and comes in small pairs.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

from ddstool.game import Game            # noqa: E402
from ddstool.unitmap import UNIT_BASE    # noqa: E402
from ddstool import aicalc               # noqa: E402

ENC = '/battle/ENCOUNT.TBL'
UNIT = '/battle/UNIT.TBL'
AICALC = '/battle/AICALC.TBL'
REC, HDR = 76, 16


def load(iso):
    g = Game(iso)
    enc = g.read(ENC)
    unit = g.read(UNIT)
    try:
        ai = g.read(AICALC)
    except Exception:
        ai = None
    g.close()
    return enc, unit, ai


def ai_actions(ai, eid):
    """The flat AI action list of enemy id `eid`.

    AICALC's flat action record `k` belongs to UNIT record `k + 128`, i.e. to
    enemy id `k + 1` - so enemy id N uses record N - 1.
    """
    if ai is None:
        return None
    index = aicalc.index_for_unit_record(UNIT_BASE + eid)
    if not (0 <= index < aicalc.AI_TABLE_COUNT):
        return None
    out = []
    for _wo, _so, weight, skill in aicalc.flat_weight_offsets(ai, index):
        if weight or skill:
            out.append((weight, skill))
    return out


def unit_of(unit, eid):
    rec = UNIT_BASE + eid
    r = unit[HDR + REC * rec:HDR + REC * rec + REC]
    if not r:
        return None
    return {
        'rec': rec,
        'skills': [struct.unpack_from('<H', r, 2 * k)[0] for k in range(5)]
                  + [struct.unpack_from('<H', r, 72)[0], struct.unpack_from('<H', r, 74)[0]],
        'model': r[52],
        'level': r[53],
        'hp': struct.unpack_from('<H', r, 54)[0],
        'hp_cur': struct.unpack_from('<H', r, 56)[0],
        'mp': struct.unpack_from('<H', r, 58)[0],
        'mp_cur': struct.unpack_from('<H', r, 60)[0],
        'stats': list(r[64:69]),
        'kind': r[69],
        'raw': r,
    }


def records_of(enc, eid):
    out = []
    for r in range(1024):
        b = enc[40 * r + 10:40 * r + 20]
        v = [x for x in struct.unpack_from('<5H', b) if x and x != 0xFFFF]
        if eid in v:
            out.append((r, v))
    return out


def fmt_records(recs, limit=16):
    if not recs:
        return '-'
    s = ' '.join(str(r) for r, _ in recs[:limit])
    if len(recs) > limit:
        s += ' ...(%d)' % len(recs)
    return s


def load_names(path):
    """Optional id -> display name map.  The game stores enemy names in the
    message tables with the Chinese patch's own glyph encoding, which is not
    decoded yet, so names come from a small user-maintained file:
        {"76": "アルプ", "75": "カハク", ...}
    """
    if not path or not os.path.exists(path):
        return {}
    try:
        import json
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                out[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
    return out


def dump_all(enc, unit, args, names):
    print('%-5s %-14s %-4s %-7s %-7s %-18s %-5s %-5s %s' % (
        'id', 'name', 'lv', 'hp', 'mp', 'stats(力魔体速勇)', 'mdl', 'kind', 'encounter records'))
    rows = []
    for eid in range(0, 400):
        u = unit_of(unit, eid)
        if not u or not u['hp'] or u['hp'] >= 32767:
            continue
        if args.hp and not (args.hp[0] <= u['hp'] <= args.hp[1]):
            continue
        if args.model is not None and u['model'] != args.model:
            continue
        recs = records_of(enc, eid)
        if args.used_only and not recs:
            continue
        rows.append((eid, u, recs))
    if args.sort == 'hp':
        rows.sort(key=lambda r: r[1]['hp'])
    elif args.sort == 'level':
        rows.sort(key=lambda r: r[1]['level'])
    for eid, u, recs in rows:
        print('%-5d %-14s %-4d %-7d %-7d %-18s %-5d %-5d %s' % (
            eid, names.get(eid, '?'), u['level'], u['hp'], u['mp'], u['stats'],
            u['model'], u['kind'], fmt_records(recs)))
    print('\n%d enemies listed.  (name = "?" means no entry in the names file)' % len(rows))


def show(enc, unit, ai, eid, names):
    u = unit_of(unit, eid)
    if not u:
        print('id %d: out of range' % eid)
        return
    print('=== enemy id %d  (%s)  UNIT.TBL record %d ===' % (
        eid, names.get(eid, 'name unknown'), u['rec']))
    print('  level      : %d' % u['level'])
    print('  HP         : %d (cur %d)' % (u['hp'], u['hp_cur']))
    print('  MP         : %d (cur %d)' % (u['mp'], u['mp_cur']))
    print('  stats      : 力%d 魔%d 体%d 速%d 运%d  (sum %d)' % (
        u['stats'][0], u['stats'][1], u['stats'][2], u['stats'][3], u['stats'][4],
        sum(u['stats'])))
    print('  model/kind : %d / %d' % (u['model'], u['kind']))
    print('  skills     : %s' % u['skills'][:5])
    print('  +72/+74    : %s   (likely drop items, not skills)' % u['skills'][5:])
    acts = ai_actions(ai, eid)
    if acts is None:
        print('  AI actions : (AICALC.TBL unavailable)')
    elif not acts:
        print('  AI actions : (none recorded - this unit relies on the '
              'FlowScript AI or has no action list)')
    else:
        mine = set(x for x in u['skills'][:5] if x)
        known = sum(1 for w, s in acts if s in mine)
        print('  AI actions : %d entr%s, %d reference one of its own skills'
              % (len(acts), 'y' if len(acts) == 1 else 'ies', known))
        for w, s in acts:
            mark = ' ' if s in mine else '*'
            print('     weight %-4d skill %-5d %s' % (w, s, mark))
        if known != len(acts):
            print('     (* = not one of its five skill slots; such an entry is '
                  'never re-pointed by the randomizer)')
    recs = records_of(enc, eid)
    if recs:
        print('  encounter records (%d):' % len(recs))
        for r, ids in recs[:20]:
            print('     rec %4d  ids %s' % (r, ids))
    else:
        print('  encounter records: (not referenced by any normal record)')
    if u['hp'] >= 32767:
        print('  NOTE: placeholder / special unit (HP 32767) - never randomized')
    if not names.get(eid):
        print('  (add a name to bans/enemy_names.json to see it here)')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('ids', nargs='*', type=int)
    ap.add_argument('--iso', default=os.path.join(ROOT, 'DDSAGA2.iso'))
    ap.add_argument('--all', action='store_true', help='dump the whole table')
    ap.add_argument('--hp', nargs=2, type=int, metavar=('MIN', 'MAX'))
    ap.add_argument('--model', type=int, default=None)
    ap.add_argument('--used-only', action='store_true',
                    help='only enemies referenced by an encounter record')
    ap.add_argument('--names', default=os.path.join(ROOT, 'bans', 'enemy_names.json'),
                    help='optional id -> name json used for the display name')
    ap.add_argument('--sort', choices=['id', 'hp', 'level'], default='id')
    ap.add_argument('--write-ban', metavar='FILE',
                    help='write the given ids to a ban file and exit')
    ap.add_argument('--write-names-template', metavar='FILE',
                    help='write an id -> name template (names left empty) and exit')
    a = ap.parse_args()

    if a.write_ban:
        if not a.ids:
            print('--write-ban needs at least one id')
            return 1
        d = os.path.dirname(os.path.abspath(a.write_ban))
        if d:
            os.makedirs(d, exist_ok=True) if not os.path.isdir(d) else None
        import json
        with open(a.write_ban, 'w') as f:
            json.dump({'ban': sorted(set(a.ids)),
                       '_comment': 'enemy ids that must never appear as field encounters'},
                      f, indent=2)
        print('wrote %s with %d id(s)' % (a.write_ban, len(set(a.ids))))
        return 0

    enc, unit, ai = load(a.iso)

    if a.write_names_template:
        import json
        out = {}
        for eid in range(0, 400):
            u = unit_of(unit, eid)
            if not u or not u['hp'] or u['hp'] >= 32767:
                continue
            out[str(eid)] = ''
        d = os.path.dirname(os.path.abspath(a.write_names_template))
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(a.write_names_template, 'w') as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print('wrote %s with %d ids - fill in the names (wiki or in-game)' % (
            a.write_names_template, len(out)))
        return 0

    names = load_names(a.names)
    if a.all:
        dump_all(enc, unit, a, names)
        return 0
    if not a.ids:
        ap.print_help()
        return 1
    for i, eid in enumerate(a.ids):
        if i:
            print()
        show(enc, unit, ai, eid, names)
    return 0


if __name__ == '__main__':
    sys.exit(main())
