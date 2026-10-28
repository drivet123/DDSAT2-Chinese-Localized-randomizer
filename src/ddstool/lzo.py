"""LZO1X decompressor (pure python).

Implements the LZO1X-1 stream format, following the instruction layout of the
reference decoder (minilzo.c).  Used here for the compressed glyph data inside
the /font/*.fnt files.
"""
import sys


class LzoError(Exception):
    pass


def decompress(src, dst_len=None, max_out=1 << 27):
    out = bytearray()
    ip = 0
    n = len(src)

    def add_literals(ip, t):
        out.extend(src[ip:ip + t])
        return ip + t

    def copy_match(m_pos, m_len):
        start = len(out) - m_pos
        if start < 0:
            raise LzoError('back-ref before start (pos %d, out %d)' % (m_pos, len(out)))
        for _ in range(m_len):
            out.append(out[start])
            start += 1

    # -- first instruction -------------------------------------------------
    if n < 3:
        raise LzoError('stream too short')
    t = src[ip]
    ip += 1
    if t > 17:
        t -= 17
        if t < 4:
            raise LzoError('invalid leading literal length %d' % t)
        ip = add_literals(ip, t)
        if ip >= n:
            return bytes(out)
        t = src[ip]
        ip += 1
    else:
        # no leading literal: process the first byte as a match token
        ip -= 1

    while True:
        if t < 16:
            # literal run, then a match token
            if t == 0:
                while src[ip] == 0:
                    t += 255
                    ip += 1
                t += 15 + src[ip]
                ip += 1
            ip = add_literals(ip, t + 3)
            t = src[ip]
            ip += 1

        if t >= 64:
            m_pos = (t >> 2) & 7
            m_len = (t >> 5) + 1
            if m_pos == 0:
                if ip + 1 >= n:
                    return bytes(out)
                m_pos = src[ip] | (src[ip + 1] << 8)
                ip += 2
            else:
                m_pos += src[ip] << 8
                ip += 1
            m_pos += 1
        elif t >= 32:
            m_len = t & 31
            if m_len == 0:
                while src[ip] == 0:
                    m_len += 255
                    ip += 1
                m_len += 31 + src[ip]
                ip += 1
            if ip + 1 >= n:
                return bytes(out)
            m_pos = (src[ip] | (src[ip + 1] << 8)) >> 2
            ip += 2
            m_pos += 1
        elif t >= 16:
            m_pos = (t & 8) << 11
            m_len = t & 7
            if m_len == 0:
                while src[ip] == 0:
                    m_len += 255
                    ip += 1
                m_len += 7 + src[ip]
                ip += 1
            m_len += 2
            if ip + 1 >= n:
                return bytes(out)
            m_pos += src[ip] | (src[ip + 1] << 8)
            ip += 2
            if m_pos == 0:
                return bytes(out)          # end of stream marker
            m_pos = (m_pos >> 3) + 1
        else:
            m_pos = ((t & 3) << 8) + src[ip]
            ip += 1
            m_len = (t >> 2) + 2
            m_pos = (m_pos >> 2) + 1
            m_pos += 0x4000

        copy_match(m_pos, m_len)
        if dst_len and len(out) >= dst_len:
            break
        if len(out) > max_out:
            raise LzoError('output grew past limit')
        if ip >= n:
            break
        t = src[ip]
        ip += 1

    return bytes(out)


if __name__ == '__main__':
    print('usage: from ddstool import lzo', file=sys.stderr)
