# DDS3.DDT / DDS3.IMG container.
#
# DDS3.DDT is a breadth-first serialised tree of 12-byte records:
#   u32 name_offset;  u32 lba;  s32 size;
# file: lba = sector offset inside DDS3.IMG, size = byte size
# dir : lba = offset of the children record array, size = -child_count
import struct

SECTOR = 2048
RECORD = 12


class DDTError(Exception):
    pass


class Entry(object):
    __slots__ = ('name', 'path', 'record_off', 'lba', 'size', 'is_dir',
                 'child_array', 'child_count', 'parent')

    def __init__(self, name, path, record_off, lba, size, is_dir,
                 child_array=0, child_count=0, parent=None):
        self.name = name
        self.path = path
        self.record_off = record_off
        self.lba = lba
        self.size = size
        self.is_dir = is_dir
        self.child_array = child_array
        self.child_count = child_count
        self.parent = parent

    @property
    def offset(self):
        return self.lba * SECTOR

    def __repr__(self):
        kind = 'DIR ' if self.is_dir else 'FILE'
        return '<%s %s lba=%d size=%d>' % (kind, self.path, self.lba, self.size)


class DDT(object):
    def __init__(self, data):
        self.data = bytearray(data)
        self.entries = []
        self.by_path = {}
        self.by_offset = {}
        self._parse()

    def _name(self, off):
        if off == 0:
            return '/'
        if off >= len(self.data):
            return '<bad:%x>' % off
        end = self.data.find(0, off)
        if end < 0:
            end = len(self.data)
        return bytes(self.data[off:end]).decode('latin-1')

    def _parse(self):
        data = self.data
        if len(data) < RECORD:
            raise DDTError('DDT too small')

        def walk(array, count, prefix, parent, depth, seen):
            if depth > 64:
                raise DDTError('DDT tree too deep')
            for i in range(count):
                off = array + i * RECORD
                if off + RECORD > len(data):
                    raise DDTError('record out of range')
                name_off, f2, f3 = struct.unpack_from('<IIi', bytes(data), off)
                name = self._name(name_off)
                path = prefix + '/' + name
                if f3 < 0:
                    child_count = -f3
                    if f2 in seen:
                        raise DDTError('cycle in DDT at %s' % path)
                    e = Entry(name, path, off, 0, 0, True, f2, child_count, parent)
                    self.entries.append(e)
                    self.by_path[path] = e
                    seen.add(f2)
                    walk(f2, child_count, path, e, depth + 1, seen)
                else:
                    e = Entry(name, path, off, f2, f3, False, 0, 0, parent)
                    self.entries.append(e)
                    self.by_path[path] = e
                    self.by_offset[f2 * SECTOR] = e

        name_off, f2, f3 = struct.unpack_from('<IIi', bytes(data), 0)
        if f3 >= 0:
            raise DDTError('root is not a directory')
        root = Entry('/', '/', 0, 0, 0, True, f2, -f3, None)
        self.entries.append(root)
        self.by_path['/'] = root
        walk(f2, -f3, '', root, 0, set([f2]))

    def files(self):
        return [e for e in self.entries if not e.is_dir]

    def dirs(self):
        return [e for e in self.entries if e.is_dir]

    def set_location(self, path, lba, size):
        e = self.by_path[path]
        if e.is_dir:
            raise DDTError('%s is a directory' % path)
        e.lba = lba
        e.size = size
        struct.pack_into('<Ii', self.data, e.record_off + 4, lba, size)
        self.by_offset[lba * SECTOR] = e

    def set_size(self, path, size):
        e = self.by_path[path]
        e.size = size
        struct.pack_into('<i', self.data, e.record_off + 8, size)

    def to_bytes(self):
        return bytes(self.data)

    def overlapping(self):
        fs = sorted(self.files(), key=lambda e: e.lba)
        out = []
        for i in range(1, len(fs)):
            prev = fs[i - 1]
            cur = fs[i]
            end = prev.offset + prev.size
            if cur.offset < end:
                out.append((prev, cur, end - cur.offset))
        return out

    def imagespan(self):
        return max((e.offset + e.size) for e in self.files())
