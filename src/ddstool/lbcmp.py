"""`.LB` member compression - the complete format.

The format was recovered with a known-plaintext oracle: the `BF` member of each
`/fld/f/fNNN/fNNN_NNN.LB` is also present as a loose `fNNN.bf`, so every
candidate reading of the stream can be checked against the bytes it is supposed
to produce.  With the table below **all 22 field containers decode exactly**
(1 136 745 bytes, byte for byte), and every other `.LB` member in the retail
image decodes to exactly its declared size as well.

Token layout
------------
An opcode is the high nibble of a byte, a small parameter is the low nibble.
Tokens without operands are ONE byte.

    op   normal form                       long form (low nibble == 0)
    --   -------------------------------    ----------------------------------
    0    lo literal bytes                  [u16 le count] + count literals
    1    16 + lo literal bytes             -
    2    lo zero bytes                     [u16 le count] zero bytes
    3    16 + lo zero bytes                -
    4    lo copies of the next byte        [u16 le count] + value byte
    5    16 + lo copies of the next byte   -
    6    lo bytes from an 8 bit distance   [u16 le count] + u8  distance
    7    16 + lo bytes, 8 bit distance     -
    8    lo bytes from a 16 bit distance   [u16 le count] + u16 le distance
    9    16 + lo bytes, 16 bit distance    -
    10   lo (byte, 0) pairs                [u16 le count] + count bytes
    11   16 + lo (byte, 0) pairs           -

every family that uses a base of zero has a long form: when the short form would
encode a length of zero the opcode instead carries a 16 bit length, followed by
whatever operand the family normally takes.  The base-16 siblings (`1`,`3`,`5`,
`7`,`9`,`11`) cannot encode zero, so they have no long form.

The seven rules that had to be discovered, each one found by measuring the
byte-exact prefix over the whole corpus:

1. `op 2` is a ONE byte token.  Reading it as "zero count plus literal count"
   worked for a few hundred bytes and then silently desynchronised; the second
   byte is just the next token.
2. `op 0` with a zero low nibble is a long literal run, not an empty one.
3. `op 2`, `op 4`, `op 10` likewise escape instead of emitting nothing.
4. `op 4` / `op 5` are byte fills that take the fill value as an operand - they
   are NOT the `32 + lo` / `48 + lo` zero runs they were first assumed to be.
   `op 4` occurred exactly once in `f001`, precisely where the decoder derailed.
5. A copy with a computed length of zero (`op 6` / `op 8`) is a long copy.
6. `op 10` stores a table of little endian u16 values whose high byte is zero -
   the shape of the FLW0 string / operand tables.
7. The distance of the long `op 8` form is 16 bit, that of `op 6` is 8 bit.

A note on `.LB` members: `stored` may exceed the length of the compressed
stream, in which case the tail is zero fill.  Stop as soon as `unpacked` bytes
have been produced - do not insist on consuming the whole member.

See docs/METHOD_ENEMY_SWAP.md section 11.11.
"""
import struct

#: op -> number of leading zero bytes the low nibble is added to
ZERO_BASE = {2: 0, 3: 16}
#: op -> base for the byte-fill family; the fill value is the next byte
FILL_BASE = {4: 0, 5: 16}
#: op -> base length for the 8 bit distance copy family
COPY_BASE = {6: 0, 7: 16}
#: op -> base length for the 16 bit distance copy family
DIST16_BASE = {8: 0, 9: 16}
#: op -> base for the "byte then zero" pair family
PAIR_BASE = {10: 0, 11: 16}
#: short forms whose low nibble may be clear, and which then use the long form
LONG_FORMS = frozenset([0, 2, 4, 6, 8, 10])

KNOWN_OPS = tuple(range(12))


class LbCodecError(Exception):
    """Raised for an opcode or operand the decoder cannot handle."""

    def __init__(self, message, ip=None, out_len=None, out=None):
        Exception.__init__(self, message)
        self.ip = ip
        self.out_len = out_len
        self.out = out


def _copy(out, dist, length):
    start = len(out) - dist
    if dist < 1 or start < 0:
        raise LbCodecError('distance %d invalid at output %d' % (dist, len(out)))
    for _ in range(length):
        out.append(out[start])
        start += 1


def decode(src, want):
    """Decode `src` into exactly `want` bytes.

    Stops as soon as `want` bytes exist, so trailing padding in the member is
    ignored.  Raises LbCodecError on anything the decoder cannot handle; the
    partial result is available as the exception's `.out`.
    """
    out = bytearray()
    ip = 0
    try:
        while len(out) < want:
            if ip >= len(src):
                raise LbCodecError('ran out of input at %d/%d' % (len(out), want),
                                   ip=ip, out_len=len(out))
            a = src[ip]
            op = a >> 4
            lo = a & 0x0F
            if op == 0:
                if lo == 0:
                    m = struct.unpack_from('<H', src, ip + 1)[0]
                    ip += 3
                else:
                    m = lo
                    ip += 1
                out.extend(src[ip:ip + m])
                ip += m
            elif op == 1:
                ip += 1
                m = 16 + lo
                out.extend(src[ip:ip + m])
                ip += m
            elif op in ZERO_BASE:
                if op == 2 and lo == 0:
                    m = struct.unpack_from('<H', src, ip + 1)[0]
                    ip += 3
                else:
                    m = ZERO_BASE[op] + lo
                    ip += 1
                out.extend(b'\x00' * m)
            elif op in FILL_BASE:
                if op == 4 and lo == 0:
                    m = struct.unpack_from('<H', src, ip + 1)[0]
                    value = src[ip + 3]
                    ip += 4
                else:
                    m = FILL_BASE[op] + lo
                    value = src[ip + 1]
                    ip += 2
                out.extend(bytes([value]) * m)
            elif op in COPY_BASE:
                if op == 6 and lo == 0:
                    m = struct.unpack_from('<H', src, ip + 1)[0]
                    dist = src[ip + 3]
                    ip += 4
                else:
                    m = COPY_BASE[op] + lo
                    dist = src[ip + 1]
                    ip += 2
                _copy(out, dist, m)
            elif op in DIST16_BASE:
                if op == 8 and lo == 0:
                    m = struct.unpack_from('<H', src, ip + 1)[0]
                    dist = struct.unpack_from('<H', src, ip + 3)[0]
                    ip += 5
                else:
                    m = DIST16_BASE[op] + lo
                    dist = struct.unpack_from('<H', src, ip + 1)[0]
                    ip += 3
                _copy(out, dist, m)
            elif op in PAIR_BASE:
                if op == 10 and lo == 0:
                    m = struct.unpack_from('<H', src, ip + 1)[0]
                    ip += 3
                else:
                    m = PAIR_BASE[op] + lo
                    ip += 1
                for k in range(m):
                    out.append(src[ip + k])
                    out.append(0)
                ip += m
            else:
                raise LbCodecError('opcode %d at input %d (output %d)'
                                   % (op, ip, len(out)), ip=ip, out_len=len(out))
    except LbCodecError as exc:
        exc.out = bytes(out)
        raise
    return bytes(out)


