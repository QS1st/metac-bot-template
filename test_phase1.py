"""
Tests for the logic added to the upstream template.

These run without API keys, network access, or the forecasting-tools package —
the point is to prove our own code is correct before it touches a live
question. The template's own code is not under test here.

Run:  python3 test_phase1.py
"""

import ast
import hashlib as _hashlib
import json as _json
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
    module.json = _json
    module.hashlib = _hashlib
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
        # AnnAssign as well as Assign. "RUN_WARNINGS: list[str] = []" is an
        # annotated assignment, and lifting only plain ones meant the loader
        # reported "patch did not apply" for a constant that was present and
        # correct — a false alarm that looks exactly like a real build failure.
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if isinstance(node, ast.AnnAssign):
                targets = [node.target.id] if isinstance(node.target, ast.Name) else []
            else:
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

    print("\n  -- the file's MODULE STRUCTURE, which nothing checked --")
    # THE BUG THIS EXISTS FOR (second audit pass, 21 Sept 2026). The scoring-grid
    # helper was anchored on "    parser = argparse.ArgumentParser(" — a line
    # INSIDE the `if __name__ == "__main__":` suite. Inserting a column-0 `def`
    # above it terminated the suite, and every line from argparse to EOF became
    # unreachable code inside the helper, after its returns.
    #
    # The bot would have started, configured logging, and exited 0. A green tick
    # every ten minutes for four months and not one forecast — the exact "green
    # tick would be a lie" failure this file guards against, in code that could
    # never run. NOTHING caught it: the patch/main diff agreed, the rebuild was
    # byte-identical, and load() execs individual function nodes so dead code
    # compiles perfectly. 255 checks passed over a bot that does nothing.
    _tree = ast.parse(src)
    _main = [b for b in _tree.body
             if isinstance(b, ast.If) and "__main__" in ast.dump(b.test)]
    check("the __main__ guard is found exactly once", len(_main), 1)
    check("the __main__ block runs to the last line of the file",
          _main[0].end_lineno, len(src.splitlines()))
    check("nothing is defined after it",
          _tree.body[-1] is _main[0], True)
    # The things that must be INSIDE it, not stranded in a helper's dead tail.
    _mainsrc = "\n".join(src.splitlines()[_main[0].lineno - 1:_main[0].end_lineno])
    for _needed in ("argparse.ArgumentParser(", "check_environment(", "MetaculusClient(",
                    "forecast_on_tournament(", "write_step_summary(", "raise SystemExit("):
        check(f"the __main__ block still contains {_needed}", _needed in _mainsrc, True)

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
          bool(_re.search(r"ApiFilter\(\s*allowed_tournaments=\[tournament_id\]", src)), True)
    check("the probe looks at the SAME population as the fetch",
          bool(_re.search(r'group_question_mode="unpack_subquestions"', src)), True)

    print("\n  -- one client, shared with the publish path --")
    # ForecastBot builds its own client unless handed one, so anything set here
    # never reached publishing — which is where the blocking sleeps are.
    # Code lines only — a comment elsewhere shows the verification snippet.
    check("exactly one MetaculusClient is constructed in code",
          len(_re.findall(r"^\s*[^#\s].*MetaculusClient\(", src, _re.M)), 1)
    check("and it is handed to the bot",
          bool(_re.search(r"metaculus_client=client", src)), True)
    check("with a shorter inter-request sleep",
          bool(_re.search(r"sleep_seconds_between_requests=1\.0", src)), True)
    check("ApiFilter is imported", bool(_re.search(r"^    ApiFilter,$", src, _re.M)), True)
    check("a missing tournament is reported as a problem",
          bool(_re.search(r"SEASON_MISSING_MESSAGE\.format\(", src)), True)
    check("a quiet window does NOT fail the run",
          bool(_re.search(r"Normal between", src)), True)

    # EMPTY MEANS DIFFERENT THINGS FOR THE TWO HALVES.
    #
    # A season runs continuously for four months, so an empty seasonal
    # tournament is a wrong or retired ID and must red the run. MiniBench is a
    # chain of two-week rounds behind a slug that repoints between them, so
    # empty is a NORMAL state there for hours or days at a time.
    #
    # Learned live on 5 Sept 2026: the first run of the tournament workflow
    # failed with "MiniBench TOURNAMENT NOT FOUND" two days before a round we
    # were entering. Left fatal it would have reddened every run in every
    # inter-round gap, permanently — and red that always fires is the same as
    # no red at all.
    # BEHAVIOURAL, not textual. The first version of these checks was five
    # regexes, and an audit on 5 Sept 2026 showed all five stayed green through
    # the exact refactor they existed to catch — including moving the opt-out
    # from the MiniBench call to the SEASONAL one. That is the same failure this
    # project has already written up twice: a test that agrees with itself.
    fetchv, modsF = load("fetch_and_verify_tournament",
                         consts=("SEASON_MISSING_MESSAGE", "SEASON_MISSING_FIXES",
                                 "SKIP_GROUP_QUESTIONS", "BOT_TOURNAMENT_SLUG_MARKERS",
                                 "QUESTIONS_FOUND", "RUN_WARNINGS"))
    import asyncio as _aio
    modsF.asyncio = _aio
    modsF.ApiFilter = lambda **kw: kw
    modsF.drop_group_questions = lambda q, label: q
    modsF.tournament_slug_problem = lambda t, q, label: None

    class _Q:
        def __init__(self, slugs): self.tournament_slugs = slugs

    class _Client:
        """open_qs is what the tournament returns now; all_qs is the probe."""
        def __init__(self, open_qs, all_qs): self._o, self._a = open_qs, all_qs
        def get_all_open_questions_from_tournament(self, t): return list(self._o)
        async def get_questions_matching_filter(self, f): return list(self._a)

    q, p = fetchv(_Client([], []), "minibench", "MiniBench", empty_is_fatal=False)
    check("an empty MiniBench returns NO problem", (q, p), ([], None))
    q, p = fetchv(_Client([], []), 33121, "Seasonal")
    check("an empty SEASONAL tournament still returns a problem", (q, bool(p)), ([], True))
    check("...and the default is fatal, so a new call site is strict",
          bool(fetchv(_Client([], []), 33121, "Anything")[1]), True)

    # The quiet window: nothing OPEN, but questions exist behind it. This is
    # most runs in a live tournament, and it must never fail.
    check("a quiet window with questions behind it does not fail",
          fetchv(_Client([], [_Q(["minibench"])]), "minibench", "MiniBench")[1], None)

    # THE LEAK THE AUDIT FOUND. Before this was made keyword-only,
    # fetch_and_verify_tournament(client, id, "Seasonal", False) silently
    # disarmed the season, and every regex test still passed.
    try:
        fetchv(_Client([], []), 33121, "Seasonal", False)
        leaked = True
    except TypeError:
        leaked = False
    check("the opt-out CANNOT be passed positionally", leaked, False)

    # And bind the opt-out to the MiniBench call site specifically, rather than
    # counting occurrences anywhere in the file.
    _mb = _re.search(r'fetch_and_verify_tournament\((?:[^()]|\([^)]*\))*?"MiniBench".*?\n\s*\)', src, _re.S)
    check("MiniBench is the call that carries the opt-out",
          bool(_mb) and "empty_is_fatal=False" in _mb.group(0), True)
    check("the seasonal call takes the strict default, with no opt-out",
          bool(_re.search(r'fetch_and_verify_tournament\(\s*\n?\s*client, seasonal_id, "Seasonal"\s*\n?\s*\)', src)), True)
    check("an empty non-fatal tournament raises a GitHub annotation, not just a log line",
          bool(_re.search(r'::warning title=', src)), True)

    print("\n  -- the run verdict reaches the GitHub run page --")
    # Reading a run meant scrolling ~2000 log lines. Over four months that is
    # the difference between noticing a problem and not. Tested behaviourally:
    # the function writes a real file and we read it back.
    wss, modsS = load("write_step_summary",
                      consts=("RUN_WARNINGS", "QUESTIONS_FOUND", "BALANCE_NOTE"))
    modsS.predictions_per_report = lambda: 5

    import tempfile as _tf, pathlib as _pl
    def _summary(**kw):
        base = dict(run_mode="tournament", submitted=0, failed=0, attempted=0,
                    thin_research=0, problems=[], seasonal_id=None,
                    tournament_url="https://example.invalid/t/")
        base.update(kw)
        with _tf.TemporaryDirectory() as d:
            p = _pl.Path(d) / "summary.md"
            _os.environ["GITHUB_STEP_SUMMARY"] = str(p)
            try:
                wss(**base)
                return p.read_text() if p.exists() else ""
            finally:
                _os.environ.pop("GITHUB_STEP_SUMMARY", None)

    out = _summary(submitted=3, attempted=3)
    check("a clean run reports OK and the count", "OK - 3 forecast(s) submitted" in out, True)
    check("...and names the model tier actually used", f"`{modsS.MODEL_TIER}`" in out, True)
    check("...and says the season was skipped when there is none",
          "no season declared" in out, True)

    out = _summary(submitted=2, failed=1, attempted=3)
    check("a partial run is PARTIAL, not OK", out.startswith("## PARTIAL"), True)
    out = _summary(submitted=0, failed=3, attempted=3)
    check("a run where everything errored is FAILED", out.startswith("## FAILED"), True)

    # The most important case: a RED run must still get a summary, and the
    # problems must be IN it. That is the run somebody actually has to read.
    out = _summary(problems=["AIB_TOURNAMENT_ID is not set.", "second problem"])
    check("a refusing run still writes a summary", bool(out), True)
    check("...leads with REFUSING TO PASS", out.startswith("## REFUSING TO PASS"), True)
    check("...and lists every problem verbatim",
          ("AIB_TOURNAMENT_ID is not set." in out) and ("second problem" in out), True)

    # It must be written BEFORE the exit decision, or a red run gets nothing.
    _w = src.find("write_step_summary(")
    _x = src.find('raise SystemExit("REFUSING TO PASS: "')
    check("both the summary call and the exit exist", (_w > -1 and _x > -1), True)
    check("the summary is written before the run can exit", _w < _x, True)

    # And it must never become a new way for the run to die.
    _os.environ.pop("GITHUB_STEP_SUMMARY", None)
    try:
        wss(run_mode="tournament", submitted=1, failed=0, attempted=1,
            thin_research=0, problems=[], seasonal_id=None, tournament_url=None)
        survived_unset = True
    except Exception:
        survived_unset = False
    check("no GITHUB_STEP_SUMMARY set is a no-op, not a crash", survived_unset, True)

    _os.environ["GITHUB_STEP_SUMMARY"] = "/nonexistent-dir-xyz/summary.md"
    try:
        wss(run_mode="tournament", submitted=1, failed=0, attempted=1,
            thin_research=0, problems=[], seasonal_id=None, tournament_url=None)
        survived_bad = True
    except Exception:
        survived_bad = False
    finally:
        _os.environ.pop("GITHUB_STEP_SUMMARY", None)
    check("an unwritable path warns rather than killing the run", survived_bad, True)

    # A run that forecast nothing BECAUSE something is wrong must not say OK.
    # Audit, 6 Sept 2026: the summary printed "OK - no open questions" on the
    # exact run where MiniBench had gone empty, talking over the only detector
    # we have for a dead slug.
    modsS.RUN_WARNINGS = ["MiniBench holds no questions yet"]
    out = _summary()
    check("nothing forecast WITH a warning is not reported as OK",
          out.startswith("## NOTHING FORECAST"), True)
    check("...and the warning itself is printed", "MiniBench holds no" in out, True)
    modsS.RUN_WARNINGS = []
    out = _summary()
    check("nothing forecast with NO warning is a quiet OK",
          out.startswith("## OK - nothing new"), True)

    # Questions FOUND is not the same as questions attempted: attempted is 0
    # both when the tournament is empty and when everything is already
    # forecast, which is the normal steady state for most runs.
    modsS.QUESTIONS_FOUND = {"MiniBench": 5}
    out = _summary(submitted=0, attempted=0)
    check("the summary distinguishes questions found from questions attempted",
          "MiniBench open questions" in out and "| 5 |" in out, True)
    modsS.QUESTIONS_FOUND = {}

    # Problems on one half must not hide successful work on the other.
    out = _summary(submitted=2, attempted=2, problems=["seasonal half broke"])
    check("a refusal still records what DID get submitted",
          "| Submitted | 2 |" in out, True)

    # Markdown safety. Not reachable today; it becomes reachable the moment
    # anything dynamic reaches these strings, and it fails silently.
    out = _summary(problems=["pipe | inside", "two\nlines"])
    check("a pipe cannot break out of a cell or bullet", "|" not in out.split("### Problems")[1], True)
    check("a newline cannot escape its bullet",
          out.split("### Problems")[1].strip().count("\n"), 1)

    seasonal, mods3 = load("resolve_seasonal_tournament",
                           consts=("SEASON_MISSING_MESSAGE", "NO_SEASON_VALUES",
                                   "NO_SEASON_EXPIRY"))

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

        # THE NO-SEASON SENTINEL, added 5 Sept 2026.
        #
        # Between seasons there is no seasonal tournament to point at, and both
        # of the other states turn every run red: unset raises REFUSING TO
        # PASS, and a season that has not opened yet holds zero questions,
        # which SEASON_MISSING cannot tell apart from a retired one. At 144
        # runs a day that floods the only alarm channel this project has.
        #
        # The sentinel must return NO id and NO problem: the seasonal half is
        # skipped, MiniBench still runs, and the workflow stays green.
        for value in mods3.NO_SEASON_VALUES:
            _os.environ["AIB_TOURNAMENT_ID"] = value
            check(f"sentinel {value!r} skips the season without a problem",
                  seasonal(), (None, None))
        _os.environ["AIB_TOURNAMENT_ID"] = "NONE"
        check("the sentinel is case-insensitive", seasonal(), (None, None))
        _os.environ["AIB_TOURNAMENT_ID"] = "  Off  "
        check("...and tolerates surrounding whitespace", seasonal(), (None, None))

        # The sentinel must not be reachable by accident. If a real tournament
        # id or slug ever collided with one of these, setting it would silently
        # skip the season for a whole tournament.
        for real in ("33121", "fall-futureeval-2026", "minibench", "33022"):
            check(f"a real target {real!r} is not mistaken for the sentinel",
                  real.lower() in mods3.NO_SEASON_VALUES, False)

        # And it must stay DISTINCT from unset. Declaring the gap is a
        # deliberate act; forgetting to set the variable is not, and the two
        # must not produce the same outcome.
        _os.environ.pop("AIB_TOURNAMENT_ID", None)
        check("unset still reports a problem, unlike the sentinel",
              bool(seasonal()[1]), True)

        # THE SENTINEL MUST EXPIRE.
        #
        # Every detector on the seasonal side — the question count, the slug
        # check, SEASON_MISSING — sits behind the sentinel, so declaring the
        # gap switches them all off. Left declared through the season opening,
        # the bot forecasts MiniBench only, GREEN, for four months, and nothing
        # in the code can notice. Found by audit on 5 Sept 2026, in the guard
        # written to prevent exactly that failure.
        #
        # The old date guard was retired because it expired into SILENCE. This
        # one expires into NOISE, which is the safe direction: firing late
        # wastes attention, failing to fire costs a season.
        # 30 Sept, not 28th. The two days of grace exist because Metaculus
        # creates a tournament project before populating it — proved by
        # "minibench" holding zero questions on 5 Sept for a 7 Sept round. On
        # the 28th, an expiry of the 28th would have made BOTH settings red:
        # "none" expired, and 33121 empty-and-fatal.
        check("the expiry allows two days of grace past the Fall opening",
              (mods3.NO_SEASON_EXPIRY.year, mods3.NO_SEASON_EXPIRY.month,
               mods3.NO_SEASON_EXPIRY.day), (2026, 9, 30))
        check("the expiry is timezone-aware, so the comparison cannot raise",
              mods3.NO_SEASON_EXPIRY.tzinfo is not None, True)

        class _FrozenClock:
            """Stands in for datetime, so the expiry is testable without waiting."""
            def __init__(self, when):
                self._when = when
            def now(self, tz=None):
                return self._when

        real_clock = mods3.datetime
        try:
            for label, when, expect_problem in (
                ("the day before the season opens", _datetime(2026, 9, 27, 23, 0, tzinfo=_timezone.utc), False),
                ("the morning the season opens, still in grace", _datetime(2026, 9, 28, 9, 0, tzinfo=_timezone.utc), False),
                ("the last hour of grace", _datetime(2026, 9, 29, 23, 0, tzinfo=_timezone.utc), False),
                ("the moment grace runs out", _datetime(2026, 9, 30, 0, 0, tzinfo=_timezone.utc), True),
                ("a month into the season", _datetime(2026, 10, 30, tzinfo=_timezone.utc), True),
            ):
                mods3.datetime = _FrozenClock(when)
                _os.environ["AIB_TOURNAMENT_ID"] = "none"
                tid, problem = seasonal()
                check(f"sentinel on {label}: id is still None", tid, None)
                check(f"sentinel on {label}: problem raised = {expect_problem}",
                      bool(problem), expect_problem)
            # The expired message must name the variable AND the real Fall id,
            # because whoever reads it will be fixing it in a hurry.
            mods3.datetime = _FrozenClock(_datetime(2026, 10, 1, tzinfo=_timezone.utc))
            _os.environ["AIB_TOURNAMENT_ID"] = "none"
            expired = seasonal()[1]
            check("the expired message names AIB_TOURNAMENT_ID",
                  "AIB_TOURNAMENT_ID" in expired, True)
            check("...and the Fall 2026 id, so the fix needs no lookup",
                  "33121" in expired, True)
        finally:
            mods3.datetime = real_clock
    finally:
        _os.environ.pop("AIB_TOURNAMENT_ID", None)
        if saved is not None:
            _os.environ["AIB_TOURNAMENT_ID"] = saved

    print("\n  -- the ambiguity flag survives markdown --")
    # The season tier runs claude-fable-5, which bolds headings by habit. The
    # tighter first pattern matched none of these, so the guard could have sat
    # inert for four months on the only tier that scores.
    for label, text in [
        ("bold whole line", "**AMBIGUITY: HIGH**"),
        ("bold label", "**AMBIGUITY:** HIGH"),
        ("bold value", "AMBIGUITY: **HIGH**"),
        ("bullet", "- AMBIGUITY: HIGH"),
        ("heading", "### AMBIGUITY: HIGH"),
        ("trailing stop", "AMBIGUITY: HIGH."),
        ("italics", "_AMBIGUITY: HIGH_"),
        ("blockquote", "> AMBIGUITY: HIGH"),
    ]:
        check(f"decorated flag still tightens: {label}", caps(text), TIGHT)
    # ...and the loosening must NOT reintroduce the failure line-anchoring
    # exists to prevent: a model restating the instruction inline.
    check("restated inline STILL does not trigger",
          caps("I must output either AMBIGUITY: LOW or AMBIGUITY: HIGH."), NORMAL)
    check("trailing prose on the line does not trigger",
          caps("AMBIGUITY: HIGH (competing readings)"), NORMAL)
    check("bulleted options then a real answer takes the answer",
          caps("Options:\n- AMBIGUITY: LOW\n- AMBIGUITY: HIGH\nReasoning.\nAMBIGUITY: LOW"), NORMAL)

    print("\n  -- group questions are ON, because skipping was proven --")
    # Settled 2 Sept 2026 by check_group_questions.py against the
    # bot-testing-area: all four group subquestions reported
    # already_forecasted=True, so skip_previously_forecasted_questions holds for
    # them and we are not at risk of breaching the one-forecast-per-question
    # rule. The switch stays, so the decision is reversible on new evidence.
    drop, mods5 = load("drop_group_questions", consts=("SKIP_GROUP_QUESTIONS",))

    class GQ:
        def __init__(self, gid): self.question_ids_of_group = gid

    check("group questions are forecast by default now", mods5.SKIP_GROUP_QUESTIONS, False)
    check("so nothing is dropped", len(drop([GQ(None), GQ([1, 2]), GQ(None)], "Seasonal")), 3)
    check("the evidence is recorded next to the switch",
          "Four for four" in src and "43329" in src, True)

    # The switch must still WORK if we ever have to flip it back.
    mods5.SKIP_GROUP_QUESTIONS = True
    check("flipped back, group questions are excluded",
          len(drop([GQ(None), GQ([1, 2]), GQ(None)], "Seasonal")), 2)
    check("a list with no groups is untouched", len(drop([GQ(None)], "Seasonal")), 1)
    check("an empty list survives", drop([], "Seasonal"), [])
    mods5.SKIP_GROUP_QUESTIONS = False

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
          bool(_re.search(r'fetch_and_verify_tournament\(\s*client,\s*client\.CURRENT_MINIBENCH_ID,\s*"MiniBench"', src)), True)
    check("no un-guarded forecast_on_tournament remains in the tournament branch",
          "forecast_on_tournament(\n                client.CURRENT_MINIBENCH_ID" in src, False)
    # An unset AIB_TOURNAMENT_ID must not forfeit MiniBench.
    mb = _re.search(r'client\.CURRENT_MINIBENCH_ID,\s*\n?\s*"MiniBench"', src).start()
    sid = src.index("seasonal_id, seasonal_problem = resolve_seasonal_tournament()")
    check("MiniBench is dispatched BEFORE the seasonal id is resolved", mb < sid, True)
    # With the sentinel set, resolve returns (None, None). Fetching tournament
    # None would raise inside the SDK, so the caller must check the id too and
    # not merely the absence of a problem.
    check("the caller does not fetch a None tournament",
          bool(_re.search(r"if seasonal_problem is None and seasonal_id is not None:", src)), True)
    # The summary banner is what a human actually reads. With no season it must
    # not print .../tournament/None/.
    check("the banner falls back to the MiniBench url when there is no season",
          bool(_re.search(r'else "https://www\.metaculus\.com/tournament/minibench/"', src)), True)

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
    print("\n  -- the numeric prompt has the binary prompt's safeguards --")
    # Edit 2 built the still-open guard, the adversarial criteria read and the
    # base-rate step, and wired ALL of them into the binary prompt only. Numeric
    # and discrete were ~45% of the 7-25 Sept MiniBench round and 31% of the
    # Spring seasonal tournament, so close to half of every round was forecast
    # with none of them — by a bot whose stated first pillar is adversarial
    # reading of the criteria. Found by audit, 21 Sept 2026.
    _num = src.index("async def _run_forecast_on_numeric")
    _dat = src.index("async def _run_forecast_on_date")
    numeric_prompt = src[_num:_dat]

    check("the numeric prompt has the still-open guard",
          "STILL OPEN and has NOT yet resolved" in numeric_prompt, True)
    check("...the adversarial criteria read",
          "read the resolution criteria adversarially" in numeric_prompt, True)
    check("...an AMBIGUITY flag with both literals",
          ("AMBIGUITY: LOW" in numeric_prompt) and ("AMBIGUITY: HIGH" in numeric_prompt), True)
    check("...and an explicit base-rate step",
          "base rate or reference class" in numeric_prompt, True)

    # The numeric version must be ADAPTED, not copied. On a binary question the
    # criteria decide WHETHER something counts; on a numeric one they decide
    # WHICH PUBLISHED FIGURE counts — source, date, units, rounding.
    check("the criteria step is numeric-specific, not the binary wording",
          "which published figure, from which" in numeric_prompt, True)
    check("...and the binary-only phrasing did not leak in",
          "the annual maximum" in numeric_prompt, False)

    # Base rate must anchor BEFORE the scenarios, not after them.
    check("the base rate comes before the low/high scenarios",
          numeric_prompt.index("base rate or reference class")
          < numeric_prompt.index("results in a low outcome"), True)

    # The lettered list gained a slot for the grid judgement. It keys on the
    # PUBLISHED GRID rather than whole-number-ness, because edit 23 retracted
    # that framing: an audit measured the real question parameters and found
    # Metaculus already bins discrete questions one-per-integer.
    check("the lettered list has a slot for the published-grid judgement",
          "The grid the resolution source publishes on" in numeric_prompt, True)

    # HONEST LIMIT: on binary, AMBIGUITY: HIGH is ENFORCED by caps_for_reasoning.
    # On numeric it is ADVISORY — enforcing it would mean rewriting percentiles
    # after the fact, which is the _sorted_percentiles mistake. Assert the
    # asymmetry deliberately so nobody later assumes parity.
    check("the numeric ambiguity flag asks the MODEL to widen, not code",
          "widen your 10 to 90 interval" in numeric_prompt, True)
    check("...and no numeric caps helper was introduced",
          bool(_re.search(r"def caps_for_numeric|def widen_\w+\(", src)), False)

    print("\n  -- concentration is keyed on the SCORING GRID, not whole numbers --")
    # RETRACTION. Edit 20 claimed probability on 0.3 for a count question "cannot
    # happen and is simply thrown away". FALSE. An audit pulled the real
    # parameters of every numeric/discrete question in the 7-25 Sept round:
    # Metaculus publishes q45541 as DISCRETE with five bins of width 1.0 centred
    # on integers, so 0.3 lands in the "0" bin. Our actual output scored 0.365 of
    # probability on the outcome, not 0.0124. Straddling there is +0.18 nats when
    # right, -0.31 when wrong. It wins only where bins are FINER than the
    # published grid (q45561: +2.84 nats) — about 1 question in 30.
    check("the false 'thrown away' claim is gone from the prompt",
          "simply thrown away" in src, False)
    check("concentration is conditioned on the bins being finer than the grid",
          "FINER than the grid the resolution source publishes on" in src, True)
    check("...and says to forecast smoothly otherwise",
          "do none of this: forecast smoothly" in src, True)
    check("the scoring grid is computed and interpolated, not guessed",
          ("_scoring_grid_message(question)" in src) and ("{grid_message}" in src), True)

    # LITERAL BACKSLASH-N. Edit 20 shipped `increasing.\\n` in a non-raw string,
    # so the cap-at-two guardrail rendered mid-sentence behind a stray escape
    # instead of as its own bullet — in the prompt that runs on ~51% of a round.
    # Same class as the 1 Sept newline bug, mirrored. Every existing guard was
    # blind: the patch/main diff agreed, and the tests asserted PRESENCE, not
    # STRUCTURE. Audit, 21 Sept 2026.
    _numstart = src.index("async def _run_forecast_on_numeric")
    _datstart = src.index("async def _run_forecast_on_date")
    _binstart = src.index("async def _run_forecast_on_binary")
    # Spans keyed on markers, never on a character count: the binary span used
    # to be _binstart + 6000 with 888 characters of headroom, so it would have
    # stopped covering the prompt without failing. Second audit, 21 Sept 2026.
    for _label, _span in (
        ("numeric", src[_numstart:_datstart]),
        ("binary", src[_binstart:src.index("##################################### MULTIPLE CHOICE")]),
        ("multiple choice", src[src.index("async def _multiple_choice_prompt_to_forecast"):
                                src.index("##################################### NUMERIC")]),
        ("date", src[src.index("async def _date_prompt_to_forecast"):
                     src.index("def _create_upper_and_lower_bound_messages")]),
    ):
        check(f"no literal backslash-n leaked into the {_label} prompt",
              "\\n" in _span, False)

    # The grid helper is advisory. It must never kill a forecast.
    grid, modsG = load("_scoring_grid_message")
    class _Q:
        def __init__(self, **kw): self.__dict__.update(kw)
    check("a normal question yields a grid line",
          "scored over" in grid(_Q(cdf_size=201, lower_bound=0, upper_bound=100)), True)
    # cdf_size counts CDF POINTS; the bins are the gaps between them, one
    # fewer. A discrete question like q45541 (-0.5 to 4.5, five unit bins)
    # arrives as six points, and the message must say five bins of width 1.
    _disc = grid(_Q(cdf_size=6, lower_bound=-0.5, upper_bound=4.5))
    check("a discrete question reports five bins, not six", "5 bins" in _disc, True)
    check("...each one wide", "about 1 wide" in _disc, True)
    # Four significant figures. A coarser format would round a 0.205-wide bin
    # to 0.2 and a 1.0004 to 1, which is the difference the concentrate-or-
    # smooth decision turns on. Mutation survivor, third audit pass.
    check("the width is stated to four significant figures",
          "{width:.4g}" in src, True)
    check("a 201-point grid is 200 bins",
          "200 bins" in grid(_Q(cdf_size=201, lower_bound=0, upper_bound=100)), True)
    # A log-scaled question has NO single bin width — the bins are geometric.
    # Stating one would feed a lie to the concentrate-or-smooth decision.
    check("a log-scaled question says nothing at all",
          grid(_Q(cdf_size=201, lower_bound=1, upper_bound=1000, zero_point=0)), "")
    for _bad in (_Q(cdf_size=None, lower_bound=0, upper_bound=1),
                 _Q(cdf_size=1, lower_bound=0, upper_bound=1),
                 _Q(cdf_size=201, lower_bound=5, upper_bound=5),
                 _Q(cdf_size=201, lower_bound=None, upper_bound=1),
                 _Q()):
        check("a degenerate question yields an empty string, never a raise",
              grid(_bad), "")

    # Edit 22 put base rates and reference-class figures upstream of a
    # single-shot parser. It must be told to ignore them.
    check("the numeric parser is told not to round the straddle away",
          "Preserve the values exactly as written, including small decimal offsets" in src, True)
    check("the parser is scoped to the final percentile block",
          'Parse ONLY the final "Percentile NN: value" block' in src, True)
    check("...and item (f) keys on the published grid, not whole-number-ness",
          "The grid the resolution source publishes on" in src, True)

    check("the parser is told never to emit a literal zero",
          bool(_re.search(r"NEVER emit exactly 0", src)), True)
    check("and to make the probabilities sum to 1",
          bool(_re.search(r"sum to\s+exactly 1\.00", src)), True)
    check("the old 0%-option instruction is gone",
          "make it an entry in your final list with 0% probability" in src, False)

    print("\n  -- the guard arrives FIRST, and the flag has its own name --")
    # Edit 22 anchored the still-open guard and the adversarial read on the
    # upstream "Before answering you write:" block. Edit 20 had already inserted
    # four formatting bullets ABOVE that point, so the built prompt said "FIRST,
    # before anything else" in NINTH position. An instruction that says first and
    # arrives ninth is a contradiction, and the model resolves it by ignoring one
    # half. Edit 24 re-anchors it. Audit, 21 Sept 2026.
    check("the still-open guard precedes the formatting bullets",
          numeric_prompt.index("STILL OPEN and has NOT yet resolved")
          < numeric_prompt.index("Formatting Instructions:"), True)
    check("...and so does the adversarial read",
          numeric_prompt.index("FIRST, before anything else")
          < numeric_prompt.index("Formatting Instructions:"), True)
    check("...and the base-rate list still comes after them",
          numeric_prompt.index("Formatting Instructions:")
          < numeric_prompt.index("base rate or reference class"), True)

    # Moving a block is exactly where a stray duplicate creeps in. One did, on
    # the first build of edit 24: the bound messages were emitted twice because
    # the replacement re-stated its own anchor. Caught here, before the commit.
    check("the bound messages appear exactly once",
          (numeric_prompt.count("{lower_bound_message}"),
           numeric_prompt.count("{upper_bound_message}")), (1, 1))
    check("...and so does the formatting block",
          numeric_prompt.count("Formatting Instructions:"), 1)
    check("...and the still-open guard",
          numeric_prompt.count("STILL OPEN and has NOT yet resolved"), 1)

    # The numeric flag used to reuse the BINARY literal — the same token
    # caps_for_reasoning() matches to clamp a probability to [0.10, 0.90]. Two
    # meanings behind one string in one codebase, and ungreppable.
    check("the numeric flag is FIGURE AMBIGUITY",
          ("FIGURE AMBIGUITY: LOW" in numeric_prompt)
          and ("FIGURE AMBIGUITY: HIGH" in numeric_prompt), True)
    check("...and the bare binary literal is gone from the numeric prompt",
          bool(_re.search(r"(?<!FIGURE )AMBIGUITY: (LOW|HIGH)", numeric_prompt)), False)
    check("the binary prompt still uses the bare literal it enforces",
          bool(_re.search(r"(?<!FIGURE )AMBIGUITY: HIGH",
                          src[src.index("async def _run_forecast_on_binary"):
                              src.index("async def _run_forecast_on_multiple_choice")])), True)

    # The criteria block was unlabelled on numeric, so edit 24 asked for a strict
    # reading of something the prompt never named. Binary's sentence, verbatim.
    check("the numeric prompt labels the resolution criteria",
          "These criteria have not yet been satisfied" in numeric_prompt, True)
    # And so does every other path. Edit 27's evidence — 90 peer points lost for
    # not stating the "assume it has not happened yet" convention — is not
    # numeric-specific, so leaving multiple choice and date unlabelled stopped
    # one path short of the argument. Third audit pass, 21 Sept 2026.
    check("...and so do all four prompts",
          src.count("These criteria have not yet been satisfied"), 4)
    for _p, _a, _b in (("multiple choice", "async def _run_forecast_on_multiple_choice",
                        "async def _multiple_choice_prompt_to_forecast"),
                       ("date", "async def _run_forecast_on_date",
                        "async def _date_prompt_to_forecast")):
        check(f"the {_p} prompt labels its criteria",
              "These criteria have not yet been satisfied" in src[src.index(_a):src.index(_b)], True)

    # ADVISORY, and now at least VISIBLE. Nothing enforces the numeric flag, so
    # the only honest thing to do is log whether one came back.
    _numpath = src[src.index("async def _numeric_prompt_to_forecast"):
                   src.index("##################################### DATE QUESTIONS")]
    check("the numeric path logs the flag it asked for",
          "_figure_ambiguity_flag(reasoning)" in _numpath, True)
    check("...under the name the prompt uses, so the log is greppable",
          '"FIGURE AMBIGUITY on %s: %s"' in _numpath, True)
    # A parser that raises kills the sample before any line below it runs, so
    # telemetry placed after the parse goes quiet on exactly the samples worth
    # looking at. Third audit pass, 21 Sept 2026.
    check("...and it is logged BEFORE the parse can kill the sample",
          _numpath.index("_figure_ambiguity_flag")
          < _numpath.index("_structure_output_paced"), True)
    check("...and the date path does not, because it never asks",
          "_figure_ambiguity_flag" in src[src.index("async def _date_prompt_to_forecast"):
                                          src.index("def _create_upper_and_lower_bound_messages")], False)

    flag, _ = load("_figure_ambiguity_flag")
    check("no flag returns None", flag("reasoning with no flag"), None)
    check("empty is safe", flag(""), None)
    check("None is safe", flag(None), None)
    check("a HIGH line is read", flag("blah\nFIGURE AMBIGUITY: HIGH\nblah"), "HIGH")
    check("a LOW line is read", flag("blah\nFIGURE AMBIGUITY: LOW"), "LOW")
    check("lower case is accepted", flag("figure ambiguity: high"), "HIGH")
    check("markdown decoration is accepted", flag("**FIGURE AMBIGUITY: HIGH**"), "HIGH")
    check("a bullet is accepted", flag("- FIGURE AMBIGUITY: LOW"), "LOW")
    check("the LAST line wins", flag("FIGURE AMBIGUITY: LOW\nthen\nFIGURE AMBIGUITY: HIGH"), "HIGH")
    check("mid-sentence is not an answer",
          flag("the FIGURE AMBIGUITY: HIGH marker goes here"), None)
    check("a hyphenated label still counts", flag("FIGURE-AMBIGUITY: HIGH"), "HIGH")
    # Telemetry must never kill a forecast, the same rule _scoring_grid_message
    # follows. Anything it cannot read is None, not a raise. Fourth audit pass.
    check("an object it cannot read is None, not a raise", flag(object()), None)
    check("a list is None, not a raise", flag([1, 2, 3]), None)
    # The two flags must not read each other. This is the whole point of the
    # rename: caps_for_reasoning anchors its line at AMBIGUITY, so a line
    # beginning FIGURE fails the anchor, and vice versa.
    check("the binary flag does not match the numeric one",
          caps("FIGURE AMBIGUITY: HIGH"), NORMAL)
    check("the numeric flag does not match the binary one",
          flag("AMBIGUITY: HIGH"), None)

    _binq = src[src.index("async def _run_forecast_on_binary"):
                src.index("async def _binary_prompt_to_forecast")]

    print("\n  -- the empty-research alarm reads the BASE research --")
    # BLOCKER, audit 22 Sept 2026. The subquestion block is appended before the
    # alarm reads the string, and its own headers clear 200 characters unaided.
    # Under a total research outage, control questions would all trip the
    # counter and in-arm questions structurally could not — a failure rate of
    # almost exactly 0.5 against a "> 0.5" test. The one alarm standing between
    # four unattended months and a bot forecasting from model weights while
    # exiting green, silenced by a boundary condition.
    _rr = src[src.index("async def run_research"):src.index("##################################### BINARY")]
    check("the base length is captured before enrichment",
          _rr.index("base_research_chars = len(research.strip())")
          < _rr.index("_add_subquestion_research"), True)
    check("...and the alarm tests that, not the enriched string",
          "if base_research_chars < 200:" in _rr, True)
    check("...the enriched string is never length-tested",
          bool(_re.search(r"len\(research\.strip\(\)\) < 200", _rr)), False)
    check("base research is fetched BEFORE the subquestion pass",
          _rr.index("self._invoke_researcher(researcher, prompt)")
          < _rr.index("self._add_subquestion_research(question, research)"), True)

    print("\n  -- the researcher dispatch, on every branch --")
    # 14 of 17 audit mutations survived because _invoke_researcher and
    # _add_subquestion_research had NO coverage beyond call-site counts.
    # Dropping an AskNews variant from the tuple sends that researcher silently
    # down the generic branch.
    _disp = src[src.index("async def _invoke_researcher"):src.index("async def _add_subquestion_research")]
    for _needed in ("asknews/news-summaries", "asknews/deep-research/low-depth",
                    "asknews/deep-research/medium-depth", "asknews/deep-research/high-depth"):
        check(f"the dispatch still routes {_needed}", _needed in _disp, True)
    check("...GeneralLlm is checked first, before any string comparison",
          _disp.index("isinstance(researcher, GeneralLlm)") < _disp.index("asknews/news-summaries"), True)
    check("...smart-searcher is isinstance-guarded, unlike upstream",
          'isinstance(researcher, str) and researcher.startswith("smart-searcher")' in _disp, True)
    check("...the no-research sentinels return an empty string",
          bool(_re.search(r'if not researcher or researcher in \("None", "no_research"\):', _disp)), True)
    check("...with exactly one generic fallback",
          _disp.count('self.get_llm("researcher", "llm").invoke(prompt)'), 1)
    check("one dispatch, two callers", src.count("await self._invoke_researcher("), 2)

    print("\n  -- subquestion research, randomised by a STABLE hash --")
    # Spring 2026 put "researches subquestions" at r = +0.24, q = 0.475, n = 41,
    # on a sample where 62% of respondents won a prize against 28% of the field.
    # That is a hypothesis, so half the season gets it and half does not.
    arm, _amod = load("_subquestion_arm", consts=())
    _ids = list(range(45500, 45900))
    _t = sum(1 for i in _ids if arm(i))
    # NOT just "is it in range": a helper starved of hashlib returns False for
    # everything through its own except, which reads as a 0% split. Assert both
    # arms are populated first. That mistake was made here, twice today, in two
    # different helpers.
    check("both arms are actually populated", 0 < _t < len(_ids), True)
    check("the split is near even over 400 ids", 0.40 < _t / len(_ids) < 0.60, True)
    check("...and is stable across calls", all(arm(i) == arm(i) for i in _ids), True)
    # THE WHOLE POINT. Python's builtin hash() is salted per process, so the same
    # question retried in a later run would land in the other arm and pollute
    # both. sha256 cannot do that, and the assignment can be recomputed months
    # later from the id alone without trusting a log.
    check("the assignment uses sha256, not the salted builtin hash",
          "hashlib.sha256" in src and _re.search(r"hash\(f?\"subq", src) is None, True)
    check("hashlib is imported at module level",
          bool(_re.search(r"^import hashlib", src, _re.MULTILINE)), True)
    # Fails to CONTROL, never to treatment: an unreadable id must not quietly
    # spend money on an experiment it cannot record.
    check("a missing id falls to control", arm(None), False)
    check("...and an empty string", arm(""), False)
    check("...and anything that is not an int or a str", arm(object()), False)
    check("string ids are supported", isinstance(arm("fall-2026-q1"), bool), True)

    _rs = src[src.index("async def _add_subquestion_research"):
              src.index("##################################### BINARY QUESTIONS")]
    check("the arm is logged for every question, both sides",
          'phase="research"' in _rs and "subquestion_arm=in_arm" in _rs, True)
    check("...before the early return, so control questions are recorded too",
          _rs.index("subquestion_arm=in_arm") < _rs.index("if not in_arm:"), True)
    check("the subquestions actually asked are logged",
          'phase="subquestions"' in _rs and "asked=subquestions" in _rs, True)
    # Two, not three. Audited arithmetic: a generation call plus three serial
    # research calls inside the three-slot limiter pushed a 40-question batch
    # from ~28 to ~35 minutes against what was then a 30-minute kill, and a
    # killed run loses most of a batch rather than a tail of it.
    check("at most two subquestions are researched", "][:2]" in _rs, True)
    check("...and a preamble line ending in a colon is not one of them",
          'not line.strip().endswith(":")' in _rs, True)
    # A length floor as well as the colon rule: without it, a stray blank or a
    # one-word line becomes a "subquestion" and buys a research call of its own.
    # Survived the first sweep.
    check("...and a line must be substantial to count at all",
          bool(_re.search(r'len\(line\.strip\(" -\*.t"\)\) > 15', _rs)), True)
    check("the subquestion researcher is asked for dated, sourced facts",
          "DATED, SOURCED facts" in _rs, True)
    check("...and each finding is headed by its own subquestion",
          "### {sub}" in _rs, True)
    # The join key. Everything downstream pairs the arm to the samples by
    # question id; swapping one path to the url breaks the A/B silently.
    check("the arm is keyed on the question id, not the url",
          '_subquestion_arm(getattr(question, "id_of_question", None))' in _rs, True)
    # Exact line: anything appended to it (a length test, a tier test) would
    # confound the arm with the thing appended and quietly destroy the A/B.
    check("...and nothing else conditions the arm",
          '        in_arm = _subquestion_arm(getattr(question, "id_of_question", None))\n' in _rs, True)
    for _p, _a, _b in (("binary", "async def _binary_prompt_to_forecast", "#### MULTIPLE CHOICE"),
                       ("multiple choice", "async def _multiple_choice_prompt_to_forecast", "#### NUMERIC"),
                       ("numeric", "async def _numeric_prompt_to_forecast", "#### DATE"),
                       ("date", "async def _date_prompt_to_forecast", "def _create_upper_and_lower_bound_messages")):
        _sp = src[src.index(_a):src.index(_b, src.index(_a))]
        check(f"the {_p} line joins on q=question.id_of_question",
              "q=question.id_of_question" in _sp, True)

    check("the subquestion researcher is told not to forecast",
          "do not state an expected outcome" in _rs, True)
    # Additive enrichment, not a forecast: a failure here must cost only the
    # subquestions.
    check("a failure returns the ORIGINAL research",
          bool(_re.search(r"except Exception as exc:.*?\n.*?logger\.warning.*?\n\s+return research", _rs, _re.S)), True)
    check("...and both arms share one researcher dispatch, so they cannot drift",
          src.count("async def _invoke_researcher") == 1
          and _rs.count("self._invoke_researcher(") == 1, True)

    print("\n  -- telemetry: one line per sample, so the season is analysable --")
    # The season is being entered to MEASURE, with several changes shipping at
    # once. Without a per-sample record the analysis afterwards is archaeology.
    check("json is imported at module level, not swallowed by a comment",
          bool(_re.search(r"^import json", src, _re.MULTILINE)), True)
    check("the marker is a single greppable token", 'TELEMETRY_MARKER = "IBJ-TELEMETRY"' in src, True)
    # Four sample lines plus two from the research pass (the A/B arm, and the
    # subquestions actually asked). The per-path loop below is what pins the
    # four; this pins the total so a stray call cannot appear unnoticed.
    check("six telemetry call sites, and no more", src.count("        _telemetry("), 6)
    for _fn, _end in (("_binary_prompt_to_forecast", "#### MULTIPLE CHOICE"),
                      ("_multiple_choice_prompt_to_forecast", "#### NUMERIC"),
                      ("_numeric_prompt_to_forecast", "#### DATE"),
                      ("_date_prompt_to_forecast", "def _create_upper_and_lower_bound_messages")):
        _b = src[src.index(f"async def {_fn}"):src.index(_end, src.index(f"async def {_fn}"))]
        check(f"{_fn} emits telemetry", "_telemetry(" in _b, True)
        # On the SUCCESS path only. A sample that fails never reaches the line,
        # which is how the parse-failure count falls out by subtraction without
        # any exception handling around a forecast.
        check(f"...{_fn} logs immediately before the return",
              _b.index("_telemetry(") < _b.rindex("return ReasonedPrediction"), True)
    # The date prompt never asks for a FIGURE AMBIGUITY flag, so logging one
    # would fill a column with nulls and imply a question that was never put.
    _dt = src[src.index("async def _date_prompt_to_forecast"):
              src.index("def _create_upper_and_lower_bound_messages")]
    check("the date line does not carry the numeric flag", "figure_ambiguity" in _dt, False)
    # ...but the NUMERIC line must, since that prompt does ask for it and the
    # whole point is to see whether the adversarial read is happening.
    _nt = src[src.index("async def _numeric_prompt_to_forecast"):
              src.index("##################################### DATE QUESTIONS")]
    check("the numeric line carries the figure-ambiguity flag",
          "figure_ambiguity=_figure_ambiguity_flag(reasoning)" in _nt, True)
    check("...and every sample's percentiles, for the aggregation counterfactual",
          "pc.percentile, pc.value" in _nt, True)
    _bt = src[src.index("async def _binary_prompt_to_forecast"):
              src.index("##################################### MULTIPLE CHOICE")]
    check("the binary line carries the status quo pair",
          "sq_anchor=" in _bt and "sq_final=" in _bt, True)
    check("...and the pre-cap value, so the caps can be measured too",
          "raw=binary_prediction.prediction_in_decimal" in _bt, True)
    check("...and the caps themselves, so their effect stays measurable",
          "floor=floor" in _bt and "ceiling=ceiling" in _bt, True)

    # RUN them. Telemetry that raises costs a forecast, and a regex that misses
    # makes the season unanalysable — neither shows up in a source grep.
    # TELEMETRY_MARKER must be lifted too: without it _telemetry raises NameError
    # inside its own except and emits nothing, silently. Which is exactly what
    # happened the first time this test was written.
    tel, sqp, _tmod = load("_telemetry", "_status_quo_pair", consts=("TELEMETRY_MARKER",))
    _emitted = []
    _tmod.logger.info = lambda *a: _emitted.append(a[0] % a[1:] if len(a) > 1 else a[0])
    tel(q=1, kind="BinaryQuestion", sample=0.31, sq_anchor=8.0, sq_final=65.0)
    check("a line is emitted with the marker", _emitted[-1].startswith("IBJ-TELEMETRY "), True)
    check("...and the payload is valid JSON",
          _json.loads(_emitted[-1].split(" ", 1)[1])["sq_final"], 65.0)
    # A tuple key defeats json's default=str and raises TypeError. object() does
    # NOT — default=str absorbs it — so the first version of this check passed
    # while the swallow was mutated to a re-raise. Mutation sweep, 22 Sept 2026.
    check("an unserialisable payload does not raise",
          raises(lambda: tel(q=2, sample={(1, 2): 3})), False)
    check("...and the good line before it still got out", len(_emitted) >= 1, True)

    for _label, _text, _want in (
        ("the walk-back case, anchor and revised final",
         "(f) 8%\n(g) 65%\n(h) cannot name a completed step, moving to 20%.", (8.0, 65.0)),
        ("prose around the numbers", "(f) status quo continuing: 12\n(g) final: 34", (12.0, 34.0)),
        ("markdown decoration", "**(f)** 5%\n**(g)** 9%", (5.0, 9.0)),
        ("a bulleted pair, which a narrower class would miss",
         "- (f) 5%\n- (g) 9%", (5.0, 9.0)),
        ("a blockquoted pair", "> (f) 30\n> (g) 44", (30.0, 44.0)),
        ("the LAST of each letter wins", "(f) 10%\nrevised\n(f) 15%\n(g) 20%", (15.0, 20.0)),
        ("no pair at all", "no letters here", (None, None)),
        ("empty reasoning", "", (None, None)),
        ("None reasoning", None, (None, None)),
    ):
        check(f"status quo pair: {_label}", sqp(_text), _want)
    check("...and rubbish returns None rather than raising", sqp(object()), (None, None))

    print("\n  -- the researcher does not pre-judge the answer --")
    # Upstream asked the research model for a rundown "including if the question
    # would resolve Yes or No based on current information", and handed that
    # verdict to the forecaster as "Your research assistant says:". All five of
    # our confident binary failures were institutional questions with fresh
    # announcement coverage — exactly what a news model asked "would this resolve
    # Yes?" says yes to. Found by an audit aimed at a different question.
    _res = src[src.index("async def run_research"):src.index("##################################### BINARY")]
    check("the researcher is NOT asked whether it would resolve Yes or No",
          "including if the question would resolve Yes or No" in _res, False)
    check("...and is told plainly that the verdict is not its job",
          "you do not say whether the question would resolve Yes or No" in _res, True)
    check("...it is asked for dated, sourced facts",
          "DATED, SOURCED facts" in _res, True)
    check("...and for what has NOT happened yet, which the status quo check needs",
          bool(_re.search(r"have NOT yet happened as of today", _res)), True)
    check("...keeping intent separate from completion",
          bool(_re.search(r"a stated intention clearly separate from the thing itself", _res)), True)
    # The downstream patch for the same symptom stays: research can still mislead
    # without a verdict attached.
    check("the still-open guard downstream is retained",
          "STILL OPEN and has NOT yet resolved" in src, True)

    print("\n  -- one anchor, not two --")
    # (b) said "treat that rate as your starting anchor" and (f), eleven lines
    # later, said "That is your anchor." Two instructions, same word, different
    # numbers. A model resolves that by picking whichever is nearer the answer it
    # already prefers. Audit, 22 Sept 2026.
    check("only the status quo check claims the word anchor",
          len(_re.findall(r"is your anchor", _binq)), 1)
    check("...and the reference-class rate is named as evidence instead",
          "It is evidence,\n                not your anchor" in _binq, True)
    check("...(b) is no longer called the base rate's anchor",
          "treat that rate as your starting anchor" in _binq, False)
    check("...and still asks for a number",
          "state the rate as a number" in _binq, True)

    print("\n  -- the status quo check: the measured fix, 22 Sept 2026 --")
    # MEASURED over 59 scored questions. The low half of our binary book was well
    # calibrated (said 24%, delivered 20%); the high half said 71% and delivered
    # 50%. Ten confident-YES calls returned -240.5 from five failures against
    # +67.0 from five successes — 88% of the binary loss from 40% of the binary
    # questions. The bot's own comments show it naming the status quo correctly
    # and then overriding it on momentum: q45527 "(c) Status quo outcome: NO",
    # forecast 86%, resolved NO, -80.9.
    check("the binary prompt runs a status quo check", "STATUS QUO CHECK" in _binq, True)
    check("...it asks for the anchor as a NUMBER, not a narrative",
          "probability implied by the status quo simply continuing" in _binq, True)
    check("...and for the final probability as a number too",
          "(g) Your final probability, as a number." in _binq, True)
    check("...it sets a threshold for departing from the anchor",
          "more than 20 points above (f)" in _binq, True)
    # SCOPED TO THE MEASUREMENT. The <=50% book was well calibrated (24% stated,
    # 20% delivered) and must not be pushed down with the bad half. A
    # status-quo-NO anchor sits near 5-15%, so an ungated 20-point rule would
    # have fired on a perfectly good 30%. Audit, 22 Sept 2026.
    check("...and only on the half of the book that was measured broken",
          "(g) is ABOVE 50% and more than 20 points above (f)" in _binq, True)
    # M7 survived an earlier mutation sweep: neutering the walk-back left every
    # check green. The DIRECTION of the correction is the correction.
    check("...and the correction walks the number DOWN, not anywhere",
          "move (g) back toward (f)" in _binq, True)
    check("...and that number is what gets stated",
          bool(_re.search(r"number you end on after \(h\) is the one you state", _binq)), True)
    check("...the momentum warning survives",
          "Momentum in the news is not the" in _binq, True)
    check("...and demands COMPLETED evidence to depart",
          "COMPLETED, DATED, VERIFIABLE step" in _binq, True)
    # The specific failure: an announcement was read as completion, five times.
    for _notevidence in ("An announcement", "a stated intention", "a scheduled meeting",
                         "press coverage", "elapsed time"):
        check(f"...{_notevidence!r} is named as NOT a completed step",
              _notevidence in _binq, True)
    check("...and the instruction is to move back toward the anchor",
          "move (g) back toward (f)" in _binq, True)
    check("the institutional slippage rule is present",
          "Institutions miss deadlines" in _binq, True)
    # Whitespace-insensitive: these phrases wrap across lines in the prompt.
    check("...naming the classes that actually cost us",
          all(bool(_re.search(x.replace(" ", r"\s+"), _binq))
              for x in ("introduced a bill", "begun formal talks",
                        "crossed a reporting threshold")), True)
    # The check is last. If the adversarial read came after it, the model would
    # re-open the case for YES — the ordering bug already made once on numeric.
    check("the status quo check comes AFTER the adversarial criteria read",
          _binq.index("STATUS QUO CHECK") > _binq.index("read the resolution criteria adversarially"), True)
    check("...and after the Yes/No scenarios",
          _binq.index("STATUS QUO CHECK") > _binq.index("results in a Yes outcome"), True)
    # NOT just "before the answer line" — that was trivially true while the
    # rationale paragraph and the conditional disclaimer sat BETWEEN the check and
    # the answer, letting the model re-argue itself back up after checking. The
    # block must come after the rationale, immediately before the answer.
    check("...and is genuinely the last block before the final answer",
          _binq.index("good forecasters put extra weight")
          < _binq.index("STATUS QUO CHECK")
          < _binq.index('"Probability: ZZ%"'), True)
    check("...with nothing between it and the answer but its own text",
          _binq.index('"Probability: ZZ%"') - _binq.index("Momentum in the news") < 200, True)
    check("the final-answer line is not duplicated",
          _binq.count("The last thing you write is your final answer"), 1)
    # DELIBERATELY MINIMAL. If this is ever measured, the mover must be knowable.
    check("the lettered list was NOT reordered",
          _binq.index("(d) A brief description of a scenario that results in a No outcome.")
          < _binq.index("(e) A brief description of a scenario that results in a Yes outcome."), True)

    # NEW HAZARD CREATED BY THE ABOVE: (f) is a probability stated as a number
    # immediately before the answer — the likeliest thing for a cheap parser to
    # lift by mistake. Caught before any run.
    # BLOCKER, caught by audit before any run. The parser was told "the answer is
    # (g)" — but (h) exists to REVISE (g) downward, so on exactly the questions
    # edit 30 corrects, the parser was pointed at the PRE-correction number. The
    # edit inverted itself, and since it newly invites a high (g) before the
    # walk-back it could have published higher than before.
    check("the parser is warned off the status quo anchor",
          "STATUS QUO ANCHOR at (f)" in src, True)
    check("...and off the working figure at (g) as well",
          "the working figure at (g)" in src, True)
    check("...it is told the FINAL line is authoritative",
          'The answer is ALWAYS the final "Probability:" line' in src, True)
    check("...and that (h) may revise (g)",
          bool(_re.search(r"state a figure at \(g\) and then REVISE it at \(h\)", src)), True)
    check("...and it is NEVER told to take (g)",
          "the answer is (g)" in src, False)

    print("\n  -- multiple choice: the floor went on the option that resolved --")
    # 4 questions, -139.6, avg -34.91. Both big losses were the nothing-happened
    # option sitting at our 0.01 floor: "Rank 21 or lower" (-129.9) and "No public
    # S-1 filed yet" (-31.8). Same defect as the binary one, different shape.
    _mcq = src[src.index("async def _run_forecast_on_multiple_choice"):
               src.index("async def _multiple_choice_prompt_to_forecast")]
    check("the MC prompt makes the model NAME the status quo option",
          "the status quo option" in _mcq and "Name it exactly as it appears" in _mcq, True)
    check("...describing how to recognise it",
          all(x in _mcq for x in ("lowest band", "none of the", "present state")), True)
    # Decimals, not percentages: the MC answer format is probabilities summing to
    # 1, and unlike binary its parsing instruction has no percent-to-decimal rule.
    check("...and floors it at 0.10, in the units the path actually uses",
          "at least 0.10" in _mcq, True)
    check("...with no percent signs introduced into a decimal prompt",
          bool(_re.search(r"\d%", _mcq)), False)
    # An upper bound or a hedge would make the floor advisory; both survived an
    # earlier mutation sweep undetected.
    check("...and the floor is a floor, not a range",
          bool(_re.search(r"at least 0\.10[^.]*at most", _mcq)), False)
    check("...and is not hedged into an opinion",
          bool(_re.search(r"at least 0\.10[^.]*(if you think|where reasonable|where you think)", _mcq)), False)
    check("...(d) resolves (b) onto the option list rather than asking twice",
          "Restate (b) as ONE OF THE LISTED OPTIONS" in _mcq, True)
    # Written as a whitespace-insensitive check on purpose: the phrase wraps
    # across a line in the prompt, and embedding that newline in this file is the
    # exact bug that broke the build on 1 Sept and again just now.
    check("...with completed evidence the only way below that",
          bool(_re.search(r"completed, dated,\s+verifiable event has ALREADY ruled it out", _mcq)), True)
    check("...and 1% reserved for the genuinely impossible",
          "genuinely impossible" in _mcq, True)
    # It must not contradict the parser rule it cannot see.
    # Robust, not literal: "0.01 minimum listed below" restored the defect while
    # the old literal check stayed green. The forecaster never sees the parsing
    # instruction, so the prompt must not point at anything "below".
    check("the prompt does not refer to a rule the forecaster never sees",
          bool(_re.search(r"(minimum|floor|rule)[^.]{0,30}below", _mcq)), False)
    check("the parser still refuses a literal zero",
          "NEVER emit exactly 0" in src, True)

    # No duplicate letters in either lettered list. Two mutations relettering a
    # step to collide with an existing one survived the earlier sweep.
    for _label, _span in (("binary", _binq), ("multiple choice", _mcq)):
        _letters = _re.findall(r"^\s{12}\(([a-h])\) ", _span, _re.MULTILINE)
        check(f"the {_label} lettered list has no duplicates",
              len(_letters) == len(set(_letters)), True)
        check(f"...and no gaps in the {_label} sequence",
              _letters == sorted(_letters), True)

    print("\n  -- the binary parser fills a DECIMAL field --")
    # BLOCKER, caught by audit 21 Sept 2026 before any paid run. The first
    # version of the binary parsing instruction said "as a percentage from 0 to
    # 100". The field is BinaryPrediction.prediction_in_decimal, whose validator
    # raises outside [0, 1] — and STRUCTURE_OUTPUT_ALLOWED_TRIES is 1, so a
    # raise is a dead sample, not a retry. Worse, a 1% forecast parsed as "1"
    # coerces to 0.999 and then clamps to BINARY_CEILING: a 1% belief published
    # at 98%, silently, with one warning line in a four-month log.
    _binpath = src[src.index("async def _binary_prompt_to_forecast"):
                   src.index("##################################### MULTIPLE CHOICE")]
    check("the binary parser is told the field is a decimal",
          "DECIMAL BETWEEN 0 AND 1" in _binpath, True)
    check("...and is given the conversion",
          "73% becomes 0.73" in _binpath, True)
    check("...and is NOT told to emit 0 to 100",
          "percentage from 0 to 100" in _binpath, False)
    check("...and that anything above 1 in the FIELD is wrong",
          "A value above 1 in that field is always wrong" in _binpath, True)
    check("...and to ignore every other percentage in the text",
          "Ignore every other percentage in the text" in _binpath, True)
    # BLOCKER, third audit pass. The first escape hatch read "a value between 0
    # and 1" — which is what "1%" and "0.5%" look like to a literal parser, so
    # it reopened the 1%-published-at-98% inversion that the bullet above it
    # exists to close. The hatch must key on the ABSENCE OF A PERCENT SIGN.
    check("the decimal escape hatch keys on the percent sign, not the size",
          'Never read "1%" as 1' in _binpath, True)
    check("...and the old wording is gone",
          "a value between 0 and 1" in _binpath, False)
    # SECOND ATTEMPT AT THE SAME BULLET, fourth audit pass. "WITHOUT a percent
    # sign, use unchanged" still let "Probability: 1" through: the validator
    # maps exactly 1 to 0.999 rather than raising, and the caps then publish it
    # at 0.98. The hatch is now bounded to decimals BELOW 1.
    check("...the hatch only covers decimals below 1",
          "WITHOUT a percent sign as a decimal below 1" in _binpath, True)
    check("...and a bare 1 is spelled out as 0.01",
          '"Probability: 1" is 0.01' in _binpath, True)
    check("...and a bare 73 as 0.73",
          '"Probability: 73" is 0.73' in _binpath, True)
    # Mutation survivors, fourth audit pass: the suite asserted these bullets
    # EXISTED but never what they SAID, so reversing their meaning stayed green.
    check("...the parser takes the FINAL answer, not the first",
          'The answer is ALWAYS the final "Probability:" line' in _binpath, True)
    check("...the hatch says use the value unchanged",
          "use that value unchanged" in _binpath, True)
    check("...and the sub-1% conversion is stated correctly",
          "0.5% is 0.005" in _binpath, True)
    check("...and the caps still apply after the parse",
          "caps_for_reasoning(reasoning)" in _binpath, True)

    print("\n  -- a short option list costs a SAMPLE, not the question --")
    # MultipleChoiceReport.aggregate_predictions raises on mismatched option
    # names OUTSIDE the per-sample gather, so one truncated parse forfeited the
    # whole question. Same class as the numeric get_cdf() fix. Audit, 21 Sept.
    _mcpath = src[src.index("async def _multiple_choice_prompt_to_forecast"):
                  src.index("##################################### NUMERIC")]
    check("the guard says so when it cannot read the options",
          "Option guard could not read this question's options" in src, True)
    check("the check is called on the multiple-choice path",
          "_reject_mismatched_options(predicted_option_list, question)" in _mcpath, True)
    check("...before the value is returned",
          _mcpath.index("_reject_mismatched_options")
          < _mcpath.index("return ReasonedPrediction"), True)

    # RUN it. A mutation test turned the raise into a logger.warning and every
    # check still passed, because they only grepped the source. Second audit.
    reject, _ = load("_reject_mismatched_options")
    class _Opt:
        def __init__(self, name): self.option_name = name
    class _List:
        def __init__(self, names): self.predicted_options = [_Opt(x) for x in names]
    _q3 = _Q(options=["Yes", "No", "Maybe"])
    check("a correct list is accepted", reject(_List(["Yes", "No", "Maybe"]), _q3), None)
    check("order does not matter", reject(_List(["Maybe", "Yes", "No"]), _q3), None)
    check("a truncated list is rejected", raises(reject, _List(["Yes"]), _q3), True)
    check("an extra option is rejected",
          raises(reject, _List(["Yes", "No", "Maybe", "Other"]), _q3), True)
    # A SET comparison would pass this one, and the library's separate length
    # check would then raise OUTSIDE the gather — losing the whole question, and
    # if the duplicating sample is predictions[0], losing the good ones too.
    check("a DUPLICATED option is rejected",
          raises(reject, _List(["Yes", "Yes", "No", "Maybe"]), _q3), True)
    check("a renamed option is rejected", raises(reject, _List(["yes", "No", "Maybe"]), _q3), True)
    # ...and it must go quiet, not loud, if the library ever renames a field.
    check("an unrecognised shape is ignored, not raised on",
          reject(object(), _q3), None)
    check("...and so is a question with no options", reject(_List(["Yes"]), object()), None)

    print("\n  -- the grid line never leaves an orphan bullet --")
    # _scoring_grid_message returns "" on any anomaly. The prompt used to supply
    # the "- " itself, so an empty return rendered a bare dash, and the bullet
    # below it referred to "the scoring bins above" that were no longer there.
    check("the message carries its own bullet",
          grid(_Q(cdf_size=201, lower_bound=0, upper_bound=100)).startswith("- "), True)
    check("...and an empty one carries nothing", grid(_Q()), "")
    check("the prompt no longer supplies a dash",
          "- {grid_message}" in numeric_prompt, False)
    check("the concentration bullet is self-contained",
          "this question's scoring bins are FINER" in numeric_prompt, True)
    check("...and no longer points upward at a line that may be absent",
          "the scoring bins above" in numeric_prompt, False)

    print("\n  -- the date parser is scoped like the numeric one --")
    _datepath = src[src.index("async def _date_prompt_to_forecast"):
                    src.index("def _create_upper_and_lower_bound_messages")]
    check("the date parser reads only the final block",
          'Parse ONLY the final "Percentile NN: YYYY-MM-DD" block' in _datepath, True)
    check("...and names the format the date prompt actually asks for",
          "Percentile 10: YYYY-MM-DD" in src, True)

    print("\n  -- parser guards, ported by hand from forecasting-tools v0.3.0 --")
    # v0.3.0 added them to ITS copy of the template. Bumping the dependency
    # delivers neither, because this file vendors its own bot class and every
    # parse call is ours. Verified 21 Sept 2026.
    check("the shared constant exists", "_PARSER_GUARDS = (" in src, True)
    check("...guard one refuses an already-settled reading",
          "STILL OPEN and has NOT resolved" in src, True)
    check("...guard two refuses merging two answers",
          "Never merge two candidate answers and never average them" in src, True)
    # It must NOT read as "emit one option" on the multiple-choice path, where
    # the answer is a list of N and a short list forfeits the question.
    check("...and never says 'parse exactly one'",
          "Parse exactly one" in src, False)
    check("...it says use the last one IN FULL",
          "and use it IN FULL" in src, True)
    # MUTATION SURVIVOR, third audit pass: reversing LAST to FIRST left all 276
    # checks green. "Use the last, not the first" IS the guard; the rest is
    # decoration. Assert the word itself.
    check("...and it is the LAST answer, not the first",
          "Use only the LAST complete final answer" in src, True)
    check("...with no 'FIRST complete' anywhere near it",
          "FIRST complete" in src, False)
    check("all four parse paths reference it",
          src.count("{self._PARSER_GUARDS}"), 4)
    for _fn, _end in (("_binary_prompt_to_forecast", "#### MULTIPLE CHOICE"),
                      ("_multiple_choice_prompt_to_forecast", "#### NUMERIC"),
                      ("_numeric_prompt_to_forecast", "#### DATE"),
                      ("_date_prompt_to_forecast", "def _create_upper_and_lower_bound_messages")):
        _body = src[src.index(f"async def {_fn}"):src.index(_end, src.index(f"async def {_fn}"))]
        check(f"{_fn} carries the guards", "{self._PARSER_GUARDS}" in _body, True)
        # NOT just "additional_instructions=" — that matches "=None", and a
        # mutation test proved the whole suite stays green while the blocker fix
        # is built and then thrown away. Second audit pass, 21 Sept 2026.
        check(f"{_fn} passes additional_instructions",
              "additional_instructions=parsing_instructions" in _body, True)
    # The guards are substituted into a clean_indents() prompt. If the second
    # bullet lost its twelve-space indent the whole prompt would stop dedenting.
    # The guards are substituted into a clean_indents() prompt. Evaluate the
    # literal rather than eyeballing it. NOTE, corrected 21 Sept 2026: an
    # earlier version of this comment claimed a flush-left line would stop the
    # whole prompt dedenting. It would not. clean_indents() is not
    # textwrap.dedent — it takes the deeper of the first two lines' indents and
    # lstrips anything shallower — so the twelve-space continuation is for
    # readability, not correctness. The shape is still asserted, because a
    # ragged constant is a sign the patch mangled it.
    _guards = None
    for _node in ast.walk(ast.parse(src)):
        if isinstance(_node, ast.Assign) and any(
            getattr(_t, "id", "") == "_PARSER_GUARDS" for _t in _node.targets
        ):
            _guards = ast.literal_eval(_node.value)
    check("the guards constant evaluates", _guards is None, False)
    _lines = (_guards or "").split("\n")
    check("the guards are exactly two bullets", len(_lines), 2)
    check("the first starts flush, to sit after the placeholder",
          _lines[0].startswith("- The question is STILL OPEN"), True)
    check("the second carries twelve spaces, to align under it",
          len(_lines) > 1 and _lines[1].startswith(" " * 12 + "- The text may contain"), True)
    # The old check here ("no element contains a newline") was vacuous: _lines
    # came from split("\n"), so no element ever could. Replaced with the thing
    # that actually matters — the escape must still be an ESCAPE in
    # patch_phase1.py. A real newline there is the 1 Sept bug, and it would
    # split the string literal rather than the rendered text.
    _psrc = pathlib.Path(__file__).with_name("patch_phase1.py").read_text()
    _pguards = _psrc[_psrc.index("_PARSER_GUARDS = ("):]
    _pguards = _pguards[:_pguards.index("\n    )")]
    check("the guards are one source line per bullet in the patch",
          len([_l for _l in _pguards.splitlines() if _l.strip().startswith("'")]), 2)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
