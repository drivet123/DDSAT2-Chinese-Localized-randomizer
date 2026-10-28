# Dialogue shuffling for DDS2.
#
# Message text lives in "MSG1" tables (see msgtbl.py).  Files are grouped:
#
#   cg      /event/**/*.PM1     movie / cutscene subtitles   (a loose file)
#   ingame  /fld/**/*.bf        field dialogue               (see the note below)
#           /event/**/*.bf      event dialogue
#   system  /battle/MSG.TBL, /facility/msg/**, *.bmd   never touched
#
# Entry layout (verified byte by byte):
#
#   +0     char[16] name
#   +16    u8[8]    reserved
#   +24    u16      line count
#   +26    u16      0xffff
#   +28    u32      voice[line count]   file-relative offsets
#   +28+4n u32      text length
#   +32+4n u8[]     text (custom 2-byte glyph encoding)
#
# What is swapped, and what is not
# --------------------------------
# Only the text payload moves.  The name, the reserved bytes, the line count,
# the `length` field and the whole `u32 voice[lines]` array at +28 are left
# exactly as they were.
#
# Every rule below was forced by in-game testing:
#
#  * The length field must not change.  Rebuilding an entry with a different
#    length made every CG subtitle disappear and the game freeze (v22/v24).
#  * The +28 array must not move either.  When it moved, the game showed the
#    entry's ORIGINAL line again (v24: 1330 of 1366 swaps were invisible; v23
#    bound the voice on only 630 and those were the ones that looked unchanged).
#  * Therefore only entries of IDENTICAL byte length can trade text.  Pairing
#    inside one file was wrong too: it randomised a cutscene against itself and
#    looked like nothing had happened (v25).  Pairing is now global - all files
#    of a category are pooled, so a line can come from any other cutscene.
#    On the retail data that still matches 1348/1374 CG entries (98.1%).
#  * Padding a short line with the blank glyph made subtitles flash and vanish
#    (v25), which is another reason to only pair equal lengths.
#
# Field dialogue is stored compressed in the BF member of fNNN_000.LB and is
# re-compressed back into it - see `_field_containers` and lb.replace_packed
# (docs/METHOD_ENEMY_SWAP.md section 11.11 / 11.12).
#
# Field messages are NOT shuffled as whole texts.  A field message is a stream
# of two byte glyphs and control codes, and the codes point at things that only
# exist in their own scene; moving a whole text into another scene crashed the
# game (v27, TLB Miss).  Instead the message keeps every control code where it
# is and the PURE TEXT runs between them are pooled globally and swapped:
#
#     f2 08 ff ff f2 07 07 ff | glyphs | 0a f1 04 00
#                              ^^^^^^ this part is randomised
#
# Only runs of identical byte length trade, so no message changes size, and a
# run is always an even number of bytes (whole glyphs), so a swap can never
# split a glyph.  That covers 99.7% of all field text.
import re
import struct

from . import lb
from . import lbcmp
from . import lbcmp_enc
from . import msgtbl

CG_EXT = '.pm1'
INGAME_EXT = '.bf'
INGAME_DIRS = ('/fld/', '/event/')
#: match chain used when re-compressing a field script.  32 already keeps every
#: retail BF member below its original slot, and it is the fastest setting.
FIELD_CHAIN = 32

SYSTEM_NAME_RE = re.compile(
    r'(TAKARA|ITEM|DOOR|WARP|SWITCH|KAKUHEKI|COMP_|START_EN|HELP|SHOP|QUIZ|'
    r'BATTLE|SHOOT|MONITOR|SYSTEM|ERROR|DEBUG|KYHK|CAMERA|YESNO|HOSEKI|EXIT|'
    r'SLOT|HINT|TUTORIAL|_SEL|_MENU|_SE$|_SE_|_GET|GET_)', re.I)


