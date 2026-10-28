"""Encoders for the .LB member format.

Both emit the same token set the decoder understands, so
`lbcmp.decode(compress(x), len(x)) == x` for any input.

`compress` is a fast greedy matcher.  `compress_best` is a cost-optimal parse:
every family's token cost is constant inside a length range

    literals  1 + n   (n <= 31)   3 + n   (n >= 32)
    zeros     1       (n <= 31)   3       (n >= 32)
    fills     2       (n <= 31)   4       (n >= 32)
    pairs     1 + k   (k <= 31)   3 + k   (k >= 32, k = number of pairs)
    copy u8   2       (n <= 31)   4       (n >= 32)
    copy u16  3       (n <= 31)   5       (n >= 32)

so each family contributes one linear term plus a constant, and a forward
dynamic program with sliding-window minima finds the cheapest parse in O(n).
Copies are the only non-linear part; there the match finder supplies, for every
position, the longest match reachable with an 8 bit and with a 16 bit distance.

The goal is not to beat the retail encoder, only to get a member that has been
edited to re-compress to no more than its original `stored` size.
"""
import struct
from collections import deque

INF = 1 << 30
MIN_COPY = 3
MIN_RUN = 4
MIN_PAIRS = 4
PAIR_LIMIT = 4096


def _emit_literals(out, buf):
    i = 0
    n = len(buf)
    while i < n:
        left = n - i
        if left <= 15:
            out.append(left)
            out.extend(buf[i:i + left])
            i += left
        elif left <= 31:
            out.append(0x10 | (left - 16))
            out.extend(buf[i:i + left])
            i += left
        else:
            m = min(left, 65535)
            out.append(0x00)
            out.extend(struct.pack('<H', m))
            out.extend(buf[i:i + m])
            i += m


def _emit_zero_run(out, count):
    while count > 0:
        if count <= 15:
            out.append(0x20 | count); count = 0
        elif count <= 31:
            out.append(0x30 | (count - 16)); count = 0
        else:
            m = min(count, 65535)
            out.append(0x20)
            out.extend(struct.pack('<H', m))
            count -= m


def _emit_fill_run(out, value, count):
    while count > 0:
        if count <= 15:
            out.append(0x40 | count); out.append(value); count = 0
        elif count <= 31:
            out.append(0x50 | (count - 16)); out.append(value); count = 0
        else:
            m = min(count, 65535)
            out.append(0x40)
            out.extend(struct.pack('<H', m))
            out.append(value)
            count -= m


def _emit_pairs(out, buf, count):
    while count > 0:
        if count <= 15:
            out.append(0xA0 | count)
            for k in range(count):
                out.append(buf[k])
            count = 0
        elif count <= 31:
            out.append(0xB0 | (count - 16))
            for k in range(count):
                out.append(buf[k])
            count = 0
        else:
            m = min(count, 65535)
            out.append(0xA0)
            out.extend(struct.pack('<H', m))
            for k in range(m):
                out.append(buf[k])
            count -= m


def _emit_copy(out, dist, length):
    while length > 0:
        if length <= 15 and dist <= 0xFF:
            out.append(0x60 | length); out.append(dist); length = 0
        elif length <= 31 and dist <= 0xFF:
            out.append(0x70 | (length - 16)); out.append(dist); length = 0
        elif length <= 15:
            out.append(0x80 | length); out.extend(struct.pack('<H', dist)); length = 0
        elif length <= 31:
            out.append(0x90 | (length - 16)); out.extend(struct.pack('<H', dist)); length = 0
        else:
            m = min(length, 65535)
            if dist <= 0xFF:
                out.append(0x60)
                out.extend(struct.pack('<H', m))
                out.append(dist)
            else:
                out.append(0x80)
                out.extend(struct.pack('<H', m))
                out.extend(struct.pack('<H', dist))
            length -= m


def _match_length(data, a, b, limit):
    n = len(data)
    k = 0
    while k < limit and b + k < n and data[a + k] == data[b + k]:
        k += 1
    return k


