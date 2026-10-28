"""DDS2 /voice/*.ADB (APCM) container: parser + size-preserving voice shuffle.

This module is self-contained (Python 3.7, stdlib only): feed it raw .ADB
bytes, no ISO/DDT access is required.

Empirical format (see docs/voice.md for the full evidence)
----------------------------------------------------------
Each shipped /voice/<CODE><NNNN>.ADB file is ONE 4-bit ADPCM stream plus a
fixed 32-byte header.  There is NO internal table of audio segments in any of
the 432 files on the disc; the file itself is the segment.

    offset  size   content
    0x00    0x20   header (see below)
    0x20    16*n   payload, made of 16-byte APCM blocks:
                   block 0            all zero (lead-in silence)
                   block 1 .. S-2     the encoded voice
                   block S-1          last data block, byte1 == 0x01
                   block S            stop marker  00 07 77*14
                   block S+1 or S+2   end marker   ff 00*15
                   block S+2 ..       zero padding to EOF

Header (little endian; constant in all 432 files except length):

    0x00  u32   version        == 0x0000000b
    0x04  u32   0
    0x08  u8[4] magic          b"APCM"
    0x0c  u32   0
    0x10  u32   length         payload_size // 2 for 405 files; 27 files
                               carry 512 trailing pad bytes the field does
                               not cover (Adb.stale_length)
    0x14  u32   0x00000100
    0x18  u32   0xffffffff
    0x1c  u32   0x00000100

Block framing evidence: the byte at 0x21 + 16*k is 0x00 in 2 640 512 of
2 641 376 blocks (plus 0x01 and 0x07 exactly once per file), while the byte at
0x20 + 16*k is a shift/filter nibble (high nibble <= 4, the five classic
SPU2/PSX ADPCM filter coefficients, in 99.98% of blocks).  14 bytes of 4-bit
samples per 16-byte block => 28 samples per block.

Shuffle
-------
shuffle_all / shuffle_paths / build_permutation permute whole audio regions
between files.  Every output file keeps its OWN header, its OWN stop/end
trailer and therefore its ORIGINAL byte size; a shorter source is zero padded
and a longer one is truncated on a 16-byte block boundary.  No table, offset
or size field ever has to be rewritten, so containers cannot be corrupted.
"""

import os
import random
import struct

HEADER_SIZE = 0x20
BLOCK_SIZE = 16
SAMPLES_PER_BLOCK = 28
MAGIC = b"APCM"
VERSION = 0x0B

#: 16-byte "stop" block that terminates the encoded stream.
STOP_BLOCK = b"\x00\x07" + b"\x77" * 14
#: 16-byte "end" block that follows the stop block.
END_BLOCK = b"\xff" + b"\x00" * 15


class VoiceError(Exception):
    """Raised when a buffer is not a parsable APCM/ADB container."""


def _u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


class Segment(object):
    """A byte range inside an Adb file.

    kind is one of: header, apcm, trailer, stop, pad, end.  offset/size are
    absolute within the file.
    """

    __slots__ = ("offset", "size", "kind")

    def __init__(self, offset, size, kind):
        self.offset = offset
        self.size = size
        self.kind = kind

    def __repr__(self):
        return "<Segment %s @0x%x +0x%x>" % (self.kind, self.offset, self.size)

    def __eq__(self, other):
        return (isinstance(other, Segment) and self.offset == other.offset
                and self.size == other.size and self.kind == other.kind)


