"""Go board rules and state. No dependencies, no UI.

Board coordinates internally are (x, y) with x=0 leftmost, y=0 topmost,
both 0-based on a size x size board. GTP coordinates are A-T (skipping I)
left to right and 1-19 bottom to top, so y_gtp = size - y.
"""

from __future__ import annotations

GTP_LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"

# standard handicap points on a 19x19 board, as (x, y) internal coords
HANDICAP_POINTS_19 = {
    2: [(3, 15), (15, 3)],
    3: [(3, 15), (15, 3), (15, 15)],
    4: [(3, 15), (15, 3), (15, 15), (3, 3)],
    5: [(3, 15), (15, 3), (15, 15), (3, 3), (9, 9)],
    6: [(3, 15), (15, 3), (15, 15), (3, 3), (3, 9), (15, 9)],
    7: [(3, 15), (15, 3), (15, 15), (3, 3), (3, 9), (15, 9), (9, 9)],
    8: [(3, 15), (15, 3), (15, 15), (3, 3), (3, 9), (15, 9), (9, 15), (9, 3)],
    9: [(3, 15), (15, 3), (15, 15), (3, 3), (3, 9), (15, 9), (9, 15), (9, 3), (9, 9)],
}


def handicap_points(size: int, count: int) -> list[tuple[int, int]]:
    if count <= 1:
        return []
    if size == 19 and count in HANDICAP_POINTS_19:
        return list(HANDICAP_POINTS_19[count])
    # generic fallback: star points at 1/4 and 3/4, plus centre
    lo, hi = 3 if size == 19 else max(2, size // 4), size - 1 - (3 if size == 19 else max(2, size // 4))
    mid = size // 2
    cands = [(lo, hi), (hi, lo), (hi, hi), (lo, lo),
             (lo, mid), (hi, mid), (mid, lo), (mid, hi), (mid, mid)]
    return cands[:count]


def to_gtp(x: int, y: int, size: int) -> str:
    return f"{GTP_LETTERS[x]}{size - y}"


def from_gtp(s: str, size: int) -> tuple[int, int] | None:
    s = s.strip().upper()
    if not s or s in ("PASS", "RESIGN"):
        return None
    try:
        x = GTP_LETTERS.index(s[0])
        y = size - int(s[1:])
    except (ValueError, IndexError):
        return None
    if not (0 <= x < size and 0 <= y < size):
        return None
    return x, y


def sgf_coord(x: int, y: int) -> str:
    """SGF point: two letters, 0-based from the top-left, alphabet NOT skipping
    'I' (unlike GTP). (0,0) is the top-left corner -> 'aa'."""
    return chr(ord("a") + x) + chr(ord("a") + y)


def from_sgf_coord(s: str) -> tuple[int, int] | None:
    if not s or len(s) != 2:
        return None
    return ord(s[0].lower()) - ord("a"), ord(s[1].lower()) - ord("a")


EMPTY, BLACK, WHITE = 0, 1, 2


class IllegalMove(Exception):
    pass


class Goban:
    def __init__(self, size: int = 19, komi: float = 7.5):
        self.size = size
        self.komi = komi
        self.grid = [[EMPTY] * size for _ in range(size)]
        self.moves: list[tuple[int, int, int]] = []  # (color, x, y) with x=y=-1 for pass
        self.captures = {BLACK: 0, WHITE: 0}
        self._position_history: list[tuple] = []
        self.ko_point: tuple[int, int] | None = None

    # ------------------------------------------------------------- accessors

    def get(self, x: int, y: int) -> int:
        return self.grid[y][x]

    def copy(self) -> "Goban":
        g = Goban(self.size, self.komi)
        g.grid = [row[:] for row in self.grid]
        g.moves = list(self.moves)
        g.captures = dict(self.captures)
        g._position_history = list(self._position_history)
        g.ko_point = self.ko_point
        return g

    def _key(self) -> tuple:
        return tuple(tuple(row) for row in self.grid)

    # -------------------------------------------------------------- geometry

    def neighbours(self, x: int, y: int):
        if x > 0:
            yield x - 1, y
        if x < self.size - 1:
            yield x + 1, y
        if y > 0:
            yield x, y - 1
        if y < self.size - 1:
            yield x, y + 1

    def group(self, x: int, y: int) -> tuple[set[tuple[int, int]], int]:
        """Return (stones in the connected group, liberty count)."""
        color = self.grid[y][x]
        if color == EMPTY:
            return set(), 0
        stack = [(x, y)]
        seen = {(x, y)}
        libs: set[tuple[int, int]] = set()
        while stack:
            cx, cy = stack.pop()
            for nx, ny in self.neighbours(cx, cy):
                v = self.grid[ny][nx]
                if v == EMPTY:
                    libs.add((nx, ny))
                elif v == color and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        return seen, len(libs)

    # ----------------------------------------------------------------- rules

    def is_legal(self, color: int, x: int, y: int) -> tuple[bool, str]:
        if not (0 <= x < self.size and 0 <= y < self.size):
            return False, "越界"
        if self.grid[y][x] != EMPTY:
            return False, "该点已有子"
        if self.ko_point == (x, y):
            return False, "打劫，此处暂不能下"

        # simulate
        saved = [row[:] for row in self.grid]
        self.grid[y][x] = color
        opp = WHITE if color == BLACK else BLACK
        captured = 0
        for nx, ny in self.neighbours(x, y):
            if self.grid[ny][nx] == opp:
                stones, libs = self.group(nx, ny)
                if libs == 0:
                    for sx, sy in stones:
                        self.grid[sy][sx] = EMPTY
                    captured += len(stones)
        _, own_libs = self.group(x, y)
        suicide = own_libs == 0
        self.grid = saved

        if suicide:
            return False, "自杀手"
        return True, ""

    def play(self, color: int, x: int, y: int) -> None:
        """Place a stone. Raises IllegalMove on any violation."""
        ok, why = self.is_legal(color, x, y)
        if not ok:
            raise IllegalMove(why)

        self._position_history.append(self._key())
        opp = WHITE if color == BLACK else BLACK
        self.grid[y][x] = color
        captured_points: list[tuple[int, int]] = []
        for nx, ny in self.neighbours(x, y):
            if self.grid[ny][nx] == opp:
                stones, libs = self.group(nx, ny)
                if libs == 0:
                    for sx, sy in stones:
                        self.grid[sy][sx] = EMPTY
                    captured_points.extend(stones)
        captured_here = len(captured_points)
        self.captures[color] += captured_here

        # Simple ko: a move that captures exactly one stone, leaving the
        # capturing stone itself with exactly one liberty, forbids the opponent
        # from immediately recapturing at the captured point.
        # (Deliberately not inferred from position repetition: the ko point has
        # to be known *before* the opponent's reply is validated.)
        self.ko_point = None
        if captured_here == 1:
            stones, libs = self.group(x, y)
            if len(stones) == 1 and libs == 1:
                self.ko_point = captured_points[0]

        self.moves.append((color, x, y))

    def play_pass(self, color: int) -> None:
        self._position_history.append(self._key())
        self.ko_point = None
        self.moves.append((color, -1, -1))

    def undo(self) -> bool:
        """Take back one move."""
        if not self.moves:
            return False
        color, x, y = self.moves.pop()
        if self._position_history:
            self._position_history.pop()
        # rebuild from scratch: simplest correct approach
        moves = list(self.moves)
        self.grid = [[EMPTY] * self.size for _ in range(self.size)]
        self.moves = []
        self.captures = {BLACK: 0, WHITE: 0}
        self._position_history = []
        self.ko_point = None
        for c, mx, my in moves:
            if mx < 0:
                self.play_pass(c)
            else:
                self.play(c, mx, my)
        return True

    def last_move(self) -> tuple[int, int] | None:
        for color, x, y in reversed(self.moves):
            if x >= 0:
                return x, y
        return None

    def move_number(self) -> int:
        return len(self.moves)

    # --------------------------------------------------------------- scoring

    def area_score(self) -> dict:
        """Chinese-style area score, for a quick display only (no seki handling)."""
        visited = [[False] * self.size for _ in range(self.size)]
        black_area = white_area = 0
        for y in range(self.size):
            for x in range(self.size):
                v = self.grid[y][x]
                if v == BLACK:
                    black_area += 1
                elif v == WHITE:
                    white_area += 1
                elif not visited[y][x]:
                    stack = [(x, y)]
                    visited[y][x] = True
                    region = []
                    borders = set()
                    while stack:
                        cx, cy = stack.pop()
                        region.append((cx, cy))
                        for nx, ny in self.neighbours(cx, cy):
                            nv = self.grid[ny][nx]
                            if nv == EMPTY and not visited[ny][nx]:
                                visited[ny][nx] = True
                                stack.append((nx, ny))
                            elif nv != EMPTY:
                                borders.add(nv)
                    if borders == {BLACK}:
                        black_area += len(region)
                    elif borders == {WHITE}:
                        white_area += len(region)
        return {
            "black": black_area,
            "white": white_area + self.komi,
            "diff": black_area - (white_area + self.komi),
        }


def star_points(size: int) -> list[tuple[int, int]]:
    if size == 19:
        pts = [3, 9, 15]
        return [(x, y) for x in pts for y in pts]
    if size == 13:
        pts = [3, 6, 9]
        return [(x, y) for x in pts for y in pts]
    if size == 9:
        return [(2, 2), (6, 2), (4, 4), (2, 6), (6, 6)]
    return []
