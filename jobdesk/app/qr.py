"""A QR code, without adding a dependency to install.

The dashboard's address is a private IP, a port and a 43-character token, and
that is a string nobody should be asked to retype on a phone keyboard. A QR
code is the whole answer to that, and every library that draws one is a fine
library -- this file exists because the install story for this project is four
packages and a `pip install`, and "it also needs a QR encoder" is a worse trade
than three hundred lines that never change again. The format was frozen in 2000
and this only ever encodes short ASCII URLs.

Deliberately narrow. Byte mode only, versions 1 through 10, no ECI header, no
structured append, no kanji. That covers 271 bytes at the lowest error
correction, and the longest string this program will ever hand it is about
seventy. Anything longer raises rather than quietly picking a version this file
has no tables for.

The parts worth knowing when reading it:

  * **Codewords, not bytes.** The data is split into blocks, each block gets its
    own Reed-Solomon check codewords, and the blocks are then *interleaved* so
    that a thumb over one corner damages a little of every block rather than all
    of one. That interleave is the step that looks wrong until you know why.
  * **The mask is chosen, not fixed.** Eight patterns are XORed over the data
    region in turn and scored by four penalty rules from the spec; the lowest
    score wins. It is the only part of encoding that is a judgement rather than
    arithmetic, and it exists so a scanner never meets a code that looks like a
    finder pattern where there is not one.
  * **Format bits are written twice**, in two places, because the corner that
    carries them is the corner most likely to be obscured.

Text is encoded as UTF-8 with no ECI header, which is what phone scanners
assume in practice; anything outside ASCII round-trips but is not what this was
built for.

Verified against `segno` module-for-module across every version, error level and
input length this supports; see `tests/test_qr.py`, which carries the outputs it
compared against so the check survives without the library.
"""

from __future__ import annotations

# -- the tables ---------------------------------------------------------------
#
# Everything here is transcribed from the standard rather than derived. It is
# the only part of the file that could be wrong in a way that reads as correct,
# which is exactly why the tests compare whole matrices against another
# implementation rather than spot-checking a capacity.

# Total codewords (data + error correction) in each version.
TOTAL_CODEWORDS = [26, 44, 70, 100, 134, 172, 196, 242, 292, 346]

# (error codewords per block, blocks in group 1, blocks in group 2).
# Group 2 blocks each hold exactly one more data codeword than group 1 blocks.
BLOCKS: dict[str, list[tuple[int, int, int]]] = {
    "L": [(7, 1, 0), (10, 1, 0), (15, 1, 0), (20, 1, 0), (26, 1, 0),
          (18, 2, 0), (20, 2, 0), (24, 2, 0), (30, 2, 0), (18, 2, 2)],
    "M": [(10, 1, 0), (16, 1, 0), (26, 1, 0), (18, 2, 0), (24, 2, 0),
          (16, 4, 0), (18, 4, 0), (22, 2, 2), (22, 3, 2), (26, 4, 1)],
    "Q": [(13, 1, 0), (22, 1, 0), (18, 2, 0), (26, 2, 0), (18, 2, 2),
          (24, 4, 0), (18, 2, 4), (22, 4, 2), (20, 4, 4), (24, 6, 2)],
    "H": [(17, 1, 0), (28, 1, 0), (22, 2, 0), (16, 4, 0), (22, 2, 2),
          (28, 4, 0), (26, 4, 1), (26, 4, 2), (24, 4, 4), (28, 6, 2)],
}

# Row/column centres of the alignment patterns. Their cross product, minus the
# three that would land on a finder pattern.
ALIGNMENT = [[], [6, 18], [6, 22], [6, 26], [6, 30],
             [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]]

# Unused bits after the last codeword, which are left zero.
REMAINDER = [0, 7, 7, 7, 7, 7, 0, 0, 0, 0]

# The two bits that name an error correction level on the symbol. Not the order
# you would guess: M is 0 and L is 1, because the levels were numbered by how
# common they were expected to be rather than by how much they correct.
EC_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}

LEVELS = ("L", "M", "Q", "H")


class TooLong(ValueError):
    """Raised when the input will not fit in a version this module knows."""


# -- GF(256) ------------------------------------------------------------------
#
# Reed-Solomon works in the field of 256 elements built on 0x11D, so multiply
# becomes add-the-logs and the whole encoder is table lookups.

_EXP = [0] * 512
_LOG = [0] * 256


def _build_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_build_tables()


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(n: int) -> list[int]:
    """The polynomial whose roots are the first `n` powers of 2."""
    poly = [1]
    for i in range(n):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= _mul(c, _EXP[i])
        poly = nxt
    return poly