class Adb(object):
    """Parsed .ADB APCM container.

    Attributes:
        data            the raw file bytes
        version         u32 at 0x00 (always 11 in shipped data)
        magic           bytes 0x08..0x0c (always b"APCM")
        length          u32 at 0x10 (payload/2, see stale_length)
        unk14/unk18/unk1c  the three constant u32 fields
        payload_offset  0x20
        payload_size    len(data) - 0x20
        stop_offset     offset of the 00 07 77*14 block
        end_offset      offset of the ff 00*15 block
        audio_offset    0x20
        audio_size      stop_offset - BLOCK_SIZE - audio_offset
        segments        ordered list of Segment covering the whole file
    """

    def __init__(self, data):
        data = bytes(data)
        if len(data) < HEADER_SIZE + 2 * BLOCK_SIZE:
            raise VoiceError("too small for an ADB container: %d bytes" % len(data))
        if data[8:12] != MAGIC:
            raise VoiceError("bad magic %r (expected APCM)" % (data[8:12],))
        if (len(data) - HEADER_SIZE) % BLOCK_SIZE:
            raise VoiceError("payload is not a multiple of 16 bytes")

        self.data = data
        self.version = _u32(data, 0x00)
        self.magic = data[8:12]
        self.length = _u32(data, 0x10)
        self.unk14 = _u32(data, 0x14)
        self.unk18 = _u32(data, 0x18)
        self.unk1c = _u32(data, 0x1c)

        self.payload_offset = HEADER_SIZE
        self.payload_size = len(data) - HEADER_SIZE

        self.stop_offset = self._find_block(STOP_BLOCK)
        if self.stop_offset is None:
            raise VoiceError("no stop block (00 07 77*14) found")
        end = self._find_block(END_BLOCK, self.stop_offset + BLOCK_SIZE)
        if end is None:
            raise VoiceError("no end block (ff 00*15) found after the stop block")
        self.end_offset = end

        # audio = everything before the last data block (the 0x0c/0x01 block,
        # which belongs to the trailer that we preserve byte for byte).
        self.audio_offset = HEADER_SIZE
        self.audio_size = self.stop_offset - BLOCK_SIZE - HEADER_SIZE
        if self.audio_size < 0:
            raise VoiceError("stop block overlaps the header")

        self.segments = self._build_segments()

    # ------------------------------------------------------------- helpers
    def _find_block(self, block, start=HEADER_SIZE):
        pos = self.data.find(block, start)
        while pos >= 0:
            if (pos - HEADER_SIZE) % BLOCK_SIZE == 0:
                return pos
            pos = self.data.find(block, pos + 1)
        return None

    def _build_segments(self):
        segs = [Segment(0, HEADER_SIZE, "header")]
        segs.append(Segment(self.audio_offset, self.audio_size, "apcm"))
        cur = self.audio_offset + self.audio_size
        if cur < self.stop_offset:
            segs.append(Segment(cur, self.stop_offset - cur, "trailer"))
        segs.append(Segment(self.stop_offset, BLOCK_SIZE, "stop"))
        cur = self.stop_offset + BLOCK_SIZE
        if cur < self.end_offset:
            segs.append(Segment(cur, self.end_offset - cur, "pad"))
        segs.append(Segment(self.end_offset, BLOCK_SIZE, "end"))
        cur = self.end_offset + BLOCK_SIZE
        if cur < len(self.data):
            segs.append(Segment(cur, len(self.data) - cur, "pad"))
        return segs

    @property
    def stale_length(self):
        """True when the 0x10 length field does not cover the whole payload.

        27 of the 432 files carry 512 extra zero bytes at the end that the
        field ignores; the field is payload_size // 2 for the other 405.
        """
        return self.length * 2 != self.payload_size

    @property
    def block_count(self):
        return self.payload_size // BLOCK_SIZE

    @property
    def audio_block_count(self):
        """Number of 16-byte APCM blocks that carry voice samples."""
        return self.audio_size // BLOCK_SIZE

    def blocks(self):
        """Iterate (offset, block_bytes) over the whole payload."""
        off = self.payload_offset
        end = self.payload_offset + self.payload_size
        while off < end:
            yield off, self.data[off:off + BLOCK_SIZE]
            off += BLOCK_SIZE

    def audio_blocks(self):
        """Iterate (offset, block_bytes) over the audio region only."""
        off = self.audio_offset
        end = self.audio_offset + self.audio_size
        while off < end:
            yield off, self.data[off:off + BLOCK_SIZE]
            off += BLOCK_SIZE

    def trailer(self):
        """Bytes from the last data block to EOF.

        Copying this verbatim into another file keeps that file's stop/end
        markers and zero padding valid.
        """
        return self.data[self.audio_offset + self.audio_size:]

    def to_bytes(self):
        """Re-serialise.  Byte-identical to the input."""
        return self.data

    # --------------------------------------------------------------- ADPCM
    def decode(self):
        """Best-effort decode of the audio region to 16-bit PCM samples.

        Only useful for inspecting the framing; the shuffle never decodes.
        Uses the classic 5-coefficient 4-bit predictor and treats the two
        header bytes of every 16-byte block as (filter<<4)|shift plus pad.
        """
        coef = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))
        out = []
        h1 = h2 = 0
        for _, blk in self.audio_blocks():
            shift = blk[0] & 0x0F
            filt = (blk[0] >> 4) & 0x0F
            if filt > 4:
                filt = 4
            c1, c2 = coef[filt]
            for i in range(2, 16):
                byte = blk[i]
                for nib in ((byte >> 4) & 0x0F, byte & 0x0F):
                    s = nib - 16 if nib >= 8 else nib
                    s = (s << 12) >> shift
                    s += (h1 * c1 + h2 * c2) >> 6
                    if s > 32767:
                        s = 32767
                    elif s < -32768:
                        s = -32768
                    out.append(s)
                    h2, h1 = h1, s
        return out


