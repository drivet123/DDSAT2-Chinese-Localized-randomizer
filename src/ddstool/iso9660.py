"""Minimal ISO9660 reader/writer for the DDS2 PS2 disc image.

Only what the randomizer needs:
  * locate files by path,
  * read/write raw byte ranges (so the giant IMG can be patched in place),
  * rebuild the PVD + directory records if sizes change.
"""
import struct
import os

SECTOR = 2048


class ISOError(Exception):
    pass


def _both16(b, off):
    return struct.unpack_from('<H', b, off)[0]


def _both32(b, off):
    return struct.unpack_from('<I', b, off)[0]


class DirRecord(object):
    __slots__ = ('name', 'lba', 'size', 'flags', 'ext_attr_len', 'record_off', 'record_len')

    def __init__(self, name, lba, size, flags, ext_attr_len=0, record_off=0, record_len=0):
        self.name = name
        self.lba = lba
        self.size = size
        self.flags = flags
        self.ext_attr_len = ext_attr_len
        self.record_off = record_off
        self.record_len = record_len

    @property
    def is_dir(self):
        return bool(self.flags & 0x02)

    def __repr__(self):
        return '<%s %s lba=%d size=%d>' % ('DIR' if self.is_dir else 'FILE', self.name, self.lba, self.size)


class ISO9660(object):
    def __init__(self, path, mode='rb'):
        self.path = path
        self.f = open(path, mode)
        self.f.seek(16 * SECTOR)
        pvd = self.f.read(SECTOR)
        if pvd[1:6] != b'CD001':
            raise ISOError('not an ISO9660 image (no CD001 at sector 16)')
        self.volume_size = _both32(pvd, 80)          # in logical blocks
        self.block_size = _both16(pvd, 128) or SECTOR
        self.root = self._parse_record(pvd, 156, 156)
        self._dir_cache = {}

    # ------------------------------------------------------------------ io
    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def read_at(self, offset, size):
        self.f.seek(offset)
        return self.f.read(size)

    def read_lba(self, lba, size):
        return self.read_at(lba * SECTOR, size)

    def write_at(self, offset, data):
        self.f.seek(offset)
        self.f.write(data)

    def write_lba(self, lba, data):
        self.write_at(lba * SECTOR, data)

    def flush(self):
        self.f.flush()
        try:
            os.fsync(self.f.fileno())
        except (AttributeError, OSError):
            pass

    # ------------------------------------------------------------- directory
    def _parse_record(self, data, off, record_off):
        ln = data[off]
        if ln == 0:
            return None
        ext_attr_len = data[off + 1]
        lba = _both32(data, off + 2)
        size = _both32(data, off + 10)
        flags = data[off + 25]
        name_len = data[off + 32]
        raw = data[off + 33:off + 33 + name_len]
        if raw == b'\x00':
            name = '.'
        elif raw == b'\x01':
            name = '..'
        else:
            name = raw.split(b';')[0].decode('latin-1')
        return DirRecord(name, lba, size, flags, ext_attr_len, record_off + off, ln)

    def listdir(self, rec):
        """List directory records (excluding . and ..)."""
        key = (rec.lba, rec.size)
        if key in self._dir_cache:
            return self._dir_cache[key]
        data = self.read_lba(rec.lba, rec.size)
        out = []
        off = 0
        while off < len(data):
            if data[off] == 0:
                off = (off // SECTOR + 1) * SECTOR
                continue
            r = self._parse_record(data, off, rec.lba * SECTOR)
            if r is not None and r.name not in ('.', '..'):
                out.append(r)
            off += data[off]
        self._dir_cache[key] = out
        return out

    def walk(self, rec=None, prefix=''):
        """Yield every record with a full path (paths start with '/')."""
        if rec is None:
            rec = self.root
        for r in self.listdir(rec):
            path = prefix + '/' + r.name
            yield path, r
            if r.is_dir:
                for item in self.walk(r, path):
                    yield item

    def find(self, path):
        path = '/' + path.strip('/')
        parts = [p for p in path.split('/') if p]
        rec = self.root
        cur = ''
        for p in parts:
            found = None
            for r in self.listdir(rec):
                if r.name.lower() == p.lower():
                    found = r
                    break
            if found is None:
                return None
            rec = found
            cur += '/' + p
        return rec

    def read_file(self, rec):
        return self.read_lba(rec.lba, rec.size)
