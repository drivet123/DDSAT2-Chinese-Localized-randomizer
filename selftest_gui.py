#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Self test for the GUI front end.

Builds the window off-screen, flips every switch and checks that the Config
that would be handed to the randomizer matches the widgets, that the ban list
and the extra-byte list parse, and that a config file survives a round trip.
No ISO is built.  Run with:

    python selftest_gui.py

Skipped (exit 0) when PySide2 is not installed, so it is safe to chain after
selftest.py on a machine without Qt.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'src'))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide2 import QtWidgets
except ImportError:
    print('PySide2 not installed - GUI self test skipped')
    sys.exit(0)

import dds2_randomizer_gui as gui


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = gui.Window()
    ok = True

    def check(name, cond):
        print('%-52s %s' % (name, 'OK' if cond else 'FAIL'))
        return bool(cond)

    tabs = w.findChildren(QtWidgets.QTabWidget)[0]
    names = [tabs.tabText(i) for i in range(tabs.count())]
    ok &= check('GUI builds (%d tabs: %s)' % (len(names), '/'.join(names)), len(names) == 4)
    ok &= check('GUI has no boss tab (shielded)',
                not any('boss' in n.lower() or 'Boss' in n for n in names))

    cfg = w._config()
    ok &= check('defaults: encounters on', cfg.randomize_encounters is True)
    ok &= check('defaults: AI remap on', cfg.remap_enemy_ai is True)
    ok &= check('defaults: text on', cfg.shuffle_text is True)
    ok &= check('defaults: voice off', cfg.shuffle_voice is False)
    ok &= check('defaults: mantras on', cfg.randomize_mantras is True)
    ok &= check('defaults: mantra mode is the skill pool',
                cfg.mantra_swap_mode == 'skills')
    ok &= check('defaults: analysis reveal off', cfg.reveal_enemies is False)
    ok &= check('defaults: black varuna off', cfg.black_varuna is False)
    ok &= check('defaults: variable length text off', cfg.field_varlen is False)
    ok &= check('defaults: cg text mode is global',
                cfg.text_cg_mode == 'global')
    ok &= check('defaults: seed generated', isinstance(cfg.seed, int))

    # a text-only build must switch every other part off
    for cb in (w.enc, w.skills, w.ai, w.mantra):
        cb.setChecked(False)
    w.text.setChecked(True)
    cfg = w._config()
    ok &= check('text-only build disables the rest',
                cfg.shuffle_text and not cfg.randomize_encounters
                and not cfg.shuffle_bosses and not cfg.remap_enemy_ai
                and not cfg.randomize_mantras)
    ok &= check('text-only summary is accurate',
                w.summary.text() == 'CG 字幕随机（global）')
    ok &= check('text tab states the equal-length limit',
                '等长互换' in w.text.text())

    # numeric / list fields
    w.ban_ids.setText('120, 121 ,130')
    w.stats_mode.setCurrentIndex(1)
    w.voice_scope.setCurrentIndex(1)
    w.voice.setChecked(True)
    w.varuna.setChecked(True)
    w.reveal.setChecked(True)
    cfg = w._config()
    ok &= check('boss shuffle stays shielded', cfg.shuffle_bosses is False)
    ok &= check('black varuna switch reaches Config', cfg.black_varuna is True)
    ok &= check('black varuna shows up in the summary',
                '黑伐楼罗' in w.summary.text())
    ok &= check('reveal switch reaches Config', cfg.reveal_enemies is True)
    ok &= check('varlen switch reaches Config', cfg.field_varlen is False)
    w.cg_mode.setCurrentIndex(2)
    ok &= check('cg text mode reaches Config', w._config().text_cg_mode == 'off')
    w.cg_mode.setCurrentIndex(1)
    ok &= check('cg text mode file', w._config().text_cg_mode == 'file')
    w.cg_mode.setCurrentIndex(0)
    ok &= check('ban id list parses', cfg.ban_enemies == ['120', '121', '130'])
    ok &= check('variety slider', abs(cfg.encounter_variety - 0.5) < 1e-9)
    ok &= check('power tolerance', abs(cfg.enemy_power_tolerance - 0.25) < 1e-9)
    ok &= check('stats mode', cfg.stats_mode == 'scale')
    ok &= check('voice scope', cfg.shuffle_voice_scope == 'per_file')

    # config round trip through a real file
    path = os.path.join(tempfile.gettempdir(), 'dds2_gui_selftest.json')
    cfg.save(path)
    back = type(cfg)()
    back.load(path)
    w._apply(back)
    again = w._config()
    fields = ('ban_enemies', 'unit_patch_bytes',
              'encounter_variety', 'enemy_power_tolerance',
              'stats_mode', 'shuffle_voice_scope', 'shuffle_voice',
              'reveal_enemies', 'randomize_encounters', 'black_varuna')
    bad = [f for f in fields if getattr(cfg, f) != getattr(again, f)]
    ok &= check('config save/load round trip', not bad)

    # "nothing selected" must say so honestly
    for cb in (w.enc, w.skills, w.ai, w.text, w.voice, w.reveal, w.mantra, w.varuna):
        cb.setChecked(False)
    w._sync()
    ok &= check('empty selection is reported', '什么都不做' in w.summary.text())

    print('\n%s' % ('GUI SELFTEST PASSED' if ok else 'GUI SELFTEST FAILED'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
