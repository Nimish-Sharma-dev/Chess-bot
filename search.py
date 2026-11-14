## SEARCH ENGINE
##
## Iterative-deepening negamax with:
##   * alpha-beta pruning and principal-variation search (PVS)
##   * transposition table keyed by incremental Zobrist hash
##   * quiescence search (captures/promotions, and full evasions in check)
##     so the engine never stops thinking in the middle of a trade
##   * null-move pruning, late-move reductions and check extensions
##   * killer moves + history heuristic + MVV-LVA move ordering
##   * repetition and fifty-move draw detection inside the tree
##
## A Search instance keeps its transposition table and history between moves,
## so thinking gets cheaper as the game goes on. On timeout the engine
## returns the best move from the last completed depth; the search runs on a
## throwaway copy of the board, so an aborted search never corrupts the game.

import time

from board import (
    Board, FLAG_EP, PAWN, QUEEN, move_from, move_to, move_promo, move_flag,
)
from evaluate import evaluate as evaluate_board

MATE = 30000
INF = MATE + 200
MAX_PLY = 128

TT_EXACT, TT_LOWER, TT_UPPER = 0, 1, 2

VAL = (0, 100, 320, 330, 500, 900, 10000)


class SearchTimeout(Exception):
    pass


class Search:
    def __init__(self):
        self.tt = {}
        self.eval_cache = {}
        self.history_heur = {}
        self.board = None
        self.nodes = 0
        self.deadline = 0.0
        self.killers = [[0, 0] for _ in range(MAX_PLY + 2)]

    def _evaluate(self):
        """Static eval memoised by position hash -- quiescence re-evaluates
        the same positions constantly, so this pays for itself many times."""
        board = self.board
        cached = self.eval_cache.get(board.hash)
        if cached is None:
            cached = evaluate_board(board)
            if len(self.eval_cache) > 2_000_000:
                self.eval_cache.clear()
            self.eval_cache[board.hash] = cached
        return cached

    ## ---------- public API ----------

    def find_best_move(self, board, max_depth=64, time_limit=2.0,
                       noise=0.0, rng=None):
        """Returns (move, score_from_mover_pov, completed_depth, nodes).

        noise > 0 plays weaker-but-varied: the root is searched with full
        windows to a shallow depth and the choice is perturbed by Gaussian
        noise, giving a natural casual strength instead of rigid play."""
        self.board = board
        self.deadline = time.time() + max(time_limit, 0.05)
        self.nodes = 0
        self.killers = [[0, 0] for _ in range(MAX_PLY + 2)]

        root = board.gen_legal()
        if not root:
            return None, 0, 0, 0
        if len(root) == 1:
            return root[0], 0, 0, 0

        exact_root = noise > 0.0
        if exact_root:
            ## Full-window root search keeps every score exact for the noise
            ## pick, which costs ~branching-factor more per iteration; cap
            ## the depth so the time budget still holds.
            max_depth = min(max_depth, 4)

        scores = dict.fromkeys(root, -INF)
        best_move = root[0]
        best_score = -INF
        completed = 0

        try:
            for depth in range(1, max_depth + 1):
                alpha = -INF
                beta = INF
                iter_best = None
                for m in sorted(root, key=lambda x: scores[x], reverse=True):
                    board.make(m)
                    if exact_root or iter_best is None:
                        ## Full window keeps every root score exact (needed
                        ## for the noise pick); the search never tightens
                        ## alpha in that mode.
                        score = -self._negamax(depth - 1, -INF, INF, 1)
                    else:
                        score = -self._negamax(depth - 1, -alpha - 1, -alpha, 1)
                        if score > alpha:
                            score = -self._negamax(depth - 1, -beta, -alpha, 1)
                    board.undo()
                    scores[m] = score
                    if iter_best is None or score > alpha:
                        alpha = score
                        iter_best = m
                if exact_root and rng is not None:
                    noisy = {m: scores[m] + rng.gauss(0, noise) for m in root}
                    iter_best = max(root, key=noisy.get)
                    alpha = scores[iter_best]
                best_move = iter_best
                best_score = alpha
                completed = depth
        except SearchTimeout:
            pass

        return best_move, best_score, completed, self.nodes

    ## ---------- internals ----------

    def _check_time(self):
        if self.nodes % 2048 == 0 and time.time() > self.deadline:
            raise SearchTimeout

    def _negamax(self, depth, alpha, beta, ply, can_null=True):
        self.nodes += 1
        self._check_time()
        board = self.board

        if board.halfmove >= 100 or (ply > 0 and board.is_repetition(2)):
            return 0

        in_chk = board.in_check(board.side)
        if in_chk:
            depth += 1  # check extension

        if depth <= 0 or ply >= MAX_PLY:
            return self._qsearch(alpha, beta, ply)

        ## Transposition table
        key = board.hash
        tte = self.tt.get(key)
        tt_move = 0
        if tte is not None:
            tt_depth, tt_score, tt_flag, tt_move = tte
            if tt_depth >= depth and ply > 0:
                s = tt_score
                if s > MATE - 1000:
                    s -= ply
                elif s < -MATE + 1000:
                    s += ply
                if tt_flag == TT_EXACT:
                    return s
                if tt_flag == TT_LOWER and s >= beta:
                    return s
                if tt_flag == TT_UPPER and s <= alpha:
                    return s

        ## Null-move pruning: give the opponent a free move; if our position
        ## still stands off with a reduced search, this node is too good to
        ## need full effort (skipped in check / pawn-only endings: zugzwang).
        if (can_null and not in_chk and depth >= 3
                and beta < MATE - 1000
                and board.has_non_pawn_material(board.side)):
            r = 3 if depth >= 6 else 2
            board.make_null()
            score = -self._negamax(depth - 1 - r, -beta, -beta + 1, ply + 1, False)
            board.undo_null()
            if score >= beta:
                return beta

        moves = board.gen_pseudo(board.side)
        moves = self._order(moves, tt_move, ply)

        best = -INF
        best_move = 0
        legal = 0
        old_alpha = alpha
        them_occ = board.occ[board.side ^ 1]

        for i, m in enumerate(moves):
            to = (m >> 6) & 63
            is_cap = ((m >> 16) & 7) == FLAG_EP or (them_occ >> to) & 1
            is_promo = ((m >> 12) & 7) != 0

            board.make(m)
            if board.attacked_by(board.side, board.king_square(board.side ^ 1)):
                board.undo()
                continue
            legal += 1

            if legal == 1:
                score = -self._negamax(depth - 1, -beta, -alpha, ply + 1)
            else:
                may_reduce = (i >= 4 and depth >= 3 and not in_chk
                              and not is_cap and not is_promo)
                if may_reduce and board.in_check(board.side):
                    may_reduce = False  # don't reduce check-giving moves
                d = depth - 2 if may_reduce else depth - 1
                score = -self._negamax(d, -alpha - 1, -alpha, ply + 1)
                if score > alpha and (may_reduce or score < beta):
                    score = -self._negamax(depth - 1, -beta, -alpha, ply + 1)
            board.undo()

            if score > best:
                best = score
                best_move = m
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        if not is_cap:
                            ks = self.killers[ply]
                            if ks[0] != m:
                                ks[1] = ks[0]
                                ks[0] = m
                            hk = (board.side << 20) | (m & 0xFFFFF)
                            self.history_heur[hk] = self.history_heur.get(hk, 0) + depth * depth
                        break

        if legal == 0:
            return -MATE + ply if in_chk else 0

        if best >= beta:
            flag, stored = TT_LOWER, best
        elif best > old_alpha:
            flag, stored = TT_EXACT, best
        else:
            flag, stored = TT_UPPER, best
        if stored > MATE - 1000:
            stored += ply
        elif stored < -MATE + 1000:
            stored -= ply
        if len(self.tt) > 1_200_000:
            self.tt.clear()
        self.tt[key] = (depth, stored, flag, best_move)
        return best

    def _qsearch(self, alpha, beta, ply):
        self.nodes += 1
        self._check_time()
        board = self.board
        if ply >= MAX_PLY:
            return self._evaluate()

        in_chk = board.in_check(board.side)
        them_occ = board.occ[board.side ^ 1]
        best = -INF

        if not in_chk:
            best = self._evaluate()
            if best >= beta:
                return best
            if best > alpha:
                alpha = best

            ## Captures and queen promotions only, MVV-LVA ordered.
            tactical = []
            for m in board.gen_pseudo(board.side):
                to = (m >> 6) & 63
                promo = (m >> 12) & 7
                if ((m >> 16) & 7) == FLAG_EP:
                    victim = PAWN
                elif (them_occ >> to) & 1:
                    victim = board._type_at(to, board.side ^ 1)
                else:
                    if promo != QUEEN:
                        continue
                    victim = 0
                tactical.append((VAL[victim] * 100 - VAL[board._type_at(m & 63, board.side)], m))
            tactical.sort(reverse=True)

            for _, m in tactical:
                ## Delta pruning: skip captures that cannot raise alpha
                if not ((m >> 12) & 7) and best + 220 <= alpha:
                    continue
                board.make(m)
                if board.attacked_by(board.side, board.king_square(board.side ^ 1)):
                    board.undo()
                    continue
                score = -self._qsearch(-beta, -alpha, ply + 1)
                board.undo()
                if score > best:
                    best = score
                    if score > alpha:
                        alpha = score
                        if score >= beta:
                            return score
            return best

        ## In check we must look at every evasion -- a "quiet" move here may
        ## be the only thing standing between us and mate.
        legal = 0
        for m in board.gen_pseudo(board.side):
            board.make(m)
            if board.attacked_by(board.side, board.king_square(board.side ^ 1)):
                board.undo()
                continue
            legal += 1
            score = -self._qsearch(-beta, -alpha, ply + 1)
            board.undo()
            if score > best:
                best = score
                if score > alpha:
                    alpha = score
                    if score >= beta:
                        return score
        if legal == 0:
            return -MATE + ply
        return best

    def _order(self, moves, tt_move, ply):
        board = self.board
        them = board.side ^ 1
        them_occ = board.occ[them]
        killers = self.killers[ply]
        hist = self.history_heur

        scored = []
        for m in moves:
            if m == tt_move:
                s = 1 << 30
            else:
                to = (m >> 6) & 63
                promo = (m >> 12) & 7
                if ((m >> 16) & 7) == FLAG_EP:
                    victim = PAWN
                elif (them_occ >> to) & 1:
                    victim = board._type_at(to, them)
                else:
                    victim = 0
                if victim:
                    s = (1 << 26) + VAL[victim] * 64 - VAL[board._type_at(m & 63, board.side)]
                elif promo:
                    s = (1 << 25) + promo
                elif m == killers[0]:
                    s = 1 << 24
                elif m == killers[1]:
                    s = (1 << 24) - 1
                else:
                    s = hist.get((board.side << 20) | (m & 0xFFFFF), 0)
            scored.append((s, m))
        scored.sort(reverse=True)
        return [m for _, m in scored]
