"""
Tests for the logic added to the upstream template.

These run without API keys, network access, or the forecasting-tools package —
the point is to prove our own code is correct before it touches a live
question. The template's own code is not under test here.

Run:  python3 test_phase1.py
"""

import ast
import pathlib
import re as _re
import sys
import types


def load(*names):
    """Load named top-level functions out of main.py without importing it.

    Importing main.py would pull in forecasting-tools and require credentials,
    so we lift the functions we wrote and give them a stub namespace.
    """
    source = pathlib.Path(__file__).with_name("main.py").read_text()
    tree = ast.parse(source)
    module = types.ModuleType("harness")
    module.re = _re
    module.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None
    )
    module.BINARY_FLOOR, module.BINARY_CEILING = 0.02, 0.98
    module.AMBIGUOUS_FLOOR, module.AMBIGUOUS_CEILING = 0.10, 0.90
    module.MC_OPTION_FLOOR = 0.01

    wanted = set(names)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            code = compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec")
            exec(code, module.__dict__)
            found[node.name] = module.__dict__[node.name]
    missing = wanted - set(found)
    if missing:
        raise AssertionError(f"not found in main.py — patch did not apply: {sorted(missing)}")
    return [found[n] for n in names]


class P:
    """Stand-in for forecasting_tools.Percentile."""

    def __init__(self, percentile, value):
        self.percentile, self.value = percentile, value

    def __repr__(self):
        return f"P({self.percentile}, {self.value})"


def raises(fn, *args):
    try:
        fn(*args)
        return False
    except Exception:
        return True


def run():
    sorted_percentiles, caps, floor_mc = load(
        "_sorted_percentiles", "caps_for_reasoning", "floor_and_renormalise"
    )
    failures = []

    def check(label, got, expected):
        if got == expected:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label}\n        expected {expected}\n        got      {got}")
            failures.append(label)

    def approx(label, got, expected, tol=1e-9):
        ok = len(got) == len(expected) and all(
            abs(a - b) < tol for a, b in zip(got, expected)
        )
        check(label, True, True) if ok else check(label, got, expected)

    vals = lambda r: [p.value for p in r]
    pcts = lambda r: [p.percentile for p in r]

    print("  -- numeric percentiles --")
    clean = [P(0.1, 10), P(0.5, 20), P(0.9, 30)]
    check("well-formed input is unchanged", vals(sorted_percentiles(clean)), [10, 20, 30])

    shuffled = [P(0.9, 30), P(0.1, 10), P(0.5, 20)]
    check("out-of-order percentiles are sorted", pcts(sorted_percentiles(shuffled)), [0.1, 0.5, 0.9])
    check("values follow their percentiles", vals(sorted_percentiles(shuffled)), [10, 20, 30])

    # These two previously asserted that a corrupt sample was REPAIRED into a
    # monotonic one. An audit showed the repair published a near point-mass at
    # the wrong end of the range — worse than failing. Rejecting discards one
    # sample and keeps the others, which is what the library does natively.
    check("decreasing values are REJECTED, not repaired",
          raises(sorted_percentiles, [P(0.1, 50), P(0.5, 20), P(0.9, 80)]), True)
    check("fully reversed values are REJECTED",
          raises(sorted_percentiles, [P(0.1, 90), P(0.5, 50), P(0.9, 10)]), True)

    check("equal values are legitimate and pass", vals(sorted_percentiles([P(0.1, 7), P(0.5, 7), P(0.9, 7)])), [7, 7, 7])
    check("negative values are preserved", vals(sorted_percentiles([P(0.1, -30), P(0.5, -10), P(0.9, 5)])), [-30, -10, 5])
    check("single element survives", vals(sorted_percentiles([P(0.5, 1)])), [1])
    check("empty list survives", vals(sorted_percentiles([])), [])

    print("\n  -- ambiguity-bounded caps --")
    NORMAL, TIGHT = (0.02, 0.98), (0.10, 0.90)
    check("no flag falls back to normal caps", caps("some reasoning"), NORMAL)
    check("empty reasoning is safe", caps(""), NORMAL)
    check("None reasoning is safe", caps(None), NORMAL)
    check("AMBIGUITY: LOW gives normal caps", caps("blah\nAMBIGUITY: LOW\nblah"), NORMAL)
    check("AMBIGUITY: HIGH tightens the caps", caps("blah\nAMBIGUITY: HIGH\nblah"), TIGHT)
    check("lower case is accepted", caps("ambiguity: high"), TIGHT)
    check("odd spacing is accepted", caps("AMBIGUITY  :   HIGH"), TIGHT)
    check("contradictory HIGH and LOW falls back to normal",
          caps("AMBIGUITY: HIGH ... later ... AMBIGUITY: LOW"), NORMAL)
    check("the bare word does not trigger", caps("there is ambiguity here"), NORMAL)
    check("'not ambiguous' does not trigger", caps("this is not ambiguous at all"), NORMAL)

    print("\n  -- multiple-choice floor --")
    # A 0% option that resolves scores about -691. Flooring costs ~1%.
    out = floor_mc([("a", 0.0), ("b", 1.0)])
    check("a 0% option is lifted off the floor", all(p >= 0.01 for _, p in out), True)
    approx("probabilities still sum to 1", [round(sum(p for _, p in out), 9)], [1.0])
    check("option names are preserved in order", [n for n, _ in out], ["a", "b"])

    out = floor_mc([("a", 0.25), ("b", 0.25), ("c", 0.25), ("d", 0.25)])
    approx("an already-valid distribution is left alone", [p for _, p in out], [0.25] * 4)

    out = floor_mc([("a", 0.0), ("b", 0.0), ("c", 1.0)])
    approx("sum is 1 after flooring several zeros", [round(sum(p for _, p in out), 9)], [1.0])
    check("the confident option stays the largest", max(out, key=lambda x: x[1])[0], "c")

    out = floor_mc([("a", 0.0), ("b", 0.0)])
    approx("all-zero input becomes uniform, not a divide-by-zero", [p for _, p in out], [0.5, 0.5])

    check("empty list survives", floor_mc([]), [])

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
