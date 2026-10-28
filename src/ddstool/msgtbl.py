# DDS2 "MSG1" message tables (used by field, event and movie files).
#
# Block layout (little endian):
#   char  magic[4] = "MSG1"
#   u32   reserved0
#   u32   block_size          message area size, from magic
#   u32   entry_count         (unreliable, informational)
#   u32   table_count         (unreliable, informational)
#   u32   0x20000
#   u32   reserved1
#   struct { u32 flag; u32 rel_offset; } table[];   // rel to table start
#   ... entries, contiguous in table order ...
#
# Every entry:
#   char name[16];
#   u8   reserved[8];
#   u16  flag;         // usually 1
#   u16  0xffff;
#   u32  data_offset;  // offset of the voice clip inside the character .ADB
#   u32  length;       // text payload length in bytes
#   u8   text[length]; // custom 2-byte glyph encoding
#   u8   padding[..];  // variable padding up to the next table offset
#
# Because the table gives the exact entry boundaries we can treat a whole
# entry (name + voice reference + text + padding) as one opaque blob and
# move it between slots.  Moving the whole blob keeps subtitle and voice
# bound together.
import os
import struct

MAGIC = b'MSG1'
HEADER = 24          # magic + 5 dwords
TABLE_START = 24


class Entry(object):
    __slots__ = ('path', 'block', 'slot_offset', 'size', 'blob', 'name', 'index')

    def __init__(self, path, block, slot_offset, size, blob, index=0):
        self.path = path
        self.block = block
        self.slot_offset = slot_offset
        self.size = size
        self.blob = blob
        #: position in the block's table - what `rebuild_block` keys on
        self.index = index
        self.name = blob[:16].split(b'\x00')[0].decode('latin-1')

    def __repr__(self):
        return '<Entry %s %s @%#x size=%d>' % (self.path, self.name, self.slot_offset, self.size)


def _u32(d, o):
    return struct.unpack_from('<I', d, o)[0]


def find_blocks(data):
    out = []
    start = 0
    while True:
        p = data.find(MAGIC, start)
        if p < 0:
            break
        out.append(p)
        start = p + 4
    return out


def _parse_block(data, pos, hard_end):
    """Return the Entry list of one MSG1 block, or [] if it is not sane."""
    if pos + TABLE_START > len(data):
        return []
    block_size = _u32(data, pos + 8)
    block_end = pos + 4 + block_size
    if block_end > hard_end or block_end > len(data):
        block_end = hard_end
    count = _u32(data, pos + 16)
    table = pos + TABLE_START
    rels = []
    if 0 < count < 20000 and table + 8 * count <= len(data):
        for i in range(count):
            rels.append(struct.unpack_from('<I', data, table + 8 * i + 4)[0])
    else:
        guard = 0
        while table + 8 * (len(rels) + 1) <= len(data) and guard < 20000:
            flag, rel = struct.unpack_from('<II', data, table + 8 * len(rels))
            if rel == 0:
                break
            rels.append(rel)
            guard += 1
    if not rels:
        return []
    entries = []
    for i, rel in enumerate(rels):
        off = table + rel
        if off < table or off >= len(data):
            return []
        if i + 1 < len(rels):
            end = table + rels[i + 1]
        else:
            end = block_end
        if end <= off or end > len(data):
            return []
        entries.append(Entry('', pos, off, end - off, bytes(data[off:end]), i))
    return entries


def parse(data, path=''):
    """Return every Entry of every MSG1 block in |data|."""
    blocks = find_blocks(data)
    out = []
    for i, pos in enumerate(blocks):
        hard_end = blocks[i + 1] if i + 1 < len(blocks) else len(data)
        for e in _parse_block(data, pos, hard_end):
            e.path = path
            out.append(e)
    return out


def set_entry(data, entry, blob):
    """Write |blob| into the entry slot, zero padding to the slot size."""
    if len(blob) > entry.size:
        raise ValueError('blob does not fit')
    buf = bytearray(data)
    buf[entry.slot_offset:entry.slot_offset + entry.size] = blob + b'\x00' * (entry.size - len(blob))
    return bytes(buf)