def decode_prefix(src, want):
    """Decode as far as possible: returns ``(output, error_or_None, ip)``."""
    try:
        return decode(src, want), None, None
    except LbCodecError as exc:
        return exc.out, exc, exc.ip


def decode_consumed(src, want):
    """Decode `want` bytes and report how many input bytes that took.

    The members are walked by the position the decoder stopped at, so the
    consumed length - not `stored` - is what fixes where the next chunk header
    sits.
    """
    out = bytearray()
    ip = 0
    n = len(src)
    while len(out) < want:
        if ip >= n:
            raise LbCodecError('ran out of input at %d/%d' % (len(out), want),
                               ip=ip, out_len=len(out))
        ip, err = step(src, ip, out)
        if err:
            raise LbCodecError(err, ip=ip, out_len=len(out), out=bytes(out))
    return bytes(out), ip


def step(src, ip, out):
    """Apply one token to `out`.  Returns (new_ip, error_or_None)."""
    a = src[ip]
    op = a >> 4
    lo = a & 0x0F
    if op == 0:
        if lo == 0:
            m = struct.unpack_from('<H', src, ip + 1)[0]
            ip += 3
        else:
            m = lo
            ip += 1
        out.extend(src[ip:ip + m])
        return ip + m, None
    if op == 1:
        ip += 1
        m = 16 + lo
        out.extend(src[ip:ip + m])
        return ip + m, None
    if op == 2:
        if lo == 0:
            m = struct.unpack_from('<H', src, ip + 1)[0]
            ip += 3
        else:
            m = lo
            ip += 1
        out.extend(b'\x00' * m)
        return ip, None
    if op == 3:
        out.extend(b'\x00' * (16 + lo))
        return ip + 1, None
    if op in (4, 5):
        if op == 4 and lo == 0:
            m = struct.unpack_from('<H', src, ip + 1)[0]
            value = src[ip + 3]
            ip += 4
        else:
            m = FILL_BASE[op] + lo
            value = src[ip + 1]
            ip += 2
        out.extend(bytes([value]) * m)
        return ip, None
    if op in (6, 7):
        if op == 6 and lo == 0:
            m = struct.unpack_from('<H', src, ip + 1)[0]
            dist = src[ip + 3]
            ip += 4
        else:
            m = COPY_BASE[op] + lo
            dist = src[ip + 1]
            ip += 2
        try:
            _copy(out, dist, m)
        except LbCodecError as exc:
            return ip, str(exc)
        return ip, None
    if op in (8, 9):
        if op == 8 and lo == 0:
            m = struct.unpack_from('<H', src, ip + 1)[0]
            dist = struct.unpack_from('<H', src, ip + 3)[0]
            ip += 5
        else:
            m = DIST16_BASE[op] + lo
            dist = struct.unpack_from('<H', src, ip + 1)[0]
            ip += 3
        try:
            _copy(out, dist, m)
        except LbCodecError as exc:
            return ip, str(exc)
        return ip, None
    if op in (10, 11):
        if op == 10 and lo == 0:
            m = struct.unpack_from('<H', src, ip + 1)[0]
            ip += 3
        else:
            m = PAIR_BASE[op] + lo
            ip += 1
        for k in range(m):
            out.append(src[ip + k])
            out.append(0)
        return ip + m, None
    return ip + 1, 'opcode %d' % op


#: tokens that produce no output, used to pad a stream back to the length the
#: original consumed without changing what it decodes to
NOOP_TOKENS = (b'\x00\x00\x00', b'\x60\x00\x00\x01', b'\x80\x00\x00\x01\x00')


def pad_noop(packed, target, room):
    """Pad `packed` with no-output tokens so that it consumes `target` bytes.

    A stream shorter than the original would move every following chunk, so the
    replacement has to stop at the same input offset.  `room` bounds the pad.
    """
    extra = target - len(packed)
    if extra < 0:
        raise ValueError('stream is %d bytes longer than the original %d'
                         % (len(packed), target))
    if extra > room:
        raise ValueError('needs %d bytes of padding, only %d available' % (extra, room))
    tail = []
    while extra:
        for tok in NOOP_TOKENS:
            if len(tok) <= extra:
                tail.append(tok)
                extra -= len(tok)
                break
        else:
            raise ValueError('cannot pad the remaining %d bytes' % extra)
    return packed + b''.join(tail)