class Slot(object):
    __slots__ = ('path', 'entry', 'lines', 'length', 'text_off', 'text', 'name',
                 'length_off', 'cap', 'block', 'idx')

    def __init__(self, path, entry, lines, length, text_off):
        self.path = path
        self.entry = entry
        self.name = entry.name
        self.lines = lines
        self.length = length
        self.text_off = text_off
        self.length_off = entry.slot_offset + 28 + 4 * lines
        #: how many text bytes fit before the next entry starts
        self.cap = entry.slot_offset + entry.size - text_off
        #: MSG1 block this entry belongs to, and its index in that block's table
        self.block = entry.block
        self.idx = 0
        self.text = b''


#: A field message is a sequence of two byte glyphs and control runs.  A glyph
#: starts with a Shift-JIS style lead byte; everything else - `0a` (line break),
#: `00`, and the `f0`..`ff` codes - is a control byte.  Verified against the
#: retail data: across 1797 field messages this split leaves only 58 distinct
#: control runs, and every run either starts with `f0`..`ff` or is a line break.
GLYPH_LEAD = frozenset(list(range(0x80, 0xA0)) + list(range(0xE0, 0xF0)))

#: The blank glyph.  It is by far the most common isolated fragment (105 of
#: them, plus 88 runs of two) and it sits right after the `0a f1 04` page code,
#: i.e. it is a space / page separator rather than text.  Randomising it emptied
#: lines, so it is treated as a control byte: it stays where it is and never
#: enters the pool.
BLANK_GLYPH = b'\x80\x80'

#: The widest dialogue line the retail game ever shows, in glyph cells.  The box
#: does not wrap - the script authors insert the `0a` breaks by hand - so a
#: variable length swap must not push a line past what the retail data already
#: proves the box can hold.  Measured over the 1908 field messages (6249 `0a`
#: lines, 64 files): maximum 33 cells, 99.5% are 24 or fewer, 97.9% are 20 or
#: fewer.  Only 4 lines in the whole game reach 33.
FIELD_MAX_LINE = 33


#: A plain-text run shorter than this is not text.  A code's OPERANDS land in the
#: lead-byte range: `f208ffff f413 05 ff e6 08 f207` is a code whose operand pair
#: `e6 08` looks exactly like a glyph, so the mask folded it into a run and the
#: swap rewrote the control block to `f208ffff f413 05 ff 81 ff f207`.  That is
#: what broke cutscenes in game: the corrupt entry stopped displaying (and its
#: voice, keyed to the subtitle, stopped too), and everything after it in the
#: cutscene went with it.  Every run shorter than this is therefore pinned.
MIN_SWAP_RUN = 4


def field_mask(text):
    """1 for a byte that must not move (control code, blank, or a short run)."""
    n = len(text)
    mask = bytearray(n)
    i = 0
    while i < n:
        b = text[i]
        if b in GLYPH_LEAD and i + 1 < n:
            if text[i:i + 2] == BLANK_GLYPH:
                mask[i] = 1
                mask[i + 1] = 1
            i += 2                       # always advance by whole glyphs
        else:
            mask[i] = 1
            i += 1
    i = 0
    while i < n:
        if mask[i]:
            i += 1
            continue
        j = i
        while j < n and not mask[j]:
            j += 1
        if j - i < MIN_SWAP_RUN:
            for k in range(i, j):
                mask[k] = 1
        i = j
    return bytes(mask)


def field_fragments(text):
    """(offset, length) of every plain text run - the unit that is pooled."""
    mask = field_mask(text)
    out = []
    n = len(mask)
    i = 0
    while i < n:
        if mask[i]:
            i += 1
            continue
        j = i
        while j < n and not mask[j]:
            j += 1
        out.append((i, j - i))
        i = j
    return out


