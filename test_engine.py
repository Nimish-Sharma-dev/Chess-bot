## ENGINE TEST SUITE
##
## Perft (performance test) counts every leaf node of the legal move tree to
## a fixed depth and compares against published reference values. If all of
## these pass, move generation -- including castling, en passant, promotions,
## pins and checks -- is correct.
##
## Run:  python test_engine.py            (fast suite)
##       python test_engine.py --full     (adds startpos depth 5)

import random
import sys
import time

from board import Board, START_FEN, move_from, move_to, move_uci
from search import Search, MATE

PERFT_SUITE = [
    (START_FEN, [20, 400, 8902, 197281]),
    ## "Kiwipete": castling, pins, checked king, ep -- the classic stress test
    ("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
     [48, 2039, 97862]),
    ("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", [14, 191, 2812, 43238]),
    ("r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
     [6, 264, 9467]),
    ("rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", [44, 1486, 62379]),
    ("r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10",
     [46, 2079, 89890]),
]


def perft(board, depth):
    if depth == 1:
        return len(board.gen_legal())
    total = 0
    for m in board.gen_legal():
        board.make(m)
        total += perft(board, depth - 1)
        board.undo()
    return total


def run_perft(verbose=True):
    ok = True
    for fen, expected in PERFT_SUITE:
        board = Board(fen)
        for depth, want in enumerate(expected, start=1):
            t0 = time.perf_counter()
            got = perft(board, depth)
            dt = time.perf_counter() - t0
            status = "ok" if got == want else "FAIL"
            if got != want:
                ok = False
            if verbose:
                print(f"  [{status}] {fen.split()[0][:20]:22s} d{depth}: "
                      f"{got:>8} (want {want:>8}) {dt:5.2f}s")
    return ok


def run_divide(fen, depth):
    """Debug helper: per-split move counts (divide)."""
    board = Board(fen)
    total = 0
    for m in sorted(board.gen_legal(), key=move_uci):
        board.make(m)
        n = perft(board, depth - 1) if depth > 1 else 1
        board.undo()
        print(f"  {move_uci(m)}: {n}")
        total += n
    print(f"  total: {total}")


def run_unit_tests():
    ok = True

    ## FEN round-trip
    for fen, _ in PERFT_SUITE:
        if Board(fen).fen() != fen:
            print(f"  [FAIL] FEN round-trip: {fen}")
            ok = False
    if Board().fen() != START_FEN:
        print("  [FAIL] FEN round-trip: startpos")
        ok = False

    ## Zobrist incremental hash must equal a full recompute after every move
    rng = random.Random(42)
    board = Board()
    for _ in range(400):
        legal = board.gen_legal()
        if not legal or board.result():
            break
        board.make(rng.choice(legal))
        if board.hash != board.compute_hash():
            print(f"  [FAIL] zobrist drift after {move_uci(legal[0])} on\n{board}")
            ok = False
            break

    ## SAN spot checks
    b = Board()
    m = b.find_move(52, 36)  # e2e4
    assert m and b.san(m) == "e4", "SAN e4"
    b.make(m)
    m = b.find_move(12, 28)  # e7e5
    assert m and b.san(m) == "e5", "SAN e5"
    b.make(m)
    m = b.find_move(62, 45)  # Ng1-f3
    assert m and b.san(m) == "Nf3", "SAN Nf3"

    b = Board("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1")
    castle = b.find_move(60, 62)
    assert castle is not None and b.san(castle) == "O-O", "SAN O-O"

    ## En passant square appears in FEN after a double push
    b = Board()
    b.make(b.find_move(52, 36))
    assert "e3" in b.fen(), "ep square in FEN"

    ## Draw detection: bare kings
    b = Board("8/8/8/8/8/8/8/Kk6 w - - 0 1")
    assert b.insufficient_material(), "insufficient K vs K"

    print(f"  [{'ok' if ok else 'FAIL'}] unit tests")
    return ok


def run_search_tests():
    ok = True
    engine = Search()

    ## Mate in 1: Ra1-a8 is back-rank mate
    b = Board("6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1")
    move, score, depth, _ = engine.find_best_move(b, max_depth=4, time_limit=5.0)
    good = move is not None and move_from(move) == 56 and move_to(move) == 0
    print(f"  [{'ok' if good else 'FAIL'}] mate in 1 found: {move_uci(move) if move else None}"
          f" (score {score})")
    ok &= good

    ## Mate in 2 (Qd5-d8+ Bxd8 Re8#): engine must pick Qd8+
    b = Board("r1b2k1r/ppp1bppp/8/1B1Q4/5q2/2P5/PPP2PPP/R3R1K1 w - - 1 0")
    move, score, depth, _ = engine.find_best_move(b, max_depth=5, time_limit=8.0)
    good = move is not None and move_uci(move) == "d5d8"
    print(f"  [{'ok' if good else 'FAIL'}] mate in 2 found: {move_uci(move) if move else None}"
          f" (score {score}, depth {depth})")
    ok &= good

    ## Startpos: best move must be legal and search should reach a sane depth
    b = Board()
    t0 = time.perf_counter()
    move, score, depth, nodes = engine.find_best_move(b, max_depth=64, time_limit=2.0)
    dt = time.perf_counter() - t0
    nps = nodes / max(dt, 1e-9)
    good = move is not None and depth >= 4
    print(f"  [{'ok' if good else 'FAIL'}] startpos: {move_uci(move)} depth {depth}, "
          f"{nodes} nodes in {dt:.2f}s ({nps/1000:.0f}k nps)")
    ok &= good

    return ok


def main():
    full = "--full" in sys.argv
    print("unit tests:")
    ok = run_unit_tests()

    print("perft:")
    ok &= run_perft()

    if full:
        board = Board()
        t0 = time.perf_counter()
        got = perft(board, 5)
        print(f"  [{'ok' if got == 4865609 else 'FAIL'}] startpos d5: {got} "
              f"({time.perf_counter()-t0:.1f}s)")
        ok &= got == 4865609

    print("search:")
    ok &= run_search_tests()

    print("ALL TESTS PASSED" if ok else "SOME TESTS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