def compress(data, window=0xFFFF, max_chain=64):
    data = bytes(data)
    n = len(data)
    out = bytearray()
    table = {}
    literal = bytearray()
    i = 0
    while i < n:
        best_len = 0
        best_dist = 0
        if i + MIN_COPY <= n:
            key = data[i:i + 3]
            chain = table.get(key)
            if chain:
                limit = min(n - i, 65535)
                for pos in reversed(chain):
                    dist = i - pos
                    if dist > window:
                        break
                    if data[pos + best_len] == data[i + best_len] if best_len else True:
                        L = _match_length(data, pos, i, limit)
                        if L > best_len:
                            best_len, best_dist = L, dist
                            if L >= limit:
                                break
        # zero run
        run_len = 0
        while i + run_len < n and data[i + run_len] == 0:
            run_len += 1
        fill_len = 0
        while i + fill_len < n and data[i + fill_len] == data[i]:
            fill_len += 1
        pair_count = 0
        if i + 1 < n:
            while (pair_count < PAIR_LIMIT and i + 2 * pair_count + 1 < n
                   and data[i + 2 * pair_count + 1] == 0):
                pair_count += 1
                if pair_count >= 1:
                    # abutting pairs must not be a simple zero run
                    if i + 2 * pair_count < n:
                        pass
        pair_bytes = bytearray()
        if pair_count >= MIN_PAIRS:
            for k in range(pair_count):
                pair_bytes.append(data[i + 2 * k])

        if best_len >= MIN_COPY and best_len >= run_len and best_len >= fill_len:
            _emit_literals(out, literal); literal = bytearray()
            _emit_copy(out, best_dist, best_len)
            for k in range(best_len):
                if i + k < n:
                    key = data[i + k:i + k + 3]
                    if len(key) == 3:
                        table.setdefault(key, []).append(i + k)
                        if len(table[key]) > max_chain:
                            table[key].pop(0)
            i += best_len
        elif run_len >= MIN_RUN:
            _emit_literals(out, literal); literal = bytearray()
            _emit_zero_run(out, run_len)
            i += run_len
        elif fill_len >= MIN_RUN:
            _emit_literals(out, literal); literal = bytearray()
            _emit_fill_run(out, data[i], fill_len)
            i += fill_len
        elif pair_count >= MIN_PAIRS:
            _emit_literals(out, literal); literal = bytearray()
            _emit_pairs(out, pair_bytes, pair_count)
            i += 2 * pair_count
        else:
            if i + 3 <= n:
                key = data[i:i + 3]
                table.setdefault(key, []).append(i)
                if len(table[key]) > max_chain:
                    table[key].pop(0)
            literal.append(data[i])
            i += 1
            if len(literal) >= 8192:
                _emit_literals(out, literal); literal = bytearray()
    _emit_literals(out, literal)
    return bytes(out)


# --------------------------------------------------------------------------
# cost-optimal parse
# --------------------------------------------------------------------------

def _find_matches(data, max_chain=128, window=0xFFFF, max_len=4096):
    """For every position, the longest match with an 8 bit and a 16 bit distance.

    `max_len` caps the scan.  Without it a long run of one byte makes every
    position in the run compare the whole run, which is quadratic.
    """
    n = len(data)
    ml_u8 = [0] * n
    d_u8 = [0] * n
    ml_any = [0] * n
    d_any = [0] * n
    table = {}
    for i in range(n - 2):
        key = data[i:i + 3]
        chain = table.get(key)
        if chain:
            limit = min(n - i, 65535, max_len)
            bu8 = bany = 0
            du8 = dany = 0
            steps = 0
            for pos in reversed(chain):
                dist = i - pos
                if dist > window or steps >= max_chain:
                    break
                steps += 1
                if bany and data[pos + bany] != data[i + bany]:
                    continue
                L = 0
                while L < limit and data[pos + L] == data[i + L]:
                    L += 1
                if L > bany:
                    bany = L
                    dany = dist
                if dist <= 0xFF and L > bu8:
                    bu8 = L
                    du8 = dist
                if bany >= limit:
                    break
            ml_u8[i] = bu8
            d_u8[i] = du8
            ml_any[i] = bany
            d_any[i] = dany
        table.setdefault(key, []).append(i)
        if len(table[key]) > max_chain:
            table[key].pop(0)
    return ml_u8, d_u8, ml_any, d_any


_COPY_CUTS = (1, 2, 3, 4, 15, 16, 31)


def _lit_min_len(n):
    """Encoded length of `n` literal bytes using the cheapest token split."""
    if n <= 0:
        return 0
    chunks = (n + 30) // 31
    return n + min(chunks, 3)


def _copy_cost(dist, length):
    """Encoded length of one copy token sequence."""
    buf = bytearray()
    _emit_copy(buf, dist, length)
    return len(buf)


