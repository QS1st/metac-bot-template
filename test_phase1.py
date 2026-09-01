"""
Tests for the logic added to the upstream template.

These run without API keys, network access, or the forecasting-tools package —
the point is to prove our own code is correct before it touches a live
question. The template's own code is not under test here.

Run:  python3 test_phase1.py
"""

import ast
import os as _os
import pathlib
import re as _re
import sys
import types
from datetime import datetime as _datetime, timedelta, timezone as _timezone


def load(*names, consts=()):
    """Load named top-level functions out of main.py without importing it.

    Importing main.py would pull in forecasting-tools and require credentials,
    so we lift the functions we wrote and give them a stub namespace.

    `consts` lifts module-level constants out of main.py rather than restating
    their values here. That matters: a test that hard-codes what it expects the
    code to say can agree with itself while disagreeing with the file, which is
    exactly how the old _sorted_percentiles test asserted the wrong behaviour.
    """
    source = pathlib.Path(__file__).with_name("main.py").read_text()
    tree = ast.parse(source)
    module = types.ModuleType("harness")
    module.re = _re
    module.os = _os
    module.datetime, module.timezone = _datetime, _timezone
    module.logger = types.SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )
    module.BINARY_FLOOR, module.BINARY_CEILING = 0.02, 0.98
    module.AMBIGUOUS_FLOOR, module.AMBIGUOUS_CEILING = 0.10, 0.90

    wanted = set(names)
    wanted_consts = set(consts)
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if wanted_consts.intersection(targets):
                code = compile(
                    ast.Module(body=[node], type_ignores=[]), "main.py", "exec"
                )
                exec(code, module.__dict__)
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            code = compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec")
            exec(code, module.__dict__)
            found[node.name] = module.__dict__[node.name]
    missing = wanted - set(found)
    if missing:
        raise AssertionError(f"not found in main.py — patch did not apply: {sorted(missing)}")
    missing_consts = wanted_consts - set(module.__dict__)
    if missing_consts:
        raise AssertionError(
            f"constants not found in main.py — patch did not apply: {sorted(missing_consts)}"
        )
    return [found[n] for n in names] + [module]


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
    sorted_percentiles, caps, _ = load("_sorted_percentiles", "caps_for_reasoning")
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
    # CHANGED 1 Sept 2026 after audit. Both flags on ONE line is prose, not an
    # answer, so it must not tighten — but the old code also fell back whenever
    # the two strings appeared anywhere, which is what the model does when it
    # restates the instruction. See the two cases below.
    check("both flags inline, no answer line, stays normal",
          caps("AMBIGUITY: HIGH ... later ... AMBIGUITY: LOW"), NORMAL)
    check("restating the instruction then answering HIGH tightens",
          caps("I must output either AMBIGUITY: LOW or AMBIGUITY: HIGH.\n"
               "Reasoning here.\nAMBIGUITY: HIGH"), TIGHT)
    check("restating the instruction then answering LOW stays normal",
          caps("Options are AMBIGUITY: LOW or AMBIGUITY: HIGH.\n"
               "Reasoning.\nAMBIGUITY: LOW"), NORMAL)
    check("the LAST flag line wins, not the first",
          caps("AMBIGUITY: LOW\nOn reflection:\nAMBIGUITY: HIGH"), TIGHT)
    check("indented flag lines still count",
          caps("blah\n    AMBIGUITY: HIGH  "), TIGHT)
    check("a flag buried mid-sentence is not an answer",
          caps("the AMBIGUITY: HIGH marker would go here"), NORMAL)
    check("the bare word does not trigger", caps("there is ambiguity here"), NORMAL)
    check("'not ambiguous' does not trigger", caps("this is not ambiguous at all"), NORMAL)

    print("\n  -- multiple-choice floor: REMOVED, and why --")
    # The floor_and_renormalise tests used to live here. They passed while the
    # code did nothing, because they fed (name, probability) tuples straight in
    # and the SDK never sees data in that form. PredictedOptionList's validator
    # already clamps to [0.01, 0.99] on construction. Deleting the tests with
    # the code, and asserting the absence, so it cannot quietly return.
    check("floor_and_renormalise is gone from main.py",
          "def floor_and_renormalise" in pathlib.Path(__file__).with_name("main.py").read_text(),
          False)

    print("\n  -- the trial tier --")
    mods = load(consts=("TRIAL_MODELS", "SEASON_MODELS", "TOURNAMENT_READY_TIERS",
                        "VALID_MODEL_TIERS"))[-1]
    check("trial is a valid tier", "trial" in mods.VALID_MODEL_TIERS, True)
    check("trial may forecast a scored tournament", "trial" in mods.TOURNAMENT_READY_TIERS, True)
    # The whole point of the guard: cheap tiers must never reach a scored run.
    check("test may NOT", "test" in mods.TOURNAMENT_READY_TIERS, False)
    check("free may NOT", "free" in mods.TOURNAMENT_READY_TIERS, False)
    check("every tournament-ready tier is a real tier",
          all(t in mods.VALID_MODEL_TIERS for t in mods.TOURNAMENT_READY_TIERS), True)
    check("trial keeps live research", mods.TRIAL_MODELS.get("researcher"),
          mods.SEASON_MODELS.get("researcher"))
    check("trial defines every role",
          sorted(mods.TRIAL_MODELS) == sorted(mods.SEASON_MODELS), True)
    check("trial is genuinely cheaper, not a copy of season",
          mods.TRIAL_MODELS["default"] != mods.SEASON_MODELS["default"], True)
    check("trial routes through OpenRouter like the rest",
          all(m.startswith("openrouter/") for m in mods.TRIAL_MODELS.values()), True)

    print("\n  -- default-model rate limiting --")
    # Structural checks, not behavioural ones. The thing that cost us a run was
    # a call site nobody was pacing, so what needs guarding is the invariant
    # "every default-model call goes through the gate" — which is a property of
    # the file, and stays true only if something keeps checking it.
    src = pathlib.Path(__file__).with_name("main.py").read_text()
    mods2 = load(consts=("PER_MODEL_RPM", "PER_MODEL_BURST", "PARSER_ALLOWED_TRIES",
                         "STRUCTURE_OUTPUT_ALLOWED_TRIES"))[-1]
    ungated = _re.findall(
        r"\n {8}\w[\w.]* = await self\.get_llm\(\"default\", \"llm\"\)\.invoke", src
    )
    check("no un-gated default-model call sites remain", ungated, [])
    check(
        "all four prompt paths go through the gate",
        len(_re.findall(r"await self\._invoke_default_llm\(prompt\)", src)),
        4,
    )
    check(
        "the gate acquires BEFORE it invokes",
        bool(
            _re.search(
                r"wait_till_able_to_acquire_resources\(1\)\s*\n\s*return await self\.get_llm",
                src,
            )
        ),
        True,
    )
    check(
        "both limiters are built from the named constants, not loose numbers",
        len(
            _re.findall(
                r"capacity=PER_MODEL_BURST,\s*refresh_rate=PER_MODEL_RPM / 60", src
            )
        ),
        2,
    )
    # capacity IS the burst size. At capacity=PER_MODEL_RPM the library fires a
    # whole minute's allowance in one instant and then stalls 60s — the exact
    # burst shape that triggered OpenRouter's throttle. Simulated in the 1 Sept
    # audit. Capacity 1 means one request per interval and no burst at all.
    check("the bucket cannot burst", mods2.PER_MODEL_BURST, 1)
    # Parsing runs once per prediction, so it is the same volume as forecasting.
    # Gating only the default model is what let the first trial run fail.
    check(
        "parser calls are gated too, at all four sites",
        len(_re.findall(r"= await self\._structure_output_paced\(", src)),
        4,
    )
    check(
        "no un-gated structure_output call sites remain",
        _re.findall(r"= await structure_output\(", src),
        [],
    )
    check(
        "the parser gate acquires BEFORE it parses",
        bool(
            _re.search(
                r"_parser_model_limiter\.wait_till_able_to_acquire_resources\(1\)[\s\S]{0,600}?return await structure_output\(",
                src,
            )
        ),
        True,
    )
    # Retries live BELOW the gate and never re-acquire, so the limiter cannot
    # see them. These two constants are the only thing bounding the multiplier.
    check("structure_output's own retry loop is pinned, not inherited",
          bool(_re.search(r'kwargs\.setdefault\("allowed_tries", STRUCTURE_OUTPUT_ALLOWED_TRIES\)', src)), True)
    check("the parser is a GeneralLlm so allowed_tries can be set",
          bool(_re.search(r"allowed_tries=PARSER_ALLOWED_TRIES", src)), True)
    worst_case = mods2.PARSER_ALLOWED_TRIES * mods2.STRUCTURE_OUTPUT_ALLOWED_TRIES
    check("worst-case wire requests per acquisition stay within the limit",
          mods2.PER_MODEL_RPM * worst_case <= 20, True)
    # The gate is worthless if both roles land on one model: OpenRouter's
    # throttle is per model, so they would share one real budget.
    check(
        "trial keeps default and parser on DIFFERENT models",
        mods.TRIAL_MODELS["default"] != mods.TRIAL_MODELS["parser"],
        True,
    )
    # The SDK summarises research by default and we throw the summary away
    # (use_research_summary_to_forecast is False). Left on, it is one un-gated
    # call per question against the parser model's own budget.
    check(
        "the unused research summariser stays switched off",
        bool(_re.search(r"enable_summarize_research=False", src)),
        True,
    )
    check(
        "and we are not paying to forecast from a summary either",
        bool(_re.search(r"use_research_summary_to_forecast=False", src)),
        True,
    )
    check(
        "season keeps default and parser on DIFFERENT models",
        mods.SEASON_MODELS["default"] != mods.SEASON_MODELS["parser"],
        True,
    )
    rpm = mods2.PER_MODEL_RPM
    # OpenRouter's observed new-account limit on the default model is 20/min.
    # Retries happen below the gate and do not re-acquire, so we need slack.
    check("the pace leaves headroom under the observed 20/min limit", rpm <= 18, True)

    print("\n  -- the exit path is reachable --")
    # log_report_summary defaults to raise_errors=True and raises on ANY failed
    # question, which made everything after it — the banner and the season
    # rollover message — dead code. Found by two independent auditors, 1 Sept.
    check("log_report_summary is called with raise_errors=False",
          bool(_re.search(r"log_report_summary\(forecast_reports, raise_errors=False\)", src)), True)
    check("we still fail the run when nothing was submitted",
          bool(_re.search(r"REFUSING TO PASS", src)), True)
    check("the season message is still raised",
          bool(_re.search(r"raise SystemExit\(SEASON_STALE_MESSAGE\)", src)), True)

    print("\n  -- season rollover guard --")
    # The values come out of main.py rather than being restated here, so this
    # block cannot pass while disagreeing with the code it is testing.
    is_stale, resolve, mod = load(
        "season_is_stale",
        "resolve_seasonal_tournament",
        consts=("SEASON_GUARD_DATE", "STALE_SEASON_IDS"),
    )
    guard = mod.SEASON_GUARD_DATE
    before = guard - timedelta(seconds=1)
    after = guard + timedelta(days=30)

    check("the pinned SDK's Summer ID is on the stale list", 33022 in mod.STALE_SEASON_IDS, True)
    check("during the Summer season the guard stays quiet", is_stale(33022, before), False)
    check("after the rollover date the Summer ID is stale", is_stale(33022, after), True)
    check("the guard date itself counts as after", is_stale(33022, guard), True)
    check("the ID as a string is caught too", is_stale("33022", after), True)
    check("the Summer slug is caught too", is_stale("summer-futureeval-2026", after), True)
    check("a genuine Fall ID is not stale", is_stale(33099, after), False)
    check("MiniBench's slug is never stale", is_stale("minibench", after), False)

    class FakeClient:
        CURRENT_AI_COMPETITION_ID = 33022

    saved = _os.environ.pop("AIB_TOURNAMENT_ID", None)
    try:
        check("with no override we fall back to the SDK", resolve(FakeClient()), 33022)

        _os.environ["AIB_TOURNAMENT_ID"] = "33099"
        check("a numeric override is used, as an int", resolve(FakeClient()), 33099)

        _os.environ["AIB_TOURNAMENT_ID"] = "  33099  "
        check("whitespace around the override is stripped", resolve(FakeClient()), 33099)

        _os.environ["AIB_TOURNAMENT_ID"] = "fall-futureeval-2026"
        check("a slug override is used verbatim", resolve(FakeClient()), "fall-futureeval-2026")

        _os.environ["AIB_TOURNAMENT_ID"] = "   "
        check("a blank override falls back rather than breaking", resolve(FakeClient()), 33022)

        # The whole point: an override the guard does not recognise must clear it.
        _os.environ["AIB_TOURNAMENT_ID"] = "33099"
        check("setting the override clears the guard", is_stale(resolve(FakeClient()), after), False)
    finally:
        _os.environ.pop("AIB_TOURNAMENT_ID", None)
        if saved is not None:
            _os.environ["AIB_TOURNAMENT_ID"] = saved

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