def parse(data):
    """Parse raw bytes into an Adb (raises VoiceError)."""
    return Adb(data)


def read(path):
    """Read and parse an .ADB file from disk."""
    with open(path, "rb") as fh:
        return Adb(fh.read())


def is_adb(data):
    """True when data looks like an APCM/ADB container."""
    return len(data) >= HEADER_SIZE and data[8:12] == MAGIC


# --------------------------------------------------------------------------
# Shuffle
# --------------------------------------------------------------------------

def _fit_permutation(order, targets):
    """Source index per target with src.audio_size <= tgt.audio_size.

    order is a random permutation of source indices.  Starts from that random
    permutation and repairs every over-long assignment by swapping it with a
    pair that fits; if no such repair exists a sorted (tightest-first) fitting
    assignment is used instead.  Returns None when even that does not fit, i.e.
    when some source is longer than the largest target.
    """
    n = len(targets)
    sizes = [t.audio_size for t in targets]
    perm = list(order)
    for i in range(n):
        if sizes[perm[i]] <= sizes[i]:
            continue
        for j in range(n):
            if j == i:
                continue
            if sizes[perm[j]] <= sizes[i] and sizes[perm[i]] <= sizes[j]:
                perm[i], perm[j] = perm[j], perm[i]
                break
    if all(sizes[perm[i]] <= sizes[i] for i in range(n)):
        return perm
    srcs = sorted(range(n), key=lambda s: sizes[s])
    tgts = sorted(range(n), key=lambda t: sizes[t])
    for s, t in zip(srcs, tgts):
        if sizes[s] > sizes[t]:
            return None
    out = [None] * n
    for s, t in zip(srcs, tgts):
        out[t] = s
    return out


def build_permutation(adbs, rng=None, allow_truncate=True, derange=True):
    """Return a source index per target index for a global voice shuffle.

    With allow_truncate=True (default) this is a random permutation: a longer
    source is cut on a 16-byte block boundary.  With allow_truncate=False only
    size-safe assignments are produced (a shorter source is zero padded).
    With derange=True (default) a file is never assigned its own audio.
    """
    if rng is None:
        rng = random.Random()
    n = len(adbs)
    order = list(range(n))
    rng.shuffle(order)
    if derange and n > 1:
        for i in range(n):
            if order[i] == i:
                j = rng.randrange(n)
                while j == i:
                    j = rng.randrange(n)
                order[i], order[j] = order[j], order[i]
    if allow_truncate:
        return order
    fit = _fit_permutation(order, adbs)
    if fit is None:
        return order
    return fit


def shuffled_bytes(target, source, allow_truncate=True):
    """Build the new bytes of target using the audio of source.

    target keeps its header, its last data block, its stop/end markers and its
    zero padding, so the result is exactly len(target.data) bytes and is
    structurally identical to the original target.  The audio region is filled
    with the source audio, zero padded and (by default) truncated on a 16-byte
    block boundary.
    """
    if not allow_truncate and source.audio_size > target.audio_size:
        raise VoiceError("source audio (%d) does not fit target (%d)"
                         % (source.audio_size, target.audio_size))
    keep = min(source.audio_size, target.audio_size)
    keep -= keep % BLOCK_SIZE
    audio = source.data[source.audio_offset:source.audio_offset + keep]
    if len(audio) < target.audio_size:
        audio = audio + b"\x00" * (target.audio_size - len(audio))
    return target.data[:target.audio_offset] + audio + target.trailer()


