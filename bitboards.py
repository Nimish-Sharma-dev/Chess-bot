## BITBOARD FOUNDATION
##
## Squares are indexed 0..63 with square = row * 8 + col, where row 0 is the
## top of the board (black's back rank / rank 8) and col 0 is file a -- the
## same orientation the old 2D array used, so the renderer keeps working.
## A bitboard is a plain Python int; bit i set means a piece sits on square i.

WHITE = 0
BLACK = 1

PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING = 1, 2, 3, 4, 5, 6

FULL = 0xFFFF_FFFF_FFFF_FFFF

## Direction ids (row-step, col-step). "North" is toward row 0 / rank 8.
N, S, E, W, NE, NW, SE, SW = range(8)
_DIR_STEPS = {
    N: (-1, 0), S: (1, 0), E: (0, 1), W: (0, -1),
    NE: (-1, 1), NW: (-1, -1), SE: (1, 1), SW: (1, -1),
}

## Directions with a positive square-index delta. For those, the first
## blocker along a ray is the least significant set bit; otherwise the most.
POS_DIRS = frozenset((S, E, SE, SW))
DIAG_DIRS = (NE, NW, SE, SW)
ORTHO_DIRS = (N, S, E, W)


def _build_tables():
    files = [0] * 8
    ranks = [0] * 8
    for r in range(8):
        for c in range(8):
            files[c] |= 1 << (r * 8 + c)
            ranks[r] |= 1 << (r * 8 + c)

    rays = [[0] * 64 for _ in range(8)]
    for d, (dr, dc) in _DIR_STEPS.items():
        for sq in range(64):
            r, c = divmod(sq, 8)
            mask = 0
            r += dr
            c += dc
            while 0 <= r < 8 and 0 <= c < 8:
                mask |= 1 << (r * 8 + c)
                r += dr
                c += dc
            rays[d][sq] = mask

    def jumps(deltas):
        table = []
        for sq in range(64):
            r, c = divmod(sq, 8)
            mask = 0
            for dr, dc in deltas:
                rr, cc = r + dr, c + dc
                if 0 <= rr < 8 and 0 <= cc < 8:
                    mask |= 1 << (rr * 8 + cc)
            table.append(mask)
        return table

    knight = jumps(((2, 1), (2, -1), (-2, 1), (-2, -1), (1, 2), (1, -2), (-1, 2), (-1, -2)))
    king = jumps(((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)))

    pawn = [[0] * 64, [0] * 64]  # pawn[WHITE][sq] = squares a white pawn ON sq attacks
    for sq in range(64):
        r, c = divmod(sq, 8)
        for dc in (-1, 1):
            cc = c + dc
            if 0 <= cc < 8:
                if r - 1 >= 0:
                    pawn[WHITE][sq] |= 1 << ((r - 1) * 8 + cc)
                if r + 1 < 8:
                    pawn[BLACK][sq] |= 1 << ((r + 1) * 8 + cc)

    ## PASSED_SPAN[color][sq] = every square ahead of sq on its file AND the
    ## two neighbouring files. An enemy pawn anywhere in that span means a
    ## pawn on sq is not passed.
    passed_span = [[0] * 64, [0] * 64]
    for sq in range(64):
        r, c = divmod(sq, 8)
        span = 0
        for cc in (c - 1, c, c + 1):
            if 0 <= cc < 8:
                if r == 0 or r == 7:  # pawn can never stand here; keep span empty
                    continue
                for rr in range(r):  # everything north of sq
                    span |= 1 << (rr * 8 + cc)
        passed_span[WHITE][sq] = span
        span = 0
        for cc in (c - 1, c, c + 1):
            if 0 <= cc < 8:
                if r == 0 or r == 7:
                    continue
                for rr in range(r + 1, 8):  # everything south of sq
                    span |= 1 << (rr * 8 + cc)
        passed_span[BLACK][sq] = span

    ## KING_SHIELD[color][sq] = the 2 ranks directly in front of a king on sq
    ## across three files; used for midgame king-safety scoring.
    shield = [[0] * 64, [0] * 64]
    for sq in range(64):
        r, c = divmod(sq, 8)
        for dr in (1, 2):
            for dc in (-1, 0, 1):
                rr = r - dr
                cc = c + dc
                if 0 <= rr < 8 and 0 <= cc < 8:
                    shield[WHITE][sq] |= 1 << (rr * 8 + cc)
                rr = r + dr
                if 0 <= rr < 8 and 0 <= cc < 8:
                    shield[BLACK][sq] |= 1 << (rr * 8 + cc)

    return files, ranks, rays, knight, king, pawn, passed_span, shield


FILES, RANKS, RAYS, KNIGHT_ATT, KING_ATT, PAWN_ATT, PASSED_SPAN, KING_SHIELD = _build_tables()

NOT_FILE_A = FULL ^ FILES[0]
NOT_FILE_H = FULL ^ FILES[7]


def popcount(x):
    return x.bit_count()


def lsb(bb):
    """Index of the least significant set bit (bb must be nonzero)."""
    return (bb & -bb).bit_length() - 1


def msb(bb):
    """Index of the most significant set bit (bb must be nonzero)."""
    return bb.bit_length() - 1


def bits(bb):
    """Yield the indices of every set bit, ascending."""
    while bb:
        b = bb & -bb
        yield b.bit_length() - 1
        bb ^= b


def slide(sq, occ, dirs):
    """Classical ray attack: squares reachable from sq along dirs, stopping
    after the first blocker (which is itself attacked)."""
    att = 0
    for d in dirs:
        ray = RAYS[d][sq]
        blockers = ray & occ
        if blockers:
            first = lsb(blockers) if d in POS_DIRS else msb(blockers)
            att |= ray ^ RAYS[d][first]
        else:
            att |= ray
    return att


def bishop_attacks(sq, occ):
    return slide(sq, occ, DIAG_DIRS)


def rook_attacks(sq, occ):
    return slide(sq, occ, ORTHO_DIRS)


def queen_attacks(sq, occ):
    return slide(sq, occ, DIAG_DIRS) | slide(sq, occ, ORTHO_DIRS)


def square_name(sq):
    return "abcdefgh"[sq & 7] + "87654321"[sq >> 3]


def parse_square(name):
    return (8 - int(name[1])) * 8 + ("abcdefgh".index(name[0]))
