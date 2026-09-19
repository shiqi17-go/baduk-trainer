"""Headless checks: goban rules, and that app.py imports cleanly."""
import os
import sys
import traceback

sys.path.insert(0, r"F:\harness\baduk-trainer")
from goban import (BLACK, EMPTY, WHITE, Goban, IllegalMove, from_gtp,  # noqa: E402
                   from_sgf_coord, sgf_coord, to_gtp, handicap_points)

fails = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


print("=== coordinates ===")
check("to_gtp D4", to_gtp(3, 15, 19) == "D4", to_gtp(3, 15, 19))
check("to_gtp Q16", to_gtp(15, 3, 19) == "Q16", to_gtp(15, 3, 19))
check("from_gtp skips I", from_gtp("J4", 19) == (8, 15), str(from_gtp("J4", 19)))
check("roundtrip all 361 points",
      all(from_gtp(to_gtp(x, y, 19), 19) == (x, y) for x in range(19) for y in range(19)))

print()
print("=== board regions (a silent bug here corrupts every direction) ===")
try:
    from collections import Counter

    from explain import REGION_NAMES, region_index
    cnt = Counter()
    for yy in range(19):
        for xx in range(19):
            cnt[region_index(xx, yy, 19)] += 1
    check("all 9 regions non-empty", all(cnt[i] > 0 for i in range(9)),
          str([cnt[i] for i in range(9)]))
    check("every region has 30-50 points",
          all(30 <= cnt[i] <= 50 for i in range(9)), str([cnt[i] for i in range(9)]))
    check("covers all 361 points", sum(cnt.values()) == 361, str(sum(cnt.values())))
    for (xx, yy, want) in [(0, 0, "左上角"), (9, 9, "中腹"), (18, 18, "右下角"),
                           (9, 0, "上边"), (0, 9, "左边"), (18, 9, "右边"),
                           (9, 18, "下边"), (6, 6, "中腹"), (12, 9, "中腹"),
                           (5, 9, "左边"), (12, 18, "下边"), (6, 18, "下边")]:
        got = REGION_NAMES[region_index(xx, yy, 19)]
        check(f"({xx},{yy}) is {want}", got == want, got)
except Exception:
    traceback.print_exc()
    check("region checks ran", False)

print()
print("=== SGF coordinates (must not use the GTP alphabet) ===")
check("(0,0) -> aa", sgf_coord(0, 0) == "aa", sgf_coord(0, 0))
check("(3,3) -> dd", sgf_coord(3, 3) == "dd", sgf_coord(3, 3))
check("(15,15) -> pp", sgf_coord(15, 15) == "pp", sgf_coord(15, 15))
check("(8,0) -> ia (GTP would say ja)", sgf_coord(8, 0) == "ia", sgf_coord(8, 0))
check("GTP J4 == SGF ip, not jp",
      to_gtp(8, 15, 19) == "J4" and sgf_coord(8, 15) == "ip", sgf_coord(8, 15))
check("sgf roundtrip all 361 points",
      all(from_sgf_coord(sgf_coord(x, y)) == (x, y)
          for x in range(19) for y in range(19)))

print()
print("=== capture a corner stone ===")
# white (0,0) has exactly two liberties: (1,0) and (0,1)
g = Goban(9)
g.play(WHITE, 0, 0)
g.play(BLACK, 1, 0)
ok, why = g.is_legal(BLACK, 0, 1)
check("the capturing move is legal", ok, why)
g.play(BLACK, 0, 1)
check("white stone removed", g.get(0, 0) == EMPTY, str(g.get(0, 0)))
check("capture counted for black", g.captures[BLACK] == 1, str(g.captures[BLACK]))

print()
print("=== suicide ===")
s = Goban(9)
s.play(WHITE, 1, 0)
s.play(WHITE, 0, 1)
ok, why = s.is_legal(BLACK, 0, 0)
check("suicide rejected", not ok, why)
# white already holds (1,0) and (0,1), so white at (0,0) *connects* them: legal
ok, why = s.is_legal(WHITE, 0, 0)
check("connecting move is not suicide", ok, why)

print()
print("=== occupied point ===")
ok, why = g.is_legal(WHITE, 1, 0)
check("occupied rejected", not ok, why)

print()
print("=== ko ===")
# Real ko shape: the white stone at (2,2) has exactly one liberty (3,2), and the
# black stone that captures it ends up with exactly one liberty too.
#      x=1 2 3 4
# y=1   .  B  W  .
# y=2   B  W  o  W      o = (3,2), black's capturing move
# y=3   .  B  W  .
k = Goban(9)
for color, x, y in [(BLACK, 1, 2), (BLACK, 2, 1), (BLACK, 2, 3),
                    (WHITE, 4, 2), (WHITE, 3, 1), (WHITE, 3, 3),
                    (WHITE, 2, 2)]:
    k.play(color, x, y)