def shuffle_bytes(blobs, rng=None, allow_truncate=True):
    """Shuffle a list of raw .ADB buffers (same lengths, order and headers)."""
    adbs = [Adb(b) for b in blobs]
    perm = build_permutation(adbs, rng, allow_truncate)
    return [shuffled_bytes(adbs[t], adbs[perm[t]], allow_truncate)
            for t in range(len(adbs))]


def shuffle_all(paths, blobs, rng=None, scope="global", allow_truncate=True):
    """Randomizer hook: {path: new_bytes} for a size-preserving voice shuffle.

    paths   iterable of /voice/... paths (order defines nothing)
    blobs   mapping path -> raw file bytes
    scope   "global" permutes audio across files; "per_file" is a no-op
            because every ADB contains exactly one audio stream (see
            docs/voice.md), so there is nothing to permute inside a file.

    Files that do not parse as an APCM container are skipped, never fatal.
    """
    if rng is None:
        rng = random.Random()
    if scope == "per_file":
        return {}
    good_paths = []
    adbs = []
    for p in paths:
        b = blobs.get(p)
        if not b:
            continue
        try:
            adbs.append(Adb(b))
        except VoiceError:
            continue
        good_paths.append(p)
    if len(good_paths) < 2:
        return {}
    perm = build_permutation(adbs, rng, allow_truncate)
    out = {}
    for t in range(len(good_paths)):
        new = shuffled_bytes(adbs[t], adbs[perm[t]], allow_truncate)
        if new != adbs[t].data:
            out[good_paths[t]] = new
    return out


def shuffle_paths(paths, rng=None, allow_truncate=True, in_place=False):
    """Shuffle every .ADB in paths (files on disk).

    With in_place=False (default) returns {path: new_bytes} and does not touch
    the disk.  With in_place=True each file is rewritten with the same byte
    length and {path: source_path} is returned.
    """
    blobs = {}
    for p in paths:
        with open(p, "rb") as fh:
            blobs[p] = fh.read()
    out = shuffle_all(paths, blobs, rng, "global", allow_truncate)
    if not in_place:
        return out
    mapping = {}
    for p, data in out.items():
        with open(p, "wb") as fh:
            fh.write(data)
    # recompute the source mapping cheaply for the report
    adbs = [Adb(blobs[p]) for p in paths]
    perm = build_permutation(adbs, rng, allow_truncate)
    for t, p in enumerate(paths):
        mapping[p] = paths[perm[t]]
    return mapping


def _aligned_count(data, block):
    """Count block-aligned occurrences of a marker block."""
    n = 0
    pos = data.find(block)
    while pos >= 0:
        if (pos - HEADER_SIZE) % BLOCK_SIZE == 0:
            n += 1
        pos = data.find(block, pos + 1)
    return n


def validate(data):
    """Return a dict of structural checks for a buffer (or Adb)."""
    adb = data if isinstance(data, Adb) else Adb(data)
    return {
        "size": len(adb.data),
        "payload_multiple_of_16": (len(adb.data) - HEADER_SIZE) % BLOCK_SIZE == 0,
        "header_constant": (adb.version == VERSION and adb.magic == MAGIC
                            and adb.unk14 == 0x100 and adb.unk18 == 0xFFFFFFFF
                            and adb.unk1c == 0x100),
        "stop_block_count": _aligned_count(adb.data, STOP_BLOCK),
        "end_block_count": _aligned_count(adb.data, END_BLOCK),
        "stop_block_count_raw": adb.data.count(STOP_BLOCK),
        "end_block_count_raw": adb.data.count(END_BLOCK),
        "audio_size": adb.audio_size,
        "audio_blocks": adb.audio_block_count,
        "stale_length": adb.stale_length,
    }


if __name__ == "__main__":  # tiny self-test
    import sys
    for path in sys.argv[1:]:
        a = read(path)
        print("%s: %d bytes, %d blocks, audio %d bytes (%d blocks), stop@0x%x "
              "end@0x%x stale_length=%s"
              % (os.path.basename(path), len(a.data), a.block_count,
                 a.audio_size, a.audio_block_count, a.stop_offset,
                 a.end_offset, a.stale_length))
