"""Fast smoke test of the explanation pipeline on a hand-made game.

No game generation, just analysis -> facts -> report, so it validates
build_turn_facts / render_report quickly.
"""
import json
import os
import sys

sys.path.insert(0, r"F:\harness\baduk-trainer")
from analysis import KataGoAnalysis  # noqa: E402
from explain import (build_turn_facts, explain_priority,  # noqa: E402
                     fill_missing_losses, render_report)

# A normal opening, then Black plays a deliberately awful move (A1) so we should
# see the pipeline catch a real mistake.
MOVES = [
    ["B", "Q16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"],
    ["B", "R14"], ["W", "C6"], ["B", "A1"],            # <- blunder
    ["W", "K10"], ["B", "Q18"], ["W", "F3"],
    ["B", "D10"], ["W", "R6"], ["B", "C14"], ["W", "J4"],
]

STUDENT = "preaz_10k"

a = KataGoAnalysis()
a.start()
print("engine:", a.version, flush=True)

results = a.analyze_game(MOVES, profile_for_turn=[STUDENT] * len(MOVES),
                         turns=list(range(len(MOVES))), max_visits=400)
a.close()

facts = []
for t in sorted(results):
    color, mv = MOVES[t]
    f = build_turn_facts(t, results[t], mv, STUDENT)
    facts.append(f)
fill_missing_losses(facts, results)

print()
print("=== raw signals per turn ===")
print(f"{'turn':>4} {'mv':>6} {'mover':>5} {'best':>6} {'loss':>6} {'surp':>6} "
      f"{'KL':>5} {'hTop':>6} {'div':>4} {'prio':>5}")
for f in facts:
    loss = f"{f['loss']:.1f}" if f.get("loss") is not None else "-"
    surp = f"{f['surprisal_bits']:.1f}" if f.get("surprisal_bits") is not None else "-"
    kl = f"{f['kl_human_ai']:.2f}" if f.get("kl_human_ai") is not None else "-"
    ht = f["human_top"][0]["move"] if f.get("human_top") else "-"
    print(f"{f['move_number']:>4} {f['played']:>6} {f['mover']:>5} "
          f"{str(f.get('best_move')):>6} {loss:>6} {surp:>6} {kl:>5} {ht:>6} "
          f"{'Y' if f.get('reasoning_divergence') else '.':>4} "
          f"{explain_priority(f):>5.2f}")

print()
print("=== sanity checks ===")
f_blunder = facts[6]      # move 7 = B A1
print(f"  blunder turn: played={f_blunder['played']} best={f_blunder['best_move']} "
      f"loss={f_blunder.get('loss')}")
assert f_blunder["played"] == "A1", f_blunder["played"]
assert f_blunder.get("loss") is not None, "blunder should have a measurable loss"
assert f_blunder["loss"] > 5, f"blunder loss too small: {f_blunder['loss']}"
print("  OK: the deliberate blunder is detected with a large loss")

has_human = sum(1 for f in facts if f.get("human_p_played") is not None)
has_kl = sum(1 for f in facts if f.get("kl_human_ai") is not None)
has_reason = sum(1 for f in facts if f.get("reasoning_divergence"))
has_own = sum(1 for f in facts if f.get("ownership_delta_regions"))
print(f"  turns with human policy : {has_human}/{len(facts)}")
print(f"  turns with human-vs-AI KL: {has_kl}/{len(facts)}")
print(f"  turns with reasoning divergence: {has_reason}/{len(facts)}")
print(f"  turns with ownership delta: {has_own}/{len(facts)}")
assert has_human == len(facts), "human policy missing on some turns"
assert has_own == len(facts), "ownership delta missing on some turns"

meta = {"black_name": "handmade", "white_name": "handmade",
        "student_profile": STUDENT, "version": a.version,
        "komi": 7.5, "rules": "chinese"}
report = render_report(facts, meta, top_n=5)
print()
print(report)

out = r"F:\harness\baduk-trainer\samples\smoke.report.txt"
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    fh.write(report)
print(f"\nwritten -> {out}")
print("\nSMOKE TEST PASSED")
