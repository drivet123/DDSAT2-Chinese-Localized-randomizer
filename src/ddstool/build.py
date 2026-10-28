# Build a randomised mod ISO.
#
# The DDT/IMG layout is kept byte-for-byte compatible: each patched file is
# written back into its original DDS3.IMG slot (padded with zeroes) so the
# ISO9660 directory never has to change.  Files that grew are relocated into
# the (small, ~7.5MB) free space of the IMG and the DDT entry is updated.
import os
import shutil
import struct

from .game import Game

BUF = 8 * 1024 * 1024


def copy_iso(src, dst, progress=None, buf=BUF):
    total = os.path.getsize(src)
    done = 0
    with open(src, 'rb') as fi, open(dst, 'wb') as fo:
        while True:
            chunk = fi.read(buf)
            if not chunk:
                break
            fo.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return dst


class Patcher(object):
    def __init__(self, iso_path, progress=None):
        self.game = Game(iso_path, writable=True)
        self.progress = progress
        self.relocated = 0

    def close(self):
        self.game.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def set_file(self, path, data):
        """Write |data| to |path|; keeps size if it fits, else relocates."""
        e = self.game.entry(path)
        if len(data) <= e.size:
            self.game.write(path, data)
        else:
            self.game.write(path, data, relocate=True)
            self.relocated += 1

    def set_dir(self, pred, fn):
        """fn(path, data) -> new data for every file matching pred(path)."""
        n = 0
        for e in list(self.game.ddt.files()):
            if not pred(e.path):
                continue
            data = self.game.read(e.path)
            new = fn(e.path, data)
            if new is not None and new != data:
                self.set_file(e.path, new)
                n += 1
        return n

    def set_iso_file(self, path, data):
        """Patch a file that lives in the ISO9660 tree (not in DDS3.IMG)."""
        self.game.write_iso_file(path, data)

    def save(self):
        self.game.save_ddt()
        self.game.flush()
