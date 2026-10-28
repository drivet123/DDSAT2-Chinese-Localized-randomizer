#!/usr/bin/env python
"""Build a probe ISO that stores every field script UNCOMPRESSED.

The field dialogue lives in the `BF` member of `/fld/**/*.LB`, and that member
is compressed.  We do not know the compression scheme, but the chunk header
carries a compression flag (the same "common header" Atlus uses: file type,
compression flag, user id, size, magic, in-memory size).  So instead of
decompressing we can simply store the member raw.

This probe changes NOTHING about the content: the payload is the loose
`fNNN.bf` byte for byte.  If the field behaves exactly as before, the
"uncompressed member" trick works and field dialogue can be randomised without
ever touching the compressor.  If the field breaks, the flag is not honoured
that way and we go back to cracking the codec.

    python tools/lb_probe.py [--iso DDSAGA2.iso] [--out DDS2_probe_lb.iso]
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

from ddstool import lb                       # noqa: E402
from ddstool.build import Patcher            # noqa: E402

SECTOR = 2048


def force_extend(game, path, data):
    """Move `path` to the end of DDS3.IMG even if its size did not change."""
    off = game.alloc_extend(len(data))
    game.iso.write_at(game.img_base + off,
                      data + b'\x00' * ((-len(data)) % SECTOR))
    game.ddt.set_location(path, off // SECTOR, len(data))
    game._free = None
    return off


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--iso', default=os.path.join(ROOT, 'DDSAGA2.iso'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'DDS2_probe_lb.iso'))
    ap.add_argument('--mode', choices=('raw', 'reloc', 'size'), default='raw',
                    help="raw   - store the BF member uncompressed (tests the flag)\n"
                         "reloc - move the containers with IDENTICAL content, which "
                         "only tests the IMG growth + DDT relocation\n"
                         "size  - change NOTHING except the declared size of DDS3.IMG "
                         "in the ISO9660 directory (tests size validation)")
    a = ap.parse_args()

    from ddstool.build import copy_iso
    print('copying %s -> %s  (mode=%s)' % (os.path.basename(a.iso), os.path.basename(a.out), a.mode))
    copy_iso(a.iso, a.out)

    if a.mode == 'size':
        # nothing is moved and no member is touched: only the ISO9660 record for
        # DDS3.IMG is told it is larger.  If this alone breaks the game, the game
        # validates that size and the whole "grow the IMG" approach is dead.
        import struct
        with Patcher(a.out) as p:
            g = p.game
            new_size = g.img_size + 0x100000          # +1 MiB, sector aligned
            new_size = ((new_size + SECTOR - 1) // SECTOR) * SECTOR
            rec = g.img_rec
            g.iso.write_at(rec.record_off + 10, struct.pack('<I', new_size))
            g.iso.write_at(rec.record_off + 14, struct.pack('>I', new_size))
            g.iso.flush()
            print('   DDS3.IMG declared size: %d -> %d (record at %#x)'
                  % (g.img_size, new_size, rec.record_off))
            print('   nothing else was changed')
        print('probe written: %s' % a.out)
        return 0

    done = 0
    skipped = []
    with Patcher(a.out) as p:
        for e in list(p.game.ddt.files()):
            if not (e.path.startswith('/fld/') and e.name.lower().endswith('.lb')):
                continue
            data = p.game.read(e.path)
            try:
                c = lb.find_chunk(data, 'BF')
            except Exception as exc:
                skipped.append((e.path, str(exc)))
                continue
            if c is None:
                continue
            loose = re.sub(r'_[0-9a-fA-F]{3}\.(lb)$', '.bf', e.path, flags=re.I)
            try:
                payload = p.game.read(loose)
            except Exception:
                skipped.append((e.path, 'no loose %s' % loose))
                continue
            if len(payload) != c.unpacked:
                skipped.append((e.path, 'loose %s is %d bytes, member unpacked %d'
                                % (os.path.basename(loose), len(payload), c.unpacked)))
                continue
            if a.mode == 'raw':
                new = lb.replace_raw(data, 'BF', payload)
                p.game.write(e.path, new, relocate=True)
            else:
                new = data                     # identical bytes, just a new home
                force_extend(p.game, e.path, new)
            done += 1
            print('   %-44s %d -> %d bytes (+%d)'
                  % (e.path, len(data), len(new), len(new) - len(data)))
        p.save()

    print('\nfield containers processed: %d' % done)
    for path, why in skipped:
        print('   skipped %s (%s)' % (path, why))
    print('probe written: %s' % a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
