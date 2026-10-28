# Parser for the "MSG1" message blocks used all over DDS2 (battle tables,
# battle scripts and event .PM1/.PM2 files).
#
# Layout of one message entry (all little endian):
#     char name[16];      NUL padded message id, e.g. "XSER2010"
#     u8   reserved[8];   zero
#     u16  flag;          usually 1
#     u16  sentinel;      0xffff
#     u32  data_offset;   offset of the voice / sub data (opaque)
#     u32  length;        byte length of the text payload
#     u8   text[length];  custom 2-byte glyph encoding, NUL padded to 4
#
# Entries are contiguous, so they can be walked without the offset table.
import struct

MAGIC = b'MSG1'
ENTRY_HEAD = 36


class Message(object):
    __slots__ = ('name', 'pos', 'flag', 'data_offset', 'length', 'text_pos', 'raw')

    def __init__(self, name, pos, flag, data_offset, length, text_pos, raw):
        self.name = name
        self.pos = pos
        self.flag = flag
        self.data_offset = data_offset
        self.length = length
        self.text_pos = text_pos
        self.raw = raw

    @property
    def total(self):
        return ENTRY_HEAD + ((self.length + 3) // 4) * 4

    def __repr__(self):
        return '<Msg %s len=%d @%#x>' % (self.name, self.length, self.pos)


def _align4(n):
    return ((n + 3) // 4) * 4


def find_blocks(data):
    out = []
    start = 0
    while True:
        i = data.find(MAGIC, start)
        if i < 0:
            break
        out.append(i)
        start = i + 4
    return out


def _looks_like_entry(data, p):
    if p + ENTRY_HEAD > len(data):
        return False
    name = data[p:p + 16]
    if not (65 <= name[0] <= 90 or 97 <= name[0] <= 122):
        return False
    if 0 not in name:
        return False
    for b in name.split(b'\x00')[0]:
        if not (48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122 or b == 95):
            return False
    if data[p + 16:p + 24] != b'\x00' * 8:
        return False
    if data[p + 26:p + 28] != b'\xff\xff':
        return False
    length = struct.unpack_from('<I', data, p + 32)[0]
    return 0 <= length <= 8192


def parse_messages(data, block_pos, limit=None):
    """Return the contiguous Message entries that follow a MSG1 magic."""
    if limit is None:
        limit = len(data)
    p = block_pos + 4
    end = min(limit, len(data))
    while p < end:
        if _looks_like_entry(data, p):
            break
        p += 1
    msgs = []
    while p + ENTRY_HEAD <= end and _looks_like_entry(data, p):
        name = data[p:p + 16].split(b'\x00')[0].decode('latin-1')
        flag = struct.unpack_from('<H', data, p + 24)[0]
        doff, length = struct.unpack_from('<II', data, p + 28)
        msgs.append(Message(name, p, flag, doff, length, p + ENTRY_HEAD,
                            bytes(data[p:p + ENTRY_HEAD + _align4(length)])))
        p += ENTRY_HEAD + _align4(length)
    return msgs


def parse_file(data):
    """Return [(block_pos, [Message, ...]), ...] for a buffer."""
    out = []
    for pos in find_blocks(data):
        msgs = parse_messages(data, pos)
        if msgs:
            out.append((pos, msgs))
    return out
