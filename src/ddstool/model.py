# Character model swap: the protagonist's demon form.
#
# This layer gives Serph's (萨弗) Atma-ON transformation - Varuna (伐楼罗) - the
# "Black Varuna" (黑伐楼罗) look.  It does NOT replace the model file: only the
# texture block inside it is swapped, byte for byte.
#
# ---------------------------------------------------------------------------
# A .PB model is a flat list of chunks
#
#     [u32 type][u32 size][char magic[4]] payload          (size includes the
#                                                          8 byte prefix)
# padded so that every chunk starts on a 0x40 boundary.  The four chunks that
# matter here are `PIB0` (skeleton / node names), `TXP0` (textures), `MD00`
# (mesh) and `MT00` (materials / motions), closed by `END0`.  `TXP0` is itself
# a container: a small table followed by one `TMX0` chunk per texture.
#
# All the Varuna models in the game share that layout exactly:
#
#     file                          PIB0    TXP0      MD00      MT00
#     /model/human/pc011.PB           588  199936   307058    764316
#     /model/human/pc011_on.PB        588  199936   260862    770464
#     /model/devil/12A.PB             204   15616   249752    351558
#     e639 kuro_varuna.PB              36  199936   274128     38772
#     e639 siro_varna.PB               36  199936   286696     13007
#
# `pc011[_on]`, `kuro_varuna` and `siro_varna` have a **byte identical 199936
# byte TXP0 chunk header and table** (3 textures at relative offsets 0x40,
# 0x10480, 0x208C0, all 256x256, format 19, 1024 byte palette).  Swapping the
# whole chunk therefore keeps the container perfectly valid - only the texels
# and the palettes change.
#
# ---------------------------------------------------------------------------
# How the files were identified (see docs/METHOD_ENEMY_SWAP.md section 13):
#
#   * The party's atma model slots are `human/pcNNN[_on].PB`.  The pointer
#     tables in SLPM_657.95 (va 0x3C1BEC for `pcNNN.PB`, va 0x3C2EAC for
#     `pcNNN_on.PB`, entry i -> id i) resolve entry 0x11 to `/model/human/
#     pc011.PB` and `/model/human/pc011_on.PB`, and both name their body node
#     `varna_body` with the model root `x101_root`.  So pc011 is Varuna.
#   * The e639 event package lists its models in a 32 byte name table and
#     embeds one `PIB0` model per name; `kuro_varuna.PB` names its body
#     `varna_body` and its TXP0 palette mean is 160/32/63 (dark), while
#     `siro_varna.PB` names it `varna_body01` and is bright (183/137/112).
#   * `/model/devil/10E.PB` and `/model/devil/10F.PB` are *not* model
#     containers of that shape (their chunk walk breaks after `PIB0`), but each
#     embeds a complete copy of one of those texture blocks: `10E` at file
#     offset 0x3B040 and `10F` at 0x1D340, both 199936 bytes and byte identical
#     to the kuro / siro TXP0 chunk.  They are used as the texture donors.
#
# Replacing the whole file instead was tried first and **hung the game** the
# moment the transformation model was loaded: `10E` is not a valid
# `human/pcNNN_on` model.  Swapping only the TXP0 chunk leaves `PIB0`, `MD00`,
# `MT00`, `END0` and every offset untouched.
import struct

# The two copies of Serph's Varuna model.  `pcNNN.PB` is the plain form and
# `pcNNN_on.PB` the "atma on" variant; the game picks between them, so both are
# rewritten to keep the transformation consistent.
PLAIN = '/model/human/pc011.PB'
ON = '/model/human/pc011_on.PB'
TARGETS = (PLAIN, ON)

# Black Varuna: an event-only model whose texture block survives inside this
# file.  (Its own container is not a valid playable-character model.)
DONOR = '/model/devil/10E.PB'
# White Varuna, same relationship - kept for reference / a future option.
SIRO = '/model/devil/10F.PB'