check("white ko stone has one liberty",
      k.group(2, 2)[1] == 1, str(k.group(2, 2)[1]))
ok, why = k.is_legal(BLACK, 3, 2)
check("ko capture is legal", ok, why)
k.play(BLACK, 3, 2)
check("ko stone captured", k.get(2, 2) == EMPTY, str(k.get(2, 2)))
check("capturing stone has one liberty",
      k.group(3, 2)[1] == 1, str(k.group(3, 2)[1]))
check("ko point recorded", k.ko_point == (2, 2), str(k.ko_point))
ok, why = k.is_legal(WHITE, 2, 2)
check("immediate ko recapture rejected", not ok, why)
# playing elsewhere lifts the ko ban
k.play(WHITE, 7, 7)
check("ko point cleared after a move elsewhere", k.ko_point is None, str(k.ko_point))
ok, why = k.is_legal(WHITE, 2, 2)
check("recapture legal after ko threat answered", ok, why)
k.play(WHITE, 2, 2)
check("recapture did capture black", k.get(3, 2) == EMPTY, str(k.get(3, 2)))

print()
print("=== a plain capture is NOT a ko ===")
p = Goban(9)
p.play(WHITE, 4, 4)
p.play(BLACK, 3, 4)
p.play(BLACK, 5, 4)
p.play(BLACK, 4, 3)
p.play(BLACK, 4, 5)     # captures white (4,4); the black stones have 3 libs each
check("white captured", p.get(4, 4) == EMPTY, str(p.get(4, 4)))
check("no ko point set", p.ko_point is None, str(p.ko_point))
ok, why = p.is_legal(WHITE, 4, 4)
check("refill is suicide here, not ko (correct)", not ok, why)

print()
print("=== undo ===")
u = Goban(9)
u.play(BLACK, 4, 4)
u.play(WHITE, 4, 5)
u.play(BLACK, 5, 5)
n_before = u.move_number()
u.undo()
check("undo removes one move", u.move_number() == n_before - 1, str(u.move_number()))
check("undone stone gone", u.get(5, 5) == EMPTY, str(u.get(5, 5)))
check("earlier black stone still there", u.get(4, 4) == BLACK)
check("white stone still there", u.get(4, 5) == WHITE)

print()
print("=== undo restores captures ===")
c = Goban(9)
c.play(WHITE, 0, 0)
c.play(BLACK, 1, 0)
c.play(BLACK, 0, 1)     # captures white
check("capture happened", c.captures[BLACK] == 1, str(c.captures[BLACK]))
check("white gone", c.get(0, 0) == EMPTY)
c.undo()
check("white stone restored", c.get(0, 0) == WHITE, str(c.get(0, 0)))
check("capture count restored", c.captures[BLACK] == 0, str(c.captures[BLACK]))
check("capturing stone removed", c.get(0, 1) == EMPTY, str(c.get(0, 1)))

print()
print("=== pass ===")
pa = Goban(9)
pa.play_pass(BLACK)
check("pass recorded", pa.move_number() == 1)
check("pass does not change the grid", all(v == EMPTY for row in pa.grid for v in row))
check("last_move skips passes", pa.last_move() is None)

print()
print("=== handicap points ===")
for n in (2, 3, 4, 5, 6, 7, 8, 9):
    pts = handicap_points(19, n)
    inside = all(0 <= x < 19 and 0 <= y < 19 for x, y in pts)
    check(f"handicap {n}: {n} distinct, in range", len(set(pts)) == n and inside, str(len(pts)))

print()
print("=== area score ===")
a = Goban(9)
for y in range(9):
    a.play(BLACK, 0, y) if False else None
for y in range(9):
    a.play(BLACK, 4, y)
sc = a.area_score()
check("area_score returns dict", isinstance(sc, dict) and "diff" in sc, str(sc))

print()
print("=== app.py imports cleanly ===")
try:
    import app  # noqa
    check("import app", True)
    check("App class present", hasattr(app, "App"))
    check("rank labels", len(app.RANK_LABELS) >= 10, str(app.RANK_LABELS[:4]))
    check("profile_for traditional", app.profile_for(0, "传统").startswith("preaz_"))
    check("profile_for modern", app.profile_for(0, "现代").startswith("rank_"))
except Exception:
    traceback.print_exc()
    check("import app", False)

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: {fails}")
    sys.exit(1)
print("ALL HEADLESS CHECKS PASSED")
