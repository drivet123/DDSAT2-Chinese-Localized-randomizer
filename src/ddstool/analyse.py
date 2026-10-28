# Make bosses and elite enemies analysable.
#
# The Analyse (分析) window builds its rows with the seven functions listed at
# VA 0x415650.  Six of them bail out to the "?" placeholder drawer at
# 0x1B8930 when the game thinks the enemy's data must stay hidden.  Each of
# those functions guards its value path with one to three
#
#     bnel <reg>, zero, <address of the jal 0x1B8930>
#
# instructions ("if the flag is set, draw ?").  Between them there are exactly
# thirteen such branches in the block 0x1B8E00..0x1B9400.
#
# The three flags they test are
#
#   * ENCOUNT.TBL[encounter].u16 @+32 bit 0   (code 0x1B8E94)
#   * the battle unit template's +0 bit 0x800 (0x1B8ECC)
#   * the battle unit template's +22          (0x1B8EDC, a copy of UNIT.TBL
#     record +70 - the unit's category byte)
#
# Clearing those bytes in UNIT.TBL / ENCOUNT.TBL does reveal the data, but both
# bytes are used by the battle logic as well: with the unit category byte
# rewritten from 0xFF to 0 the game **hangs while loading a boss battle**.  So
# this module patches the code instead and leaves the game data alone.
#
# A `bnel A, B, target` branches when A != B and NULLIFIES the delay slot when
# it does not branch.  Replacing the comparison register with $zero gives
#
#     bnel zero, zero, target
#
# which is never taken and still nullifies the delay slot - i.e. exactly the
# "flag not set" path, with the instruction stream otherwise unchanged.  Only
# the `rs` field of those thirteen words changes.
import struct

ELF_PATH = '/SLPM_657.95'

#: the "?" placeholder drawer shared by the analyse rows
PLACEHOLDER = 0x1B8930
#: the analyse row functions and their guarded tails all live in this range
SCAN_LO = 0x1B8E00
SCAN_HI = 0x1B9400
#: opcode of `bnel`
OP_BNEL = 21
#: field mask of the `rs` operand
RS_MASK = 31 << 21


def va2fo(va, elf):
    """ELF file offset of a virtual address (VA = file offset + 0xFF000)."""
    fo = va - 0xFF000
    if not (0 <= fo < len(elf) - 4):
        raise ValueError('0x%X is outside the ELF' % va)
    return fo


def _word(elf, va):
    return struct.unpack_from('<I', elf, va2fo(va, elf))[0]


def _placeholder_calls(elf):
    """File offsets of every `jal 0x1B8930` in the analyse block."""
    out = set()
    for va in range(SCAN_LO, SCAN_HI, 4):
        w = _word(elf, va)
        if (w >> 26) != 3:                       # jal
            continue
        tgt = ((va + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
        if tgt == PLACEHOLDER:
            out.add(va2fo(va, elf))
    return out


def guard_sites(elf):
    """[(file_offset, original_word)] of every guard branch to the "?" path."""
    calls = _placeholder_calls(elf)
    out = []
    for va in range(SCAN_LO, SCAN_HI, 4):
        w = _word(elf, va)
        if (w >> 26) != OP_BNEL:
            continue
        imm = w & 0xFFFF
        simm = imm - 0x10000 if imm >= 0x8000 else imm
        if va2fo(va + 4 + (simm << 2), elf) not in calls:
            continue
        out.append((va2fo(va, elf), w))
    return out


# The thirteen sites, verified against the retail SLPM_657.95.  They are
# recomputed on every run anyway; this list is what the self test asserts, so a
# different image fails loudly instead of silently patching nothing.
EXPECTED_SITES = 13


def patch(elf):
    """Return `elf` with the thirteen analyse guards neutralised.

    Every patched word keeps its opcode and branch offset; only the `rs`
    operand becomes `$zero`, which turns `bnel flag,zero,?` into
    `bnel zero,zero,?` (never taken, delay slot nullified).
    """
    buf = bytearray(elf)
    sites = guard_sites(elf)
    for fo, w in sites:
        struct.pack_into('<I', buf, fo, w & ~RS_MASK)
    return bytes(buf)


def verify(elf):
    """(ok, sites) - True when every guard can be neutralised as expected."""
    sites = guard_sites(elf)
    ok = len(sites) == EXPECTED_SITES
    for fo, w in sites:
        rs = (w >> 21) & 31
        rt = (w >> 16) & 31
        if rs == 0 or rt != 0 or (w >> 26) != OP_BNEL:
            ok = False
    return ok, sites


def summary(sites):
    return '%d analyse guard branch(es) neutralised' % len(sites)
