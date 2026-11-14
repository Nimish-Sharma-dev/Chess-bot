## BITBOARD CHESS BOARD
##
## The position is stored as 12 bitboards (one per colour per piece type),
## plus per-colour occupancy, castling rights, an en-passant square, the side
## to move and move counters. Moves are encoded as plain ints:
##
##     bits 0-5   from square
##     bits 6-11  to square
##     bits 12-14 promotion piece type (0 = none)
##     bits 16-18 move flag
##
## Search uses make()/undo() (in-place mutation with an undo stack) instead
## of copying the whole board, and hashes are Zobrist keys maintained
## incrementally so the search can use a transposition table and detect
## repetitions.

import random

from bitboards import (
    WHITE, BLACK, PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING, FULL,
    FILES, RANKS, NOT_FILE_A, NOT_FILE_H, KNIGHT_ATT, KING_ATT, PAWN_ATT,
    DIAG_DIRS, ORTHO_DIRS, slide, popcount, lsb, bits, square_name,
)

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

## Move flags
FLAG_NORMAL, FLAG_DOUBLE, FLAG_EP, FLAG_CASTLE = 0, 1, 2, 3

## Castling right bits: 1 = white O-O, 2 = white O-O-O, 4 = black O-O, 8 = black O-O-O
WK, WQ, BK, BQ = 1, 2, 4, 8

## Moving from or to these squares strips the matching rights.
CASTLE_MASK = [15] * 64
CASTLE_MASK[60] = 15 & ~(WK | WQ)   # e1
CASTLE_MASK[63] = 15 & ~WK          # h1
CASTLE_MASK[56] = 15 & ~WQ          # a1
CASTLE_MASK[4] = 15 & ~(BK | BQ)    # e8
CASTLE_MASK[7] = 15 & ~BK           # h8
CASTLE_MASK[0] = 15 & ~BQ           # a8

PIECE_CHARS = {PAWN: "p", KNIGHT: "n", BISHOP: "b", ROOK: "r", QUEEN: "q", KING: "k"}
CHAR_PIECES = {v: k for k, v in PIECE_CHARS.items()}
PIECE_LETTERS = ".PNBRQK"


def move_make(frm, to, promo=0, flag=0):
    return frm | (to << 6) | (promo << 12) | (flag << 16)


def move_from(m):
    return m & 63


def move_to(m):
    return (m >> 6) & 63


def move_promo(m):
    return (m >> 12) & 7


def move_flag(m):
    return (m >> 16) & 7


def move_uci(m):
    s = square_name(move_from(m)) + square_name(move_to(m))
    if move_promo(m):
        s += PIECE_CHARS[move_promo(m)]
    return s


def _gen_keys():
    rng = random.Random(0x9E3779B97F4A7C15)
    piece = [[[rng.getrandbits(64) for _ in range(64)] for _ in range(7)] for _ in range(2)]
    castle = [rng.getrandbits(64) for _ in range(16)]
    ep = [rng.getrandbits(64) for _ in range(8)]
    side = rng.getrandbits(64)
    return piece, castle, ep, side


Z_PIECE, Z_CASTLE, Z_EP, Z_SIDE = _gen_keys()