def field_line_widths(text):
    """Visible glyph cells of every `0a` delimited line of a field message.

    A blank glyph counts as one cell and an inline control code as none, so the
    result is what the dialogue box actually has to lay out.  The retail maximum
    is `FIELD_MAX_LINE` (see the constant); the self test asserts that a variable
    length shuffle never exceeds it.
    """
    widths = [0]
    i = 0
    n = len(text)
    while i < n:
        b = text[i]
        if b in GLYPH_LEAD and i + 1 < n:
            widths[-1] += 1
            i += 2
        elif b == 0x0A:
            widths.append(0)
            i += 1
        else:
            i += 1
    return widths


def _equal_length_text(slot, by_len, rng):
    """`slot.text` with every text run replaced by an equal length donor.

    Used as the fallback for a block that has no room for a variable length
    layout: the message keeps its byte length, so the block's original layout
    still fits by construction.
    """
    mask = field_mask(slot.text)
    out = bytearray()
    i = 0
    n = len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out += slot.text[i:j]
        else:
            j = i
            while j < n and not mask[j]:
                j += 1
            old = slot.text[i:j]
            pool = by_len.get(len(old))
            cand = old
            if pool:
                for _ in range(8):
                    c = pool[rng.randrange(len(pool))]
                    if c != old:
                        cand = c
                        break
                else:
                    cand = pool[rng.randrange(len(pool))]
            out += cand
        i = j
    return bytes(out)


def _msg_section_limit(blob, block_pos):
    """End of the FLW0 `.data.msg` section that holds the MSG1 block, or None.

    A block's declared `block_size` can overrun the section that follows it (in
    `/event/e800/e802/sca/e802.bf` the block claims 0x47C while `.data.str`
    starts at 0x47A, and `e803.bf` claims 0x4CC against 0x4CA), so a rebuild must
    be able to stop at the section's real end.  Verified against the retail
    files: 27 blocks overrun, all of them by a handful of padding bytes.
    """
    from . import aicalc
    flw = blob.find(b'FLW0')
    if flw < 0 or block_pos < flw:
        return None
    secs = aicalc._sections(blob, flw)
    if not secs:
        return None
    for sid, usize, ucount, start in secs:
        if sid != aicalc.FLW0_SECTION_MSG:
            continue
        lo = flw + start
        hi = min(lo + usize * ucount, len(blob))
        if lo <= block_pos < hi:
            return hi
    return None


