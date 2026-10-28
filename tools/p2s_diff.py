#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Diff the EE RAM of two PCSX2 savestates.

A `.p2s` savestate is a zip archive whose `eeMemory.bin` member is the full
32 MB Emotion Engine main RAM.  Two savestates taken a moment apart (for
example right before and right after using the Analyse command) differ in a
handful of places, and the smallest one that changes is usually the flag that
was looked for.  This tool prints those places.

    python tools/p2s_diff.py before.p2s after.p2s
    python tools/p2s_diff.py before.p2s after.p2s --window 0x100000 0x2000000
    python tools/p2s_diff.py before.p2s after.p2s --min-run 8 --context

Nothing is written; the savestates are opened read-only.
"""
import argparse
import struct
import sys
import zipfile

RAM_MEMBER = 'eeMemory.bin'


def read_ram(path):
    try:
        z = zipfile.ZipFile(path)
    except Exception as exc:
        raise SystemExit('%s: not a savestate zip (%s)' % (path, exc))
    try:
        return z.read(RAM_MEMBER)
    except KeyError:
        raise SystemExit('%s: no %s member (a memory card save?)'
                         % (path, RAM_MEMBER))


def diff_runs(a, b, lo, hi, min_run):
    """[(start, length)] of the runs in [lo, hi) where a and b differ."""
    runs = []
    start = None
    for i in range(lo, hi):
        if a[i] != b[i]:
            if start is None:
                start = i
        elif start is not None:
            if i - start >= min_run:
                runs.append((start, i - start))
            start = None
    if start is not None and hi - start >= min_run:
        runs.append((start, hi - start))
    return runs


def hexdump(data, start, n=64):
    out = []
    for o in range(start, min(len(data), start + n), 16):
        row = data[o:o + 16]
        txt = ''.join(chr(c) if 32 <= c < 127 else '.' for c in row)
        out.append('      0x%08X  %-47s  %s'
                   % (o, ' '.join('%02X' % c for c in row), txt))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('before')
    ap.add_argument('after')
    ap.add_argument('--window', nargs=2, type=lambda s: int(s, 0), default=None,
                    metavar=('LO', 'HI'), help='only look at this EE RAM range')
    ap.add_argument('--min-run', type=int, default=1,
                    help='ignore runs shorter than this many bytes')
    ap.add_argument('--max-runs', type=int, default=60,
                    help='print at most this many runs')
    ap.add_argument('--context', action='store_true',
                    help='hexdump each changed run')
    args = ap.parse_args()

    a = read_ram(args.before)
    b = read_ram(args.after)
    if len(a) != len(b):
        raise SystemExit('different RAM sizes: %d vs %d' % (len(a), len(b)))
    lo, hi = (args.window or (0, len(a)))
    lo = max(0, lo)
    hi = min(len(a), hi)

    total = sum(1 for i in range(lo, hi) if a[i] != b[i])
    runs = diff_runs(a, b, lo, hi, args.min_run)
    print('%s' % args.before)
    print('%s' % args.after)
    print('%d differing byte(s) in 0x%X..0x%X, %d run(s) of >= %d byte(s)'
          % (total, lo, hi, len(runs), args.min_run))
    print()
    print('  run        address     length  before -> after')
    for start, n in runs[:args.max_runs]:
        head = ' '.join('%02X' % c for c in a[start:start + min(n, 6)])
        new = ' '.join('%02X' % c for c in b[start:start + min(n, 6)])
        print('  %-4d 0x%08X %8d  %-20s -> %s' % (runs.index((start, n)),
                                                  start, n, head, new))
        if args.context:
            for line in hexdump(a, start):
                print(line)
            print('      ---')
            for line in hexdump(b, start):
                print(line)
    if len(runs) > args.max_runs:
        print('  ... %d more run(s)' % (len(runs) - args.max_runs))
    return 0


if __name__ == '__main__':
    sys.exit(main())