class Board:
    def __init__(self, fen=START_FEN):
        self.set_fen(fen)

    ## ---------- setup / serialisation ----------

    def set_fen(self, fen):
        parts = fen.split()
        rows = parts[0].split("/")
        assert len(rows) == 8, "bad FEN board"

        self.bb = [[0] * 7 for _ in range(2)]  # bb[colour][type], type 0 unused
        for r, row in enumerate(rows):
            c = 0
            for ch in row:
                if ch.isdigit():
                    c += int(ch)
                else:
                    ptype = CHAR_PIECES[ch.lower()]
                    colour = WHITE if ch.isupper() else BLACK
                    self.bb[colour][ptype] |= 1 << (r * 8 + c)
                    c += 1

        self.occ = [0, 0]
        for colour in (WHITE, BLACK):
            for t in range(1, 7):
                self.occ[colour] |= self.bb[colour][t]

        self.side = WHITE if parts[1] == "w" else BLACK

        self.castling = 0
        if len(parts) > 2 and parts[2] != "-":
            for ch in parts[2]:
                self.castling |= {"K": WK, "Q": WQ, "k": BK, "q": BQ}[ch]

        self.ep_square = -1
        if len(parts) > 3 and parts[3] != "-":
            from bitboards import parse_square
            self.ep_square = parse_square(parts[3])

        self.halfmove = int(parts[4]) if len(parts) > 4 else 0
        self.fullmove = int(parts[5]) if len(parts) > 5 else 1

        self.history = []
        self.hash = self.compute_hash()
        self.hash_stack = [self.hash]
        self.rep_idx = 0

    def fen(self):
        rows = []
        for r in range(8):
            row = ""
            empty = 0
            for c in range(8):
                sq = r * 8 + c
                piece = self._code_at(sq)
                if piece == 0:
                    empty += 1
                else:
                    if empty:
                        row += str(empty)
                        empty = 0
                    ch = PIECE_CHARS[abs(piece)]
                    row += ch.upper() if piece > 0 else ch
            if empty:
                row += str(empty)
            rows.append(row)

        castle = ""
        if self.castling & WK:
            castle += "K"
        if self.castling & WQ:
            castle += "Q"
        if self.castling & BK:
            castle += "k"
        if self.castling & BQ:
            castle += "q"

        ep = square_name(self.ep_square) if self.ep_square >= 0 else "-"
        move = "w" if self.side == WHITE else "b"
        return f"{'/'.join(rows)} {move} {castle or '-'} {ep} {self.halfmove} {self.fullmove}"

    def copy(self):
        b = Board.__new__(Board)
        b.bb = [row[:] for row in self.bb]
        b.occ = self.occ[:]
        b.castling = self.castling
        b.ep_square = self.ep_square
        b.side = self.side
        b.halfmove = self.halfmove
        b.fullmove = self.fullmove
        b.hash = self.hash
        b.hash_stack = self.hash_stack[:]
        b.rep_idx = self.rep_idx
        b.history = []
        return b

    ## ---------- small queries ----------

    def _type_at(self, sq, colour):
        bb = self.bb[colour]
        for t in range(1, 7):
            if (bb[t] >> sq) & 1:
                return t
        return 0

    def _code_at(self, sq):
        """Signed piece code at sq: positive white, negative black, 0 empty.
        Same encoding the old 2D board / piece images use."""
        if (self.occ[WHITE] >> sq) & 1:
            return self._type_at(sq, WHITE)
        if (self.occ[BLACK] >> sq) & 1:
            return -self._type_at(sq, BLACK)
        return 0

    def piece_code(self, row, col):
        return self._code_at(row * 8 + col)

    def king_square(self, colour):
        return lsb(self.bb[colour][KING])

    def has_non_pawn_material(self, colour):
        return bool(self.bb[colour][KNIGHT] | self.bb[colour][BISHOP]
                    | self.bb[colour][ROOK] | self.bb[colour][QUEEN])

    ## ---------- attacks ----------

    def attacked_by(self, colour, sq):
        """Is `sq` attacked by any piece of `colour`?"""
        bb = self.bb[colour]
        if PAWN_ATT[colour ^ 1][sq] & bb[PAWN]:
            return True
        if KNIGHT_ATT[sq] & bb[KNIGHT]:
            return True
        if KING_ATT[sq] & bb[KING]:
            return True
        occ = self.occ[WHITE] | self.occ[BLACK]
        if slide(sq, occ, DIAG_DIRS) & (bb[BISHOP] | bb[QUEEN]):
            return True
        if slide(sq, occ, ORTHO_DIRS) & (bb[ROOK] | bb[QUEEN]):
            return True
        return False

    def in_check(self, colour):
        return self.attacked_by(colour ^ 1, self.king_square(colour))

    ## ---------- move generation ----------

    def gen_pseudo(self, colour=None):
        if colour is None:
            colour = self.side
        them = colour ^ 1
        occ_all = self.occ[WHITE] | self.occ[BLACK]
        empty = ~occ_all & FULL
        them_occ = self.occ[them]
        not_own = ~self.occ[colour] & FULL
        moves = []

        ## Pawns
        pawns = self.bb[colour][PAWN]
        if pawns:
            promo_row = 0 if colour == WHITE else 7
            promos = (QUEEN, ROOK, BISHOP, KNIGHT)
            if colour == WHITE:
                one = (pawns >> 8) & empty
                two = ((one & RANKS[5]) >> 8) & empty
                cap_w = ((pawns & NOT_FILE_A) >> 9) & them_occ
                cap_e = ((pawns & NOT_FILE_H) >> 7) & them_occ
                ep_w = ((pawns & NOT_FILE_A) >> 9) & (1 << self.ep_square if self.ep_square >= 0 else 0)
                ep_e = ((pawns & NOT_FILE_H) >> 7) & (1 << self.ep_square if self.ep_square >= 0 else 0)
            else:
                one = (pawns << 8) & empty
                two = ((one & RANKS[2]) << 8) & empty
                cap_w = ((pawns & NOT_FILE_A) << 7) & them_occ
                cap_e = ((pawns & NOT_FILE_H) << 9) & them_occ
                ep_w = ((pawns & NOT_FILE_A) << 7) & (1 << self.ep_square if self.ep_square >= 0 else 0)
                ep_e = ((pawns & NOT_FILE_H) << 9) & (1 << self.ep_square if self.ep_square >= 0 else 0)

            for to in bits(one):
                if to >> 3 == promo_row:
                    for p in promos:
                        moves.append(move_make(to + 8 if colour == WHITE else to - 8, to, p))
                else:
                    moves.append(move_make(to + 8 if colour == WHITE else to - 8, to))
            for to in bits(two):
                moves.append(move_make(to + 16 if colour == WHITE else to - 16, to, 0, FLAG_DOUBLE))
            for to in bits(cap_w):
                if to >> 3 == promo_row:
                    for p in promos:
                        moves.append(move_make(to + 9 if colour == WHITE else to - 7, to, p))
                else:
                    moves.append(move_make(to + 9 if colour == WHITE else to - 7, to))
            for to in bits(cap_e):
                if to >> 3 == promo_row:
                    for p in promos:
                        moves.append(move_make(to + 7 if colour == WHITE else to - 9, to, p))
                else:
                    moves.append(move_make(to + 7 if colour == WHITE else to - 9, to))
            for to in bits(ep_w):
                moves.append(move_make(to + 9 if colour == WHITE else to - 7, to, 0, FLAG_EP))
            for to in bits(ep_e):
                moves.append(move_make(to + 7 if colour == WHITE else to - 9, to, 0, FLAG_EP))

        ## Knights
        knights = self.bb[colour][KNIGHT]
        while knights:
            frm = lsb(knights)
            knights &= knights - 1
            for to in bits(KNIGHT_ATT[frm] & not_own):
                moves.append(move_make(frm, to))

        ## Sliders
        for ptype, dirs in ((BISHOP, DIAG_DIRS), (ROOK, ORTHO_DIRS), (QUEEN, DIAG_DIRS + ORTHO_DIRS)):
            piece_bb = self.bb[colour][ptype]
            while piece_bb:
                frm = lsb(piece_bb)
                piece_bb &= piece_bb - 1
                for to in bits(slide(frm, occ_all, dirs) & not_own):
                    moves.append(move_make(frm, to))

        ## King
        kfrm = lsb(self.bb[colour][KING])
        for to in bits(KING_ATT[kfrm] & not_own):
            moves.append(move_make(kfrm, to))

        ## Castling. Leaving/landing in check is handled by the generic
        ## legality test; here we only need "not currently in check",
        ## empty squares, the transit square safe, and the rook in place.
        if not self.attacked_by(them, kfrm):
            if colour == WHITE and kfrm == 60:
                if (self.castling & WK and not (occ_all & 0x6000_0000_0000_0000)
                        and (self.bb[WHITE][ROOK] >> 63) & 1
                        and not self.attacked_by(BLACK, 61)):
                    moves.append(move_make(60, 62, 0, FLAG_CASTLE))
                if (self.castling & WQ and not (occ_all & 0x0E00_0000_0000_0000)
                        and (self.bb[WHITE][ROOK] >> 56) & 1
                        and not self.attacked_by(BLACK, 59)):
                    moves.append(move_make(60, 58, 0, FLAG_CASTLE))
            elif colour == BLACK and kfrm == 4:
                if (self.castling & BK and not (occ_all & 0x60)
                        and (self.bb[BLACK][ROOK] >> 7) & 1
                        and not self.attacked_by(WHITE, 5)):
                    moves.append(move_make(4, 6, 0, FLAG_CASTLE))
                if (self.castling & BQ and not (occ_all & 0x0E)
                        and (self.bb[BLACK][ROOK] >> 0) & 1
                        and not self.attacked_by(WHITE, 3)):
                    moves.append(move_make(4, 2, 0, FLAG_CASTLE))

        return moves

    def gen_legal(self, colour=None):
        if colour is None:
            colour = self.side
        them = colour ^ 1
        legal = []
        for m in self.gen_pseudo(colour):
            self.make(m)
            if not self.attacked_by(them, self.king_square(colour)):
                legal.append(m)
            self.undo()
        return legal

    def find_move(self, frm, to, promo=0):
        for m in self.gen_legal():
            if move_from(m) == frm and move_to(m) == to and (promo == 0 or move_promo(m) == promo):
                return m
        return None

    ## ---------- Zobrist hashing ----------

    def _ep_hash(self):
        """EP file key, but only when a pawn of the side to move could
        actually capture on the ep square (keeps hashes FIDE-consistent)."""
        if self.ep_square < 0:
            return 0
        if PAWN_ATT[self.side ^ 1][self.ep_square] & self.bb[self.side][PAWN]:
            return Z_EP[self.ep_square & 7]
        return 0

    def compute_hash(self):
        h = 0
        for colour in (WHITE, BLACK):
            for t in range(1, 7):
                bb = self.bb[colour][t]
                while bb:
                    s = lsb(bb)
                    bb &= bb - 1
                    h ^= Z_PIECE[colour][t][s]
        h ^= Z_CASTLE[self.castling]
        h ^= self._ep_hash()
        if self.side == BLACK:
            h ^= Z_SIDE
        return h

    ## ---------- make / undo ----------

    def make(self, m):
        frm = m & 63
        to = (m >> 6) & 63
        promo = (m >> 12) & 7
        flag = (m >> 16) & 7
        us = self.side
        them = us ^ 1
        ptype = self._type_at(frm, us)

        cap_sq = to
        cap_type = 0
        if flag == FLAG_EP:
            cap_sq = to + 8 if us == WHITE else to - 8
            cap_type = PAWN
        elif (self.occ[them] >> to) & 1:
            cap_type = self._type_at(to, them)

        old = (m, cap_type, cap_sq, self.castling, self.ep_square,
               self.halfmove, self.rep_idx, self.hash, ptype)

        h = self.hash ^ Z_SIDE ^ Z_CASTLE[self.castling]
        old_ep_key = self._ep_hash()
        if old_ep_key:
            h ^= old_ep_key

        if cap_type:
            self.bb[them][cap_type] &= ~(1 << cap_sq)
            self.occ[them] &= ~(1 << cap_sq)
            h ^= Z_PIECE[them][cap_type][cap_sq]

        placed = promo if promo else ptype
        self.bb[us][ptype] &= ~(1 << frm)
        self.bb[us][placed] |= 1 << to
        self.occ[us] = (self.occ[us] & ~(1 << frm)) | (1 << to)
        h ^= Z_PIECE[us][ptype][frm] ^ Z_PIECE[us][placed][to]

        if flag == FLAG_CASTLE:
            if to == 62:
                rf, rt = 63, 61
            elif to == 58:
                rf, rt = 56, 59
            elif to == 6:
                rf, rt = 7, 5
            else:
                rf, rt = 0, 3
            self.bb[us][ROOK] = (self.bb[us][ROOK] & ~(1 << rf)) | (1 << rt)
            self.occ[us] = (self.occ[us] & ~(1 << rf)) | (1 << rt)
            h ^= Z_PIECE[us][ROOK][rf] ^ Z_PIECE[us][ROOK][rt]

        self.castling &= CASTLE_MASK[frm] & CASTLE_MASK[to]
        h ^= Z_CASTLE[self.castling]

        self.halfmove = 0 if (ptype == PAWN or cap_type) else self.halfmove + 1
        if us == BLACK:
            self.fullmove += 1

        ## Flip the side before hashing the new ep square: the ep key is only
        ## included when the side to move (now `them`) can actually capture.
        self.side = them
        if ptype == PAWN and (frm ^ to) == 16:
            self.ep_square = (frm + to) >> 1
        else:
            self.ep_square = -1
        ep_key = self._ep_hash()
        if ep_key:
            h ^= ep_key

        self.hash = h
        self.history.append(old)
        self.hash_stack.append(h)
        if ptype == PAWN or cap_type:
            self.rep_idx = len(self.hash_stack) - 1

    def undo(self):
        (m, cap_type, cap_sq, self.castling, self.ep_square,
         self.halfmove, self.rep_idx, self.hash, ptype) = self.history.pop()
        self.hash_stack.pop()

        frm = m & 63
        to = (m >> 6) & 63
        promo = (m >> 12) & 7
        flag = (m >> 16) & 7
        mover = self.side ^ 1

        placed = promo if promo else ptype
        self.bb[mover][placed] &= ~(1 << to)
        self.bb[mover][ptype] |= 1 << frm
        self.occ[mover] = (self.occ[mover] & ~(1 << to)) | (1 << frm)

        if flag == FLAG_CASTLE:
            if to == 62:
                rf, rt = 63, 61
            elif to == 58:
                rf, rt = 56, 59
            elif to == 6:
                rf, rt = 7, 5
            else:
                rf, rt = 0, 3
            self.bb[mover][ROOK] = (self.bb[mover][ROOK] & ~(1 << rt)) | (1 << rf)
            self.occ[mover] = (self.occ[mover] & ~(1 << rt)) | (1 << rf)

        if cap_type:
            other = mover ^ 1
            self.bb[other][cap_type] |= 1 << cap_sq
            self.occ[other] |= 1 << cap_sq

        if mover == BLACK:
            self.fullmove -= 1
        self.side = mover

    def make_null(self):
        """Pass the turn (used for null-move pruning). The hash stack is not
        touched: a position after a null can never repeat a real one because
        the side to move flipped with identical piece placement, which no
        legal move produces."""
        old_ep = self.ep_square
        h = self.hash ^ Z_SIDE
        key = self._ep_hash()
        if key:
            h ^= key
        self.null_state = (old_ep, self.hash)
        self.ep_square = -1
        self.side ^= 1
        self.hash = h

    def undo_null(self):
        self.ep_square, self.hash = self.null_state
        self.side ^= 1

    ## ---------- draw / result detection ----------

    def is_repetition(self, times=3):
        """Position occurred `times` times since the last irreversible move."""
        window = self.hash_stack[self.rep_idx:]
        return window.count(self.hash) >= times

    def insufficient_material(self):
        for colour in (WHITE, BLACK):
            if self.bb[colour][PAWN] | self.bb[colour][ROOK] | self.bb[colour][QUEEN]:
                return False
        minors_w = popcount(self.bb[WHITE][KNIGHT]) + popcount(self.bb[WHITE][BISHOP])
        minors_b = popcount(self.bb[BLACK][KNIGHT]) + popcount(self.bb[BLACK][BISHOP])
        if minors_w + minors_b <= 1:
            return True
        if self.bb[WHITE][KNIGHT] or self.bb[BLACK][KNIGHT]:
            return False
        ## Bishops only: draw if every bishop sits on the same square colour
        shade = -1
        for colour in (WHITE, BLACK):
            bb = self.bb[colour][BISHOP]
            while bb:
                sq = lsb(bb)
                bb &= bb - 1
                s = ((sq >> 3) + (sq & 7)) & 1
                if shade == -1:
                    shade = s
                elif shade != s:
                    return False
        return True

    def result(self):
        """None while the game runs, else (reason, winner_colour_or_None)."""
        if not self.gen_legal():
            if self.in_check(self.side):
                return ("checkmate", self.side ^ 1)
            return ("stalemate", None)
        if self.halfmove >= 100:
            return ("fifty", None)
        if self.is_repetition(3):
            return ("repetition", None)
        if self.insufficient_material():
            return ("material", None)
        return None

    ## ---------- notation ----------

    def san(self, m, legal=None):
        if legal is None:
            legal = self.gen_legal()
        frm, to, promo, flag = move_from(m), move_to(m), move_promo(m), move_flag(m)
        colour = self.side
        ptype = self._type_at(frm, colour)
        is_cap = flag == FLAG_EP or (self.occ[colour ^ 1] >> to) & 1

        if flag == FLAG_CASTLE:
            base = "O-O" if to in (62, 6) else "O-O-O"
        elif ptype == PAWN:
            base = ("abcdefgh"[frm & 7] + "x" if is_cap else "") + square_name(to)
            if promo:
                base += "=" + PIECE_LETTERS[promo]
        else:
            ## Disambiguate against identical pieces reaching the same square
            rivals = [move_from(x) for x in legal
                      if move_to(x) == to and move_from(x) != frm
                      and self._type_at(move_from(x), colour) == ptype]
            dis = ""
            if rivals:
                same_file = any((r & 7) == (frm & 7) for r in rivals)
                same_rank = any((r >> 3) == (frm >> 3) for r in rivals)
                if not same_file:
                    dis = "abcdefgh"[frm & 7]
                elif not same_rank:
                    dis = "87654321"[frm >> 3]
                else:
                    dis = square_name(frm)
            base = PIECE_LETTERS[ptype] + dis + ("x" if is_cap else "") + square_name(to)

        self.make(m)
        if self.in_check(self.side):
            base += "#" if not self.gen_legal() else "+"
        self.undo()
        return base

    ## ---------- debugging ----------

    def __str__(self):
        lines = []
        for r in range(8):
            row = f"{8 - r} "
            for c in range(8):
                code = self._code_at(r * 8 + c)
                if code == 0:
                    row += "."
                else:
                    ch = PIECE_CHARS[abs(code)]
                    row += ch.upper() if code > 0 else ch
            lines.append(row)
        lines.append("  abcdefgh")
        return "\n".join(lines)