def slot_signature(entry):
    """A cheap classification hint from the entry name."""
    n = entry.name
    return n.split('_')[0] if '_' in n else n


def block_bounds(data, pos, limit=None):
    """(first_entry_offset, block_end) of the MSG1 block that starts at `pos`.

    `block_end` is where the block's declared `block_size` says it stops, clamped
    to the buffer and to `limit`.  The entries live in
    `[first_entry_offset, block_end)`; the table is in front of them and its 8
    byte records hold the entry offsets relative to the table.

    `limit` matters: a block's own `block_size` can overrun the section that
    follows it (in `e802.bf` the block claims 0x47C while `.data.str` starts at
    0x47A), and a rebuild must never write past the real end.
    """
    block_size = _u32(data, pos + 8)
    block_end = min(pos + 4 + block_size, len(data))
    if limit is not None:
        block_end = min(block_end, limit)
    ents = _parse_block(data, pos, block_end)
    if not ents:
        # an empty block: the entries area still starts after the table
        table = pos + TABLE_START
        count = _u32(data, pos + 12)
        if not (0 < count < 20000) or table + 8 * count > block_end:
            return None
        return table + 8 * count, block_end
    return min(e.slot_offset for e in ents), block_end


def rebuild_block(buf, pos, new_texts, align=4, limit=None):
    """Rewrite one MSG1 block with new text payloads, keeping its size.

    `buf` is a mutable buffer holding the whole file, `new_texts` maps the
    entry's index in table order to its new text bytes (entries that are absent
    keep their text).  The entries are laid out again contiguously from where
    the first one started, every table offset is updated and the tail is zero
    filled - so `block_size`, the file size and every byte outside the block
    stay exactly where they were.

    `limit` clamps the block end (see `block_bounds`).

    Returns the number of entries rewritten, or None when the block does not
    have room for the new layout (in which case `buf` is left alone).
    """
    bounds = block_bounds(buf, pos, limit)
    if bounds is None:
        return None
    first, block_end = bounds
    ents = _parse_block(buf, pos, block_end)
    if not ents:
        return None
    cap = block_end - first
    table = pos + TABLE_START
    parts = []
    total = 0
    orig_end = first
    for i, e in enumerate(ents):
        o = e.slot_offset
        lines = struct.unpack_from('<H', buf, o + 24)[0]
        if lines > 32 or o + 32 + 4 * lines > len(buf):
            return None
        hdr = 32 + 4 * lines
        declared = _u32(buf, o + 28 + 4 * lines)
        text = new_texts.get(i)
        if text is None:
            text = bytes(buf[o + hdr:o + hdr + declared])
        # end of the ORIGINAL footprint of this entry: where its old text stopped.
        # It is what the new, possibly shorter, layout has to erase - and it is
        # clamped to the block so a bogus declared length cannot reach past it.
        old_end = min(o + hdr + declared, block_end)
        if old_end > orig_end:
            orig_end = old_end
        blob = bytearray(buf[o:o + hdr])
        struct.pack_into('<I', blob, 28 + 4 * lines, len(text))
        blob += text
        blob += b'\x00' * ((-len(blob)) % align)
        parts.append(bytes(blob))
        total += len(blob)
    if total > cap:
        return None
    off = first
    for i, blob in enumerate(parts):
        struct.pack_into('<I', buf, table + 8 * i + 4, off - table)
        off += len(blob)
    body = b''.join(parts)
    buf[first:first + len(body)] = body
    # erase the old text that the new, shorter layout no longer covers - but
    # only up to the end of the original layout, which is by construction inside
    # the block.  The block's declared `block_size` can overrun the section that
    # follows it, so zeroing all the way to `block_end` would corrupt it.
    if first + len(body) < orig_end:
        buf[first + len(body):orig_end] = b'\x00' * (orig_end - first - len(body))
    return len(parts)
