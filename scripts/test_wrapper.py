"""Smoke-test the KataGoEngine wrapper: a short headless game plus a rank switch."""
import os
import sys
import time

sys.path.insert(0, r"F:\harness\baduk-trainer")
from katago_engine import KataGoEngine, ALL_PROFILES  # noqa: E402


def short_game(profile: str, moves: int = 6, visits: int = 40, rules: str = "chinese"):
    print("=" * 66)
    print(f"profile={profile}  visits={visits}  rules={rules}")
    print("=" * 66, flush=True)
    eng = KataGoEngine(profile=profile, visits=visits, rules=rules)
    t0 = time.time()
    eng.start()
    print(f"  start: {time.time()-t0:.1f}s   alive={eng.alive}", flush=True)

    seq = []
    color = "B"
    times = []
    try:
        for i in range(moves):
            t = time.time()
            mv = eng.genmove(color)
            dt = time.time() - t
            times.append(dt)
            seq.append(f"{color}:{mv}")
            print(f"  {i+1:2d}. {color} {mv:5s}  {dt:5.2f}s", flush=True)
            color = "W" if color == "B" else "B"
    finally:
        eng.close()

    print(f"  moves: {' '.join(seq)}")
    print(f"  genmove avg: {sum(times)/len(times):.2f}s  max: {max(times):.2f}s")
    return seq


print("engine dir ok:", os.path.exists(r"F:\harness\baduk-trainer\engine\katago.exe"))
print("profiles available:", len(ALL_PROFILES))
print()

a = short_game("rank_5k", moves=6)
print()
b = short_game("preaz_15k", moves=6)
print()
c = short_game("rank_3d", moves=6)
print()

print("=" * 66)
print("OPENING COMPARISON (first 3 moves each)")
print(f"  rank_5k   : {' '.join(a[:3])}")
print(f"  preaz_15k : {' '.join(b[:3])}")
print(f"  rank_3d   : {' '.join(c[:3])}")

same = len(set([tuple(a[:3]), tuple(b[:3]), tuple(c[:3])]))
print(f"  distinct openings: {same}/3  -> "
      f"{'profiles differ OK' if same > 1 else 'WARNING: profiles produce identical play'}")

print()
print("=== rank switching on one engine object ===")
eng = KataGoEngine(profile="rank_10k", visits=20)
eng.start()
print("  started with", eng.profile)
mv1 = eng.genmove("B")
print("  move at rank_10k:", mv1)
eng.set_profile("rank_1k")          # triggers restart
print("  switched to", eng.profile, "alive=", eng.alive)
mv2 = eng.genmove("B")
print("  move at rank_1k :", mv2)
eng.close()
print("  closed")
print()
print("WRAPPER TEST DONE")
