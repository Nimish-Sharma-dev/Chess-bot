# ♞ PyChess

A full-featured chess engine written in Python, playable in the browser or as a desktop app.

Built on a **bitboard architecture** — all 64 squares are packed into native 64-bit integers and manipulated with bitwise operations for fast move generation, evaluation, and search.

---

## Features

- **Bitboard Engine**: 64-bit integer representation for piece positions, ray attacks, and legal move generation.
- **Alpha-Beta Search**: Minimax with alpha-beta pruning, iterative deepening, transposition tables, and quiescence search.
- **Position Evaluation**: Piece values, tapered piece-square tables (opening vs endgame), pawn structure, mobility, and king safety.
- **Three Engine Strengths**:
  - **Casual**: Fast, randomized play for beginners (depth 4, noise).
  - **Club**: Solid, tactical moves (depth 4, low noise).
  - **Master**: Full evaluation depth up to 64 plies with deep search.
- **Polished Web UI**:
  - Modern dark-mode landing screen with difficulty and colour selection.
  - Interactive board with drag-and-drop or click-to-move.
  - Live evaluation bar and algebraic move list (SAN).
  - Captured pieces trays with material advantage counter.
  - Interactive controls: **New Game**, **Undo**, **Flip Board**, **Resign**, **Sound Toggle**, **Copy PGN**, and **Main Menu**.
  - WebAudio procedural sound effects (move, capture, check, victory, defeat) and victory confetti.
- **Desktop UI (Pygame)**:
  - Hardware-accelerated desktop window at 60 FPS with animated piece movement.
  - Procedural sound synthesis with numpy.
  - Functional panel buttons (Undo, Flip, Menu, New Game) and full keyboard shortcuts (`U` undo, `F` flip, `M` mute).
- **Full Chess Rules**: Castling (kingside and queenside), en passant, pawn promotion dialog, check/checkmate, stalemate, 50-move rule, threefold repetition, and insufficient material detection.

---

## Quick Start

### 🌐 Web Version (Browser)

The web version requires **zero external dependencies** — it uses Python's standard library:

```bash
python webapp.py
```

Then open [http://localhost:8765](http://localhost:8765) in your web browser.

You can customize the listening port using the `PORT` environment variable:

```bash
PORT=3000 python webapp.py
```

### 💻 Desktop Version (Pygame)

Install the dependencies:

```bash
pip install -r requirements.txt
```

Launch the game:

```bash
python game.py
```

---

## Deployment Guide

PyChess is production-ready and can be deployed with zero hassle to various platforms:

### 1. Render / Railway / Heroku

The repository includes a `Procfile` configured for Python web hosting:

1. Push your repository to GitHub.
2. In [Render](https://render.com) or [Railway](https://railway.app), create a new **Web Service** linked to your repo.
3. Build command: `pip install -r requirements.txt` (or leave empty as webapp uses standard library).
4. Start command: `python webapp.py`
5. The app automatically binds to `0.0.0.0` and respects the `PORT` environment variable assigned by the host.

### 2. Docker

A `Dockerfile` is included:

```bash
# Build the container
docker build -t pychess .

# Run the container on port 8765
docker run -p 8765:8765 pychess
```

Then open `http://localhost:8765`.

---

## Running Engine Tests

Verify bitboard operations, move generation perft tests, and search benchmarks:

```bash
python test_engine.py
```

---

## Project Structure

| File | Description |
|---|---|
| `bitboards.py` | 64-bit integer masks, attack generation, bitwise utilities |
| `board.py` | Board representation, move make/undo, FEN, Zobrist hashing, SAN notation |
| `evaluate.py` | Evaluation function: material, piece-square tables, mobility |
| `search.py` | Alpha-beta pruning search, quiescence, iterative deepening |
| `game.py` | Desktop UI powered by Pygame |
| `webapp.py` | Threaded HTTP server and game session API |
| `webapp.html` | Responsive single-page web application with landing screen and WebAudio SFX |
| `sounds.py` | Procedural sound generation for Pygame |
| `test_engine.py` | Perft and search validation suite |
| `Procfile` | Production deployment command |
| `Dockerfile` | Container build specification |
| `requirements.txt` | Python package dependencies |

---

## License

MIT License. Free for educational and personal use.
