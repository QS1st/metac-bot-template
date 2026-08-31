"""
Tests for the Phase 1 logic we added to the template.

These run without any API keys, network access, or the forecasting-tools
package — the point is to prove our own code is correct before it ever touches
a live question. The template's own code is not under test here.

Run:  python3 test_phase1.py
"""

import ast
import pathlib
import sys
import types


# ---------------------------------------------------------------------------
# Load just our helper out of main.py, without importing the whole module
# (which would pull in forecasting-tools and need credentials).
# ---------------------------------------------------------------------------
def load_helper():
    source = pathlib.Path(__file__).with_name("main.py").read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_sorted_percentiles":
            module = types.ModuleType("harness")
            module.logger = types.SimpleNamespace(warning=lambda *a, **k: None)
            code = compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec")
            exec(code, module.__dict__)
            return module._sorted_percentiles
    raise AssertionError("_sorted_percentiles not found in main.py — patch did not apply")


class P:
    """Stand-in for forecasting_tools.Percentile."""

    def __init__(self, percentile, value):
        self.percentile = percentile
        self.value = value

    def __repr__(self):
        return f"P({self.percentile}, {self.value})"


def values(result):
    return [p.value for p in result]


def percentiles(result):
    return [p.percentile for p in result]


def load_caps():
    """Load caps_for_reasoning out of main.py without importing the module."""
    import re as _re

    source = pathlib.Path(__file__).with_name("main.py").read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "caps_for_reasoning":
            module = types.ModuleType("harness")
            module.re = _re
            module.logger = types.SimpleNamespace(
                info=lambda *a, **k: None, warning=lambda *a, **k: None
            )
            module.BINARY_FLOOR, module.BINARY_CEILING = 0.02, 0.98
            module.AMBIGUOUS_FLOOR, module.AMBIGUOUS_CEILING = 0.10, 0.90
            code = compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec")
            exec(code, module.__dict__)
            return module.caps_for_reasoning
    raise AssertionError("caps_for_reasoning not found in main.py — patch did not apply")


def run_caps_tests(check):
    caps = load_caps()
    NORMAL = (0.02, 0.98)
    TIGHT = (0.10, 0.90)

    check("no flag at all falls back to normal caps", caps("some reasoning"), NORMAL)
    check("empty reasoning is safe", caps(""), NORMAL)
    check("None reasoning is safe", caps(None), NORMAL)
    check("AMBIGUITY: LOW gives normal caps", caps("blah\nAMBIGUITY: LOW\nblah"), NORMAL)
    check("AMBIGUITY: HIGH tightens the caps", caps("blah\nAMBIGUITY: HIGH\nblah"), TIGHT)
    check("lower case is accepted", caps("ambiguity: high"), TIGHT)
    check("odd spacing is accepted", caps("AMBIGUITY  :   HIGH"), TIGHT)
    check("flag mid-sentence still counts", caps("I judge AMBIGUITY: HIGH here."), TIGHT)

    # Fail-safe: a contradictory model must never end up MORE confident.
    check(
        "contradictory HIGH and LOW falls back to normal, not tighter",
        caps("AMBIGUITY: HIGH ... later ... AMBIGUITY: LOW"),
        NORMAL,
    )
    # Near-misses must not trigger: the word alone is not the flag.
    check("the word 'ambiguity' alone does not trigger", caps("there is ambiguity here"), NORMAL)
    check("'not ambiguous' does not trigger", caps("this is not ambiguous at all"), NORMAL)


def run():
    sorted_percentiles = load_helper()
    failures = []

    def check(label, got, expected):
        if got == expected:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label}\n        expected {expected}\n        got      {got}")
            failures.append(label)

    # 1. Already well-formed input must pass through untouched.
    clean = [P(0.1, 10), P(0.5, 20), P(0.9, 30)]
    out = sorted_percentiles(clean)
    check("well-formed input is unchanged", values(out), [10, 20, 30])

    # 2. Percentiles arriving out of order get reordered by percentile.
    shuffled = [P(0.9, 30), P(0.1, 10), P(0.5, 20)]
    out = sorted_percentiles(shuffled)
    check("out-of-order percentiles are sorted", percentiles(out), [0.1, 0.5, 0.9])
    check("out-of-order values follow their percentiles", values(out), [10, 20, 30])

    # 3. The real failure mode: values that decrease as the percentile rises.
    #    This is a malformed distribution and must be repaired, not passed on.
    broken = [P(0.1, 50), P(0.5, 20), P(0.9, 80)]
    out = sorted_percentiles(broken)
    check("decreasing values are forced monotonic", values(out), [50, 50, 80])

    # 4. Severe case — fully reversed.
    reversed_vals = [P(0.1, 90), P(0.5, 50), P(0.9, 10)]
    out = sorted_percentiles(reversed_vals)
    check("fully reversed values collapse to a flat floor", values(out), [90, 90, 90])

    # 5. Equal values are legitimate and must not be treated as an error.
    flat = [P(0.1, 7), P(0.5, 7), P(0.9, 7)]
    out = sorted_percentiles(flat)
    check("equal values are left alone", values(out), [7, 7, 7])

    # 6. Negative values must survive — plenty of Metaculus questions are
    #    signed (temperature anomalies, net changes, deficits).
    signed = [P(0.1, -30), P(0.5, -10), P(0.9, 5)]
    out = sorted_percentiles(signed)
    check("negative values are preserved", values(out), [-30, -10, 5])

    # 7. Single-element and empty lists must not raise.
    check("single element survives", values(sorted_percentiles([P(0.5, 1)])), [1])
    check("empty list survives", values(sorted_percentiles([])), [])

    print("\n  -- ambiguity-bounded caps --")
    run_caps_tests(check)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
