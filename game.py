## CHESS FRONTEND
##
## A pygame UI with a main menu, difficulty levels, animated piece moves,
## sound effects, an evaluation bar, a scrollable move list in algebraic
## notation, captured-piece trays, undo, board flipping and a result card
## with confetti. The engine thinks on a background thread so the UI stays
## at 60 FPS while it searches.
##
## Run:  python game.py

import os
import random
import threading

os.environ["SDL_VIDEO_CENTERED"] = "1"

import pygame

from board import (
    Board, FLAG_CASTLE, FLAG_EP,
    move_from, move_to, move_promo, move_flag, move_uci,
)
from evaluate import evaluate as static_eval
from search import Search, MATE
from sounds import build_sounds

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

## ---------- look & feel ----------

WINDOW_W, WINDOW_H = 1080, 800
PANEL_W = 340
BOARD_SIZE = 640
SQUARE = BOARD_SIZE // 8
BOARD_X = WINDOW_W - BOARD_SIZE - 50
BOARD_Y = 80

BG = "#12151b"
PANEL = "#1b2028"
PANEL_EDGE = "#2a313c"
ACCENT = "#5b9dff"
ACCENT_DIM = "#37517a"
TEXT = "#e8ecf2"
TEXT_DIM = "#8a94a6"
LIGHT_SQ = "#f0d9b5"
DARK_SQ = "#b58863"
LAST_MOVE = (246, 236, 110)
CHECK_RED = (214, 59, 59)
SELECT_GREEN = (32, 143, 90)

PIECE_FILES = {
    1: "w_pawn_png_1024px.png", 2: "w_knight_png_1024px.png",
    3: "w_bishop_png_1024px.png", 4: "w_rook_png_1024px.png",
    5: "w_queen_png_1024px.png", 6: "w_king_png_1024px.png",
    -1: "b_pawn_png_1024px.png", -2: "b_knight_png_1024px.png",
    -3: "b_bishop_png_1024px.png", -4: "b_rook_png_1024px.png",
    -5: "b_queen_png_1024px.png", -6: "b_king_png_1024px.png",
}

PIECE_VALUE = {1: 1, 2: 3, 3: 3, 4: 5, 5: 9, 6: 0}

DIFFICULTIES = (
    ("Casual", dict(noise=130, time=0.8, depth=4)),
    ("Club", dict(noise=25, time=1.5, depth=4)),
    ("Master", dict(noise=0.0, time=2.5, depth=64)),
)

REASONS = {
    "checkmate": "Checkmate",
    "stalemate": "Stalemate",
    "fifty": "Fifty-move rule",
    "repetition": "Threefold repetition",
    "material": "Insufficient material",
}


def colour_index(player_signed):
    """The engine numbers colours 0 = white, 1 = black; the UI uses signed
    players (+1 white, -1 black) so piece codes stay comparable. Convert at
    the boundary."""
    return 0 if player_signed == 1 else 1


def cp_to_fraction(cp):
    """Centipawns (white POV) -> 0..1 share of the eval bar."""
    if cp >= MATE - 1000:
        return 1.0
    if cp <= -MATE + 1000:
        return 0.0
    return 1.0 / (1.0 + 10 ** (-cp / 400))


def fmt_score(cp):
    if cp >= MATE - 1000:
        return "mate"
    if cp <= -MATE + 1000:
        return "-mate"
    return f"{cp / 100:+.2f}"


class Button:
    def __init__(self, rect, label, font, on_click=None, filled=False, small=False):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.font = font
        self.on_click = on_click
        self.filled = filled
        self.hovered = False

    def draw(self, surface):
        mouse = pygame.mouse.get_pos()
        self.hovered = self.rect.collidepoint(mouse)
        if self.filled:
            base = ACCENT if not self.hovered else "#7db1ff"
            pygame.draw.rect(surface, base, self.rect, border_radius=10)
            colour = "#0d1117"
        else:
            base = PANEL_EDGE if not self.hovered else "#3a4453"
            pygame.draw.rect(surface, base, self.rect, border_radius=10)
            pygame.draw.rect(surface, ACCENT if self.hovered else PANEL_EDGE,
                             self.rect, 2, border_radius=10)
            colour = TEXT
        text = self.font.render(self.label, True, colour)
        surface.blit(text, text.get_rect(center=self.rect.center))

    def clicked(self, event):
        return (event.type == pygame.MOUSEBUTTONDOWN and event.button == 1
                and self.rect.collidepoint(event.pos))


