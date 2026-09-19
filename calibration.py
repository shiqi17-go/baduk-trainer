"""Rank calibration harness for KataGo's human SL profiles.

Plays headless games between profiles, records results, and reports win rates
plus Elo differences. The point is to find out what a profile's *actual*
playing strength is -- the human SL model imitates the average mistake
distribution of a rank, which is NOT guaranteed to match that rank's strength.

Key speed trick: the official human-like-play config deliberately delays every
move by 0-10 seconds (delayMoveScale / delayMoveMax) to feel human. Calibration
turns that off, otherwise a single game takes tens of minutes.

Usage:
  python calibration.py --games 2 --workers 2                 # quick timing run
  python calibration.py --plan ladder --games 20 --workers 2  # real run
  python calibration.py --report                              # summarise results so far
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from katago_engine import KataGoEngine, KataGoError  # noqa: E402
from goban import from_gtp, sgf_coord  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "calibration")
SGF_DIR = os.path.join(OUT, "sgf")
RESULTS_CSV = os.path.join(OUT, "games.csv")
os.makedirs(SGF_DIR, exist_ok=True)

# Delays off + quieter logs: this is a measurement run, not a demo.
CALIB_OVERRIDES = {
    "delayMoveScale": "0.0",
    "delayMoveMax": "0.0",
    "logToStderr": "false",
    "logSearchInfo": "false",
    "reportAnalysisWinratesAs": "BLACK",
}

# Adjacent steps give local Elo gaps. The two long-range pairs at the end are
# transitivity checks: if the chain is consistent, their measured gap should
# roughly equal the sum of the steps between them.
LADDER = [
    ("preaz_20k", "preaz_15k"),
    ("preaz_15k", "preaz_10k"),
    ("preaz_10k", "preaz_5k"),
    ("preaz_5k", "preaz_1k"),
    ("preaz_1k", "rank_1k"),
    ("rank_1k", "rank_5k"),
    ("rank_5k", "rank_1d"),
    ("rank_1d", "rank_3d"),
    ("rank_3d", "rank_5d"),
    # transitivity checks
    ("preaz_15k", "preaz_5k"),
    ("rank_1k", "rank_1d"),
]

COLUMNS = ["ts", "black_profile", "white_profile", "winner", "result",
           "moves", "seconds", "visits", "komi", "sgf", "error", "handicap"]

# Handicap sweep: the first-listed profile is the nominally WEAKER one, so as
# the handicap rises its win rate should climb from ~0% to ~50%+. The handicap
# at which it reaches even is a direct measure of the strength gap in stones,
# which the plain even-game ladder could not give us (everything saturated at
# 100%).
# Levels skew low: a smoke test showed the weaker side already winning 100% at
# 5 stones, so the crossing sits in the 2-4 range for a 5-rank nominal gap.
HANDICAP_LEVELS = [0, 2, 3, 4, 6]
HANDICAP_PAIRS = [
    ("preaz_10k", "preaz_5k"),
    ("preaz_15k", "preaz_5k"),
    ("rank_5k", "rank_1k"),
]


# --------------------------------------------------------------------- sgf

def coord_to_sgf(move: str, size: int = 19) -> str:
    """GTP move -> SGF point. Must go through the internal (x, y) grid: the GTP
    alphabet skips 'I' but SGF's does not, so a plain letter lookup shifts every
    column from the 9th onward by one."""
    pos = from_gtp(move, size)
    if pos is None:
        return ""
    return sgf_coord(*pos)


def write_sgf(path: str, black: str, white: str, moves: list[tuple[str, str]],
              size: int = 19, komi: float = 7.5, result: str = "") -> None:
    parts = [
        "(;GM[1]FF[4]CA[UTF-8]",
        f"SZ[{size}]KM[{komi}]",
        f"PB[{black}]PW[{white}]",
    ]
    if result:
        parts.append(f"RE[{result}]")
    for color, mv in moves:
        c = coord_to_sgf(mv, size)
        if mv.strip().lower() == "pass":
            parts.append(f";{color}[]")
        elif mv.strip().lower() == "resign":
            break
        elif c:
            parts.append(f";{color}[{c}]")
    parts.append(")")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))


# ------------------------------------------------------------------- worker

_thread_local = threading.local()


def _engines_for(profile_b: str, profile_w: str, visits: int) -> tuple:
    """Cache one engine per profile per worker thread; drop engines we no
    longer need so we do not accumulate GPU memory."""
    cache = getattr(_thread_local, "engines", None)
    if cache is None:
        cache = {}
        _thread_local.engines = cache

    want = {profile_b: "B", profile_w: "W"}
    for prof in list(cache):
        if prof not in want:
            try:
                cache.pop(prof).close()
            except Exception:
                pass

    made = []
    for prof in want:
        if prof not in cache or not cache[prof].alive:
            e = KataGoEngine(profile=prof, visits=visits, rules="chinese",
                             extra_overrides=CALIB_OVERRIDES)
            e.start()
            cache[prof] = e
            made.append(prof)
    return cache[profile_b], cache[profile_w]


def play_one_game(idx: int, profile_b: str, profile_w: str, visits: int,
                  komi: float = 7.5, max_moves: int = 500,
                  board_size: int = 19, save_sgf: bool = True,
                  handicap: int = 0) -> dict:
    """With handicap >= 2, Black gets that many stones on the star points and
    White moves first -- the standard way to measure a strength gap in stones."""
    row = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "black_profile": profile_b, "white_profile": profile_w,
        "winner": "", "result": "", "moves": 0, "seconds": 0.0,
        "visits": visits, "komi": komi, "sgf": "", "error": "",
        "handicap": handicap,
    }
    t0 = time.time()
    eng_b = eng_w = None
    moves: list[tuple[str, str]] = []
    try:
        eng_b, eng_w = _engines_for(profile_b, profile_w, visits)
        for e in (eng_b, eng_w):
            e._cmd(f"boardsize {board_size}")
            e._cmd(f"komi {komi}")
            e.clear_board()

        if handicap >= 2:
            from goban import handicap_points, to_gtp
            coords = [to_gtp(x, y, board_size)
                      for x, y in handicap_points(board_size, handicap)]
            for e in (eng_b, eng_w):
                e._cmd("set_free_handicap " + " ".join(coords))
            moves = [("B", c) for c in coords]
            color = "W"          # in a handicap game White moves first
        else:
            color = "B"

        passes = 0
        for n in range(max_moves):
            eng = eng_b if color == "B" else eng_w
            other = eng_w if color == "B" else eng_b
            mv = eng.genmove(color, timeout=180)
            moves.append((color, mv))
            low = mv.strip().lower()
            if low == "resign":
                row["winner"] = "W" if color == "B" else "B"
                row["result"] = f"{row['winner']}+R"
                break
            if low == "pass":
                passes += 1
            else:
                passes = 0
                other.play(color, mv)
            if passes >= 2:
                try:
                    score = eng.final_score()
                except KataGoError:
                    score = ""
                row["result"] = score or "B+0"
                if score.startswith("W"):
                    row["winner"] = "W"
                elif score.startswith("B"):
                    row["winner"] = "B"
                else:
                    row["winner"] = "draw"
                break
            color = "W" if color == "B" else "B"
        else:
            row["error"] = "move limit reached"
            try:
                score = eng_b.final_score()
                row["result"] = score
                row["winner"] = "W" if score.startswith("W") else "B"
            except Exception:
                row["winner"] = "unknown"
        row["moves"] = len(moves)
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
        row["winner"] = "error"
    finally:
        row["seconds"] = round(time.time() - t0, 1)
        if save_sgf:
            name = f"g{idx:05d}_{profile_b}_vs_{profile_w}.sgf"
            path = os.path.join(SGF_DIR, name)
            try:
                write_sgf(path, profile_b, profile_w, moves, board_size, komi,
                          row["result"])
                row["sgf"] = name
            except Exception:
                pass
    return row


def append_rows(rows: list[dict], lock: threading.Lock) -> None:
    with lock:
        exists = os.path.exists(RESULTS_CSV)
        with open(RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            if not exists:
                w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in COLUMNS})


# ---------------------------------------------------------------------- run

def build_pairs(plan: str, games: int) -> list[tuple]:
    """Each item is (black_profile, white_profile, handicap)."""
    if plan == "ladder":
        pairs = []
        for b, w in LADDER:
            pairs += [(b, w, 0)] * games
        return pairs
    if plan == "handicap":
        pairs = []
        for weaker, stronger in HANDICAP_PAIRS:
            for h in HANDICAP_LEVELS:
                # the weaker player takes Black and receives the stones
                pairs += [(weaker, stronger, h)] * games
        return pairs
    if plan.startswith("mirror:"):
        prof = plan.split(":", 1)[1]
        return [(prof, prof, 0)] * games
    if plan.startswith("pair:"):
        _, b, w = plan.split(":", 2)
        return [(b, w, 0)] * games
    if plan.startswith("hpair:"):
        _, weaker, stronger, h = plan.split(":", 3)
        return [(weaker, stronger, int(h))] * games
    raise SystemExit(f"unknown plan: {plan}")


def run(plan: str, games: int, workers: int, visits: int, komi: float,
        seed: int | None) -> None:
    if seed is not None:
        random.seed(seed)
    pairs = build_pairs(plan, games)
    random.shuffle(pairs)

    print(f"plan={plan}  games={len(pairs)}  workers={workers}  visits={visits}")
    print(f"results -> {RESULTS_CSV}")
    print(f"delays disabled: {CALIB_OVERRIDES['delayMoveScale']}/"
          f"{CALIB_OVERRIDES['delayMoveMax']}")
    print(flush=True)

    lock = threading.Lock()
    done = 0
    t_start = time.time()
    buffer: list[dict] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {}
        for i, (b, w, h) in enumerate(pairs):
            # Randomise colours so a profile is not always black -- but NOT in
            # handicap games, where Black is the side receiving the stones.
            if h == 0 and random.random() < 0.5:
                b, w = w, b
            gkomi = 0.5 if h >= 2 else komi
            futs[pool.submit(play_one_game, i, b, w, visits, gkomi,
                             handicap=h)] = (b, w, h)
        for fut in as_completed(futs):
            try:
                row = fut.result()
            except Exception:
                traceback.print_exc()
                continue
            done += 1
            buffer.append(row)
            el = time.time() - t_start
            eta = (el / done) * (len(pairs) - done)
            print(f"[{done:4d}/{len(pairs)}] {row['black_profile']:>10s} vs "
                  f"{row['white_profile']:<10s} H{row.get('handicap', 0):<2} -> "
                  f"{row['result'] or row['winner']:>8s}"
                  f"  {row['moves']:3d} moves  {row['seconds']:6.1f}s"
                  f"  | elapsed {el/60:5.1f}m  eta {eta/60:5.1f}m"
                  + (f"  ERR {row['error']}" if row["error"] else ""),
                  flush=True)
            if len(buffer) >= 5:
                append_rows(buffer, lock)
                buffer = []
    if buffer:
        append_rows(buffer, lock)
    print(f"\ndone in {(time.time()-t_start)/60:.1f} min", flush=True)
    report()


# ------------------------------------------------------------------- report

def report() -> None:
    if not os.path.exists(RESULTS_CSV):
        print("no results yet")
        return
    with open(RESULTS_CSV, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)]

    ok = [r for r in rows if r["winner"] in ("B", "W")]
    print(f"\n{'='*74}\nCALIBRATION REPORT   games={len(rows)}  usable={len(ok)}\n{'='*74}")

    # per pairing: how often does the nominally STRONGER profile win?
    def strength_key(p: str):
        # "preaz_5k" / "rank_3d" -> numeric strength, higher = stronger
        base = 0 if p.startswith("preaz") else 100
        core = p.split("_", 1)[1]
        if core.endswith("k"):
            return base + (30 - int(core[:-1]))
        return base + 30 + int(core[:-1])

    from collections import defaultdict
    pair_stats = defaultdict(lambda: [0, 0])  # key -> [stronger_wins, total]
    for r in ok:
        b, w = r["black_profile"], r["white_profile"]
        if b == w:
            continue
        sb, sw = strength_key(b), strength_key(w)
        if sb == sw:
            continue
        stronger, weaker = (b, w) if sb > sw else (w, b)
        winner = r["winner"]
        key = (weaker, stronger)
        pair_stats[key][1] += 1
        if winner == stronger:
            pair_stats[key][0] += 1

    print(f"{'weaker':>12s} vs {'stronger':<12s}  {'stronger wins':>14s}  "
          f"{'win%':>6s}   {'Elo gap':>8s}")
    for (weaker, stronger), (win, tot) in sorted(pair_stats.items()):
        p = win / tot if tot else 0
        p = min(max(p, 1e-6), 1 - 1e-6)
        gap = 400 * math.log10(p / (1 - p))
        print(f"{weaker:>12s} vs {stronger:<12s}  {win:6d}/{tot:<7d}  "
              f"{100*p:5.1f}%   {gap:+8.1f}")

    # overall win rate per profile
    print()
    print("per-profile record (as either colour):")
    rec = defaultdict(lambda: [0, 0])
    for r in ok:
        for who, won in ((r["black_profile"], r["winner"] == "B"),
                         (r["white_profile"], r["winner"] == "W")):
            rec[who][1] += 1
            if won:
                rec[who][0] += 1
    for prof, (w_, t) in sorted(rec.items(), key=lambda kv: -kv[1][0] / max(kv[1][1], 1)):
        print(f"  {prof:>12s}  {w_:3d}/{t:<3d}  {100*w_/max(t,1):5.1f}%")

    errs = [r for r in rows if r["error"]]
    if errs:
        print(f"\n{len(errs)} games had errors; last few:")
        for r in errs[-5:]:
            print("  ", r["black_profile"], "vs", r["white_profile"], "|", r["error"])

    # ---------------- handicap sweep ----------------
    handi = [r for r in ok if str(r.get("handicap", "0")) not in ("", "0")]
    if handi:
        print()
        print("=" * 74)
        print("HANDICAP SWEEP  (Black receives stones, White moves first)")
        print("=" * 74)
        print("The handicap at which the nominally weaker player reaches 50% is")
        print("the strength gap in stones.")
        print()
        print(f"{'pair (weaker as Black)':<26} {'H':>3} {'games':>6} "
              f"{'weaker wins':>12} {'win%':>7}")
        by_pair: dict[tuple, dict[int, list[int]]] = {}
        for r in handi:
            key = (r["black_profile"], r["white_profile"])
            h = int(r["handicap"])
            by_pair.setdefault(key, {}).setdefault(h, [0, 0])
            slot = by_pair[key][h]
            slot[1] += 1
            if r["winner"] == "B":      # Black is the weaker side here
                slot[0] += 1
        for key in sorted(by_pair):
            for h in sorted(by_pair[key]):
                w_, t = by_pair[key][h]
                pct = 100 * w_ / t if t else 0
                print(f"{key[0] + ' vs ' + key[1]:<26} {h:>3} {t:>6} "
                      f"{w_:>12} {pct:>6.1f}%")
            # crude even-point estimate by linear interpolation
            pts = [(h, by_pair[key][h][0] / by_pair[key][h][1])
                   for h in sorted(by_pair[key]) if by_pair[key][h][1]]
            even = None
            for (h1, p1), (h2, p2) in zip(pts, pts[1:]):
                if (p1 - 0.5) * (p2 - 0.5) <= 0 and p2 != p1:
                    even = h1 + (0.5 - p1) * (h2 - h1) / (p2 - p1)
                    break
            if even is not None:
                print(f"{'':<26} {'':>3} {'':>6} {'':>12}    -> 平衡点约 H={even:.1f} 子")
            else:
                print(f"{'':<26} 平衡点在已测范围内未出现（需扩大让子范围）")
            print()

    secs = [float(r["seconds"]) for r in ok if r["seconds"]]
    if secs:
        print(f"\ngame duration: avg {sum(secs)/len(secs)/60:.1f} min  "
              f"min {min(secs)/60:.1f}  max {max(secs)/60:.1f}  (n={len(secs)})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="ladder")
    ap.add_argument("--games", type=int, default=2)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--visits", type=int, default=40)
    ap.add_argument("--komi", type=float, default=7.5)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if args.report:
        report()
        return
    run(args.plan, args.games, args.workers, args.visits, args.komi, args.seed)


if __name__ == "__main__":
    main()
