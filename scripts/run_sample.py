"""End-to-end sample: play a game, analyse every turn, render an explanation.

Produces:
    samples/<name>.sgf            the game
    samples/<name>.report.txt     the explanation report
    samples/<name>.facts.json     the raw fact sheet
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis import KataGoAnalysis  # noqa: E402
from explain import (build_turn_facts, explain_priority,  # noqa: E402
                     fill_missing_losses, fmt_move, render_report)
from goban import GTP_LETTERS, sgf_coord  # noqa: E402
from katago_engine import KataGoEngine  # noqa: E402

SAMPLES = os.path.join(ROOT, "samples")
os.makedirs(SAMPLES, exist_ok=True)

FAST = {"delayMoveScale": "0.0", "delayMoveMax": "0.0",
        "logToStderr": "false", "logSearchInfo": "false"}


def play_game(black_profile: str, white_profile: str, max_moves: int = 320,
              visits: int = 40) -> tuple[list[list[str]], str]:
    """Play one game with delays off. Returns (moves, result)."""
    print(f"playing {black_profile} (B) vs {white_profile} (W) ...", flush=True)
    b = KataGoEngine(profile=black_profile, visits=visits, rules="chinese",
                     extra_overrides=FAST)
    w = KataGoEngine(profile=white_profile, visits=visits, rules="chinese",
                     extra_overrides=FAST)
    b.start()
    w.start()
    moves: list[list[str]] = []
    result = ""
    try:
        color = "B"
        passes = 0
        t0 = time.time()
        for i in range(max_moves):
            eng, other = (b, w) if color == "B" else (w, b)
            mv = eng.genmove(color, timeout=240)
            low = mv.strip().lower()
            if low == "resign":
                result = ("W" if color == "B" else "B") + "+R"
                break
            moves.append([color, mv])
            passes = passes + 1 if low == "pass" else 0
            if passes >= 2:
                try:
                    result = b.final_score()
                except Exception:
                    result = "?"
                break
            other.play(color, mv)
            color = "W" if color == "B" else "B"
            if (i + 1) % 50 == 0:
                print(f"  {i+1} moves, {time.time()-t0:.0f}s", flush=True)
    finally:
        b.close()
        w.close()
    print(f"  game over: {len(moves)} moves, result {result}", flush=True)
    return moves, result


def save_sgf(path: str, moves: list[list[str]], black: str, white: str,
             komi: float = 7.5, result: str = "") -> None:
    parts = ["(;GM[1]FF[4]CA[UTF-8]AP[weiqi-explain:0.1]",
             "SZ[19]", f"KM[{komi}]", f"PB[{black}]", f"PW[{white}]"]
    if result:
        parts.append(f"RE[{result}]")
    for color, mv in moves:
        if mv.lower() == "pass":
            parts.append(f";{color}[]")
        else:
            from goban import from_gtp
            pos = from_gtp(mv, 19)
            parts.append(f";{color}[{sgf_coord(*pos)}]" if pos else "")
    parts.append(")")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="sample1")
    ap.add_argument("--black", default="preaz_5k")
    ap.add_argument("--white", default="preaz_10k")
    ap.add_argument("--student", default="preaz_10k",
                    help="rank profile used to model the student's choices")
    ap.add_argument("--visits", type=int, default=400,
                    help="analysis visits per turn")
    ap.add_argument("--max-analyze", type=int, default=0,
                    help="only analyse the first N turns (0 = all)")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--reuse", default="", help="reuse an existing sgf/prefix")
    args = ap.parse_args()

    prefix = os.path.join(SAMPLES, args.name)
    sgf_path = prefix + ".sgf"

    if args.reuse and os.path.exists(args.reuse):
        from goban import from_sgf_coord
        text = open(args.reuse, encoding="utf-8").read()
        import re
        moves = []
        for m in re.finditer(r";([BW])\[([a-s]{0,2})\]", text):
            color, coord = m.group(1), m.group(2)
            if coord:
                x, y = from_sgf_coord(coord)
                moves.append([color, f"{GTP_LETTERS[x]}{19-y}"])
            else:
                moves.append([color, "pass"])
        result = ""
        rm = re.search(r"RE\[([^\]]*)\]", text)
        if rm:
            result = rm.group(1)
        print(f"reused {args.reuse}: {len(moves)} moves")
    else:
        moves, result = play_game(args.black, args.white)
        save_sgf(sgf_path, moves, args.black, args.white, result=result)
        print(f"saved {sgf_path}")

    if not moves:
        print("no moves to analyse")
        return

    turns = list(range(len(moves)))
    if args.max_analyze:
        turns = turns[:args.max_analyze]

    print()
    print(f"analysing {len(turns)} turns at {args.visits} visits, "
          f"student profile {args.student} ...", flush=True)
    a = KataGoAnalysis()
    a.start()
    print("  engine:", a.version, flush=True)

    t0 = time.time()
    results = a.analyze_game(
        moves,
        profile_for_turn=[args.student] * len(moves),
        turns=turns,
        max_visits=args.visits,
    )
    print(f"  analysis done in {time.time()-t0:.0f}s "
          f"({(time.time()-t0)/max(len(turns),1):.1f}s per turn)", flush=True)

    facts = []
    for t in sorted(results):
        if t >= len(moves):
            continue
        color, mv = moves[t]
        f = build_turn_facts(t, results[t], mv, args.student)
        if f["mover"] != color:
            f["mover_mismatch"] = f"engine says {f['mover']}, sgf says {color}"
        facts.append(f)
    fill_missing_losses(facts, results)
    a.close()

    meta = {
        "black_name": args.black, "white_name": args.white,
        "student_profile": args.student, "version": a.version,
        "komi": 7.5, "rules": "chinese",
    }
    report = render_report(facts, meta, top_n=args.top)

    rpath = prefix + ".report.txt"
    with open(rpath, "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(prefix + ".facts.json", "w", encoding="utf-8") as fh:
        json.dump(facts, fh, ensure_ascii=False, indent=1)

    print()
    print(report)
    print()
    print(f"report -> {rpath}")
    print(f"facts  -> {prefix}.facts.json")


if __name__ == "__main__":
    main()
