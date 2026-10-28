#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Freeze the current source tree into releases/<version>/ and a zip.

The snapshot is self-contained: it carries everything except the game image,
so a build can be reproduced from it alone.  A MANIFEST.json records the exact
file list with sizes and SHA-256 hashes.

    python tools/make_release.py 1.0
"""
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

INCLUDE_DIRS = ['src', 'tools', 'docs', 'bans']
INCLUDE_FILES = [
    'dds2_randomizer.py',
    'dds2_randomizer_gui.py',
    'selftest.py',
    'selftest_gui.py',
    'run_gui.bat',
    'README.md',
    'FINDINGS.md',
    'VERSION',
]
SKIP_EXT = {'.pyc', '.iso', '.p2s', '.zip'}
SKIP_DIRS = {'__pycache__', '.git'}
# pcsx2_*.ps1 / ps2*.ps1 are leftover emulator-driving experiments; the game is
# driven by hand, and those scripts are not part of the tool.
SKIP_FILES = set([
    'tools/pcsx2_driver.ps1', 'tools/pcsx2_experiment.ps1', 'tools/pcsx2_keys.ps1',
    'tools/pcsx2_long.ps1', 'tools/pcsx2_play.ps1', 'tools/pcsx2_post.ps1',
    'tools/pcsx2_run.ps1', 'tools/pcsx2_run_orig.ps1', 'tools/pcsx2_shot.ps1',
    'tools/ps2key.ps1', 'tools/ps2_enc_diff.ps1', 'tools/ps2_enc_step.ps1',
    'tools/ps2_enc_step2.ps1', 'tools/ps2_enc_step3.ps1', 'tools/ps2_full.ps1',
    'tools/ps2_live.ps1', 'tools/ps2_logrun.ps1', 'tools/ps2_quick.ps1',
    'tools/ps2_run2.ps1', 'tools/ps2_verified.ps1',
])
# workdir/ was used as a scratch area and ended up holding unrelated content
# (SLPM-* archives of other games, a 32 MB EE memory image, a dumped ELF and a
# full extracted copy of the ISO).  Only the small top-level reference tables
# that the code and docs actually cite are shipped.
WORKDIR_FILES = [
    'workdir/dds_functions.json',
    'workdir/FlowScriptBFFormatReference.txt',
    'workdir/FlowScriptAssemblyReference.txt',
    'workdir/FlowScriptModules.json',
    'workdir/global_scan.json',
    'workdir/msg1_scan.json',
    'workdir/voice_refs.json',
]


def collect():
    out = []
    for d in INCLUDE_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            for f in sorted(filenames):
                if os.path.splitext(f)[1].lower() in SKIP_EXT:
                    continue
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, ROOT).replace('\\', '/')
                if rel in SKIP_FILES:
                    continue
                out.append(rel)
    for f in INCLUDE_FILES + WORKDIR_FILES:
        full = os.path.join(ROOT, f)
        if os.path.isfile(full):
            out.append(f)
    return sorted(set(out))


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else '1.0'
    rel = os.path.join(ROOT, 'releases', version)
    if os.path.isdir(rel):
        shutil.rmtree(rel)
    os.makedirs(rel)

    files = collect()
    entries = []
    for relpath in files:
        src = os.path.join(ROOT, relpath)
        dst = os.path.join(rel, relpath)
        d = os.path.dirname(dst)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        shutil.copy2(src, dst)
        entries.append({'path': relpath, 'size': os.path.getsize(src),
                        'sha256': sha256(src)})

    manifest = {
        'name': 'DDS2 randomizer',
        'version': version,
        'frozen': time.strftime('%Y-%m-%d %H:%M:%S'),
        'files': len(entries),
        'bytes': sum(e['size'] for e in entries),
        'entry_points': {
            'gui': 'dds2_randomizer_gui.py  (or run_gui.bat)',
            'cli': 'dds2_randomizer.py --help',
            'selftest': 'selftest.py, selftest_gui.py',
            'enemy inspector': 'tools/enemy_info.py <id>',
            'savestate differ': 'tools/p2s_diff.py before.p2s after.p2s',
        },
        'requires': 'Python 3.7+; PySide2 for the GUI',
        'not_included': ['the game image (DDSAGA2.iso)', 'scratch/ experiments',
                         'built ISOs',
                         'the rest of workdir/ (dumps, extracted ISO tree)'],
        'contents': entries,
    }
    with open(os.path.join(rel, 'MANIFEST.json'), 'w') as f:
        json.dump(manifest, f, indent=2)

    zpath = os.path.join(ROOT, 'releases', 'DDS2_randomizer_%s.zip' % version)
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for relpath in files:
            z.write(os.path.join(ROOT, relpath),
                    '%s/%s' % ('DDS2_randomizer_%s' % version, relpath))
        z.write(os.path.join(rel, 'MANIFEST.json'),
                '%s/MANIFEST.json' % ('DDS2_randomizer_%s' % version))

    print('frozen %d files (%d bytes) -> %s' % (
        len(entries), manifest['bytes'], os.path.relpath(rel, ROOT)))
    print('archive: %s (%.1f MB)' % (
        os.path.relpath(zpath, ROOT), os.path.getsize(zpath) / 1048576.0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