def _inflate_parts(parts, extra, data):
    """Spend up to `extra` bytes on ordinary tokens only.

    Two stages: first split literal runs into single literals (`01 xx`, which
    occurs everywhere in the retail streams), then - if the parse is mostly
    copies - rewrite a copy as the literal bytes it stood for.  Both keep the
    decoded bytes identical and avoid the count-0 long runs the retail encoder
    never emits.
    """
    if extra <= 0:
        return parts, extra
    out = []
    for kind, arg in parts:
        if extra <= 0 or kind != 'lit' or len(arg) < 2:
            out.append((kind, arg))
            continue
        k = len(arg)
        base = _lit_min_len(k)
        best_s = best_add = 0
        for s in range(1, k):
            add = 2 * s + _lit_min_len(k - s) - base
            if 0 < add <= extra and add > best_add:
                best_s, best_add = s, add
        if best_s:
            for i in range(best_s):
                out.append(('lit', arg[i:i + 1]))
            if k - best_s:
                out.append(('lit', arg[best_s:]))
            extra -= best_add
        else:
            out.append((kind, arg))
    if extra > 0:
        out2 = []
        for kind, arg in out:
            if extra <= 0 or kind != 'copy':
                out2.append((kind, arg))
                continue
            dist, length, pos = arg
            delta = _lit_min_len(length) - _copy_cost(dist, length)
            if 0 < delta <= extra:
                out2.append(('lit', data[pos:pos + length]))
                extra -= delta
            else:
                out2.append((kind, arg))
        out = out2
    return out, extra


def compress_sized(data, want_len, **kw):
    """`compress_best` padded with ordinary tokens to exactly `want_len` bytes.

    A member is located by where its decoder stops, so a replacement stream has
    to end in the same 64 byte block as the original.  Growing the stream with
    cheap literal tokens keeps the decoded bytes identical.
    """
    parts, chosen = _best_parts(data, **kw)
    packed = _emit_parts(parts)
    if len(packed) > want_len:
        raise ValueError('cannot shrink %d to %d' % (len(packed), want_len))
    for _ in range(256):
        if len(packed) >= want_len:
            break
        parts, left = _inflate_parts(parts, want_len - len(packed), data)
        new = _emit_parts(parts)
        if len(new) == len(packed):
            break
        packed = new
    return packed


def compress_best(data, max_chain=128, window=0xFFFF, max_len=4096):
    """Cheapest parse under the token cost model.  Slower than `compress`."""
    return _emit_parts(_best_parts(data, max_chain, window, max_len)[0])