def _line_cells(text):
    """(offset, length, cells used by the REST of the line, line index) per run.

    A field message is split into lines by the `0a` control byte, and a line is
    a sequence of glyph cells and inline control codes.  `_shuffle_field_varlen`
    uses the third value to keep a swapped line inside `FIELD_MAX_LINE` cells.
    """
    mask = field_mask(text)
    n = len(text)
    runs = []                       # (offset, end, line index)
    width = [0]
    line = 0
    i = 0
    while i < n:
        if not mask[i]:
            j = i
            while j < n and not mask[j]:
                j += 1
            runs.append((i, j, line))
            width[line] += (j - i) // 2
            i = j
            continue
        if text[i] in GLYPH_LEAD and i + 1 < n:
            width[line] += 1        # a blank glyph still takes a cell
            i += 2
            continue
        if text[i] == 0x0A:
            line += 1
            width.append(0)
        i += 1
    return [(off, end - off, width[ln] - (end - off) // 2, ln)
            for off, end, ln in runs]


def _shuffle_field_varlen(slots, frags, data, original, rng):
    """Swap field text runs for runs of ANY length, one MSG1 block at a time.

    A field message is control codes and plain glyph runs (see the class
    comment).  Only the runs are randomised; the codes stay where they are.  A
    run may be replaced by one of a different length, which changes the
    message's size - so the whole MSG1 block is laid out again with
    `msgtbl.rebuild_block`, which keeps the block's size (and therefore the
    `.bf`, and therefore the compressed `.LB` member) exactly as it was.

    Each block has a fixed budget of text bytes, so the runs are only allowed to
    grow while that budget lasts; blocks that still do not fit are left alone.
    A run is also never allowed to push its LINE past `FIELD_MAX_LINE` cells.
    """
    from . import msgtbl
    import bisect
    # pool of every plain text run in the whole game, sorted by length
    order = sorted(range(len(frags)), key=lambda i: len(frags[i][3]))
    lens = [len(frags[i][3]) for i in order]
    by_len = {}
    for f in frags:
        by_len.setdefault(len(f[3]), []).append(f[3])
    capped = [0]                    # candidates refused by the line width cap

    def pick(old, path, budget, rest_cells):
        # a plain run is whole glyphs, so `len` bytes are `len // 2` cells
        room = 2 * (FIELD_MAX_LINE - rest_cells)
        allow = len(old) + (budget[0] if budget[0] > 0 else 0)
        if room < allow:
            allow = room
            capped[0] += 1
        k = bisect.bisect_right(lens, allow)
        if k <= 0:
            return None
        best = None
        for _ in range(24):
            i = order[rng.randrange(k)]
            cand = frags[i][3]
            if cand == old:
                continue
            if frags[i][0] != path:
                return cand                 # another scene is the good outcome
            best = cand
        return best

    groups = {}
    for s in slots:
        groups.setdefault((s.path, s.block), []).append(s)

    swapped = 0
    changed_msg = 0
    rebuilt = 0
    skipped = 0
    for (path, bpos), group in groups.items():
        blob = original[path]
        limit = _msg_section_limit(blob, bpos)
        bounds = msgtbl.block_bounds(blob, bpos, limit)
        if bounds is None:
            continue
        first, block_end = bounds
        avail = (block_end - first) - sum(32 + 4 * s.lines for s in group)
        budget = [avail - sum(s.length for s in group)]
        new_texts = {}
        block_swaps = 0
        block_changed = 0
        for s in group:
            runs = _line_cells(s.text)
            grow = {}               # cells this line has already gained
            out = bytearray()
            pos = 0
            for off, ln, rest, li in runs:
                out += s.text[pos:off]
                old = s.text[off:off + ln]
                cand = pick(old, path, budget, rest + grow.get(li, 0))
                if cand is None:
                    out += old
                else:
                    grow[li] = grow.get(li, 0) + (len(cand) - len(old)) // 2
                    budget[0] -= len(cand) - len(old)
                    out += cand
                    block_swaps += 1
                pos = off + ln
            out += s.text[pos:]
            new = bytes(out)
            if new != s.text:
                new_texts[s.idx] = new
                if len(new) != len(s.text):
                    block_changed += 1
        if not new_texts:
            continue
        if msgtbl.rebuild_block(data[path], bpos, new_texts, limit=limit) is None:
            # the block has no room for the new layout: fall back to the
            # equal-length swap for the same messages, so the block is not
            # simply given up on
            skipped += 1
            fixed = {}
            for s in group:
                if s.idx not in new_texts:
                    continue
                nt = _equal_length_text(s, by_len, rng)
                if nt is not None and nt != s.text:
                    fixed[s.idx] = nt
            if fixed and msgtbl.rebuild_block(data[path], bpos, fixed, limit=limit) is not None:
                rebuilt += 1
                swapped += len(fixed)
                changed_msg += sum(1 for s in group
                                   if s.idx in fixed and len(fixed[s.idx]) != len(s.text))
                continue
            continue
        rebuilt += 1
        swapped += block_swaps
        changed_msg += block_changed
    return {'text_ingame_lines_capped': capped[0],
            'text_ingame_swapped': swapped,
            'text_ingame_messages': len(slots),
            'text_ingame_fragments': len(frags),
            'text_ingame_varlen': changed_msg,
            'text_ingame_blocks_rebuilt': rebuilt,
            'text_ingame_blocks_fallback': skipped}


def _round_robin(order, path_of):
    """Reorder so neighbours come from different files as far as possible.

    Pooling the whole game is what makes a line feel like it came from another
    scene, so a partner in the same file is the least interesting outcome.
    """
    groups = {}
    for i in order:
        groups.setdefault(path_of(i), []).append(i)
    lists = list(groups.values())
    out = []
    while lists:
        nxt = []
        for g in lists:
            out.append(g.pop(0))
            if g:
                nxt.append(g)
        lists = nxt
    return out


def classify(path):
    low = path.lower().replace('\\', '/')
    if low.endswith(CG_EXT):
        return 'cg'
    if low.endswith(INGAME_EXT) and low.startswith(INGAME_DIRS):
        return 'ingame'
    return None


def supports(path):
    return classify(path) is not None


def _encode(payload, want_len=None):
    """Re-compress a field script, grown back to the length the slot expects.

    The stream must not use zero length tokens: the retail encoder never emits
    them, so the game's decoder is not known to survive a count of zero.
    Padding with single literal tokens instead keeps the decoded bytes identical.
    """
    if want_len is None:
        return lbcmp_enc.compress_best(payload, max_chain=FIELD_CHAIN)
    return lbcmp_enc.compress_sized(payload, want_len, max_chain=FIELD_CHAIN)


def _field_containers(game):
    """Map every loose `.bf` to the `.LB` member the game actually reads.

    A container only qualifies when its `BF` member decompresses byte for byte
    to the loose file, so the two are known to be the same script.
    """
    out = {}
    for e in game.ddt.files():
        low = e.path.lower()
        if not low.endswith('.lb') or not low.startswith('/fld/'):
            continue
        d, name = e.path.rsplit('/', 1)
        base = name.rsplit('.', 1)[0].split('_')[0]
        loose = d + '/' + base + '.bf'
        try:
            blob = game.read(e.path)
            plain = game.read(loose)
        except Exception:
            continue
        c = lb.find_chunk(blob, 'BF')
        if c is None or c.unpacked != len(plain):
            continue
        try:
            if lbcmp.decode(blob[c.data_offset:c.data_end], c.unpacked) != plain:
                continue
        except lbcmp.LbCodecError:
            continue
        out[loose] = (e.path, 'BF')
    return out


def _cutscene_id(path):
    """`/event/e600/e601/e601_001/E601_001.PM1` -> `E601_001`."""
    return path.rsplit('/', 1)[-1].rsplit('.', 1)[0].upper()


def shuffle_game(p, rng, log=None, field_text=None, varlen=None,
                 cg_mode=None):
    """Shuffle CG subtitles and in-game dialogue text in place.

    `field_text` controls whether the shuffled field scripts are re-compressed
    back into their `.LB` containers (default: whatever `p.config.field_text`
    says, or True).  `varlen` allows FIELD dialogue runs of different byte
    lengths to trade places (default: whatever `p.config.field_varlen` says, or
    False); CG subtitles always keep their byte length.  `cg_mode` is 'global'
    (any cutscene may trade text with any other), 'file' (only inside one
    cutscene) or 'off' (leave CG subtitles alone) - default
    `p.config.text_cg_mode`.
    """
    if field_text is None:
        field_text = getattr(getattr(p, 'config', None), 'field_text', True)
    if varlen is None:
        varlen = getattr(getattr(p, 'config', None), 'field_varlen', False)
    if cg_mode is None:
        cg_mode = getattr(getattr(p, 'config', None), 'text_cg_mode', 'global')
    stats = {}
    data = {}
    original = {}
    buckets = {'cg': [], 'ingame': []}
    for e in p.game.ddt.files():
        cat = classify(e.path)
        if not cat:
            continue
        blob = p.game.read(e.path)
        entries = msgtbl.parse(blob, e.path)
        if not entries:
            continue
        if cat == 'ingame':
            entries = [x for x in entries if not SYSTEM_NAME_RE.search(x.name)]
            if not entries:
                continue
        buf = bytearray(blob)
        data[e.path] = buf
        original[e.path] = blob
        for x in entries:
            lines = struct.unpack_from('<H', buf, x.slot_offset + 24)[0]
            if lines > 32 or x.slot_offset + 32 + 4 * lines > len(buf):
                continue
            length = struct.unpack_from('<I', buf, x.slot_offset + 28 + 4 * lines)[0]
            text_off = x.slot_offset + 32 + 4 * lines
            if length <= 0 or text_off + length > x.slot_offset + x.size:
                continue
            s = Slot(e.path, x, lines, length, text_off)
            s.idx = x.index
            s.text = bytes(buf[text_off:text_off + length])
            buckets[cat].append(s)

    total = 0
    for cat in ('cg', 'ingame'):
        slots = buckets[cat]
        if not slots:
            continue

        # Pool the whole category: a line may come from any other file, which is
        # what makes the result feel randomised instead of just reordered inside
        # one cutscene.  For field messages only the PLAIN TEXT is pooled - the
        # pairing key includes the control skeleton, so both messages put their
        # glyphs in the same slots and each keeps its own control codes.  Moving
        # a control code into another scene points it at things that scene does
        # not have, which crashes the game.
        # CG: the whole text is one unit, so entries trade complete subtitles.
        # Field: the control codes stay where they are and only the PLAIN TEXT
        # fragments between them are pooled - see the class comment below.
        if cat == 'ingame':
            frags = []
            for s in slots:
                for off, ln in field_fragments(s.text):
                    frags.append((s.path, s.text_off + off, ln,
                                  s.text[off:off + ln]))
            if varlen:
                st = _shuffle_field_varlen(slots, frags, data, original, rng)
                stats.update(st)
                total += st['text_ingame_swapped']
                if log:
                    log('ingame: %d fragment(s) swapped with a DIFFERENT length '
                        'in %d rebuilt block(s), %d message(s) changed length '
                        '(%d block(s) had no room and fell back to equal length)'
                        % (st['text_ingame_swapped'],
                           st['text_ingame_blocks_rebuilt'],
                           st['text_ingame_varlen'],
                           st['text_ingame_blocks_fallback']))
                continue
            by_len = {}
            for i, f in enumerate(frags):
                by_len.setdefault(f[2], []).append(i)
            assign = [-1] * len(frags)
            cross = 0
            for length, idxs in by_len.items():
                order = list(idxs)
                rng.shuffle(order)
                order = _round_robin(order, lambda i: frags[i][0])
                for a, b in zip(order[0::2], order[1::2]):
                    assign[a] = b
                    assign[b] = a
                    if frags[a][0] != frags[b][0]:
                        cross += 1
            swapped = 0
            for i, j in enumerate(assign):
                if j < 0 or i == j:
                    continue
                path, off, ln, _old = frags[i]
                new = frags[j][3]
                if new == frags[i][3]:
                    continue
                data[path][off:off + ln] = new
                swapped += 1
            stats['text_ingame_swapped'] = swapped
            stats['text_ingame_messages'] = len(slots)
            stats['text_ingame_fragments'] = len(frags)
            stats['text_ingame_pairs'] = sum(1 for a in assign if a >= 0) // 2
            stats['text_ingame_cross_file_pairs'] = cross
            total += swapped
            if log:
                log('ingame: %d/%d text fragments swapped in %d pairs '
                    '(%d of them across files)'
                    % (swapped, len(frags), stats['text_ingame_pairs'], cross))
            continue

        # CG subtitles only ever trade EQUAL length texts.  Letting a different
        # length through (padding the slot and updating `length`) was tried and
        # fails in game: the subtitles then appear at the wrong time and vanish
        # quickly, and some cutscenes lose their subtitles altogether - the
        # display window clearly depends on the entry's original size.  See
        # docs/METHOD_ENEMY_SWAP.md section 11.13.
        # `cg_mode` says how far a CG text run may travel.  See
        # Config.text_cg_mode.
        if cg_mode == 'off':
            stats['text_cg_swapped'] = 0
            stats['text_cg_messages'] = len(slots)
            stats['text_cg_pairs'] = 0
            stats['text_cg_cross_file_pairs'] = 0
            stats['text_cg_mode'] = cg_mode
            if log:
                log('cg: left untouched (text_cg_mode=off, %d entries)' % len(slots))
            continue


        # ONLY the plain glyph runs are traded; every control code stays exactly
        # where it was - the same rule the field path follows, and for the same
        # reason.  A CG text is cutscene specific:
        #     e601_001  f208ffff f207   . 0a f214 XX 01 00
        #     e603_001  f208ffff f41305ffdb08 f207 . 0a f215 00 00 00
        # 334 distinct leading codes across 93 files, and 36 files do not even
        # share one between their own entries.  Swapping a WHOLE text (the old
        # behaviour) carried those codes into a cutscene that does not know them,
        # and in game the subtitles then appeared late, held for the wrong time
        # or vanished - with equal byte lengths too, so it was never a length
        # problem.  Pinning the codes also keeps each entry's line structure, so
        # the display shape is identical to retail and only the words change.
        frags = []
        for s in slots:
            for off, ln in field_fragments(s.text):
                frags.append((s.path, s.text_off + off, ln,
                              s.text[off:off + ln]))
        by_len = {}
        for i, f in enumerate(frags):
            key = f[2] if cg_mode == 'global' else (f[0], f[2])
            by_len.setdefault(key, []).append(i)

        assign = [-1] * len(frags)
        cross = 0
        for _key, idxs in by_len.items():
            order = list(idxs)
            rng.shuffle(order)
            if cg_mode == 'global':
                # prefer a partner from another cutscene: a line from the same
                # movie is the least interesting outcome
                order = _round_robin(order, lambda i: frags[i][0])
            for a, b in zip(order[0::2], order[1::2]):
                assign[a] = b
                assign[b] = a
                if frags[a][0] != frags[b][0]:
                    cross += 1

        swapped = 0
        for i, j in enumerate(assign):
            if j < 0 or i == j:
                continue
            path, off, ln, old = frags[i]
            new = frags[j][3]
            if new == old or len(new) != ln:      # cannot happen, but be safe
                continue
            data[path][off:off + ln] = new
            swapped += 1

        stats['text_%s_swapped' % cat] = swapped
        stats['text_%s_messages' % cat] = len(slots)
        stats['text_%s_fragments' % cat] = len(frags)
        stats['text_%s_pairs' % cat] = sum(1 for a in assign if a >= 0) // 2
        stats['text_%s_cross_file_pairs' % cat] = cross
        stats['text_%s_mode' % cat] = cg_mode
        total += swapped
        if log:
            log('%s: %d/%d text run(s) swapped in %d pairs (%d across cutscenes); '
                'control codes stay put'
                % (cat, swapped, len(frags), stats['text_%s_pairs' % cat], cross))

    # The game reads the compressed BF member, not the loose .bf.  Resolve the
    # mapping BEFORE any file is written: it checks each container's member
    # against the loose script, which would no longer match afterwards.  The
    # member keeps its slot, so the .LB keeps its size and nothing else moves.
    mapping = _field_containers(p.game) if field_text else {}

    for path, buf in data.items():
        p.set_file(path, bytes(buf))
    stats['text_messages_swapped'] = total

    if field_text:
        rewritten = 0
        skipped = []
        for path, buf in data.items():
            if not path.lower().endswith('.bf'):
                continue
            target = mapping.get(path)
            if target is None:
                continue
            new = bytes(buf)
            if new == original.get(path):
                continue
            lb_path, member = target
            try:
                blob = p.game.read(lb_path)
                out = lb.replace_packed(blob, member, new, _encode)
            except Exception as exc:
                skipped.append('%s: %s' % (path, exc))
                continue
            if out == blob:
                continue
            p.set_file(lb_path, out)
            rewritten += 1
        stats['text_field_containers'] = rewritten
        stats['text_field_skipped'] = skipped
        if log and rewritten:
            log('field dialogue: %d container(s) rewritten' % rewritten)
        for s in skipped:
            if log:
                log('field dialogue skipped: %s' % s)
    return stats