def _remainder(data: list[int], n: int) -> list[int]:
    """The `n` error correction codewords for one block."""
    gen = _generator(n)
    acc = list(data) + [0] * n
    for i in range(len(data)):
        lead = acc[i]
        if lead == 0:
            continue
        for j, g in enumerate(gen):
            acc[i + j] ^= _mul(g, lead)
    return acc[len(data):]


# -- bits in, codewords out ---------------------------------------------------


def _capacity(version: int, ec: str) -> int:
    """Data codewords available at this version and level."""
    per_block, g1, g2 = BLOCKS[ec][version - 1]
    return TOTAL_CODEWORDS[version - 1] - per_block * (g1 + g2)


def _pick_version(length: int, ec: str) -> int:
    for version in range(1, 11):
        # 4 bits of mode, then 8 or 16 bits of length, then the data.
        header = 4 + (8 if version < 10 else 16)
        if _capacity(version, ec) * 8 >= header + length * 8:
            return version
    raise TooLong(f"{length} bytes will not fit in a version 10 {ec} symbol; "
                  f"the most this module encodes at {ec} is "
                  f"{_capacity(10, ec) - 3} bytes")


def _bitstream(data: bytes, version: int, ec: str) -> list[int]:
    count_bits = 8 if version < 10 else 16
    bits: list[int] = [0, 1, 0, 0]                       # byte mode
    for i in range(count_bits - 1, -1, -1):
        bits.append((len(data) >> i) & 1)
    for byte in data:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)

    capacity = _capacity(version, ec) * 8
    bits.extend([0] * min(4, capacity - len(bits)))      # terminator
    bits.extend([0] * (-len(bits) % 8))                  # to a byte boundary

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2)
                 for i in range(0, len(bits), 8)]
    # The two pad codewords alternate for no reason beyond the standard saying
    # so; they are there to give the mask something varied to work against.
    padded = 0
    # The alternation is counted from the first pad, not from the start of the
    # symbol: whether the data happened to end on an odd codeword must not
    # change which pad comes first.
    pad = (0xEC, 0x11)
    while len(codewords) < _capacity(version, ec):
        codewords.append(pad[padded % 2])
        padded += 1
    return codewords


def _interleave(codewords: list[int], version: int, ec: str) -> list[int]:
    per_block, g1, g2 = BLOCKS[ec][version - 1]
    short = _capacity(version, ec) // (g1 + g2)

    blocks: list[list[int]] = []
    at = 0
    for _ in range(g1):
        blocks.append(codewords[at:at + short])
        at += short
    for _ in range(g2):
        blocks.append(codewords[at:at + short + 1])
        at += short + 1

    checks = [_remainder(b, per_block) for b in blocks]

    out: list[int] = []
    for i in range(short + 1):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(per_block):
        for c in checks:
            out.append(c[i])
    return out


# -- the symbol ---------------------------------------------------------------


def _bch(value: int, generator: int, width: int) -> int:
    """The BCH check bits appended to format and version information."""
    rest = value << width
    top = generator.bit_length()
    while rest.bit_length() >= top:
        rest ^= generator << (rest.bit_length() - top)
    return rest


def _format_bits(ec: str, mask: int) -> int:
    value = (EC_BITS[ec] << 3) | mask
    # The XOR is what stops an all-zero format (M, mask 0) from being a valid
    # symbol that reads as blank.
    return ((value << 10) | _bch(value, 0x537, 10)) ^ 0x5412


def _version_bits(version: int) -> int:
    return (version << 12) | _bch(version, 0x1F25, 12)


