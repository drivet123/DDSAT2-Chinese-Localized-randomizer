import os
import struct
from .iso9660 import ISO9660, SECTOR
from .ddtimg import DDT, Entry


class GameError(Exception):
    pass


class Game(object):
    # High level access to a DDS2 PS2 ISO: ISO9660 + DDS3.DDT + DDS3.IMG.

    def __init__(self, iso_path, writable=False):
        self.iso_path = iso_path
        self.iso = ISO9660(iso_path, 'r+b' if writable else 'rb')
        self.ddt_rec = self.iso.find('/DDS3.DDT')
        self.img_rec = self.iso.find('/DDS3.IMG')
        if self.ddt_rec is None or self.img_rec is None:
            raise GameError('DDS3.DDT / DDS3.IMG not found')
        self.ddt = DDT(self.iso.read_file(self.ddt_rec))
        self.img_base = self.img_rec.lba * SECTOR
        self.img_size = self.img_rec.size
        self._free = None

    def close(self):
        self.iso.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ------------------------------------------------------------- reading
    def entry(self, path):
        e = self.ddt.by_path.get(path)
        if e is None:
            raise GameError('no such file: %s' % path)
        return e

    def read(self, path):
        e = self.entry(path)
        if e.is_dir:
            raise GameError('%s is a directory' % path)
        return self.iso.read_at(self.img_base + e.offset, e.size)

    def listdir(self, path):
        e = self.ddt.by_path.get(path)
        if e is None or not e.is_dir:
            return []
        out = []
        for x in self.ddt.entries:
            if x.parent is e:
                out.append(x)
        return out

    def glob(self, prefix):
        prefix = prefix.rstrip('/')
        return [e.path for e in self.ddt.files() if e.path.startswith(prefix + '/')]

    # ------------------------------------------------- ISO9660 root files
    # A few tables the randomizer needs (the game executable SLPM_657.95, which
    # holds the per mantras skills) live in the ISO9660 filesystem instead of
    # DDS3.IMG, so they are not part of the DDT tree.  They can still be patched
    # in place: the directory record keeps its LBA and size as long as the
    # payload keeps its length.
    def iso_file(self, path):
        rec = self.iso.find(path)
        if rec is None:
            raise GameError('no such ISO9660 file: %s' % path)
        return rec

    def read_iso_file(self, path):
        return self.iso.read_file(self.iso_file(path))

    def write_iso_file(self, path, data):
        rec = self.iso_file(path)
        if len(data) != rec.size:
            raise GameError('%s changed size (%d -> %d bytes)' % (path, rec.size, len(data)))
        self.iso.write_at(rec.lba * SECTOR, data)
        return rec.lba * SECTOR

    # ------------------------------------------------------------- writing
    def _free_intervals(self):
        if self._free is not None:
            return self._free
        fs = sorted(self.ddt.files(), key=lambda e: e.offset)
        free = []
        pos = 0
        for e in fs:
            if e.offset > pos:
                free.append((pos, e.offset))
            pos = max(pos, e.offset + e.size)
        if pos < self.img_size:
            free.append((pos, self.img_size))
        self._free = free
        return free

    def alloc(self, size, align=SECTOR):
        # allocate a sector aligned hole in the IMG, returns byte offset
        need = ((size + align - 1) // align) * align
        for idx, (a, b) in enumerate(self._free_intervals()):
            start = ((a + align - 1) // align) * align
            if b - start >= need:
                self._free[idx] = (start + need, b)
                return start
        return None

    def alloc_extend(self, size, align=SECTOR):
        """Append to the end of DDS3.IMG and grow it.

        The retail ISO has ~3 GB of unused space between the end of DDS3.IMG
        and the next file (ZZZ50MB.DAT), while every hole *inside* the IMG is
        smaller than 2 KB - so a file that has to grow must go here.  `save_ddt`
        then widens the ISO9660 record for DDS3.IMG to match.

        The new size is rounded up to a whole sector: the retail IMG is exactly
        1466599424 bytes = 716113 sectors, and a size that is not sector aligned
        makes the ISO9660 reader drop the tail of the file.
        """
        off = ((self.img_size + align - 1) // align) * align
        end = off + size
        end = ((end + SECTOR - 1) // SECTOR) * SECTOR
        self.img_size = end
        self._img_grew = True
        return off

    def write(self, path, data, relocate=False):
        e = self.entry(path)
        if e.is_dir:
            raise GameError('%s is a directory' % path)
        if len(data) <= e.size:
            padded = data + b'\x00' * (e.size - len(data))
            self.iso.write_at(self.img_base + e.offset, padded)
            return e.offset
        if not relocate:
            raise GameError('%s grew from %d to %d bytes' % (path, e.size, len(data)))
        off = self.alloc(len(data))
        if off is None:
            off = self.alloc_extend(len(data))
        self.iso.write_at(self.img_base + off, data + b'\x00' * ((-len(data)) % SECTOR))
        self.ddt.set_location(path, off // SECTOR, len(data))
        self._free = None
        return off

    def save_ddt(self):
        blob = self.ddt.to_bytes()
        if len(blob) > self.ddt_rec.size:
            raise GameError('DDT grew, rebuild required')
        blob = blob + b'\x00' * (self.ddt_rec.size - len(blob))
        self.iso.write_lba(self.ddt_rec.lba, blob)
        if getattr(self, '_img_grew', False):
            # the ISO9660 record for DDS3.IMG still says the old size
            rec = self.img_rec
            import struct as _s
            self.iso.write_at(rec.record_off + 10, _s.pack('<I', self.img_size))
            self.iso.write_at(rec.record_off + 14, _s.pack('>I', self.img_size))
            rec.size = self.img_size

    def flush(self):
        self.iso.flush()
