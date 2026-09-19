"""Why is trainer.py not bundled? Diagnose PyInstaller's module graph for it."""
import importlib.util
import sys

sys.path.insert(0, r"F:\harness\baduk-trainer")

print("=== importlib can find trainer ===")
spec = importlib.util.find_spec("trainer")
print("  find_spec(trainer) ->", spec.origin if spec else "None")

spec = importlib.util.find_spec("library")
print("  find_spec(library) ->", spec.origin if spec else "None")

print()
print("=== import them ===")
try:
    import trainer
    print("  trainer OK:", trainer.__file__)
except Exception as e:
    import traceback
    traceback.print_exc()

try:
    import library
    print("  library OK:", library.__file__)
except Exception:
    import traceback
    traceback.print_exc()

print()
print("=== PyInstaller modulegraph for main.py ===")
try:
    from PyInstaller.modulegraph import ModuleGraph
    g = ModuleGraph([r"F:\harness\baduk-trainer"])
    g.run_script(r"F:\harness\baduk-trainer\main.py")
    nodes = set(g.flatten())
    for want in ("trainer", "library", "analysis", "goban", "explain",
                 "katago_engine", "selftest", "app", "study", "main"):
        present = want in nodes
        print(f"  {'FOUND' if present else 'MISSING'}  {want}")
    if not present:
        pass
    # show any nodes containing 'trainer'
    tnodes = [n for n in nodes if "trainer" in str(n)]
    print("  trainer-ish nodes:", tnodes[:5])
except Exception:
    import traceback
    traceback.print_exc()
print("DONE")