class _Grid:
    """The modules, plus a record of which ones the data may not be written to."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.px = [[0] * size for _ in range(size)]
        self.fixed = [[False] * size for _ in range(size)]

    def set(self, x: int, y: int, on: int) -> None:
        self.px[y][x] = 1 if on else 0
        self.fixed[y][x] = True


def _finder(g: _Grid, x: int, y: int) -> None:
    for dy in range(-1, 8):
        for dx in range(-1, 8):
            px, py = x + dx, y + dy
            if not (0 <= px < g.size and 0 <= py < g.size):
                continue
            ring = max(abs(dx - 3), abs(dy - 3))
            g.set(px, py, ring != 2 and ring <= 3)


def _skeleton(version: int) -> _Grid:
    size = version * 4 + 17
    g = _Grid(size)

    _finder(g, 0, 0)
    _finder(g, size - 7, 0)
    _finder(g, 0, size - 7)

    centres = ALIGNMENT[version - 1]
    for cy in centres:
        for cx in centres:
            if (cx, cy) in ((6, 6), (6, size - 7), (size - 7, 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    g.set(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    for i in range(8, size - 8):
        g.set(i, 6, i % 2 == 0)
        g.set(6, i, i % 2 == 0)

    # Reserved now, written after the mask is chosen.
    for i in range(9):
        if not g.fixed[8][i]:
            g.set(i, 8, 0)
        if not g.fixed[i][8]:
            g.set(8, i, 0)
    for i in range(8):
        g.set(size - 1 - i, 8, 0)
        g.set(8, size - 1 - i, 0)


    # Reserved, not written: like the format bits, the version block is filled
    # in after a mask has been chosen. See `encode`.
    if version >= 7:
        for i in range(18):
            g.set(i // 3, size - 11 + i % 3, 0)
            g.set(size - 11 + i % 3, i // 3, 0)

    return g


def _write_version(px: list[list[int]], version: int) -> None:
    if version < 7:
        return
    size = len(px)
    bits = _version_bits(version)
    for i in range(18):
        on = (bits >> i) & 1
        px[size - 11 + i % 3][i // 3] = on
        px[i // 3][size - 11 + i % 3] = on


def _place(g: _Grid, stream: list[int]) -> None:
    """Two modules wide, upward then downward, skipping the timing column."""
    size = g.size
    at = 0
    x = size - 1
    up = True
    while x > 0:
        if x == 6:                                  # the vertical timing pattern
            x -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for y in rows:
            for dx in (0, 1):
                cx = x - dx
                if g.fixed[y][cx]:
                    continue
                g.px[y][cx] = stream[at] if at < len(stream) else 0
                at += 1
        x -= 2
        up = not up


def _mask_fn(n: int):
    return (
        lambda x, y: (x + y) % 2 == 0,
        lambda x, y: y % 2 == 0,
        lambda x, y: x % 3 == 0,
        lambda x, y: (x + y) % 3 == 0,
        lambda x, y: (y // 2 + x // 3) % 2 == 0,
        lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
        lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
    )[n]


def _apply(g: _Grid, mask: int) -> list[list[int]]:
    fn = _mask_fn(mask)
    return [[g.px[y][x] ^ (1 if not g.fixed[y][x] and fn(x, y) else 0)
             for x in range(g.size)] for y in range(g.size)]


def _write_format(px: list[list[int]], ec: str, mask: int) -> None:
    size = len(px)
    # Always dark, always in the same place, and the only module in the symbol
    # that carries no information at all. It belongs to the format block rather
    # than the skeleton, and that is not a filing decision: the mask is scored
    # before any of this is written, so leaving it dark during scoring would put
    # one stray module into all eight comparisons.
    px[size - 8][8] = 1

    bits = _format_bits(ec, mask)
    for i in range(15):
        on = (bits >> i) & 1
        # First copy, wrapped around the top-left finder: up its right-hand
        # side, over the corner, then left along the row beneath it.
        if i < 6:
            px[i][8] = on
        elif i == 6:
            px[7][8] = on
        elif i == 7:
            px[8][8] = on
        elif i == 8:
            px[8][7] = on
        else:
            px[8][14 - i] = on
        # Second copy, split between the other two corners.
        if i < 8:
            px[8][size - 1 - i] = on
        else:
            px[size - 15 + i][8] = on


def _penalty(px: list[list[int]]) -> int:
    size = len(px)
    score = 0
    lines = [list(row) for row in px] + [list(col) for col in zip(*px)]

    # Rule 1: runs of five or more of one colour, in either direction.
    for line in lines:
        run, prev = 1, line[0]
        for value in line[1:]:
            if value == prev:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, prev = 1, value
        if run >= 5:
            score += run - 2

    # Rule 2: every 2x2 block of one colour.
    for y in range(size - 1):
        for x in range(size - 1):
            if px[y][x] == px[y][x + 1] == px[y + 1][x] == px[y + 1][x + 1]:
                score += 3

    # Rule 3: anything that looks like a finder pattern's 1:1:3:1:1 ratio with
    # light beside it. This is the rule the masking step exists for.
    #
    # Two details the spec leaves to the reader, settled here the way the
    # reference implementations settle them: the pattern scores 40 once even
    # when it has light on *both* sides, and a run of light shorter than four
    # still counts if it reaches the edge of the symbol -- the quiet zone
    # outside is light too.
    want = [1, 0, 1, 1, 1, 0, 1]
    for line in lines:
        i = 0
        while i <= size - 7:
            if line[i:i + 7] != want:
                i += 1
                continue
            before = line[max(i - 4, 0):i]
            after = line[i + 7:i + 11]
            if not any(before) or not any(after):
                score += 40
                i += 7
            else:
                # No light either side, so this cannot be the start of a match;
                # the next one can only begin at the middle dark run.
                i += 4

    # Rule 4: how far the whole symbol is from half dark. Kept in integers --
    # the float form of this rounds a symbol sitting exactly on a five percent
    # boundary the other way.
    total = size * size
    dark = sum(sum(row) for row in px)
    score += 10 * (abs(dark * 100 - 50 * total) // (5 * total))
    return score


def encode(text: str, *, ec: str = "M", version: int | None = None,
           mask: int | None = None) -> list[list[int]]:
    """The modules of a QR code for `text`, as rows of 0 and 1, no quiet zone.

    `version` and `mask` exist for the tests, which pin both so that a
    comparison against another encoder is comparing the same symbol rather than
    two equally valid ones.
    """
    if ec not in BLOCKS:
        raise ValueError(f"error correction level must be one of {LEVELS}")
    data = text.encode("utf-8")
    version = version or _pick_version(len(data), ec)
    if not 1 <= version <= 10:
        raise TooLong("this module encodes versions 1 through 10")
    header = 4 + (8 if version < 10 else 16)
    if len(data) * 8 + header > _capacity(version, ec) * 8:
        raise TooLong(f"{len(data)} bytes will not fit in version {version}{ec}")

    codewords = _interleave(_bitstream(data, version, ec), version, ec)
    stream: list[int] = []
    for word in codewords:
        for i in range(7, -1, -1):
            stream.append((word >> i) & 1)
    stream.extend([0] * REMAINDER[version - 1])

    grid = _skeleton(version)
    _place(grid, stream)

    # The candidates are scored *before* the format and version blocks are
    # filled in. That reads like an oversight and is not: those blocks are the
    # same size and roughly the same shape whichever mask wins, so scoring them
    # would add a near-constant to all eight and let a few dozen fixed modules
    # tip a close comparison. The spec is explicit about the order.
    best: tuple[int, int, list[list[int]]] | None = None
    for candidate in ([mask] if mask is not None else range(8)):
        px = _apply(grid, candidate)
        cost = _penalty(px)
        if best is None or cost < best[0]:
            best = (cost, candidate, px)

    _, chosen, px = best
    _write_format(px, ec, chosen)
    _write_version(px, version)
    return px


# -- drawing it ---------------------------------------------------------------


def svg(text: str, *, ec: str = "M", quiet: int = 4) -> str:
    """One `<svg>` element, sized in modules, for the page to scale as it likes.

    Drawn as a single `<path>` of runs rather than a rect per module. A version
    4 symbol is thirteen hundred modules, and thirteen hundred DOM nodes is a
    visible pause on a phone; one path is one node.
    """
    px = encode(text, ec=ec)
    size = len(px) + quiet * 2
    parts = []
    for y, row in enumerate(px):
        x = 0
        while x < len(row):
            if not row[x]:
                x += 1
                continue
            run = 1
            while x + run < len(row) and row[x + run]:
                run += 1
            parts.append(f"M{x + quiet} {y + quiet}h{run}v1h-{run}z")
            x += run
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
            f'shape-rendering="crispEdges" role="img">'
            f'<rect width="{size}" height="{size}" fill="#fff"/>'
            f'<path fill="#000" d="{"".join(parts)}"/></svg>')


def text_art(text: str, *, ec: str = "M", quiet: int = 2) -> str:
    """The same symbol for a terminal, two rows of modules per line of output.

    A character cell is about twice as tall as it is wide, so a code drawn one
    module per cell comes out stretched and, on a smaller window, wrapped and
    unscannable. Half-block characters put two rows in one line, which makes the
    aspect ratio right and halves the height.
    """
    px = encode(text, ec=ec)
    width = len(px) + quiet * 2
    rows = ([[0] * width for _ in range(quiet)]
            + [[0] * quiet + row + [0] * quiet for row in px]
            + [[0] * width for _ in range(quiet)])
    if len(rows) % 2:
        rows.append([0] * width)

    # Dark modules are printed as the *light* half-blocks. A terminal is light
    # text on a dark ground, so drawing dark modules as filled blocks produces a
    # photographic negative, and a scanner needs the finder patterns darker than
    # the quiet zone rather than lighter.
    glyph = {(0, 0): "█", (1, 1): " ", (1, 0): "▄", (0, 1): "▀"}
    out = []
    for i in range(0, len(rows), 2):
        top, bottom = rows[i], rows[i + 1]
        out.append("".join(glyph[(top[x], bottom[x])] for x in range(width)))
    return "\n".join(out)
