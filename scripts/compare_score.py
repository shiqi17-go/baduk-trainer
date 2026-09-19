"""Compare the two "position assessment" numbers the app can show.

1. goban.area_score() -- a naive area count (stones + fully enclosed empty
   points). Used by the 对弈 tab's status line.
2. KataGo's scoreLead -- the neural net's prediction.

If these disagree badly, the naive one is the thing the user is seeing as
"very inaccurate".
"""
import sys
import time

sys.path.insert(0, r"F:\harness\baduk-trainer")
from analysis import KataGoAnalysis
from goban import BLACK, WHITE, Goban, from_gtp
from study import parse_sgf_mainline

GTP = "ABCDEFGHJKLMNOPQRSTUVWXYZ"


def build(moves, upto):
    g = Goban(19)
    out = []
    for color, mv in moves[:upto]:
        c = BLACK if color == "B" else WHITE
        pos = from_gtp(mv, 19)
        if pos:
            ok, _ = g.is_legal(c, pos[0], pos[1])
            if ok:
                g.play(c, pos[0], pos[1])
                out.append([color, mv])
    return g, out


raw = parse_sgf_mainline(r"F:\harness\baduk-trainer\samples\student10k.sgf")
moves = [[c, f"{GTP[p[0]]}{19-p[1]}"] for c, p in raw if p is not None]

a = KataGoAnalysis()
a.start()
print(f"{'手数':>5} {'我的粗略数':>22} {'KataGo 预测':>18} {'差':>10}")
print("-" * 62)
for upto in (10, 30, 60, 100, 144):
    g, played = build(moves, upto)
    s = g.area_score()          # naive: stones + enclosed empty
    r = a.query({"moves": played, "rules": "chinese", "komi": 7.5,
                 "boardXSize": 19, "boardYSize": 19,
                 "analyzeTurns": [len(played)], "maxVisits": 300},
                expect_turns=1)
    lead = r["rootInfo"]["scoreLead"]      # black's perspective
    naive = s["diff"]                       # black - (white + komi)
    print(f"{upto:>5} {'黑%.0f 白%.0f 差%+.1f' % (s['black'], s['white'], naive):>22} "
          f"{'黑%+.1f 目' % lead:>18} {abs(naive - lead):>9.1f}")
a.close()
print()
print("我的粗略数 = 棋子数 + 被完全包围的空点（不考虑死活、外势、未定区域）")
print("KataGo 预测 = 神经网络对最终目差的判断")