def _best_parts(data, max_chain=128, window=0xFFFF, max_len=4096):
    """Cheapest parse under the token cost model.  Slower than `compress`."""
    data = bytes(data)
    n = len(data)
    if n == 0:
        return b''
    ml_u8, d_u8, ml_any, d_any = _find_matches(data, max_chain, window, max_len)
    dp = [INF] * (n + 1)
    ch = [None] * (n + 1)
    dp[0] = 0

    litq = deque()                       # (dp[i]-i, i) for i in [j-31, j-1]
    litp_v = INF; litp_i = -1            # min(dp[i]-i) for i <= j-32
    zmin_v = INF; zmin_i = -1; zrun = 0
    fmin_v = INF; fmin_i = -1; frun = 0; fval = -1
    pmin_v = [INF, INF]; pmin_i = [-1, -1]; plen = [0, 0]

    for j in range(1, n + 1):
        b = data[j - 1]

        # ---- update the run states with the byte that ends at j ----
        if b == 0:
            zrun += 1
            if zrun == 1 or dp[j - 1] < zmin_v:
                zmin_v = dp[j - 1]; zmin_i = j - 1
        else:
            zrun = 0; zmin_v = INF; zmin_i = -1
        if b == fval:
            frun += 1
            if dp[j - 1] < fmin_v:
                fmin_v = dp[j - 1]; fmin_i = j - 1
        else:
            fval = b; frun = 1
            fmin_v = dp[j - 1]; fmin_i = j - 1
        q = j & 1
        if b == 0 and j >= 2:
            plen[q] += 1
            v = 2 * dp[j - 2] - (j - 2)
            if v < pmin_v[q]:
                pmin_v[q] = v; pmin_i[q] = j - 2
        else:
            plen[q] = 0; pmin_v[q] = INF; pmin_i[q] = -1

        # ---- literals ----
        v = dp[j - 1] - (j - 1)
        while litq and litq[-1][0] >= v:
            litq.pop()
        litq.append((v, j - 1))
        while litq[0][1] < j - 31:
            litq.popleft()
        cand = litq[0][0] + 1 + j
        if cand < dp[j]:
            dp[j] = cand; ch[j] = ('lit', litq[0][1], 0)
        if litp_v < INF:
            cand = litp_v + 3 + j
            if cand < dp[j]:
                dp[j] = cand; ch[j] = ('lit', litp_i, 0)
        i0 = j - 32
        if i0 >= 0 and dp[i0] < INF:
            v = dp[i0] - i0
            if v < litp_v:
                litp_v = v; litp_i = i0

        # ---- zero runs ----
        if zrun and zmin_v < INF:
            cand = zmin_v + 1
            if cand < dp[j]:
                dp[j] = cand; ch[j] = ('zero', zmin_i, 0)
            if zrun > 31:
                cand = zmin_v + 3
                if cand < dp[j]:
                    dp[j] = cand; ch[j] = ('zero', zmin_i, 0)

        # ---- byte fills ----
        if frun and fmin_v < INF:
            cand = fmin_v + 2
            if cand < dp[j]:
                dp[j] = cand; ch[j] = ('fill', fmin_i, 0)
            if frun > 31:
                cand = fmin_v + 4
                if cand < dp[j]:
                    dp[j] = cand; ch[j] = ('fill', fmin_i, 0)

        # ---- (byte, 0) pairs ----
        if plen[q] and pmin_v[q] < INF:
            # 2 * cost = pmin_v + j, cost = 1 + pairs
            cand = (pmin_v[q] + j) // 2 + 1
            if cand < dp[j]:
                dp[j] = cand; ch[j] = ('pair', pmin_i[q], 0)
            if plen[q] > 31:
                cand = (pmin_v[q] + j) // 2 + 3
                if cand < dp[j]:
                    dp[j] = cand; ch[j] = ('pair', pmin_i[q], 0)

        # ---- copies starting here ----
        if j < n and dp[j] < INF:
            base = dp[j]
            m8 = ml_u8[j]
            if m8:
                for L in _COPY_CUTS:
                    if L > m8:
                        break
                    t = j + L
                    if t <= n and base + 2 < dp[t]:
                        dp[t] = base + 2; ch[t] = ('c8', j, L)
                L = m8 if m8 <= 31 else 31
                t = j + L
                if t <= n and base + 2 < dp[t]:
                    dp[t] = base + 2; ch[t] = ('c8', j, L)
                if m8 > 31 and j + m8 <= n and base + 4 < dp[j + m8]:
                    dp[j + m8] = base + 4; ch[j + m8] = ('c8l', j, m8)
            m16 = ml_any[j]
            if m16:
                for L in _COPY_CUTS:
                    if L > m16:
                        break
                    t = j + L
                    if t <= n and base + 3 < dp[t]:
                        dp[t] = base + 3; ch[t] = ('c16', j, L)
                L = m16 if m16 <= 31 else 31
                t = j + L
                if t <= n and base + 3 < dp[t]:
                    dp[t] = base + 3; ch[t] = ('c16', j, L)
                if m16 > 31 and j + m16 <= n and base + 5 < dp[j + m16]:
                    dp[j + m16] = base + 5; ch[j + m16] = ('c16l', j, m16)

    # ---- walk the choices back and emit ----
    parts = []
    j = n
    while j > 0:
        c = ch[j]
        if c is None:
            raise ValueError('no parse for %d' % j)
        kind, i, L = c
        if kind == 'lit':
            parts.append(('lit', data[i:j]))
        elif kind == 'zero':
            parts.append(('zero', j - i))
        elif kind == 'fill':
            parts.append(('fill', (data[i], j - i)))
        elif kind == 'pair':
            parts.append(('pair', bytes(data[i + 2 * k] for k in range((j - i) // 2))))
        elif kind == 'c8':
            parts.append(('copy', (d_u8[i], L, i)))
        elif kind == 'c8l':
            parts.append(('copy', (d_u8[i], L, i)))
        elif kind == 'c16':
            parts.append(('copy', (d_any[i], L, i)))
        elif kind == 'c16l':
            parts.append(('copy', (d_any[i], L, i)))
        else:
            raise ValueError('bad choice %r' % (c,))
        j = i
    parts.reverse()
    return parts, dp[n]


def _emit_parts(parts):
    out = bytearray()
    for kind, arg in parts:
        if kind == 'lit':
            _emit_literals(out, arg)
        elif kind == 'zero':
            _emit_zero_run(out, arg)
        elif kind == 'fill':
            _emit_fill_run(out, arg[0], arg[1])
        elif kind == 'pair':
            _emit_pairs(out, arg, len(arg))
        else:
            _emit_copy(out, arg[0], arg[1])
    return bytes(out)