class Confetti:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.parts = []

    def burst(self):
        colours = [(240, 84, 110), (255, 195, 69), (86, 204, 137),
                   (91, 157, 255), (196, 128, 255), (255, 240, 200)]
        for _ in range(160):
            self.parts.append({
                "x": random.uniform(0, self.w),
                "y": random.uniform(-self.h * 0.5, 0),
                "vx": random.uniform(-40, 40),
                "vy": random.uniform(80, 240),
                "size": random.uniform(4, 9),
                "angle": random.uniform(0, 6.28),
                "spin": random.uniform(-6, 6),
                "colour": random.choice(colours),
            })

    def update(self, dt):
        for p in self.parts:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vy"] += 60 * dt
            p["angle"] += p["spin"] * dt
        self.parts = [p for p in self.parts if p["y"] < self.h + 20]

    def draw(self, surface):
        for p in self.parts:
            rect = pygame.Rect(0, 0, p["size"], p["size"] * 0.6)
            rect.center = (p["x"], p["y"])
            img = pygame.Surface(rect.size)
            img.fill(p["colour"])
            img = pygame.transform.rotate(img, p["angle"] * 57.3)
            surface.blit(img, rect)


class Game:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
        pygame.display.set_caption("PyChess -- bitboard edition")
        self.clock = pygame.time.Clock()

        def font(size, bold=False):
            try:
                return pygame.font.SysFont("segoeui,arial", size, bold=bold)
            except Exception:
                return pygame.font.Font(None, size + 6)

        self.f_big = font(52, bold=True)
        self.f_mid = font(28, bold=True)
        self.f_ui = font(22)
        self.f_small = font(17)
        self.f_tiny = font(14)

        piece_px = int(SQUARE * 0.92)
        self.piece_images = {}
        tray_px = 26
        self.tray_images = {}
        for code, fname in PIECE_FILES.items():
            img = pygame.image.load(os.path.join(BASE_DIR, fname)).convert_alpha()
            self.piece_images[code] = pygame.transform.smoothscale(img, (piece_px, piece_px))
            self.tray_images[code] = pygame.transform.smoothscale(img, (tray_px, tray_px))

        self.sounds = build_sounds()
        self.muted = False

        self.search = Search()
        self.rng = random.Random()

        self.state = "menu"  # menu | playing | gameover
        self.player_colour = 1
        self.diff_idx = 1

        self._reset_game_state()
        self.confetti = Confetti(WINDOW_W, WINDOW_H)
        self.menu_buttons = self._make_menu_buttons()

    ## ---------- game lifecycle ----------

    def _reset_game_state(self):
        self.board = Board()
        self.selected = None
        self.legal_for_selected = []
        self.last_move = None
        self.san_history = []
        self.eval_cp = 0
        self.engine_info = None
        self.anim = None            # (from_sq, to_sq, piece_code, start_ms)
        self.pending_promo = None   # (frm, to, [moves])
        self.captured = {1: [], -1: []}  # code -> captured pieces shown in that side's tray
        self.move_scroll = 0.0
        self.engine_result = None
        self.engine_thread = None
        self.think_start = None
        self.gameover_info = None   # (reason, winner_colour)

    def start_game(self, player_colour):
        self._reset_game_state()
        self.player_colour = player_colour
        self.state = "playing"

    def back_to_menu(self):
        self.state = "menu"
        self.menu_buttons = self._make_menu_buttons()

    def _make_menu_buttons(self):
        cy = 380
        return {
            "white": Button((WINDOW_W // 2 - 180, cy, 360, 56), "Play as White",
                            self.f_mid, lambda: self.start_game(1), filled=True),
            "black": Button((WINDOW_W // 2 - 180, cy + 72, 360, 56), "Play as Black",
                            self.f_mid, lambda: self.start_game(-1), filled=True),
            "diff": Button((WINDOW_W // 2 - 180, cy + 160, 360, 48),
                           f"Engine: {DIFFICULTIES[self.diff_idx][0]}  (click to change)",
                           self.f_ui, self._cycle_difficulty),
            "sound": Button((WINDOW_W // 2 - 180, cy + 222, 172, 44),
                            self._sound_label(), self.f_ui, self._toggle_sound),
            "quit": Button((WINDOW_W // 2 + 8, cy + 222, 172, 44), "Quit",
                           self.f_ui, self._quit),
        }

    def _cycle_difficulty(self):
        self.diff_idx = (self.diff_idx + 1) % len(DIFFICULTIES)
        self.menu_buttons["diff"].label = (
            f"Engine: {DIFFICULTIES[self.diff_idx][0]}  (click to change)")
        self.play("click")

    def _sound_label(self):
        return f"Sound: {'off' if self.muted else 'on'}  (M)"

    def _toggle_sound(self):
        self.muted = not self.muted
        self.menu_buttons["sound"].label = self._sound_label()

    def _quit(self):
        pygame.event.post(pygame.event.Event(pygame.QUIT))

    def play(self, name):
        if not self.muted and name in self.sounds:
            self.sounds[name].play()

    ## ---------- engine ----------

    @property
    def cpu_colour(self):
        return -self.player_colour

    @property
    def difficulty(self):
        return DIFFICULTIES[self.diff_idx][1]

    def maybe_start_engine(self):
        if (self.state == "playing" and self.engine_thread is None
                and self.board.side == colour_index(self.cpu_colour)):
            self.engine_result = None
            self.think_start = pygame.time.get_ticks()
            work = self.board.copy()
            diff = self.difficulty
            noise = diff["noise"]
            rng = self.rng if noise else None

            def worker():
                try:
                    res = self.search.find_best_move(
                        work, max_depth=diff["depth"],
                        time_limit=diff["time"], noise=noise, rng=rng)
                except Exception:
                    res = (None, 0, 0, 0)
                self.engine_result = res

            self.engine_thread = threading.Thread(target=worker, daemon=True)
            self.engine_thread.start()

    def poll_engine(self):
        if self.engine_thread is not None and not self.engine_thread.is_alive():
            self.engine_thread = None
            res = self.engine_result
            self.engine_result = None
            if (self.state == "playing" and res and res[0] is not None
                    and self.board.side == colour_index(self.cpu_colour)):
                move, score, depth, _nodes = res
                self.apply_move(move, animate=True)
                ## score is from the CPU's point of view
                white_pov = score if self.cpu_colour == 1 else -score
                self.engine_info = (white_pov, depth)

    def undo(self):
        if (self.engine_thread is not None or self.state == "menu"
                or not self.board.history):
            return
        ## Rewind until it is the player's turn again (normally two plies:
        ## the engine's reply and the player's own move).
        steps = 0
        while self.board.history:
            self.board.undo()
            steps += 1
            if self.board.side == colour_index(self.player_colour):
                break
        del self.san_history[max(0, len(self.san_history) - steps):]
        self.selected = None
        self.legal_for_selected = []
        self.last_move = None
        self.pending_promo = None
        self.anim = None
        self.gameover_info = None
        if self.state == "gameover":
            self.state = "playing"
        self._rebuild_captured()
        self.eval_cp = static_eval(self.board)
        self.engine_info = None
        self.play("click")

    def _rebuild_captured(self):
        self.captured = {1: [], -1: []}

    ## ---------- moves ----------

    def apply_move(self, m, animate=True):
        board = self.board
        frm, to, flag = move_from(m), move_to(m), move_flag(m)
        mover = 1 if board.side == 0 else -1  # signed: white +1, black -1

        mover_code = board._code_at(frm)
        if flag == FLAG_EP:
            cap_sq = to + 8 if mover == 1 else to - 8
        else:
            cap_sq = to
        cap_code = board._code_at(cap_sq)

        san = board.san(m)
        board.make(m)
        self.last_move = (frm, to)
        self.san_history.append(san)
        self.selected = None
        self.legal_for_selected = []
        self.engine_info = None

        if cap_code:
            self.captured[mover].append(cap_code)
        if animate:
            self.anim = (frm, to, mover_code, pygame.time.get_ticks())

        if cap_code:
            self.play("capture")
        elif flag == FLAG_CASTLE:
            self.play("castle")
        else:
            self.play("move")
        if board.in_check(board.side):
            self.play("check")

        self.eval_cp = static_eval(board)

        result = board.result()
        if result:
            reason, winner = result
            self.gameover_info = (reason, winner)
            self.state = "gameover"
            if winner is None:
                self.play("draw")
            elif winner == colour_index(self.player_colour):
                self.play("win")
                self.confetti.burst()
            else:
                self.play("lose")

    ## ---------- input ----------

    def view_to_square(self, pos):
        """Window coords -> square index, respecting board orientation."""
        bx, by = pos[0] - BOARD_X, pos[1] - BOARD_Y
        if not (0 <= bx < BOARD_SIZE and 0 <= by < BOARD_SIZE):
            return None
        vr, vc = by // SQUARE, bx // SQUARE
        if self.player_colour == 1:
            return int(vr) * 8 + int(vc)
        return (7 - int(vr)) * 8 + (7 - int(vc))

    def square_to_view(self, sq):
        row, col = divmod(sq, 8)
        if self.player_colour == 1:
            return row, col
        return 7 - row, 7 - col

    def handle_click_board(self, pos):
        sq = self.view_to_square(pos)
        if sq is None:
            return
        board = self.board
        code = board._code_at(sq)

        if self.selected is not None:
            matches = [m for m in self.legal_for_selected if move_to(m) == sq]
            if matches:
                if len(matches) > 1:  # promotion choice
                    self.pending_promo = (self.selected, sq, matches)
                    self.selected = None
                    self.legal_for_selected = []
                    return
                self.apply_move(matches[0])
                return
            if code * self.player_colour > 0:
                if sq != self.selected:
                    self.select_square(sq)
                    return
            self.play("illegal")
            self.selected = None
            self.legal_for_selected = []
            return

        if code * self.player_colour > 0:
            self.select_square(sq)

    def select_square(self, sq):
        self.selected = sq
        legal = self.board.gen_legal(colour_index(self.player_colour))
        self.legal_for_selected = [m for m in legal if move_from(m) == sq]
        self.play("select")

    def handle_event(self, event):
        if event.type == pygame.QUIT:
            self.running = False
            return
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_m:
                self._toggle_sound()
            if self.state == "playing":
                if event.key == pygame.K_u:
                    self.undo()
                elif event.key == pygame.K_f:
                    self.player_colour = -self.player_colour
                    self.play("click")
            elif event.key == pygame.K_ESCAPE:
                self.back_to_menu()
            return

        if event.type == pygame.MOUSEWHEEL and self.state == "playing":
            self.move_scroll = max(0.0, self.move_scroll - event.y * 34)
            return

        if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
            return

        if self.state == "menu":
            for b in self.menu_buttons.values():
                if b.clicked(event):
                    self.play("click")
                    b.on_click()
                    return
        elif self.state == "gameover":
            card = self._gameover_card()
            for name, rect in card["buttons"].items():
                if rect.collidepoint(event.pos):
                    self.play("click")
                    if name == "rematch":
                        self.start_game(self.player_colour)
                    elif name == "menu":
                        self.back_to_menu()
                    elif name == "quit":
                        self.running = False
                    return
            if self.state == "gameover":
                return  # clicks on the board do nothing while the card is up
        elif self.state == "playing":
            if self.pending_promo:
                for piece, rect in self._promo_rects().items():
                    if rect.collidepoint(event.pos):
                        for m in self.pending_promo[2]:
                            if move_promo(m) == piece:
                                self.apply_move(m)
                                break
                        self.pending_promo = None
                        return
                return  # must pick a piece
            if (self.engine_thread is None
                    and self.board.side == colour_index(self.player_colour)):
                self.handle_click_board(event.pos)

    ## ---------- drawing ----------

    def draw_board(self):
        surf = self.screen
        ## Frame + coordinates
        pygame.draw.rect(surf, PANEL_EDGE,
                         (BOARD_X - 14, BOARD_Y - 14, BOARD_SIZE + 28, BOARD_SIZE + 28),
                         border_radius=8)
        for i in range(8):
            file_ch = "abcdefgh"[i] if self.player_colour == 1 else "hgfedcba"[i]
            rank_ch = "87654321"[i] if self.player_colour == 1 else "12345678"[i]
            fx = BOARD_X + i * SQUARE + SQUARE // 2
            ry = BOARD_Y + i * SQUARE + SQUARE // 2
            surf.blit(self.f_small.render(file_ch, True, TEXT_DIM), (fx - 5, BOARD_Y - 30))
            surf.blit(self.f_small.render(rank_ch, True, TEXT_DIM), (BOARD_X - 26, ry - 10))
            surf.blit(self.f_small.render(file_ch, True, TEXT_DIM), (fx - 5, BOARD_Y + BOARD_SIZE + 8))

        ## Squares + highlights
        check_sq = None
        if self.board.in_check(self.board.side):
            check_sq = self.board.king_square(self.board.side)

        for vr in range(8):
            for vc in range(8):
                sq = vr * 8 + vc if self.player_colour == 1 else (7 - vr) * 8 + (7 - vc)
                x = BOARD_X + vc * SQUARE
                y = BOARD_Y + vr * SQUARE
                colour = LIGHT_SQ if (vr + vc) % 2 == 0 else DARK_SQ
                pygame.draw.rect(surf, colour, (x, y, SQUARE, SQUARE))

                if self.last_move and sq in self.last_move:
                    overlay = pygame.Surface((SQUARE, SQUARE), pygame.SRCALPHA)
                    overlay.fill((*LAST_MOVE, 90))
                    surf.blit(overlay, (x, y))

                if sq == check_sq:
                    overlay = pygame.Surface((SQUARE, SQUARE), pygame.SRCALPHA)
                    overlay.fill((*CHECK_RED, 130))
                    surf.blit(overlay, (x, y))
                    pygame.draw.rect(surf, CHECK_RED, (x + 1, y + 1, SQUARE - 2, SQUARE - 2), 3)

                if self.selected == sq:
                    pygame.draw.rect(surf, SELECT_GREEN, (x + 1, y + 1, SQUARE - 2, SQUARE - 2), 4)

        ## Legal move markers
        if self.selected is not None:
            for m in self.legal_for_selected:
                tsq = move_to(m)
                vr, vc = self.square_to_view(tsq)
                cx = BOARD_X + vc * SQUARE + SQUARE // 2
                cy = BOARD_Y + vr * SQUARE + SQUARE // 2
                marker = pygame.Surface((SQUARE, SQUARE), pygame.SRCALPHA)
                is_capture = bool(self.board.occ[self.board.side ^ 1] >> tsq & 1) \
                    or move_flag(m) == FLAG_EP
                if is_capture:
                    pygame.draw.circle(marker, (30, 30, 30, 110), (SQUARE // 2,) * 2,
                                       SQUARE // 2 - 3, 6)
                else:
                    pygame.draw.circle(marker, (30, 30, 30, 90), (SQUARE // 2,) * 2, 9)
                surf.blit(marker, (BOARD_X + vc * SQUARE, BOARD_Y + vr * SQUARE))

        ## Pieces (skip the destination of an in-flight animation)
        anim_to = self.anim[1] if self.anim else None
        for vr in range(8):
            for vc in range(8):
                sq = vr * 8 + vc if self.player_colour == 1 else (7 - vr) * 8 + (7 - vc)
                if sq == anim_to:
                    continue
                code = self.board._code_at(sq)
                if code:
                    x = BOARD_X + vc * SQUARE + (SQUARE - self.piece_images[code].get_width()) // 2
                    y = BOARD_Y + vr * SQUARE + (SQUARE - self.piece_images[code].get_height()) // 2
                    surf.blit(self.piece_images[code], (x, y))

        ## Moving piece
        if self.anim:
            frm, to, code, t0 = self.anim
            dur = 220
            t = min(1.0, (pygame.time.get_ticks() - t0) / dur)
            t = 1 - (1 - t) ** 3  # ease-out
            fr, fc = self.square_to_view(frm)
            tr, tc = self.square_to_view(to)
            x = BOARD_X + (fc + (tc - fc) * t) * SQUARE
            y = BOARD_Y + (fr + (tr - fr) * t) * SQUARE
            img = self.piece_images[code]
            surf.blit(img, (x + (SQUARE - img.get_width()) // 2,
                            y + (SQUARE - img.get_height()) // 2))
            if t >= 1.0:
                self.anim = None

        ## Promotion picker
        if self.pending_promo:
            self.draw_promotion()

    def _promo_rects(self):
        frm, to, _ = self.pending_promo
        vr, vc = self.square_to_view(to)
        px = self.piece_images[5].get_width()
        gap = 4
        rects = {}
        top = vr == 0
        for i, piece in enumerate((5, 4, 3, 2)):
            y = BOARD_Y + vr * SQUARE + (i * (px + gap) if top else -(i + 1) * (px + gap))
            y = max(BOARD_Y, min(y, BOARD_Y + BOARD_SIZE - px))
            rects[piece] = pygame.Rect(BOARD_X + vc * SQUARE + (SQUARE - px) // 2, y, px, px)
        return rects

    def draw_promotion(self):
        surf = self.screen
        dim = pygame.Surface((WINDOW_W, WINDOW_H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 110))
        surf.blit(dim, (0, 0))
        for piece, rect in self._promo_rects().items():
            pygame.draw.rect(surf, "#f4f0e6", rect, border_radius=8)
            pygame.draw.rect(surf, PANEL_EDGE, rect, 2, border_radius=8)
            surf.blit(self.piece_images[piece], rect.topleft)

    def draw_panel(self):
        surf = self.screen
        pygame.draw.rect(surf, PANEL, (0, 0, PANEL_W, WINDOW_H))
        pygame.draw.rect(surf, PANEL_EDGE, (PANEL_W - 2, 0, 2, WINDOW_H))

        x0 = 24
        ## Header
        surf.blit(self.f_mid.render("PyChess", True, ACCENT), (x0, 18))
        diff_name = DIFFICULTIES[self.diff_idx][0]
        who = ("Your move" if self.board.side == colour_index(self.player_colour)
               else "Engine thinking")
        if self.state != "playing":
            who = "Game over"
        status = f"{diff_name} -- {who}"
        if self.engine_thread is not None:
            dots = "." * (1 + (pygame.time.get_ticks() // 400) % 3)
            status += dots
        surf.blit(self.f_small.render(status, True, TEXT_DIM), (x0, 56))

        ## Eval bar
        bar_x, bar_y, bar_w, bar_h = x0, 92, 30, 460
        frac = cp_to_fraction(self.eval_cp)
        pygame.draw.rect(surf, "#20242c", (bar_x, bar_y, bar_w, bar_h), border_radius=6)
        white_h = int(bar_h * frac)
        if white_h:
            pygame.draw.rect(surf, "#e8ecf2", (bar_x, bar_y + bar_h - white_h, bar_w, white_h),
                             border_radius=6)
        pygame.draw.rect(surf, PANEL_EDGE, (bar_x, bar_y, bar_w, bar_h), 2, border_radius=6)
        pygame.draw.line(surf, ACCENT_DIM, (bar_x, bar_y + bar_h // 2),
                         (bar_x + bar_w, bar_y + bar_h // 2), 1)
        score = fmt_score(self.eval_cp if self.player_colour == 1 else -self.eval_cp)
        surf.blit(self.f_small.render(score, True, TEXT), (bar_x + 2, bar_y + bar_h + 8))

        ## Engine info
        if self.engine_info:
            score, depth = self.engine_info
            shown = score if self.player_colour == 1 else -score
            surf.blit(self.f_small.render(
                f"engine {fmt_score(shown)} @ depth {depth}", True, TEXT_DIM),
                (bar_x + 60, bar_y + bar_h + 8))

        ## Move list
        mlx, mly = bar_x + 52, bar_y
        mlw, mlh = PANEL_W - mlx - 20, bar_h
        pygame.draw.rect(surf, "#151a21", (mlx, mly, mlw, mlh), border_radius=8)
        pygame.draw.rect(surf, PANEL_EDGE, (mlx, mly, mlw, mlh), 1, border_radius=8)

        rows = []
        for i in range(0, len(self.san_history), 2):
            num = f"{i // 2 + 1:>2}."
            white = self.san_history[i]
            black = self.san_history[i + 1] if i + 1 < len(self.san_history) else ""
            rows.append((num, white, black))

        line_h = 26
        visible = (mlh - 16) // line_h
        max_scroll = max(0.0, len(rows) * line_h - (mlh - 16))
        self.move_scroll = min(self.move_scroll, max_scroll)
        if self.engine_thread is None:
            self.move_scroll = max_scroll  # follow the latest move
        clip = pygame.Rect(mlx + 2, mly + 2, mlw - 4, mlh - 4)
        surf.set_clip(clip)
        start_row = int(self.move_scroll // line_h)
        for idx in range(start_row, min(len(rows), start_row + visible + 1)):
            y = mly + 8 + idx * line_h - self.move_scroll
            num, wmv, bmv = rows[idx]
            surf.blit(self.f_small.render(num, True, TEXT_DIM), (mlx + 10, y))
            surf.blit(self.f_small.render(wmv, True, TEXT), (mlx + 48, y))
            surf.blit(self.f_small.render(bmv, True, TEXT), (mlx + 120, y))
        surf.set_clip(None)

        ## Captured trays
        tray_y = bar_y + bar_h + 44
        self.draw_tray(1, x0, tray_y, right_to_left=True)
        self.draw_tray(-1, x0, tray_y + 36, right_to_left=True)

        ## Buttons
        by = WINDOW_H - 108
        bx = x0
        bw, bh, gap = (PANEL_W - 48 - 3 * 8) // 4, 44, 8
        self.panel_buttons = []
        for label, action in (("Undo", self.undo), ("Flip", self._flip),
                              ("Menu", self.back_to_menu), ("New", lambda: self.start_game(self.player_colour))):
            rect = pygame.Rect(bx, by, bw, bh)
            btn = Button(rect, label, self.f_small, action)
            self.panel_buttons.append(btn)
            bx += bw + gap
        for btn in self.panel_buttons:
            btn.draw(surf)

        hint = "U undo  F flip  M mute"
        surf.blit(self.f_tiny.render(hint, True, TEXT_DIM), (x0, WINDOW_H - 46))

    def draw_tray(self, colour, x, y, right_to_left=True):
        """Pieces captured BY `colour`, with material advantage."""
        surf = self.screen
        label = "White" if colour == 1 else "Black"
        surf.blit(self.f_tiny.render(f"{label} took:", True, TEXT_DIM), (x, y + 6))
        cx = x + 84
        gained = 0
        for code in self.captured[colour]:
            gained += PIECE_VALUE[abs(code)]
            surf.blit(self.tray_images[code], (cx, y))
            cx += 26
        opponent = -colour
        lost = sum(PIECE_VALUE[abs(c)] for c in self.captured[opponent])
        if gained > lost:
            surf.blit(self.f_tiny.render(f"+{gained - lost}", True, "#7ddb9a"), (cx + 4, y + 6))

    def _flip(self):
        self.player_colour = -self.player_colour
        self.play("click")

    def _gameover_card(self):
        reason, winner = self.gameover_info
        if winner is None:
            title = "Draw"
        elif winner == self.player_colour:
            title = "You win!"
        else:
            title = "Engine wins"
        sub = REASONS.get(reason, reason)

        card_w, card_h = 460, 300
        card = pygame.Rect(0, 0, card_w, card_h)
        card.center = (BOARD_X + BOARD_SIZE // 2, BOARD_Y + BOARD_SIZE // 2)
        bw, bh = 190, 50
        buttons = {
            "rematch": pygame.Rect(card.centerx - bw - 8, card.bottom - bh - 24, bw, bh),
            "menu": pygame.Rect(card.centerx + 8, card.bottom - bh - 24, bw, bh),
        }
        return {"title": title, "sub": sub, "card": card, "buttons": buttons}

    def draw_gameover(self):
        surf = self.screen
        info = self._gameover_card()
        dim = pygame.Surface((WINDOW_W, WINDOW_H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 150))
        surf.blit(dim, (0, 0))
        card = info["card"]
        pygame.draw.rect(surf, PANEL, card, border_radius=14)
        pygame.draw.rect(surf, ACCENT, card, 2, border_radius=14)
        title = self.f_big.render(info["title"], True, TEXT)
        surf.blit(title, title.get_rect(center=(card.centerx, card.y + 90)))
        sub = self.f_ui.render(info["sub"], True, TEXT_DIM)
        surf.blit(sub, sub.get_rect(center=(card.centerx, card.y + 140)))
        for rect in info["buttons"].values():
            Button(rect, "", self.f_ui).draw(surf)
        for label, rect in zip(("Rematch", "Menu"), info["buttons"].values()):
            text = self.f_ui.render(label, True, TEXT)
            surf.blit(text, text.get_rect(center=rect.center))

    def draw_menu(self):
        surf = self.screen
        surf.fill(BG)
        ## Soft radial-ish backdrop: layered rounded squares
        for i, alpha in ((180, 16), (130, 12), (80, 8)):
            rect = pygame.Rect(0, 0, i * 4, i * 4)
            rect.center = (WINDOW_W // 2, WINDOW_H // 2 - 40)
            layer = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(layer, (*pygame.Color(ACCENT)[:3], alpha), rect, border_radius=40)
            surf.blit(layer, rect.topleft)

        knight = self.piece_images[-2]
        surf.blit(knight, (WINDOW_W // 2 - knight.get_width() // 2, 120))
        title = self.f_big.render("PyChess", True, TEXT)
        surf.blit(title, title.get_rect(center=(WINDOW_W // 2, 250)))
        sub = self.f_ui.render("bitboard engine -- pick your side", True, TEXT_DIM)
        surf.blit(sub, sub.get_rect(center=(WINDOW_W // 2, 292)))

        for b in self.menu_buttons.values():
            b.draw(surf)

    ## ---------- main loop ----------

    def run(self):
        self.running = True
        while self.running:
            dt = self.clock.tick(60) / 1000
            for event in pygame.event.get():
                self.handle_event(event)

            self.maybe_start_engine()
            self.poll_engine()

            self.screen.fill(BG)
            if self.state == "menu":
                self.draw_menu()
            else:
                self.draw_panel()
                self.draw_board()
                if self.state == "gameover":
                    self.confetti.update(dt)
                    self.confetti.draw(self.screen)
                    self.draw_gameover()
            pygame.display.flip()
        pygame.quit()


if __name__ == "__main__":
    Game().run()
