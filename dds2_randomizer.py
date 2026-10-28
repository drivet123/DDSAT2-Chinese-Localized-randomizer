#!/usr/bin/env python
# Digital Devil Saga 2 randomizer - command line front end.
#
#   python dds2_randomizer.py --iso DDSAGA2.iso --out DDS2_random.iso --seed 1234
#
import argparse
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'src'))

from ddstool.config import Config
from ddstool.randomizer import Randomizer


def build_parser():
    p = argparse.ArgumentParser(description='Randomize a Digital Devil Saga 2 ISO.')
    p.add_argument('--iso', default=os.path.join(HERE, 'DDSAGA2.iso'), help='input ISO')
    p.add_argument('--out', default=os.path.join(HERE, 'DDS2_random.iso'), help='output ISO')
    p.add_argument('--seed', type=int, default=None, help='random seed (default: random)')
    p.add_argument('--config', default=None, help='load options from a json file')
    p.add_argument('--save-config', default=None, help='write the effective options to a json file')
    p.add_argument('--variety', type=float, default=None, help='encounter variety 0..1 (default 0.5)')
    p.add_argument('--tolerance', type=float, default=None, help='enemy power tolerance (default 0.30)')
    p.add_argument('--no-encounters', action='store_true')
    p.add_argument('--no-enemy-skills', action='store_true')
    p.add_argument('--no-bosses', action='store_true')
    p.add_argument('--bosses', action='store_true',
                   help='enable the shielded story-battle shuffle (off by default: '
                        'story battles are already covered by the normal enemy '
                        'permutation; this extra pass is being reworked)')
    p.add_argument('--no-text', action='store_true')
    p.add_argument('--no-field-text', action='store_true',
                   help='do not re-compress the shuffled field scripts back '
                        'into their fNNN_000.LB containers (they stay inert '
                        'like before)')
    p.add_argument('--field-varlen', action='store_true',
                   help='EXPERIMENTAL: let FIELD dialogue runs of DIFFERENT byte '
                        'lengths trade places.  The whole MSG1 block is laid out '
                        'again so the .bf, and the compressed .LB member, keep their '
                        'size.  CG subtitles always keep their byte length')
    p.add_argument('--cg-text', choices=['global', 'file', 'off'], default='global',
                   help='where a CG subtitle text run may come from: global = any '
                        'cutscene (default), file = only the same cutscene, '
                        'off = leave CG subtitles alone.  Control codes never '
                        'move, so each entry keeps its own line structure')
    p.add_argument('--no-voice', action='store_true')
    p.add_argument('--voice-scope', choices=['global', 'per_file'], default='global')
    p.add_argument('--chaos-voice', action='store_true',
                   help='additionally scramble the raw .ADB voice files (breaks lip sync)')
    p.add_argument('--only', default=None,
                   help='comma separated subset of: '
                        'encounters,skills,bosses,text,voice,mantras,models,reveal')
    p.add_argument('--no-mantras', action='store_true',
                   help='do not randomize the Mantra (曼斗罗) skill lists')
    p.add_argument('--mantras', action='store_true',
                   help='randomize which skills each Mantra (曼斗罗) teaches '
                        '(on by default; kept so older command lines still work)')
    p.add_argument('--mantra-mode', choices=['skills', 'sets'], default='skills',
                   help="'skills' (default): every skill goes into one pool and is "
                        "dealt back out, each mantra keeping its number of skills; "
                        "'sets': trade whole skill sets 1:1")
    p.add_argument('--mantra-costs', action='store_true',
                   help='with --mantras: also permute the karma cost of each mantra')
    p.add_argument('--black-varuna', action='store_true',
                   help="replace 萨弗's demon form (Varuna / 伐楼罗) with the "
                        "Black Varuna (黑伐楼罗) model (cosmetic, off by default)")
    p.add_argument('--ban-file', default=None,
                   help='file listing enemy ids that must never appear as field '
                        'encounters (one id per line, or a json {"ban":[..]})')
    p.add_argument('--ban', default=None,
                   help='comma separated enemy ids to ban (in addition to --ban-file)')
    p.add_argument('--stats-mode', choices=['copy', 'scale'], default='copy',
                   help="how the five stats are aligned to the replaced enemy: "
                        "'copy' takes them verbatim (default), 'scale' only matches the sum")
    p.add_argument('--reveal-enemies', action='store_true',
                   help='make bosses and elite enemies analysable: neutralises the '
                        'thirteen guard branches in SLPM_657.95 that show "?" in the '
                        'analyse window (the game data itself is not touched)')
    p.add_argument('--no-enemy-table', action='store_true',
                   help='do not write the <out>_enemies.txt / .csv reference table')
    p.add_argument('--no-ai-remap', action='store_true',
                   help='do not rewrite the skill ids in AICALC.TBL (enemy AI)')
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = Config()
    if args.config:
        cfg.load(args.config)
    if args.seed is not None:
        cfg.seed = args.seed
    if args.variety is not None:
        cfg.encounter_variety = args.variety
    if args.tolerance is not None:
        cfg.enemy_power_tolerance = args.tolerance
    cfg.shuffle_voice_scope = args.voice_scope
    cfg.stats_mode = args.stats_mode
    if args.reveal_enemies:
        cfg.reveal_enemies = True
    if args.no_enemy_table:
        cfg.write_enemy_table = False
    if args.no_ai_remap:
        cfg.remap_enemy_ai = False
    if args.ban_file:
        cfg.ban_file = args.ban_file
    if args.ban:
        extra = [x.strip() for x in args.ban.split(',') if x.strip()]
        cfg.ban_enemies = list(cfg.ban_enemies) + extra
    if args.no_encounters:
        cfg.randomize_encounters = False
    if args.no_enemy_skills:
        cfg.randomize_enemy_skills = False
    if args.no_bosses:
        cfg.shuffle_bosses = False
    if args.bosses:
        cfg.shuffle_bosses = True
    if args.no_text:
        cfg.shuffle_text = False
    if args.no_field_text:
        cfg.field_text = False
    if args.field_varlen:
        cfg.field_varlen = True
    cfg.text_cg_mode = args.cg_text
    if args.no_voice:
        cfg.shuffle_voice = False
    if args.chaos_voice:
        cfg.shuffle_voice = True
    if args.no_mantras:
        cfg.randomize_mantras = False
    if args.mantras:
        cfg.randomize_mantras = True
    cfg.mantra_swap_mode = args.mantra_mode
    if args.mantra_costs:
        cfg.randomize_mantras = True
        cfg.mantra_shuffle_cost = True
    if args.black_varuna:
        cfg.black_varuna = True
    if args.only:
        want = set(x.strip() for x in args.only.split(',') if x.strip())
        cfg.randomize_encounters = 'encounters' in want
        cfg.randomize_enemy_skills = 'skills' in want
        cfg.shuffle_bosses = 'bosses' in want
        cfg.shuffle_text = 'text' in want
        cfg.shuffle_voice = 'voice' in want
        cfg.randomize_mantras = 'mantras' in want
        cfg.black_varuna = 'models' in want
        cfg.reveal_enemies = 'reveal' in want

    if cfg.seed is None:
        import random as _r
        cfg.seed = _r.randrange(1, 2 ** 31)
    print('seed: %d' % cfg.seed)
    print('options: %s' % cfg.summary())
    if args.save_config:
        cfg.save(args.save_config)

    def log(msg):
        print(msg)
        sys.stdout.flush()

    try:
        r = Randomizer(args.iso, args.out, cfg, log=log)
        stats = r.run()
    except Exception:
        traceback.print_exc()
        return 1
    print('--- statistics ---')
    for k in sorted(stats):
        print('%-24s %s' % (k, stats[k]))
    print('done: %s' % r.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())