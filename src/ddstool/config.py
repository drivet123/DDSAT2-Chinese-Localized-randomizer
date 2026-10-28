# Randomizer configuration.
import json
import random


class Config(object):
    def __init__(self):
        self.seed = None
        # normal encounters
        self.randomize_encounters = True
        self.encounter_variety = 0.5      # 0 = none, 1 = very wide power window
        self.randomize_enemy_skills = True
        self.scale_enemy_stats = True     # nudge stats towards the replaced unit
        self.enemy_power_tolerance = 0.25 # allowed relative HP / stat budget difference
        # enemies that must never show up as field encounters.  Either a list of
        # ids or a path to a file with one id per line (see tools/enemy_info.py).
        # Their encounter records are rewritten to another encounter of the same
        # block, and the ids are removed from the randomization pool.
        self.ban_enemies = []
        self.ban_file = None
        # how the five stats (str/mag/vit/agi/luk) are aligned to the enemy that
        # used to stand in the slot:
        #   'copy'  - copy the original stat vector verbatim (default)
        #   'scale' - scale the incoming stats so the sum matches the original
        self.stats_mode = 'copy'
        # Analysis ("分析") visibility.  Bosses and elite enemies answer with "?"
        # because the analyse window's row functions branch to a placeholder.
        # `reveal_enemies` neutralises those thirteen guard branches in
        # SLPM_657.95 (see ddstool/analyse.py); the game data is left alone,
        # because rewriting UNIT.TBL +70 made boss battles hang on load.
        # See docs/METHOD_ENEMY_SWAP.md section 7 for the code addresses.
        self.reveal_enemies = False
        # Deprecated: the old "clear these UNIT.TBL bytes" probes.  They are
        # kept so old JSON configs still load, but are no longer used.
        self.reveal_all_bytes = False
        self.unit_patch_bytes = []
        # write an id -> stats reference table next to the output ISO
        self.write_enemy_table = True
        # rewrite the skill ids inside /battle/AICALC.TBL so an enemy actually
        # casts the skills of the unit it was swapped with
        self.remap_enemy_ai = True
        # bosses
        # SHIELDED (2026-09-12): the dedicated story-battle shuffle is off by
        # default.  Story battles are already shaken up through the ordinary
        # enemy permutation, and the extra swap/rescale pass is being reworked.
        # The code is kept intact - enable it with --bosses if you want it back.
        self.shuffle_bosses = False
        self.boss_boss_multiplier = True  # rescale stats to the original boss
        self.boss_hp_threshold = 3000     # units with at least this HP are bosses
        self.boss_match_strength = 1.0    # 0 = keep incoming strength, 1 = match original
        # text / voice
        self.shuffle_text = True
        # How CG subtitle entries may trade text (see `textdata.classify`: the
        # `/event/**/*.PM1` tables).  Only the plain glyph runs inside a subtitle
        # are ever traded - every control code stays where it is, because a CG
        # text is cutscene specific (e601 writes `f208ffff f207`, e603 writes
        # `f208ffff f41305ffdb08 f207`: 334 distinct leading codes over 93 files).
        #   'global' - a run may come from any cutscene (default)
        #   'file'   - a run may only come from the same cutscene
        #   'off'    - CG subtitles are left exactly as they are
        # Reported in game (2026-10): trading WHOLE texts across cutscenes made
        # subtitles appear late, hold for the wrong time and sometimes vanish -
        # with EQUAL lengths too, so it was never a length problem.
        self.text_cg_mode = 'global'
        # Pre-rendered movie cutscenes (/MOVIE, /MOVIE2, /MOVIE3 - the opening one
        # is /MOVIE2/E601_001.IPS) carry their audio inside the MPEG stream, and
        # their subtitle table is read by the MOVIE player rather than by the
        # in-engine message box.  In game every build that scrambled such a table
        # stopped showing subtitles - and stopped playing the movie's audio - a few
        # lines in, while builds that left the CG tables alone were fine.  Their
        # .PM1 files are therefore left untouched by default.  See
        # docs/METHOD_ENEMY_SWAP.md section 11.13.1e.
        self.cg_skip_movies = True
        # CG voices follow their subtitles automatically (whole message
        # entries are swapped).  shuffle_voice additionally scrambles the
        # raw .ADB files, which is chaotic and breaks the lip sync.
        self.shuffle_voice = False
        self.shuffle_voice_scope = 'global'   # 'global' or 'per_file'
        # Field dialogue is stored compressed in the BF member of fNNN_000.LB,
        # and the loose fNNN.bf next to it is only a build artefact.  With this
        # on the shuffled script is re-compressed back into the container (the
        # member keeps its slot, so the ISO size does not change).
        self.field_text = True
        # EXPERIMENTAL: allow FIELD dialogue runs of different byte lengths to
        # trade places.  The whole MSG1 block is laid out again so that the
        # `.bf` - and therefore the compressed `.LB` member - keeps its size.
        # CG subtitles can NOT do this: changing their length made the subtitles
        # appear at the wrong time and vanish (see docs/METHOD_ENEMY_SWAP.md
        # section 11.13), so they always keep their byte length.
        self.field_varlen = False
        # Mantra (曼斗罗).  Which skills a mantra teaches, and the stat bonus it
        # hands out.  The data lives in the game executable (SLPM_657.95,
        # ISO9660 root), not in DDS3.IMG - see ddstool/mantra.py.  A node always
        # keeps its cost, its rank/type and its place on the hex grid.
        # Verified in game (2026-09-13): the skill lists and the starting skill
        # sets follow the shuffle correctly, so this layer is on by default.
        self.randomize_mantras = True
        #   'skills' - every skill of every teaching mantra goes into one pool and
        #              is dealt back out; each mantra keeps the *number* of skills
        #              it had, but the skills themselves come from all over the
        #              grid, and a mantra never gets one of its own back.
        #   'sets'   - the older, coarser mode: whole skill sets are traded 1:1,
        #              so a mantra ends up with exactly another mantra's set.
        self.mantra_swap_mode = 'skills'
        # The permanent stat bonus (STR/MAG/VIT/AGI/LUK) a mantra hands out when
        # it is learned.  Only the 8 story-given event mantras carry one, so
        # those five-stat vectors are permuted among themselves.
        self.mantra_shuffle_bonus = True
        self.mantra_shuffle_cost = False   # also permute the karma cost
        # write a mantra id -> cost / bonus / skills reference table next to
        # the output ISO (like the enemy table)
        self.write_mantra_table = True
        # Character model swap (opt-in, cosmetic).  Serph's (萨弗) Atma-ON demon
        # form is Varuna (伐楼罗); with this on, /model/human/pc011_on.PB and
        # pc011.PB are overwritten with the "Black Varuna" (黑伐楼罗) model the
        # game uses in the e639 scene (/model/devil/10E.PB).  The donor is
        # smaller than both slots, so the files keep their size and nothing in
        # DDS3.IMG moves - see ddstool/model.py.
        self.black_varuna = False
        # misc
        self.backup = True
        self.verify = True

    def rng(self):
        return random.Random(self.seed)

    def summary(self):
        return {k: v for k, v in sorted(self.__dict__.items())}

    def save(self, path):
        with open(path, 'w') as f:
            json.dump(self.summary(), f, indent=2)

    def load(self, path):
        with open(path) as f:
            data = json.load(f)
        for k, v in data.items():
            if hasattr(self, k):
                setattr(self, k, v)