TEXTURE_MAGIC = b'TXP0'
TEXTURE_TYPE = 9          # the type tag of a TXP0 chunk
TABLE_HEAD = 0x40         # bytes of the chunk that must match before splicing


def _texture_chunk(data, size=None, head=None):
    """(offset, size) of the `TXP0` chunk of `data`.

    `size` and `head` pin down which one is meant: the donor file can be an
    arbitrary container, so the caller passes the geometry of the target chunk
    and only a chunk with the same length and the same 0x40 byte table is
    accepted.
    """
    pos = data.find(TEXTURE_MAGIC)
    while pos != -1:
        start = pos - 8
        if start >= 0:
            typ, sz = struct.unpack_from('<II', data, start)
            if (typ == TEXTURE_TYPE and sz >= TABLE_HEAD
                    and start + sz <= len(data)
                    and (size is None or sz == size)
                    and (head is None
                         or data[start:start + len(head)] == head)):
                return start, sz
        pos = data.find(TEXTURE_MAGIC, pos + 4)
    return None


def available(game):
    """True when the donor and at least one target carry a TXP0 chunk."""
    try:
        donor = game.read(DONOR)
    except Exception:
        return False
    if _texture_chunk(donor) is None:
        return False
    for path in TARGETS:
        try:
            if _texture_chunk(game.read(path)) is not None:
                return True
        except Exception:
            continue
    return False


def plan(game):
    """(path, file_size, txp0_offset, txp0_size, ok) for every target."""
    try:
        donor = game.read(DONOR)
    except Exception:
        return []
    out = []
    for path in TARGETS:
        try:
            data = game.read(path)
        except Exception:
            continue
        loc = _texture_chunk(data)
        if loc is None:
            out.append((path, len(data), 0, 0, False))
            continue
        start, size = loc
        head = data[start:start + TABLE_HEAD]
        ok = _texture_chunk(donor, size, head) is not None
        out.append((path, len(data), start, size, ok))
    return out


def swap(game, patcher, log=None):
    """Give Serph's Varuna model the Black Varuna textures.

    Only the `TXP0` chunk changes; the file keeps its size and every other
    offset, so the DDT index and DDS3.IMG never move.  Returns the statistics
    dictionary that the randomizer merges into its own report.
    """
    def say(msg):
        if log:
            log(msg)

    stats = {'model_donor': DONOR, 'model_files_swapped': 0,
             'model_texture_bytes': 0, 'model_texture_bytes_changed': 0,
             'model_donor_chunk': 0, 'model_targets': []}
    if not available(game):
        say('model: no usable texture chunk in %s or the Varuna slots' % DONOR)
        return stats
    donor = game.read(DONOR)

    for path, size, start, tsize, ok in plan(game):
        if not ok or not tsize:
            say('model: %s has no matching texture chunk - skipped' % path)
            continue
        data = game.read(path)
        head = data[start:start + TABLE_HEAD]
        dstart, _ = _texture_chunk(donor, tsize, head)
        new = (data[:start] + donor[dstart:dstart + tsize]
               + data[start + tsize:])
        if len(new) != len(data):
            say('model: %s would change size - skipped' % path)
            continue
        changed = sum(1 for i in range(start, start + tsize)
                      if data[i] != new[i])
        patcher.set_file(path, new)
        stats['model_files_swapped'] += 1
        stats['model_texture_bytes'] += tsize
        stats['model_texture_bytes_changed'] += changed
        stats['model_donor_chunk'] = dstart
        stats['model_targets'].append(path)
        say('model: %s TXP0 <-> %s@0x%X (%d bytes, %d changed)'
            % (path, DONOR, dstart, tsize, changed))
    if not stats['model_files_swapped']:
        say('model: nothing to do, %s or the Varuna slots are missing' % DONOR)
    return stats


def summary(stats):
    return ('%d model(s) got the Black Varuna textures (%d texture bytes, '
            '%d changed)'
            % (stats.get('model_files_swapped', 0),
               stats.get('model_texture_bytes', 0),
               stats.get('model_texture_bytes_changed', 0)))
