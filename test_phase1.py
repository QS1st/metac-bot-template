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
    # Some constants are tier-dependent (LLM_ALLOWED_TRIES, MAX_CONCURRENT_QUESTIONS).
    # Mirror main.py's own resolution so lifting them works without importing.
    module.MODEL_TIER = (_os.environ.get("MODEL_TIER") or "test").strip().lower()
    module.USE_FREE_MODELS = module.MODEL_TIER in ("free", "test")

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
    """True if fn(*args) raises.

    Catches SystemExit explicitly as well as Exception: SystemExit inherits
    from BaseException, so a bare `except Exception` lets it through and kills
    the whole test run instead of recording a pass. The guards in main.py use
    SystemExit deliberately, so this helper has to see them.
    """
    try:
        fn(*args)
        return False
    except (Exception, SystemExit):
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
    # Lifted from main.py, not restated. Hard-coding these meant all 15 caps
    # assertions would still pass if BINARY_FLOOR were changed to 0.30.
    _capmod = load(consts=("BINARY_FLOOR", "BINARY_CEILING",
                           "AMBIGUOUS_FLOOR", "AMBIGUOUS_CEILING"))[-1]
    NORMAL = (_capmod.BINARY_FLOOR, _capmod.BINARY_CEILING)
    TIGHT = (_capmod.AMBIGUOUS_FLOOR, _capmod.AMBIGUOUS_CEILING)
    check("the caps really are tighter than the template's 0.01/0.99",
          NORMAL[0] > 0.01 and NORMAL[1] < 0.99, True)
    check("and the ambiguous caps are tighter still",
          TIGHT[0] > NORMAL[0] and TIGHT[1] < NORMAL[1], True)
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
                         "STRUCTURE_OUTPUT_ALLOWED_TRIES", "LLM_ALLOWED_TRIES"))[-1]
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
    # BOTH buckets, not just the parser. The first version of this test computed
    # only the parser arithmetic — which passed — and stayed silent about the
    # default model, where LLM_ALLOWED_TRIES=3 gave 30/min against a limit of 20.
    # A test that checks the half that works is worse than no test.
    parser_worst = mods2.PARSER_ALLOWED_TRIES * mods2.STRUCTURE_OUTPUT_ALLOWED_TRIES
    default_worst = mods2.LLM_ALLOWED_TRIES
    check("parser bucket worst case stays within the observed limit",
          mods2.PER_MODEL_RPM * parser_worst <= 20, True)
    check("DEFAULT bucket worst case stays within the observed limit",
          mods2.PER_MODEL_RPM * default_worst <= 20, True)
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

    print("\n  -- patch and main.py agree (the cheap half of the CI check) --")
    # CI already rebuilds main.py from the patch and diffs it, but that needs a
    # network clone of upstream and only runs after a push. This does the half
    # that needs neither: every replacement string in patch_phase1.py must
    # appear verbatim in main.py.
    #
    # It exists because on 1 Sept 2026 a comment containing a backslash-n was
    # embedded in a NON-raw """ string in the patch. Python turned it into a
    # real newline, which split the comment and corrupted the regex beside it.
    # The committed build had a SyntaxError. CI caught it; this catches it
    # before the commit, and before a paid run is wasted on it.
    patch_src = pathlib.Path(__file__).with_name("patch_phase1.py").read_text()
    mismatched = []
    replacements = 0
    for node in ast.walk(ast.parse(patch_src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("replace", "replace_all"):
            try:
                new_text = ast.literal_eval(node.args[1])
                label = ast.literal_eval(node.args[-1])
            except Exception:
                continue
            replacements += 1
            if new_text not in src:
                mismatched.append(label)
    check("every patch replacement appears verbatim in main.py", mismatched, [])
    check("the patch actually has replacements to check", replacements > 10, True)

    # ...and the OTHER direction, which is what actually bit on 2 Sept 2026.
    # The check above only proves the patch's edits are IN main.py. It says
    # nothing about main.py containing edits the patch does not produce — and
    # that is exactly what happened: the multiple-choice parsing instruction was
    # rewritten in main.py with no corresponding patch entry, so CI rebuilt a
    # main.py without it and the diff failed after the commit.
    #
    # Reverse-applying every replacement should strip our work back out. Any of
    # our markers still standing in the residue is an edit with no patch entry.
    # A heuristic, not a proof — CI's rebuild-and-diff remains the authority —
    # but it runs in a second and catches this class before a commit.
    residue = src
    for node in ast.walk(ast.parse(patch_src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("replace", "replace_all"):
            try:
                old_text = ast.literal_eval(node.args[0])
                new_text = ast.literal_eval(node.args[1])
            except Exception:
                continue
            residue = residue.replace(new_text, old_text)

    # If a cached copy of upstream main.py is available, do the REAL thing
    # instead: run the patch and diff, exactly as CI does. Set UPSTREAM_MAIN to
    # its path. The heuristic below is only a stand-in for when it is absent —
    # which is the case in CI itself, where the workflow clones upstream and
    # runs its own authoritative rebuild-and-diff a step later.
    #
    # This exists because the heuristic was not enough. It is a hand-maintained
    # list of markers, and on 2 Sept 2026 it missed two orphaned edits in a row
    # (the multiple-choice parsing instruction, then the date path's get_cdf)
    # simply because neither happened to contain a listed marker. Two commits
    # of Iain's time went on discovering what a real diff would have shown in
    # one second.
    upstream_path = _os.environ.get("UPSTREAM_MAIN")
    if upstream_path and pathlib.Path(upstream_path).exists():
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as td:
            built = pathlib.Path(td) / "rebuilt.py"
            proc = subprocess.run(
                [sys.executable, str(pathlib.Path(__file__).with_name("patch_phase1.py")),
                 upstream_path, str(built)],
                capture_output=True, text=True,
            )
            check("the patch applies cleanly to upstream", proc.returncode, 0)
            if proc.returncode == 0:
                check("main.py is byte-identical to the patch output",
                      built.read_text() == src, True)

    OURS = ("Audit, 1 Sept", "audit on 1 Sept", "audit 2 Sept", "REFUSING TO",
            "PER_MODEL_RPM", "EMPTY_RESEARCH_COUNT", "AMBIGUITY: LOW",
            "NEVER emit exactly 0", "seasonal_missing", "_invoke_default_llm",
            "_structure_output_paced", "AIB_TOURNAMENT_ID")
    orphans = sorted({m for m in OURS if m in residue})
    check("no edit in main.py lacks a patch entry", orphans, [])

    print("\n  -- the exit path is reachable --")
    # log_report_summary defaults to raise_errors=True and raises on ANY failed
    # question, which made everything after it — the banner and the season
    # rollover message — dead code. Found by two independent auditors, 1 Sept.
    check("log_report_summary is called with raise_errors=False",
          bool(_re.search(r"log_report_summary\(forecast_reports, raise_errors=False\)", src)), True)
    check("we still fail the run when nothing was submitted",
          bool(_re.search(r"REFUSING TO PASS", src)), True)
    check("problems are collected and raised together",
          bool(_re.search(r'raise SystemExit\("REFUSING TO PASS: " \+ " \| "\.join\(problems\)\)', src)), True)

    print("\n  -- season guard: a COUNT check, not a date and a denylist --")
    # The old guard (SEASON_GUARD_DATE + STALE_SEASON_IDS + season_is_stale) was
    # retired on 1 Sept 2026. It tested what the tournament ID *is*, so a typo in
    # AIB_TOURNAMENT_ID walked straight past it: zero questions, green tick, every
    # ten minutes for four months. It also expired — the Winter 2027 rollover had
    # no alarm at all. Assert the removal so it cannot creep back.
    for gone in ("season_is_stale", "SEASON_GUARD_DATE", "STALE_SEASON_IDS",
                 "SEASON_STALE_MESSAGE"):
        check(f"{gone} is gone", gone in src, False)

    check("the seasonal question count is logged every run",
          bool(_re.search(r'"%s tournament %r: %d open questions"', src)), True)
    # Zero OPEN questions is normal between windows; zero questions AT ALL is a
    # dead ID. The probe distinguishes them and only runs when we would
    # otherwise have exited silently.
    check("an existence probe runs when nothing is open",
          bool(_re.search(r"ApiFilter\(allowed_tournaments=\[tournament_id\]\)", src)), True)
    check("ApiFilter is imported", bool(_re.search(r"^    ApiFilter,$", src, _re.M)), True)
    check("a missing tournament is reported as a problem",
          bool(_re.search(r"SEASON_MISSING_MESSAGE\.format\(", src)), True)
    check("a quiet window does NOT fail the run",
          bool(_re.search(r"Normal between", src)), True)

    seasonal, mods3 = load("resolve_seasonal_tournament", consts=("SEASON_MISSING_MESSAGE",))

    saved = _os.environ.pop("AIB_TOURNAMENT_ID", None)
    try:
        # The SDK fallback is GONE. It used to return CURRENT_AI_COMPETITION_ID,
        # which poetry.lock pins to Summer 2026 — so an unset variable quietly
        # aimed a whole Fall season at a finished tournament. The count check
        # downstream cannot catch that: a retired tournament still HAS
        # questions, they are just all closed. Requiring the variable is the
        # only thing that closes both failures without dates or ID lists.
        # Returns (id, problem) rather than raising, so the caller can still
        # forecast MiniBench before failing the run. Raising here forfeited a
        # working scored series for the whole rollover window.
        tid, problem = seasonal()
        check("an unset AIB_TOURNAMENT_ID yields no id", tid, None)
        check("...and reports a problem instead of raising", bool(problem), True)
        check("the old SDK fallback is gone from main.py",
              "client.CURRENT_AI_COMPETITION_ID" in src, False)

        _os.environ["AIB_TOURNAMENT_ID"] = "33099"
        check("a numeric override is used, as an int", seasonal(), (33099, None))
        _os.environ["AIB_TOURNAMENT_ID"] = "  33099  "
        check("whitespace around the override is stripped", seasonal(), (33099, None))
        _os.environ["AIB_TOURNAMENT_ID"] = "fall-futureeval-2026"
        check("a slug override is used verbatim", seasonal(), ("fall-futureeval-2026", None))
        _os.environ["AIB_TOURNAMENT_ID"] = "   "
        check("a blank override reports a problem, it does not fall back",
              bool(seasonal()[1]), True)
    finally:
        _os.environ.pop("AIB_TOURNAMENT_ID", None)
        if saved is not None:
            _os.environ["AIB_TOURNAMENT_ID"] = saved

    print("\n  -- the tournament must actually BE a bot tournament --")
    # The count check alone cannot tell a typo from a correct ID: Metaculus
    # project IDs are dense, so a transposed digit usually lands on another
    # REAL project, which has questions and passes. Verified from the slugs the
    # questions already carry, so it costs no extra request.
    slug_problem, mods4 = load("tournament_slug_problem",
                               consts=("BOT_TOURNAMENT_SLUG_MARKERS",))

    class Q:
        def __init__(self, slugs):
            self.tournament_slugs = slugs

    check("a real bot tournament passes",
          slug_problem(33099, [Q(["fall-futureeval-2026"])], "Seasonal"), None)
    check("MiniBench passes", slug_problem("minibench", [Q(["minibench"])], "MiniBench"), None)
    check("the older aib naming still passes",
          slug_problem(32813, [Q(["fall-aib-2025"])], "Seasonal"), None)
    check("a typo landing on a real but unrelated project is REFUSED",
          slug_problem(33021, [Q(["us-midterms-2026"])], "Seasonal") is not None, True)
    check("...and the message names the tournament",
          "33021" in (slug_problem(33021, [Q(["us-midterms-2026"])], "Seasonal") or ""), True)
    check("one matching slug among several is enough",
          slug_problem(1, [Q(["some-series"]), Q(["summer-futureeval-2026"])], "Seasonal"), None)
    # Fails SAFE: refusing on absent metadata would turn an API change into an
    # outage of our own making.
    check("missing slug metadata does NOT refuse the run",
          slug_problem(1, [Q([]), Q(None)], "Seasonal"), None)
    check("no questions at all does not refuse here either",
          slug_problem(1, [], "Seasonal"), None)

    print("\n  -- MiniBench is guarded and runs first --")
    check("MiniBench goes through fetch_and_verify_tournament",
          bool(_re.search(r'fetch_and_verify_tournament\(\s*client, client\.CURRENT_MINIBENCH_ID, "MiniBench"', src)), True)
    check("no un-guarded forecast_on_tournament remains in the tournament branch",
          "forecast_on_tournament(\n                client.CURRENT_MINIBENCH_ID" in src, False)
    # An unset AIB_TOURNAMENT_ID must not forfeit MiniBench.
    mb = src.index('client.CURRENT_MINIBENCH_ID, "MiniBench"')
    sid = src.index("seasonal_id, seasonal_problem = resolve_seasonal_tournament()")
    check("MiniBench is dispatched BEFORE the seasonal id is resolved", mb < sid, True)

    print("\n  -- empty research fails on a rate, not one instance --")
    thresholds = load(consts=("EMPTY_RESEARCH_MIN_TO_FAIL", "EMPTY_RESEARCH_FAIL_RATE"))[-1]
    check("a minimum count is required before failing",
          thresholds.EMPTY_RESEARCH_MIN_TO_FAIL >= 2, True)
    check("and a majority rate", 0 < thresholds.EMPTY_RESEARCH_FAIL_RATE <= 0.5, True)
    check("one thin research string cannot red the run",
          bool(_re.search(r"EMPTY_RESEARCH_COUNT >= EMPTY_RESEARCH_MIN_TO_FAIL and rate > EMPTY_RESEARCH_FAIL_RATE", src)), True)
    print("\n  -- research and forfeit guards --")
    check("empty research is counted, not just logged",
          bool(_re.search(r"EMPTY_RESEARCH_COUNT \+= 1", src)), True)
    check("and the count can turn the run red",
          bool(_re.search(r"if EMPTY_RESEARCH_COUNT and attempted:", src)), True)
    # NumericReport.aggregate_predictions expands every sample's CDF in a list
    # comprehension, so a raise there kills the QUESTION, not the sample. Forcing
    # expansion inside the per-sample coroutine restores the 3-of-5 tolerance.
    check("numeric and date samples force CDF expansion per sample",
          len(_re.findall(r"prediction\.get_cdf\(\)", src)), 2)
    # Our own parsing instruction used to manufacture the validator's rejection.
    check("the parser is told never to emit a literal zero",
          bool(_re.search(r"NEVER emit exactly 0", src)), True)
    check("and to make the probabilities sum to 1",
          bool(_re.search(r"sum to\s+exactly 1\.00", src)), True)
    check("the old 0%-option instruction is gone",
          "make it an entry in your final list with 0% probability" in src, False)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
