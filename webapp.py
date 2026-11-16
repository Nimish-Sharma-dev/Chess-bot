## WEB FRONTEND
##
## Serves a browser-playable chess UI backed by the same bitboard engine the
## pygame app uses -- stdlib only, no frameworks. Each browser session gets
## its own Board + Search instance.
##
## Run:  python webapp.py          ->  http://0.0.0.0:8765
##       PORT=3000 python webapp.py

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from board import Board, move_from, move_to, move_promo
from evaluate import evaluate as static_eval
from search import Search, MATE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8765))

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
        self.last = None
        self.engine = None            # (white-pov score, depth)
        self._resigned = False
        self._resign_winner = None

    def get_captured(self):
        """Reconstruct captures accurately from move history."""
        caps = {1: [], -1: []}
        for i, hist in enumerate(self.board.history):
            cap_type = hist[1]
            if cap_type:
                if i % 2 == 0:
                    caps[1].append(-cap_type)  # White moved, captured black piece
                else:
                    caps[-1].append(cap_type)  # Black moved, captured white piece
        return caps

    def state(self):
        board = self.board
        result = board.result()
        if self._resigned and result is None:
            result = ("resignation", self._resign_winner)

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
            "captured": {str(k): v for k, v in self.get_captured().items()},
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
        rng=None)
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

    san = board.san(m)
    board.make(m)
    sess.san.append(san)
    sess.last = (frm, to)
    sess.engine = None

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
    sess._resigned = False
    sess.last = None
    sess.engine = None
    # If the user is playing Black and undo brings the game back to start, let the engine play White's move
    if sess.player == -1 and board.side == 0 and not board.history:
        apply_engine_move(sess)


def resign(sess):
    """Force a resignation result."""
    winner = 1 if sess.player == -1 else 0
    sess._resigned = True
    sess._resign_winner = winner


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _session(self, token):
        return SESSIONS.get(token or "")

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

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
                if len(SESSIONS) > 64:
                    SESSIONS.pop(next(iter(SESSIONS)))
                player = 1 if data.get("colour", 1) == 1 else -1
                sess = Session(player, data.get("difficulty", "club"))
                SESSIONS[token] = sess
                # If player is Black, the engine (White) plays first
                if player == -1:
                    apply_engine_move(sess)
                self._send(200, sess.state())
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
            elif url.path == "/api/resign":
                resign(sess)
                self._send(200, {"ok": True, "state": sess.state()})
            else:
                self._send(404, {"error": "not found"})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"PyChess web running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
