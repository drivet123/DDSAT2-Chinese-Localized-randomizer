"""The `/fld/**/*.LB` chunk containers.

An `.LB` file is a flat sequence of chunks.  Each chunk is a 16-byte header
followed by its data:

    +0   u8    file type      (1 for a member of this container)
    +1   u8    compression flag (1 = the data is compressed)
    +2   u16   member index
    +4   u32   stored size    (bytes that follow the header)
    +8   char[4] member name  ("INF\\0", "BF\\0\\0", "AMB\\0", ...)
    +12  u32   unpacked size

There is NO offset field: a reader walks the chunks by reading each header and
skipping `stored` bytes, so growing one chunk simply shifts everything after it.
(This matches the "common header" Atlus uses elsewhere: file type, compression
flag, user id, size, magic, in-memory size.)

The member names line up one for one with the loose files next to the `.LB`
(`INF`/`NPL`/`SKY`/`WAP`/`AMB`), and member 5 is `BF` - the field script, i.e.
the loose `fNNN.bf`.  The loose file is only a build artefact: the game reads
the copy inside the `.LB`, which is why patching the loose `.bf` changes
nothing.

`replace_packed` re-compresses one member back into its slot with `lbcmp` /
`lbcmp_enc`, leaving the container size, the header and the offset of every
other member untouched.  `replace_raw` stores a member uncompressed instead,
which is only useful for throwaway probes because it grows the file.
"""
import struct

HEADER = 16
ALIGN = 64
NAMES = ('INF', 'NPL', 'SKY', 'WAP', 'BF', 'AMB')


class LbError(Exception):
    pass


class Chunk(object):
    __slots__ = ('offset', 'file_type', 'compressed', 'index', 'stored',
                 'name', 'unpacked')

    def __init__(self, offset, file_type, compressed, index, stored, name, unpacked):
        self.offset = offset
        self.file_type = file_type
        self.compressed = compressed
        self.index = index
        self.stored = stored
        self.name = name
        self.unpacked = unpacked

    @property
    def data_offset(self):
        return self.offset + HEADER

    @property
    def data_end(self):
        return self.data_offset + self.stored

    def __repr__(self):
        return '<Chunk %d %s stored=%d unpacked=%d %s>' % (
            self.index, self.name, self.stored, self.unpacked,
            'compressed' if self.compressed else 'raw')


def _looks_like_header(data, o):
    if o + HEADER > len(data):
        return None
    if data[o] != 1 or data[o + 3] != 0:
        return None
    stored = struct.unpack_from('<I', data, o + 4)[0]
    raw = data[o + 8:o + 12]
    unpacked = struct.unpack_from('<I', data, o + 12)[0]
    idx = struct.unpack_from('<H', data, o + 2)[0]
    if not (0 < stored < (1 << 26)):
        return None
    if not (0 < unpacked < (1 << 28)):
        return None
    if not (0 < idx < 256):
        return None
    # name: 2..4 printable characters, zero padded
    name = raw.split(b'\x00')[0]
    if not (2 <= len(name) <= 4):
        return None
    if not all(0x20 <= c < 0x7f for c in name):
        return None
    return Chunk(o, data[o], data[o + 1], idx, stored,
                 name.decode('latin-1'), unpacked)


def chunks(data):
    """Every chunk, walked from the start of the file."""
    out = []
    o = 0
    while o + HEADER <= len(data):
        c = _looks_like_header(data, o)
        if c is None:
            break
        out.append(c)
        nxt = c.data_end
        # the writer pads each chunk out to a 64 byte boundary
        padded = ((nxt + ALIGN - 1) // ALIGN) * ALIGN
        if _looks_like_header(data, padded):
            o = padded
            continue
        # tolerate a member whose stored size over-reports by a few bytes
        found = None
        for probe in range(nxt - 64, nxt + 128):
            if probe <= o:
                continue
            if _looks_like_header(data, probe):
                found = probe
                break
        if found is None:
            break
        o = found
    return out


def find_chunk(data, name):
    for c in chunks(data):
        if c.name == name:
            return c
    return None


def next_chunk_offset(data, c):
    """Offset of the header that follows chunk `c`."""
    nxt = c.data_end
    padded = ((nxt + ALIGN - 1) // ALIGN) * ALIGN
    if _looks_like_header(data, padded):
        return padded
    for probe in range(max(c.data_offset + 1, nxt - 64), nxt + 128):
        if _looks_like_header(data, probe):
            return probe
    return len(data)


def replace_raw(data, name, payload):
    """Return a copy of `data` with member `name` stored uncompressed.

    `payload` becomes the member's content verbatim; the file may get longer,
    which is fine because the chunks after it are simply shifted.
    """
    c = find_chunk(data, name)
    if c is None:
        raise LbError('no %s member' % name)
    tail_off = next_chunk_offset(data, c)
    head = bytearray(data[:c.offset])
    hdr = bytearray(data[c.offset:c.offset + HEADER])
    hdr[1] = 0                                     # compression flag: off
    struct.pack_into('<I', hdr, 4, len(payload))   # stored size
    struct.pack_into('<I', hdr, 12, len(payload))  # unpacked size
    body = bytes(hdr) + payload
    pad = (-len(body)) % ALIGN
    return bytes(head) + body + b'\x00' * pad + bytes(data[tail_off:])


def unpacked_size(data, name):
    c = find_chunk(data, name)
    return c.unpacked if c else 0


#: every compressed member's stream is followed by this terminator; the reader
#: stops at the end of the output, then skips the terminator, and the next chunk
#: starts at the next 64 byte boundary.  `stored` is informational only - it is
#: `consumed + 17` in the retail data and does not locate the next chunk.
TERMINATOR = 0xFF


def available(data, name):
    """Bytes the member's data area may occupy, i.e. up to the next chunk."""
    c = find_chunk(data, name)
    if c is None:
        raise LbError('no %s member' % name)
    return next_chunk_offset(data, c) - c.data_offset


def replace_packed(data, name, payload, encoder):
    """Re-compress member `name` in place without moving anything else.

    A member is located by where its decoder stops plus the `0xFF` terminator,
    and the chunk that follows starts at the next 64 byte boundary.  A straight
    shorter stream would therefore move every following chunk, so the caller's
    `encoder(payload, want_len)` is asked for a stream of exactly the length the
    retail stream consumed: then the terminator, the alignment and every offset
    after the member stay byte for byte where they were, and the header does not
    change at all.

    The encoder must lengthen the stream with tokens the game certainly handles
    (splitting literal runs), never with zero-length tricks.
    """
    from . import lbcmp
    c = find_chunk(data, name)
    if c is None:
        raise LbError('no %s member' % name)
    if len(payload) != c.unpacked:
        raise LbError('%s: payload is %d bytes, member holds %d'
                      % (name, len(payload), c.unpacked))
    nxt = next_chunk_offset(data, c)
    doff = c.data_offset
    src = data[doff:c.data_end]
    used = lbcmp.decode_consumed(src, c.unpacked)[1]
    packed = encoder(payload, used)
    if len(packed) != used:
        raise LbError('%s: encoder produced %d bytes, slot expects %d'
                      % (name, len(packed), used))
    buf = bytearray(data)
    buf[doff:doff + used] = packed
    end = doff + used
    if end < len(buf):
        buf[end] = TERMINATOR
    check = next_chunk_offset(bytes(buf), find_chunk(bytes(buf), name))
    if check != nxt:
        raise LbError('%s: layout would move %d -> %d' % (name, nxt, check))
    return bytes(buf)
