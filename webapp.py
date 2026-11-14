## WEB FRONTEND
##
## Serves a browser-playable chess UI backed by the same bitboard engine the
## pygame app uses -- stdlib only, no frameworks. Each browser session gets
## its own Board + Search instance.
##
## Run:  python webapp.py          ->  http://127.0.0.1:8765

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from board import Board, move_from, move_to, move_promo
from evaluate import evaluate as static_eval
from search import Search, MATE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "127.0.0.1", 8765

DIFFICULTIES = {
    "casual": dict(noise=130, time=0.8, depth=4),
    "club": dict(noise=25, time=1.5, depth=4),
    "master": dict(noise=0.0, time=2.5, depth=64),
}

LOCK = threading.Lock()
SESSIONS = {}


class Session:
    def __init__(self, player, difficulty):
        self.board = Board()
        self.search = Search()
        self.player = player          # signed: +1 white, -1 black
        self.diff = DIFFICULTIES.get(difficulty, DIFFICULTIES["club"])
        self.san = []
        self.captured = {1: [], -1: []}
        self.last = None
        self.engine = None            # (white-pov score, depth)

    def state(self):
        board = self.board
        result = board.result()
        pieces = [board._code_at(sq) for sq in range(64)]
        legal = None
        if result is None and board.side == (0 if self.player == 1 else 1):
            legal = [[move_from(m), move_to(m), move_promo(m)] for m in board.gen_legal()]
        check_sq = None
        if board.in_check(board.side):
            check_sq = board.king_square(board.side)
        return {
            "pieces": pieces,
            "side": board.side,
            "player": self.player,
            "legal": legal,
            "last": list(self.last) if self.last else None,
            "check": check_sq,
            "san": self.san,
            "eval": static_eval(board),
            "engine": list(self.engine) if self.engine else None,
            "captured": {str(k): v for k, v in self.captured.items()},
            "result": ({"reason": result[0], "winner": result[1]} if result else None),
        }


def colour_index(player_signed):
    return 0 if player_signed == 1 else 1


def apply_engine_move(sess):
    """Search and play the engine's reply. Returns nothing; updates sess."""
    board = sess.board
    move, score, depth, _ = sess.search.find_best_move(
        board.copy(), max_depth=sess.diff["depth"],
        time_limit=sess.diff["time"],
        noise=sess.diff["noise"],
        rng=None)  # deterministic per session is fine; noise gives variety
    if move is None:
        return
    san = board.san(move)
    frm, to = move_from(move), move_to(move)
    board.make(move)
    sess.san.append(san)
    sess.last = (frm, to)
    white_pov = score if sess.player == -1 else -score
    sess.engine = (white_pov, depth)


def player_move(sess, frm, to, promo):
    board = sess.board
    legal = board.gen_legal()
    matches = [m for m in legal
               if move_from(m) == frm and move_to(m) == to
               and (promo == 0 or move_promo(m) == promo)]
    if not matches:
        return False
    m = matches[0]

    mover = 1 if board.side == 0 else -1
    if (m >> 16) & 7 == 2:  # FLAG_EP
        cap_sq = to + 8 if mover == 1 else to - 8
    else:
        cap_sq = to
    cap_code = board._code_at(cap_sq)

    san = board.san(m)
    board.make(m)
    sess.san.append(san)
    sess.last = (frm, to)
    sess.engine = None
    if cap_code:
        sess.captured[mover].append(cap_code)

    if board.result() is None and board.side == colour_index(-sess.player):
        apply_engine_move(sess)
    return True


def undo(sess):
    board = sess.board
    if not board.history:
        return
    while board.history:
        board.undo()
        sess.san.pop() if sess.san else None
        if board.side == colour_index(sess.player):
            break
    ## SAN entries correspond to plies; we popped one per undo above
    sess.last = None
    sess.engine = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet console
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _session(self, token):
        return SESSIONS.get(token or "")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            with open(os.path.join(BASE_DIR, "webapp.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif url.path.startswith("/img/"):
            fname = os.path.basename(url.path)
            path = os.path.join(BASE_DIR, fname)
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, {"error": "not found"})
        elif url.path == "/api/state":
            qs = parse_qs(url.query)
            sess = self._session((qs.get("token") or [""])[0])
            if not sess:
                self._send(404, {"error": "no session"})
                return
            with LOCK:
                self._send(200, sess.state())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "bad json"})
            return

        url = urlparse(self.path)
        token = data.get("token") or ""
        with LOCK:
            if url.path == "/api/new":
                if len(SESSIONS) > 64:  # keep memory bounded
                    SESSIONS.pop(next(iter(SESSIONS)))
                player = 1 if data.get("colour", 1) == 1 else -1
                SESSIONS[token] = Session(player, data.get("difficulty", "club"))
                self._send(200, SESSIONS[token].state())
                return

            sess = self._session(token)
            if not sess:
                self._send(404, {"error": "no session"})
                return

            if url.path == "/api/move":
                try:
                    ok = player_move(sess, int(data["from"]), int(data["to"]),
                                     int(data.get("promo") or 0))
                except (KeyError, ValueError):
                    ok = False
                self._send(200, {"ok": ok, "state": sess.state()})
            elif url.path == "/api/undo":
                undo(sess)
                self._send(200, {"ok": True, "state": sess.state()})
            else:
                self._send(404, {"error": "not found"})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"PyChess web running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
