"""
Phase 1 patch for the Metaculus template bot.

Applies four evidence-backed, low-risk changes to the upstream template and
writes the result to main.py in the build directory. Every edit asserts that it
matched, so the script fails loudly rather than silently producing a bot that
looks fine and isn't.

Evidence source: Metaculus, "AI Forecasting in 2026: What 11 Analyses Say"
(8 Jul 2026) and the Fall 2025 bot-maker survey (39 respondents).
"""

import sys
import pathlib

SRC = pathlib.Path(sys.argv[1])
DST = pathlib.Path(sys.argv[2])

text = SRC.read_text(encoding="utf-8")
edits = 0


def replace(old, new, label):
    global text, edits
    assert text.count(old) == 1, f"PATCH FAILED [{label}]: found {text.count(old)} matches, expected exactly 1"
    text = text.replace(old, new)
    edits += 1
    print(f"  ok  {label}")


def replace_all(old, new, expected, label):
    """Same contract as replace(), but for an edit that must hit N sites.

    The count is stated rather than inferred, so if upstream adds or removes a
    call site the build fails instead of quietly gating three of four.
    """
    global text, edits
    found = text.count(old)
    assert found == expected, f"PATCH FAILED [{label}]: found {found} matches, expected exactly {expected}"
    text = text.replace(old, new)
    edits += 1
    print(f"  ok  {label} ({expected} sites)")


# ---------------------------------------------------------------------------
# 1. PREDICTION CAPS  (Fall 2025 survey: strongest within-winners
#    differentiator, r = +0.48, p = 0.005. Template ships 0.01/0.99; we tighten
#    modestly to 0.02/0.98. One over-confident error at 99% can erase a season,
#    while the upside of an extreme correct call is bounded by the scoring rule.)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 9. CONCURRENCY  (template ships 1 question at a time; see the run-time budget
#    note in the config block for why that is wrong for a 90-minute window.)
# ---------------------------------------------------------------------------
replace(
    """    _max_concurrent_questions = (
        1  # Set this to whatever works for your search-provider/ai-model rate limits
    )""",
    """    _max_concurrent_questions = MAX_CONCURRENT_QUESTIONS""",
    "concurrent questions from run-time budget",
)

replace(
    "        decimal_pred = max(0.01, min(0.99, binary_prediction.prediction_in_decimal))",
    "        floor, ceiling = caps_for_reasoning(reasoning)\n        decimal_pred = max(floor, min(ceiling, binary_prediction.prediction_in_decimal))",
    "binary prediction caps (ambiguity-bounded)",
)

# ---------------------------------------------------------------------------
# 2. OPEN-QUESTION GUARD  (Named repeatedly in Fall 2025 advice and the
#    FutureEval Discord as a recurring, expensive failure: the bot reads news
#    suggesting the outcome has happened, concludes the question has already
#    resolved, and submits a near-certain forecast on a question that is still
#    open and can still move.)
# ---------------------------------------------------------------------------
replace(
    """            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The status quo outcome if nothing changed.
            (c) A brief description of a scenario that results in a No outcome.
            (d) A brief description of a scenario that results in a Yes outcome.
""",
    """            This question is STILL OPEN and has NOT yet resolved. If your research
            appears to show the outcome is already settled, treat that as a warning
            sign rather than a conclusion: re-read the resolution criteria and the
            resolution date, and check whether the reported event actually satisfies
            them. Reporting that resembles the outcome is not the same as the outcome.

            FIRST, before anything else, read the resolution criteria adversarially.
            You are forecasting the exact wording, not the general topic. A single
            word routinely changes the answer — "the annual maximum" and "a NEW
            annual maximum" are different questions, and forecasters who answer the
            topic rather than the text lose on questions they understood perfectly.

            Write:
            (i)  The strictest reasonable reading of the resolution criteria, stated
                 as a precise test that some observable fact would have to pass.
            (ii) Any OTHER reading a careful person might take. If a different
                 reading would resolve the question differently, say so explicitly.

            Then, on its own line, exactly one of:
            AMBIGUITY: LOW
            AMBIGUITY: HIGH
            Use HIGH only when competing readings would genuinely resolve
            differently — not merely because the future is uncertain. Uncertainty
            about the world is normal and belongs in your probability. Uncertainty
            about what is being ASKED is different, and we handle it separately.

            Then write:
            (a) The time left until the outcome to the question is known.
            (b) The reference-class rate: how often outcomes of this general kind
                occur over a comparable period. Name the reference class, give the
                numbers behind it, and state the rate as a number. It is evidence,
                not your anchor - the status quo check below owns that word.
            (c) The status quo outcome if nothing changed.
            (d) A brief description of a scenario that results in a No outcome.
            (e) A brief description of a scenario that results in a Yes outcome.
""",
    "open-question guard + explicit base rate (binary)",
)

# ---------------------------------------------------------------------------
# 3. NUMERIC SAFETY NET  (Multiple Fall 2025 winners flagged the template's
#    numeric handling as weak; the top open-source bot lost ~80 points to a
#    single units bug. Percentiles that arrive out of order produce a malformed
#    distribution, so we sort defensively rather than trusting the prompt.)
# ---------------------------------------------------------------------------
replace(
    """        prediction = NumericDistribution.from_question(percentile_list, question)
        logger.info(
            f"Forecasted URL {question.page_url} with prediction: {prediction.declared_percentiles}."
        )
        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)

    ##################################### DATE QUESTIONS #####################################""",
    """        percentile_list = _sorted_percentiles(percentile_list)
        prediction = NumericDistribution.from_question(percentile_list, question)
        # Force the CDF here, inside the per-sample coroutine, so a bad sample
        # fails as a SAMPLE. NumericReport.aggregate_predictions expands every
        # sample with a list comprehension, so a raise there kills the whole
        # question even when four of five samples were perfect — which defeats
        # the entire point of rejecting bad samples rather than repairing them.
        # Several checks (CDF spacing, distance from bounds, log-scale zero
        # point) only fire at expansion, not at construction. Audit, 1 Sept 2026.
        prediction.get_cdf()
        logger.info(
            f"Forecasted URL {question.page_url} with prediction: {prediction.declared_percentiles}."
        )
        _telemetry(
            q=question.id_of_question,
            url=question.page_url,
            kind=type(question).__name__,
            sample=[(pc.percentile, pc.value) for pc in prediction.declared_percentiles],
            figure_ambiguity=_figure_ambiguity_flag(reasoning),
            chars=len(reasoning or ""),
        )
        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)

    ##################################### DATE QUESTIONS #####################################""",
    "wire monotonicity guard into numeric path, plus the numeric sample telemetry",
)

replace(
    "if __name__ == \"__main__\":",
    '''def caps_for_reasoning(reasoning: str) -> tuple[float, float]:
    """Return (floor, ceiling) for a binary forecast, tightened if the model
    flagged the resolution criteria as genuinely ambiguous.

    Matches only a flag on its OWN LINE, and takes the LAST one.

    The first version searched the whole text for HIGH and for LOW, and fell
    back to the normal caps whenever both appeared. An audit on 1 Sept 2026
    showed that made the guard almost inert: the prompt itself hands the model
    both literal strings (see the binary prompt), and models routinely restate
    the instruction before answering it — "I must output either AMBIGUITY: LOW
    or AMBIGUITY: HIGH ... AMBIGUITY: HIGH". Both strings present meant normal
    caps, so the guard failed OPEN on exactly the questions it exists for. The
    old unit test asserted that behaviour as correct, which locked it in.

    Line anchoring separates the restated instruction from the answer, and
    last-match-wins takes the model's conclusion rather than its preamble.
    Still fails safe: no flag at all yields the normal caps, and a model that
    only ever echoes the instruction ends on HIGH, which tightens. Tightening
    is the safe direction under a scoring rule this asymmetric.
    """
    text = reasoning or ""
    # Tolerates markdown decoration: **AMBIGUITY: HIGH**, "- AMBIGUITY: HIGH",
    # "### AMBIGUITY: HIGH", a trailing full stop, italics, blockquotes. The
    # tighter first version matched none of those, and the SEASON tier runs
    # claude-fable-5, which bolds headings by habit — so the guard could have
    # sat inert for four months on the only tier that scores. Audit, 2 Sept.
    #
    # Line-anchoring and last-match-wins are retained, and still refuse the
    # restated instruction ("I must output either AMBIGUITY: LOW or AMBIGUITY:
    # HIGH.") that they were introduced to defeat: the line must BEGIN with the
    # marker, decoration aside. Verified against thirteen cases.
    #
    # Deliberately NO newline escape in any class: this text is embedded in a
    # non-raw string inside patch_phase1.py, where a backslash-n becomes a real
    # newline and silently corrupts both comment and pattern. It did, on 1 Sept
    # 2026. Every backslash below is doubled over there.
    flags = re.findall(
        r"^[ \\t\\r>#*_-]*AMBIGUITY[ \\t\\r*_]*:[ \\t\\r*_]*(HIGH|LOW)[ \\t\\r*_.!:]*$",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if flags and flags[-1].upper() == "HIGH":
        logger.info(
            "Resolution criteria flagged AMBIGUOUS — capping to %.2f-%.2f",
            AMBIGUOUS_FLOOR,
            AMBIGUOUS_CEILING,
        )
        return AMBIGUOUS_FLOOR, AMBIGUOUS_CEILING
    if not flags:
        logger.warning("No AMBIGUITY flag found on its own line — using normal caps")
    return BINARY_FLOOR, BINARY_CEILING


def _figure_ambiguity_flag(reasoning: str):
    """Return "HIGH", "LOW" or None for the numeric prompt's FIGURE AMBIGUITY flag.

    TELEMETRY ONLY. Nothing downstream acts on the return value. It exists
    because the numeric prompt asks for the flag and, until 21 Sept 2026, no
    line of the run log ever mentioned whether one came back — so there was no
    way to tell a model that was doing the adversarial read from one that was
    silently skipping it. On binary the equivalent flag is ENFORCED by
    caps_for_reasoning(); here it is advisory, and the honest way to say so is
    to log it rather than pretend it did something.

    The literal is FIGURE AMBIGUITY, not AMBIGUITY, so the two flags cannot be
    confused in a log or matched by each other's pattern: caps_for_reasoning
    anchors its line at AMBIGUITY and will not match a line beginning FIGURE.

    Same decoration tolerance and same last-match-wins rule as the binary flag,
    for the same reasons. Every backslash below is doubled in patch_phase1.py.
    """
    try:
        flags = re.findall(
            r"^[ \\t\\r>#*_-]*FIGURE[ \\t\\r*_-]+AMBIGUITY[ \\t\\r*_]*:[ \\t\\r*_]*(HIGH|LOW)[ \\t\\r*_.!:]*$",
            reasoning or "",
            re.IGNORECASE | re.MULTILINE,
        )
    except Exception:  # telemetry must never kill a forecast
        return None
    return flags[-1].upper() if flags else None


def _sorted_percentiles(percentile_list):
    """Sort percentiles by declared percentile; REJECT a non-monotonic sample.

    This used to "repair" bad output by forcing the values monotonic. That was
    wrong, and an audit on 31 Aug 2026 demonstrated why: a fully reversed parse
    [90, 50, 10] became [90, 90, 90] — a near point-mass at the wrong end of
    the range, published with confidence. Under a log score that is close to
    the worst thing a forecast can do, and the original unit test asserted it
    as correct behaviour.

    Rejecting is strictly better. The bot draws several independent samples per
    question and aggregates them, so raising here discards ONE bad sample and
    keeps the good ones — which is what the library does natively. The repair
    replaced a safe failure with a confident wrong answer.

    Sorting by declared percentile is kept: that is order-only and harmless.
    """
    ordered = sorted(percentile_list, key=lambda p: p.percentile)
    running_max = None
    for entry in ordered:
        if running_max is not None and entry.value < running_max:
            raise ValueError(
                "Non-monotonic numeric percentiles: value "
                f"{entry.value} at percentile {entry.percentile} is below an "
                f"earlier value of {running_max}. Discarding this sample "
                "rather than publishing a corrupted distribution."
            )
        running_max = entry.value
    return ordered


if __name__ == "__main__":''',
    "numeric percentile monotonicity guard",
)


# ---------------------------------------------------------------------------
# 4. CONFIGURATION BLOCK  (Everything tunable in one place at the top of the
#    file, so later phases change constants rather than scattered logic. Also
#    carries the season-rollover helpers and the empty-research counter.)
# ---------------------------------------------------------------------------
replace(
    'dotenv.load_dotenv()\nlogger = logging.getLogger(__name__)',
    'dotenv.load_dotenv()\nlogger = logging.getLogger(__name__)\n\n# Used by caps_for_reasoning(). The upstream template does not import re, and\n# a missing import here would raise on EVERY binary question — caught by the\n# build check added 31 Aug 2026, which is why that check exists.\nimport re  # noqa: E402\n\n# Used by the telemetry line: one JSON object per sample, so a season of logs\n# can be parsed rather than read.\nimport json  # noqa: E402\n\n# Used by resolve_seasonal_tournament() to read the AIB_TOURNAMENT_ID\n# repository variable. Not imported upstream either; same failure mode.\nimport os  # noqa: E402\n\n# Used by _subquestion_arm. sha256 rather than the builtin hash(), which is\n# randomised per process by PYTHONHASHSEED - the same question would land in a\n# different arm on every run and the A/B would measure nothing.\nimport hashlib  # noqa: E402\n\n# Counts questions forecast with little or no research, so the run can be\n# failed at the end rather than only logged. See the check near the bottom of\n# the file. Module-level because run_research is a method on the bot and the\n# exit decision is made in __main__.\nEMPTY_RESEARCH_COUNT = 0\n\n# The run fails only if BOTH are exceeded: a minority of thin research is\n# normal on niche questions, a majority means the researcher is broken. Failing\n# on a single instance manufactures red-fatigue, which costs more than it saves\n# when red is the only alarm this project has.\nEMPTY_RESEARCH_MIN_TO_FAIL = 3\nEMPTY_RESEARCH_FAIL_RATE = 0.5\n\n# Non-fatal things the run wants to say on the GitHub run page. Audit,\n# 6 Sept 2026: without this the step summary printed "OK - no open questions"\n# on the exact run where MiniBench had gone empty, talking over the only\n# detector we have for a dead slug. A summary that contradicts the warning is\n# worse than no summary.\nRUN_WARNINGS: list[str] = []\n\n# Questions each half actually HELD, before skip_previously_forecasted drops\n# the ones already done. Without it the summary could only report questions\n# attempted, which is 0 both when the tournament is empty and when everything\n# is already forecast — the normal steady state for most runs. Those two need\n# to look different at a glance.\nQUESTIONS_FOUND: dict = {}\n\n# Filled by preflight_check_balance(). None means "could not tell".\nBALANCE_NOTE = None\n\n# =============================================================================\n# PHASE 1 CONFIGURATION  —  all tunables live here, nowhere else.\n#\n# Evidence grades below refer to Metaculus, "AI Forecasting in 2026: What 11\n# Analyses Say" (8 Jul 2026), which synthesises 11 analyses plus the Fall 2025\n# survey of 39 bot makers (29 prize winners, 10 non-winners).\n# =============================================================================\n\n# Binary prediction caps. MODERATE evidence, and the strongest single\n# differentiator measured among winners (r = +0.48, p = 0.005). 38% of Fall\n# 2025 winners cap; 47% of the top fifteen do, against 29% of the bottom half.\nBINARY_FLOOR = 0.02\nBINARY_CEILING = 0.98\n\n# AMBIGUITY-BOUNDED CONFIDENCE.\n#\n# Peer score = 100 x (ln(p) - ln(geometric mean of other bots)). It is brutally\n# asymmetric: moving 99% -> 99.9% gains 0.009 when right and costs 2.3 when\n# wrong. The expensive error is confident-and-wrong.\n#\n# A systematic source of confident-and-wrong is not misjudging the world but\n# answering a different question from the one asked. Observed live in the\n# Metaculus bot Discord, 29 Aug 2026: "a lot of bots including mine\n# misinterpreted this question... interpreting as \'July is the annual max\'\n# instead of \'July is a NEW annual max\'." A whole cohort, one word.\n#\n# Other entrants enumerate interpretations to INFORM the forecast. We\n# additionally let interpretation ambiguity BOUND it: where competing readings\n# of the criteria would resolve differently, the bot is not entitled to\n# confidence however sure it is about the world. Uncertainty about the world\n# belongs in the probability; uncertainty about the QUESTION is handled here.\nAMBIGUOUS_FLOOR = 0.10\nAMBIGUOUS_CEILING = 0.90\n\n# MULTIPLE-CHOICE FLOOR — REMOVED 1 Sept 2026. Kept as a note, not as code.\n#\n# We floored multiple-choice options at 0.01 and renormalised, on the reasoning\n# that a 0% option which then RESOLVES scores about -691 and erases thirty good\n# questions. The reasoning about the scoring rule was right. The code was\n# pointless: PredictedOptionList carries a model_validator that runs on every\n# construction and already clamps each option to [0.01, 0.99] — the identical\n# value — before structure_output hands it back. Ours could only ever move a\n# probability by about 2e-4.\n#\n# It survived a week because the unit tests fed it (name, probability) tuples\n# directly, bypassing the SDK model. Data the SDK cannot produce, and in one\n# case actively rejects: an all-zero list raises on the sum check. The test\n# agreed with itself and never asked what the SDK does to the value afterwards\n# — the very failure this project had already diagnosed once, in the numeric\n# path, and written up as a lesson.\n#\n# Recorded rather than deleted silently, because the disclosure document should\n# show the retraction as well as the change.\n\n# Number of independent forecasts aggregated per question. STRONG evidence for\n# ensembling (86% of winners aggregate). Phase 2 will widen this across model\n# families; for now it is repeated sampling of one model.\n#\n# On the free tier this is forced to 1. Free models are served from a shared\n# upstream pool and rate-limit hard (a 429 killed our second test run); five\n# predictions per question means five forecast calls plus five parse calls,\n# which trips the limit within a couple of questions. One prediction is enough\n# to prove the plumbing, which is all the free tier is for.\nRESEARCH_REPORTS_PER_QUESTION = 1\n\n# -----------------------------------------------------------------------------\n# MODELS\n#\n# The template ships with no llms= block, so forecasting-tools picks defaults.\n# One of those defaults is openai/gpt-4o-search-preview, which OpenRouter does\n# not serve — it 404s on every question. So we name every model explicitly.\n#\n# All IDs below were verified against https://openrouter.ai/api/v1/models on\n# 29 Aug 2026. OpenRouter\'s free tier rotates with little notice, so if the bot\n# starts returning "No endpoints found", re-check that endpoint first.\n# -----------------------------------------------------------------------------\n\n# FOUR TIERS.\n#\n#   "free"   — :free models, zero balance. KEPT FOR REFERENCE, NOT RECOMMENDED.\n#              Four runs died on it across three providers. Every :free model\n#              has exactly ONE serving endpoint on one provider\'s shared pool,\n#              so there is no failover and the pool rate-limits under any load.\n#   "test"   — cheap paid models, ONE prediction a question. Development only.\n#   "trial"  — cheap paid models, FIVE predictions and live research. A real\n#              forecasting configuration we can afford to fund ourselves, for\n#              scored MiniBench rounds before the Metaculus credits arrive.\n#   "season" — frontier models, on Metaculus\'s credits, for the tournament.\n#\n# The difference is structural, not a matter of picking a better free model\n# (checked 1 Sept 2026 via the /endpoints API):\n#   z-ai/glm-5.2:free .......  1 endpoint   (Decart)          -> 429\'d us\n#   nvidia/nemotron:free ....  1 endpoint   (Nvidia)          -> 404\'d us\n#   google/gemma-4-31b:free .  1 endpoint   (Google AI Studio)-> 429\'d us\n#   openai/gpt-5-nano ....... 4 endpoints   (OpenAI, Azure)\n#   openai/gpt-oss-120b ..... 20 endpoints  (AkashML, CoreWeave, DeepInfra,\n#                                            Novita, SiliconFlow, Google, ...)\n# OpenRouter routes around dead endpoints automatically, so a paid model\n# tolerates a provider outage that kills a free one outright.\n#\n# The tier can be overridden by an environment variable, so a run can be\n# re-pointed without a commit and a CI cycle — the same reasoning as\n# AIB_TOURNAMENT_ID further down. Set a GitHub repository variable named\n# MODEL_TIER, and delete it to fall back to the default below. Note this does\n# NOT weaken assert_tier_matches_mode: a scored tournament still refuses to run\n# on anything outside TOURNAMENT_READY_TIERS, wherever the value came from.\n# (Changing the variable needs a GitHub password re-prompt, so it is a\n# deliberate human act by construction.)\nVALID_MODEL_TIERS = ("free", "test", "trial", "season")\nMODEL_TIER = (os.environ.get("MODEL_TIER") or "test").strip().lower()\nif MODEL_TIER not in VALID_MODEL_TIERS:\n    # Fail here rather than three steps later inside build_llm_config, so a\n    # typo in the repository variable names itself instead of surfacing as a\n    # confusing model error after the questions have already been fetched.\n    raise SystemExit(\n        f"MODEL_TIER must be one of {VALID_MODEL_TIERS}, got {MODEL_TIER!r}. "\n        "Check the MODEL_TIER repository variable."\n    )\n\n# Back-compat: several helpers below still ask "are we on the cheap tier?"\nUSE_FREE_MODELS = MODEL_TIER in ("free", "test")\n\n# Free tier. Not frontier, not competitive — these exist to prove the bot can\n# read a question, form a forecast and post it. "no_research" skips the search\n# step entirely, which removes a dependency we don\'t need while smoke-testing.\n# Parser and summarizer deliberately sit on a DIFFERENT upstream provider from\n# the default model. One provider must not be a single point of failure.\n#\n# EVERY free model on OpenRouter has exactly ONE serving endpoint — a single\n# provider, with no failover. That is why free-tier outages are total rather\n# than degraded. Checked 1 Sept 2026 via\n#   https://openrouter.ai/api/v1/models/<id>:free/endpoints\n# which exposes a per-endpoint `status` (0 = normal, negative = degraded).\n#\n# Providers that have already failed us, and are avoided here:\n#   Nvidia           — nemotron-3-ultra 404\'d mid-run on 30 Aug ("Provider\n#                      returned error"), despite still being listed and still\n#                      reporting status 0. Listing is not availability.\n#   Google AI Studio — gemma-4-31b 429\'d on 29 Aug from its shared free pool.\n#\n# So: default on Decart, parser/summarizer on GMICloud. Neither has failed us,\n# and they are independent of each other.\nFREE_MODELS = {\n    "default": "openrouter/z-ai/glm-5.2:free",\n    "summarizer": "openrouter/minimax/minimax-m3:free",\n    "parser": "openrouter/minimax/minimax-m3:free",\n    "researcher": "no_research",\n}\n\n# TEST tier — cheap paid models, chosen for endpoint COUNT as much as price.\n# Prices verified against OpenRouter\'s live model list, 1 Sept 2026, per 1M\n# tokens (input / output):\n#   openai/gpt-5-nano    $0.050 / $0.400   400k ctx,  4 endpoints\n#   openai/gpt-oss-120b  $0.037 / $0.170   131k ctx, 20 endpoints\n# A 7-question smoke test at one prediction each is roughly 14 calls and well\n# under 100k tokens total — comfortably under two pence a run. The $10 balance\n# should therefore cover several hundred test runs, not several.\nTEST_MODELS = {\n    "default": "openrouter/openai/gpt-5-nano",\n    "summarizer": "openrouter/openai/gpt-oss-120b",\n    "parser": "openrouter/openai/gpt-oss-120b",\n    # Test with research ON, so we are testing what we will actually run.\n    "researcher": "openrouter/perplexity/sonar",\n}\n\n# Endpoint-status preflight. Logs the health of each configured free model\n# before forecasting starts, so a provider outage appears at the top of the run\n# log as a warning rather than as a wall of 404s two minutes in. Deliberately\n# WARN-ONLY: a status check is not worth turning into a new way for the run to\n# die, and status 0 has already proved not to guarantee availability.\nPREFLIGHT_FREE_MODELS = True\n\n# -----------------------------------------------------------------------------\n# RUN-TIME BUDGET\n#\n# Tournament questions are open for only 1.5 hours (temporarily 3), launch at\n# random hours, and arrive up to FIVE at a time. A run that overruns the window\n# scores zero on every question it did not reach — and a missed question is a\n# zero in a total that is then squared, so misses compound.\n#\n# The top open-source bot\'s author attributes ~150 forfeited peer points, most\n# of a placing tier, to missed questions. None of it was a forecasting problem.\n# -----------------------------------------------------------------------------\n\n# How many questions to work on at once. The template ships 1, which is right\n# for a rate-limited free tier and wrong for a 90-minute window with five\n# questions in it. Serial worst case in-season is roughly 5 questions x 5\n# predictions x ~30s = ~12 minutes; at 3 concurrent that is ~4-5 minutes.\n# Paid models have many endpoints and real capacity, so concurrency is safe\n# here in a way it never was on a shared free pool.\nMAX_CONCURRENT_QUESTIONS = 1 if MODEL_TIER == "free" else 3\n\n# Retries per LLM call. This is the "never retry a slow failure" rule.\n# At timeout=120s, the old value of 6 meant one stubborn call could burn TWELVE\n# MINUTES on its own — retrying a timeout multiplies the wait rather than\n# fixing anything. Free-tier 429s are transient and worth retrying; paid\n# failures usually are not, and OpenRouter already fails over between endpoints.\nLLM_ALLOWED_TRIES = 6 if MODEL_TIER == "free" else 2\nLLM_TIMEOUT_SECONDS = 120 if MODEL_TIER == "free" else 90\n\n# Season tier. claude-fable-5 is the default because it currently sits top of\n# Metaculus\'s own FutureEval model leaderboard (13.23, ahead of Claude Opus 4.8\n# on 13.06 and GPT-5.5 Instant on 12.81). Phase 2 will spread the ensemble\n# across families for decorrelation — see ENSEMBLE_MODELS below.\nSEASON_MODELS = {\n    "default": "openrouter/anthropic/claude-fable-5",\n    "summarizer": "openrouter/google/gemini-3.7-flash",\n    "parser": "openrouter/google/gemini-3.7-flash",\n    # LIVE WEB SEARCH. This was "no_research" until 31 Aug 2026, which would\n    # have entered a tournament of 300-500 near-term news questions with the\n    # bot forecasting from model weights alone, against a training cutoff.\n    # The prompt would still have said "Your research assistant says:" followed\n    # by nothing. Metaculus\'s own evidence: removing search degrades Brier by\n    # 3.6x. The likely result was a NEGATIVE total peer score — and since the\n    # prize is max(total, 0) squared, negative pays nothing at all.\n    #\n    # perplexity/sonar searches the live web and runs on the OpenRouter key we\n    # already hold — no extra registration. $1/$1 per 1M tokens plus $0.005 per\n    # search, so a 400-question season costs roughly $3 in research.\n    "researcher": "openrouter/perplexity/sonar",\n}\n\n# TRIAL TIER. Strong-but-cheap, for scored runs we are paying for ourselves.\n#\n# CORRECTED 5 Sept 2026. The earlier note here read "around 8 percent off the\n# pace", taken from a model leaderboard. The Summer 2026 final leaderboard,\n# read per question rather than by tournament total, puts the gap far wider:\n# claude-fable-5-high averaged 6.94 peer points a question against\n# gemini-3.5-flash on 5.03 — about a quarter below, not 8 percent. Ranking\n# those bots by tournament TOTAL is what produced the wrong figure: total is\n# average score times questions answered, and the reference bots joined on\n# different dates, so totals mostly measure coverage.\n# OpenRouter prices them, verified live against /api/v1/models the same day, at\n# $0.75/$3.75 and $10/$50 per million tokens: Fable is 13.3x dearer for that 8\n# percent.\n#\n# Measured cost on the season tier was about $0.10 a prediction, so this tier\n# should land nearer $0.0075 — a 60-question MiniBench round for a few pounds\n# rather than about $30. Frontier models are the right call on Metaculus\'s\n# credits and the wrong one on ours.\n#\n# Research stays on Sonar. Cutting search is the one economy that reliably\n# loses more than it saves.\n# Parser and summariser are a DIFFERENT model from the default, and that is the\n# whole point rather than a detail. OpenRouter\'s new-account throttle is per\n# model, so putting every role on one model makes them share one 20/minute\n# budget. The first trial run did exactly that — 63 rejections, including\n# "Could not summarize research" — because pacing the default model to 15/min\n# is worthless if the parser is spending the same allowance un-paced.\n# gpt-oss-120b is $0.037/$0.17 per million, 20x cheaper again, and has 20\n# serving endpoints. Both prices verified live against /api/v1/models.\nTRIAL_MODELS = {\n    "default": "openrouter/google/gemini-3.6-flash",\n    "summarizer": "openrouter/openai/gpt-oss-120b",\n    "parser": "openrouter/openai/gpt-oss-120b",\n    "researcher": "openrouter/perplexity/sonar",\n}\n\n# PACING THE DEFAULT MODEL.\n#\n# Measured, not guessed. The first ever season-tier run, 31 Aug 2026, returned:\n#   "Rate limit exceeded: new-account-rpm/anthropic/claude-5-fable-20260609.\n#    Rate limit reached: new accounts are limited to 20 requests per minute"\n# with X-RateLimit-Limit: 20 and limit_source: openrouter_new_account. Out of\n# that run: 87 calls rejected, 18 of 45 predictions landed, and 13 questions\n# forfeited outright by the SDK\'s "at least half the samples must succeed"\n# rule. Nothing in the bot noticed anything was wrong with its own design.\n#\n# The cause is burstiness, not volume. _max_concurrent_questions bounds\n# run_research ONLY; once questions clear research their predictions all fire\n# together, so nine questions at five predictions each puts dozens of calls at\n# one model in the same second. Throttling questions would not have fixed it.\n#\n# 15 a minute against a limit of 20 leaves headroom for the retries GeneralLlm\n# makes underneath this gate — those do not re-acquire, so they are invisible\n# to the limiter and must simply be left room for. Capacity equals the\n# per-minute figure, which is the library\'s intended "requests per minute"\n# shape: a minute\'s worth may burst, then the bucket refills over the next\n# minute before more is allowed.\n#\n# ONE BUCKET PER MODEL, not one per bot. The throttle is keyed on the model, so\n# there are two buckets below: one for the default model and one for the\n# parser. Parsing runs once per prediction, so it generates the SAME volume as\n# forecasting — gating only the default model, as the first version of this did,\n# leaves half the traffic un-paced. The two roles must also BE different models,\n# or the two buckets simply share one real budget and neither is honoured.\n#\n# Cost of the pacing: a five-question tournament pass is 25 default calls, so\n# under three minutes of the 90-minute window. Not the binding constraint.\n#\n# REVISED 1 Sept 2026 after audit, from 15 to 10. Two reasons, both measured\n# rather than assumed:\n#\n#   1. Retries live BELOW this gate and never re-acquire, so they are invisible\n#      to the bucket. Capping the parser at PARSER_ALLOWED_TRIES brings the\n#      worst-case multiplier down from 6 to 2, but 2 x 15 = 30 still breaches a\n#      limit of 20. At 10 a minute, even every single call retrying once stays\n#      inside 20. Headroom of 100% is the point: we are buying certainty with\n#      time, and time is the thing we have.\n#   2. Capacity is now 1, not PER_MODEL_RPM. An audit simulated the library\'s\n#      actual behaviour: with capacity=15 the bucket fires all fifteen requests\n#      in the same instant and then stalls for a full 60 seconds, because\n#      RefreshingBucketRateLimiter refills to FULL once emptied rather than one\n#      unit at a time. The 60-second average held while the instantaneous rate\n#      was ~15 per second — the very burst shape that triggered the throttle.\n#      Capacity 1 gives one request every six seconds and no burst at all.\n#\n# REVISED AGAIN 2 Sept 2026, and made tier-aware. Two reasons:\n#   1. 10 x LLM_ALLOWED_TRIES(2) = 20 against a limit of 20 is a boundary, not\n#      a margin, and retries are CORRELATED with being at the limit — the thing\n#      that triggers a retry is usually the 429 itself. 8 x 2 = 16 leaves room.\n#   2. The free tier keeps LLM_ALLOWED_TRIES = 6, because free-tier 429s are\n#      transient and retrying is the right response there. At 10/min that is a\n#      worst case of 60 against a limit of 20 — the unit test caught it, having\n#      been extended to check BOTH buckets rather than only the parser. The\n#      free tier was unsafe by our own stated standard and nobody had noticed,\n#      because the arithmetic was only ever checked at the default tier.\n#      3 x 6 = 18 holds.\nPER_MODEL_RPM = 3 if MODEL_TIER == "free" else 8\nPER_MODEL_BURST = 1\n\n# Retries inside the parser. The SDK default is 2, and structure_output wraps\n# it in its own loop, so this is one half of a multiplier we cannot see from\n# the rate limiter. One retry is worth having — a lost parse costs a whole\n# sample, and three lost samples forfeit the question.\nPARSER_ALLOWED_TRIES = 2\n\n# structure_output\'s own outer retry loop, which wraps the parser LLM\'s. The\n# SDK default is 3; combined with PARSER_ALLOWED_TRIES that is a 6x multiplier\n# on every acquisition. At 1 the worst case is 2, which the 10/min pace covers.\nSTRUCTURE_OUTPUT_ALLOWED_TRIES = 1\n\n# Phase 2 ensemble members, kept here so the intent is recorded even though\n# nothing reads this yet. Chosen across four families deliberately: the\n# published research says decorrelation is what makes an ensemble worth having.\nENSEMBLE_MODELS = [\n    "openrouter/anthropic/claude-fable-5",\n    "openrouter/openai/gpt-5.5",\n    "openrouter/google/gemini-3.1-pro-preview",\n    "openrouter/x-ai/grok-4.6",\n]\n\n\n# Tiers that constitute a real forecasting configuration: five predictions a\n# question and live research. "trial" qualifies on both counts — it is simply\n# cheaper, and it is what we can afford for scored MiniBench rounds until the\n# Metaculus credits arrive. "test" and "free" do not qualify, and never should.\nTOURNAMENT_READY_TIERS = ("season", "trial")\n\n\ndef assert_tier_matches_mode(run_mode: str) -> None:\n    """Refuse to forecast a scored tournament on a testing configuration.\n\n    MODEL_TIER lives in this file as a single string. Left on "test", the\n    season would run on a nano model at ONE prediction per question instead of\n    five, and would still exit green — the exact class of silent failure that\n    costs a season. A comment is not a safeguard; this is.\n    """\n    if run_mode != "tournament":\n        return\n    if MODEL_TIER not in TOURNAMENT_READY_TIERS:\n        raise SystemExit(\n            f"REFUSING TO RUN: mode=tournament but MODEL_TIER={MODEL_TIER!r}. "\n            f"The tournament is scored. Set MODEL_TIER to one of "\n            f"{TOURNAMENT_READY_TIERS}, or run --mode test_questions against "\n            "the bot testing area instead."\n        )\n    models = {"season": SEASON_MODELS, "trial": TRIAL_MODELS}[MODEL_TIER]\n    if models.get("researcher") in (None, "", "no_research", "None"):\n        raise SystemExit(\n            f"REFUSING TO RUN: the {MODEL_TIER} configuration has no researcher. "\n            "Forecasting news questions with no search produced a negative "\n            "expected score in Metaculus\'s own evidence. Set a researcher in "\n            f"{MODEL_TIER.upper()}_MODELS."\n        )\n    if MODEL_TIER == "trial":\n        logger.warning(\n            "Forecasting a SCORED tournament on the TRIAL tier: cheaper models, "\n            "roughly a quarter below the best model on the Metaculus reference "\n            "bots per question (5.03 v 6.94 average peer score, 2 Sept 2026). "\n            "Deliberate while we are paying for inference ourselves."\n        )\n\n\ndef write_step_summary(\n    *, run_mode, submitted, failed, attempted, thin_research, problems,\n    seasonal_id, tournament_url,\n) -> None:\n    """Write the run verdict to the GitHub run page. NEVER raises.\n\n    Reading a run meant scrolling roughly two thousand log lines in a\n    virtualised viewer that actively fights you: innerText returns page\n    chrome, the raw-log endpoint 404s, and the step anchors stopped working.\n    Over four months of unattended running that is the difference between\n    noticing a problem and not noticing it, and noticing is the entire second\n    pillar of this bot.\n\n    GITHUB_STEP_SUMMARY renders markdown at the top of the run page, above the\n    logs, visible without opening anything. check_group_questions.py has done\n    this since 2 Sept 2026; the bot itself did not, which was backwards — the\n    diagnostic ran once and the bot runs 144 times a day.\n\n    Written BEFORE the exit decision, deliberately, so a red run gets a\n    summary too. A red run is the one somebody actually needs to read.\n    """\n    path = os.environ.get("GITHUB_STEP_SUMMARY")\n    if not path:\n        return\n    try:\n        if problems:\n            verdict = f"REFUSING TO PASS - {len(problems)} problem(s)"\n        elif failed and not submitted:\n            verdict = f"FAILED - all {failed} question(s) errored"\n        elif failed:\n            verdict = f"PARTIAL - {submitted} submitted, {failed} failed"\n        elif submitted:\n            verdict = f"OK - {submitted} forecast(s) submitted"\n        elif RUN_WARNINGS:\n            # Do NOT say OK here. This is the shape of a dead MiniBench slug\n            # or an exhausted balance: nothing forecast, and a reason why.\n            verdict = "NOTHING FORECAST - see warnings below"\n        else:\n            verdict = "OK - nothing new to forecast"\n        season = (\n            f"`{seasonal_id}`"\n            if seasonal_id is not None\n            else "skipped, no season declared (AIB_TOURNAMENT_ID)"\n        )\n        rows = [\n            ("Mode", f"`{run_mode}`"),\n            (\n                "Model tier",\n                f"`{MODEL_TIER}` - {predictions_per_report()} prediction(s) "\n                "per question",\n            ),\n            ("Seasonal tournament", season),\n            ("Questions attempted", str(attempted)),\n            ("Submitted", str(submitted)),\n            ("Failed", str(failed)),\n            ("Thin research", f"{thin_research} of {attempted}"),\n        ]\n        for half in ("MiniBench", "Seasonal"):\n            if half in QUESTIONS_FOUND:\n                rows.append(\n                    (f"{half} open questions", str(QUESTIONS_FOUND[half]))\n                )\n        if BALANCE_NOTE:\n            rows.append(("OpenRouter", BALANCE_NOTE))\n        if tournament_url:\n            rows.append(("Target", tournament_url))\n        lines = [f"## {verdict}", "", "| Field | Value |", "|---|---|"]\n        # A newline or a pipe inside any of these would break the table or\n        # escape a bullet. Not reachable today, but it becomes reachable the\n        # moment anything dynamic is added, and the failure is silent.\n        def _cell(value):\n            return str(value).replace("|", "&#124;").replace("\\n", " ")\n\n        lines += [f"| {_cell(k)} | {_cell(v)} |" for k, v in rows]\n        if problems:\n            lines += ["", "### Problems", ""]\n            lines += [f"- {_cell(p)}" for p in problems]\n        if RUN_WARNINGS:\n            lines += ["", "### Warnings", ""]\n            lines += [f"- {_cell(w)}" for w in RUN_WARNINGS]\n        with open(path, "a", encoding="utf-8") as handle:\n            handle.write("\\n".join(lines) + "\\n")\n    except Exception as exc:\n        # An observability aid must never become a new way for the run to die.\n        logger.warning("Could not write the GitHub step summary: %s", exc)\n\n\n# Warn below this. One MiniBench question costs about $0.06 on the trial tier,\n# so a dollar is roughly fifteen questions — enough to notice and top up\n# before a round is forfeited, without crying wolf.\nBALANCE_WARN_USD = 1.00\n\n\ndef preflight_check_balance() -> None:\n    """Log the OpenRouter balance before forecasting. NEVER raises.\n\n    The highest-cost failure available to us that nothing was watching for.\n    An exhausted balance mid-round means every prediction 402s, every sample\n    fails, and the questions are forfeited — and we would learn about it from\n    a red run AFTER the three-hour window had closed. Peer scores are summed\n    and then squared, so forfeited questions compound.\n\n    Audit, 6 Sept 2026, which noted the only preflight we had returned\n    immediately on every tier except "free" — the one tier that cannot run out\n    of money.\n\n    The response shape of /api/v1/key is NOT verified against a live key (the\n    key lives in GitHub secrets and is never read locally), so every field is\n    treated as optional and absence is reported honestly rather than guessed\n    at. The key itself is never logged.\n    """\n    global BALANCE_NOTE\n    if MODEL_TIER == "free":\n        return\n    key = os.environ.get("OPENROUTER_API_KEY")\n    if not key:\n        return\n    import json\n    import urllib.request\n\n    try:\n        req = urllib.request.Request(\n            "https://openrouter.ai/api/v1/key",\n            headers={"Authorization": f"Bearer {key}"},\n        )\n        with urllib.request.urlopen(req, timeout=20) as resp:\n            payload = json.loads(resp.read().decode()) or {}\n        data = payload.get("data") or {}\n        usage = data.get("usage")\n        remaining = data.get("limit_remaining")\n        limit = data.get("limit")\n        parts = []\n        if isinstance(usage, (int, float)):\n            parts.append(f"used ${usage:.2f}")\n        if isinstance(limit, (int, float)):\n            parts.append(f"limit ${limit:.2f}")\n        if isinstance(remaining, (int, float)):\n            parts.append(f"remaining ${remaining:.2f}")\n        BALANCE_NOTE = ", ".join(parts) if parts else "no figures returned"\n        logger.info("OpenRouter key: %s", BALANCE_NOTE)\n        if isinstance(remaining, (int, float)) and remaining < BALANCE_WARN_USD:\n            msg = (\n                f"OpenRouter balance is LOW: ${remaining:.2f} remaining. At "\n                "roughly $0.06 a question this is nearly spent. Forecasts will "\n                "start failing mid-round and those questions are forfeited."\n            )\n            logger.warning(msg)\n            RUN_WARNINGS.append(msg)\n            print(f"::warning title=OpenRouter balance low::{msg}")\n    except Exception as exc:  # a health check must never break the run\n        BALANCE_NOTE = None\n        logger.warning("Could not read the OpenRouter balance (%s)", exc)\n\n\ndef preflight_check_free_models() -> None:\n    """Log the serving status of each configured free model. Never raises.\n\n    Free models have a single endpoint each, so when a provider goes down the\n    failure is total. This surfaces that at the top of the log instead of\n    leaving us to infer it from a wall of 404s.\n    """\n    # Only meaningful on the free tier: paid models have many endpoints and\n    # OpenRouter routes around dead ones, so a single status is not the story.\n    if not (MODEL_TIER == "free" and PREFLIGHT_FREE_MODELS):\n        return\n    # Imported locally: main.py does not import these at module level, and a\n    # NameError here would be swallowed by the except below — leaving a\n    # preflight that silently checks nothing, which is worse than none.\n    import json\n    import urllib.request\n\n    checked = set()\n    for role, model in FREE_MODELS.items():\n        if not model.startswith("openrouter/"):\n            continue\n        model_id = model[len("openrouter/") :]\n        if model_id in checked:\n            continue\n        checked.add(model_id)\n        try:\n            url = f"https://openrouter.ai/api/v1/models/{model_id}/endpoints"\n            with urllib.request.urlopen(url, timeout=15) as resp:\n                payload = json.loads(resp.read().decode())\n            endpoints = (payload.get("data") or {}).get("endpoints") or []\n            if not endpoints:\n                logger.warning("PREFLIGHT: %s has NO serving endpoints", model_id)\n                continue\n            for ep in endpoints:\n                status = ep.get("status", 0)\n                provider = ep.get("provider_name", "?")\n                if status == 0:\n                    logger.info("PREFLIGHT: %s ok via %s", model_id, provider)\n                else:\n                    logger.warning(\n                        "PREFLIGHT: %s reports status %s via %s — expect failures",\n                        model_id,\n                        status,\n                        provider,\n                    )\n        except Exception as exc:  # never let a health check break the run\n            logger.warning("PREFLIGHT: could not check %s (%s)", model_id, exc)\n\n\ndef predictions_per_report():\n    """Free tier gets 1; the season gets the full ensemble. See note above."""\n    return 1 if USE_FREE_MODELS else 5\n\n\ndef build_llm_config():\n    """Return the llms= mapping for the bot, per MODEL_TIER."""\n    tiers = {\n        "free": FREE_MODELS,\n        "test": TEST_MODELS,\n        "trial": TRIAL_MODELS,\n        "season": SEASON_MODELS,\n    }\n    if MODEL_TIER not in tiers:\n        raise ValueError(\n            f"MODEL_TIER must be one of {sorted(tiers)}, got {MODEL_TIER!r}"\n        )\n    chosen = tiers[MODEL_TIER]\n    logger.info(\n        "Model tier: %s | default=%s | predictions/question=%d",\n        MODEL_TIER.upper(),\n        chosen["default"],\n        predictions_per_report(),\n    )\n    return {\n        # allowed_tries is deliberately generous on the free tier: upstream\n        # 429s there are transient and shared-pool, so retrying is the correct\n        # response rather than failing the run.\n        "default": GeneralLlm(\n            model=chosen["default"],\n            temperature=0.3,\n            timeout=LLM_TIMEOUT_SECONDS,\n            allowed_tries=LLM_ALLOWED_TRIES,\n        ),\n        "summarizer": chosen["summarizer"],\n        # The parser is a GeneralLlm rather than a bare model string so we can\n        # set allowed_tries. Passed as a string, the SDK wraps it in a\n        # GeneralLlm with _DEFAULT_ALLOWED_TRIES = 2, and structure_output adds\n        # its own outer loop of allowed_tries=3 — so ONE trip through our rate\n        # limiter could become SIX requests on the wire. Retries happen below\n        # the gate and never re-acquire, so the limiter cannot see them. Audit,\n        # 1 Sept 2026: this is the better explanation for 87 rejections against\n        # 45 acquisitions than the burstiness we first blamed.\n        "parser": GeneralLlm(\n            model=chosen["parser"],\n            temperature=0.0,\n            timeout=LLM_TIMEOUT_SECONDS,\n            allowed_tries=PARSER_ALLOWED_TRIES,\n        ),\n        "researcher": chosen["researcher"],\n    }\n\n\n# =============================================================================\n# SEASON ROLLOVER\n#\n# The seasonal tournament ID is not ours to set. It arrives from the\n# forecasting-tools SDK as MetaculusClient.CURRENT_AI_COMPETITION_ID, and\n# poetry.lock pins that SDK at 0.2.92 while the workflow installs with\n# `poetry install`, which honours the lock. Read at the 0.2.92 version-bump\n# commit AND at upstream main on 31 Aug 2026, the constants are:\n#\n#     FE_SUMMER_2026_ID         = 33022   # summer-futureeval-2026\n#     CURRENT_AI_COMPETITION_ID = FE_SUMMER_2026_ID\n#     CURRENT_MINIBENCH_ID      = "minibench"   <- a slug, season-independent\n#\n# Metaculus has published no Fall 2026 ID, in the SDK or on the site. The\n# pinned value will therefore still say Summer when the Fall season opens.\n#\n# Why that is dangerous rather than merely wrong: the SDK fetches questions via\n# get_all_open_questions_from_tournament(), which filters on\n# allowed_tournaments=[id] with status "open" and returns whatever comes back.\n# A finished tournament returns ZERO questions — no exception, no warning — and\n# the run exits GREEN. Seasons run about four months. Left alone this bot would\n# forecast on nothing for an entire season while every scheduled run showed a\n# tick, which is the most expensive failure available to us.\n#\n# MiniBench is unaffected: "minibench" is a slug that survives the rollover.\n#\n# Rolling the season over needs no code change:\n#   GitHub -> Settings -> Secrets and variables -> Actions -> Variables -> New\n#   Name:  AIB_TOURNAMENT_ID\n#   Value: the Fall 2026 project ID (33121) or its slug\n# Until that is set, MiniBench is forecast as normal, the seasonal half is\n# skipped, and the run fails so the workflow turns red. MiniBench runs FIRST in\n# the dispatch precisely so an unset variable cannot forfeit it.\n# =============================================================================\n\n# The old guard was a fixed date plus a three-item denylist of known-stale IDs.\n# An audit on 1 Sept 2026 showed it tested the wrong thing: it asked what the\n# tournament ID *is*, not what it *does*. A typo in AIB_TOURNAMENT_ID sailed\n# straight past it — zero questions, green tick, every ten minutes for four\n# months. It also expired: once the Fall ID was set the guard was spent, and\n# the Winter 2027 rollover would have repeated the original failure with no\n# alarm at all.\n#\n# Replaced by a question COUNT check at the point of use. It needs no dates, no\n# ID list and no maintenance, and it is correct at every future rollover.\n# Explicit "there is no seasonal tournament right now" sentinel.\n#\n# Between seasons the seasonal half has nothing to point at, and BOTH of the\n# other options turn every run red: leaving AIB_TOURNAMENT_ID unset raises\n# REFUSING TO PASS, and setting it to a season that has not opened yet trips\n# SEASON_MISSING, because a tournament holding no questions is\n# indistinguishable from a retired one. At a run every ten minutes that is\n# about 144 failure emails a day, drowning the single alarm channel this\n# project has.\n#\n# So the gap is DECLARED, not inferred. No dates and no silent fallback:\n# somebody has to type it, and every run then says loudly that the seasonal\n# half is deliberately off. Added 5 Sept 2026, three days before the MiniBench\n# round it would otherwise have blocked.\nNO_SEASON_VALUES = ("none", "off", "skip", "-")\n\n# THE SENTINEL EXPIRES, and that is the whole point of this line.\n#\n# Audit, 5 Sept 2026, found the flaw in the first version: every detector on\n# the seasonal side (the question COUNT check, the slug check, SEASON_MISSING)\n# sits behind the sentinel, so declaring the gap removes them all. Left set to\n# "none" through the season opening, the bot would forecast MiniBench only,\n# GREEN, every ten minutes for four months, and nothing in the code could\n# notice. That is the ~150-peer-point failure this whole block exists to\n# prevent, faithfully reproduced by the guard written to prevent it.\n#\n# A date was rejected once before, for good reason: the old guard expired into\n# SILENCE, so once it was spent the next rollover had no alarm at all. This\n# one expires into NOISE, which is the safe direction. Firing late costs a\n# little wasted attention; not firing costs a season. The red it produces is\n# actionable and self-clearing — it stops the moment the variable is set — and\n# it is raised at the END of the run, so MiniBench is still forecast first.\n# 30 September, not the 28th, and the two days are deliberate. Audit, 5 Sept\n# 2026: on the 28th BOTH settings would have been red if Metaculus creates the\n# Fall project before populating it — "none" because the sentinel had expired,\n# and 33121 because an empty seasonal tournament is fatal. We have direct\n# evidence they do exactly that: "minibench" held zero questions on 5 Sept for\n# a round starting on the 7th. A guaranteed red morning is not a safety\n# feature — it is 144 emails, and the obvious way to make them stop is to\n# disarm the seasonal guard, permanently. Two days of grace buys a\n# configuration that is green while we wait for the first Fall question.\nNO_SEASON_EXPIRY = datetime(2026, 9, 30, tzinfo=timezone.utc)\n\n# The remedy differs by half, so it is not baked into the message. Telling an\n# operator to set AIB_TOURNAMENT_ID when it is MINIBENCH that broke sends\n# them to a variable with nothing to do with the fault. Audit, 5 Sept 2026.\nSEASON_MISSING_FIXES = {\n    "Seasonal": (\n        "Fix: set the repository variable AIB_TOURNAMENT_ID (Settings > "\n        "Secrets and variables > Actions > Variables) to the current "\n        "seasonal tournament ID or slug."\n    ),\n    "MiniBench": (\n        "Fix: MiniBench is keyed by the slug \'minibench\', which Metaculus "\n        "says is always the currently active round. If it now holds "\n        "nothing, the slug has changed — check the MiniBench tournament "\n        "page and the Metaculus Discord before changing anything here."\n    ),\n}\n\nSEASON_MISSING_MESSAGE = (\n    "{label} TOURNAMENT NOT FOUND: {tournament!r} contains no questions at "\n    "all. That is a wrong or retired tournament ID, not a quiet hour — a live "\n    "tournament always has questions even when none are currently open. That "\n    "half of this run forecast NOTHING. {fix}"\n)\n\n\n# Slug fragments that identify a Metaculus BOT tournament. Every question the\n# API returns carries the slugs of the tournaments it belongs to\n# (MetaculusQuestion.tournament_slugs, filled from projects.tournament[].slug),\n# so checking this costs no extra request.\n#\n# Why it exists: the question-count check alone cannot tell a typo from a\n# correct ID. Metaculus project IDs are dense — 32916, 33021, 33022 — so a\n# transposed digit usually lands on ANOTHER REAL PROJECT, which has questions,\n# passes the count check, and would have us forecasting into a tournament we\n# are not entered in. Green, every ten minutes, for four months. Found by\n# audit on 2 Sept 2026, in the guard written to prevent exactly that.\n#\n# Fragments rather than names because Metaculus has renamed the series over\n# time: aibq3, aibq4, fall-aib-2025, spring-aib-2026, summer-futureeval-2026,\n# minibench. Every one contains one of these.\nBOT_TOURNAMENT_SLUG_MARKERS = ("aib", "futureeval", "minibench")\n\n\ndef tournament_slug_problem(tournament_id, questions, label: str) -> str | None:\n    """None if these questions belong to a bot tournament, else why not.\n\n    Fails SAFE on missing metadata: if no question carries a slug we warn and\n    allow the run, because refusing on absent data would turn an API change\n    into a four-month outage of our own making.\n    """\n    slugs = {\n        s.lower()\n        for q in questions\n        for s in (getattr(q, "tournament_slugs", None) or [])\n    }\n    if not slugs:\n        logger.warning(\n            "%s tournament %r returned questions carrying no tournament slugs, "\n            "so it could not be verified as a bot tournament. Allowing the run.",\n            label,\n            tournament_id,\n        )\n        return None\n    if any(marker in s for s in slugs for marker in BOT_TOURNAMENT_SLUG_MARKERS):\n        logger.info(\n            "%s tournament %r verified as a bot tournament: %s",\n            label,\n            tournament_id,\n            sorted(slugs),\n        )\n        return None\n    return (\n        f"WRONG TOURNAMENT: {label} target {tournament_id!r} resolves to "\n        f"{sorted(slugs)}, none of which looks like a Metaculus bot tournament "\n        f"(expected a slug containing one of {BOT_TOURNAMENT_SLUG_MARKERS}). "\n        "That is almost certainly a mistyped AIB_TOURNAMENT_ID landing on a "\n        "real but unrelated project. Forecasting was skipped."\n    )\n\n\n# GROUP QUESTIONS: ON. Skipping was PROVEN to work on them, 2 Sept 2026.\n#\n# The worry was that skip_previously_forecasted_questions — the only thing\n# stopping a 10-minute cron re-forecasting the same question all season — reads\n# question.already_forecasted, which the SDK fills from\n# question_json["my_forecasts"]["history"]. For an unpacked GROUP subquestion\n# that json is deep-copied from the group payload, so the field is only there\n# if Metaculus puts my_forecasts on each subquestion. The SDK explicitly\n# patches this for CONDITIONAL questions and does nothing for groups, which\n# read like the case had never been considered. If it failed open we would\n# re-forecast group subquestions 144 times a day — wasted spend, and a breach\n# of the one-forecast-per-question rule for bot-only tournaments.\n#\n# Settled by running check_group_questions.py against the bot-testing-area,\n# where earlier Test Bot runs had already forecast the group questions:\n#\n#     9 open question(s): 4 in groups, 5 standalone.\n#     43329  in group  already forecast: True   (post 43325)\n#     43330  in group  already forecast: True   (post 43325)\n#     43323  in group  already forecast: True   (post 43322)\n#     43324  in group  already forecast: True   (post 43322)\n#\n# Four for four. Metaculus does populate my_forecasts per subquestion, so\n# skipping holds and group questions are back in play. The switch stays so the\n# decision is reversible if that ever stops being true — re-run the check\n# rather than assuming.\nSKIP_GROUP_QUESTIONS = False\n\n\ndef drop_group_questions(questions, label: str):\n    """Remove unpacked group subquestions. See SKIP_GROUP_QUESTIONS."""\n    if not SKIP_GROUP_QUESTIONS:\n        return questions\n    kept = [q for q in questions if getattr(q, "question_ids_of_group", None) is None]\n    dropped = len(questions) - len(kept)\n    if dropped:\n        logger.warning(\n            "%s: skipping %d group subquestion(s). Deliberate — see "\n            "SKIP_GROUP_QUESTIONS. We cannot yet prove the SDK reports them as "\n            "already forecast, and re-forecasting one would breach the "\n            "one-forecast-per-question rule.",\n            label,\n            dropped,\n        )\n    return kept\n\n\ndef fetch_and_verify_tournament(\n    client, tournament_id, label: str, *, empty_is_fatal: bool = True\n):\n    """Return (open_questions, problem_message_or_None).\n\n    empty_is_fatal says what "this tournament holds no questions at all"\n    MEANS, and it is not the same thing for both halves.\n\n    For the SEASON it means broken. A season runs continuously for about four\n    months, so an empty seasonal tournament is a wrong or retired ID and the\n    run must go red.\n\n    For MINIBENCH it is normal. MiniBench is a chain of back-to-back two-week\n    rounds, and "minibench" is a slug that repoints to whichever round is\n    active. Between the slug repointing and the first question of the new\n    round being created, it legitimately holds nothing.\n\n    Found the hard way on 5 Sept 2026: the first live run of the tournament\n    workflow failed with "MiniBench TOURNAMENT NOT FOUND", two days before a\n    round we were entering. Treating that as fatal would have reddened every\n    run for two days, and then again in the gap after every future round —\n    permanently, every fortnight. Red that always fires is the same as no red\n    at all, and this project has exactly one alarm channel.\n\n    The cost of the softer treatment is honest: if the MiniBench slug ever\n    really did change, we would see a warning rather than a failure. That is\n    accepted deliberately, because the alternative guarantees alarm fatigue in\n    exchange for detecting something Metaculus documents as fixed ("the\n    project ID for the currently active minibench is always minibench").\n\n    Fetches explicitly rather than letting forecast_on_tournament do it,\n    because that discards the question COUNT, and the count is what separates a\n    working tournament from a dead one.\n    """\n    questions = client.get_all_open_questions_from_tournament(tournament_id)\n    QUESTIONS_FOUND[label] = len(questions)\n    logger.info(\n        "%s tournament %r: %d open questions", label, tournament_id, len(questions)\n    )\n    questions = drop_group_questions(questions, label)\n    sample = questions\n    if not questions:\n        # Zero OPEN questions is normal. Questions accept forecasts for about\n        # 90 minutes and this runs every 10, so most runs legitimately find\n        # nothing. Zero questions AT ALL is not normal. Only pay for the second\n        # query on the runs that would otherwise have said nothing at all.\n        sample = asyncio.run(\n            client.get_questions_matching_filter(\n                # unpack_subquestions to match what the fetch above uses.\n                # ApiFilter defaults to "exclude", which drops group posts both\n                # server-side and locally — so the probe looked at a different\n                # population from the forecast set, and a tournament whose\n                # newest posts were all groups would have produced a false\n                # "NOT FOUND". Audit, 2 Sept 2026.\n                ApiFilter(\n                    allowed_tournaments=[tournament_id],\n                    group_question_mode="unpack_subquestions",\n                )\n            )\n        )\n        if not sample:\n            message = SEASON_MISSING_MESSAGE.format(\n                tournament=tournament_id,\n                label=label,\n                fix=SEASON_MISSING_FIXES.get(\n                    label, SEASON_MISSING_FIXES["Seasonal"]\n                ),\n            )\n            if empty_is_fatal:\n                return [], message\n            # A SEPARATE message, not the fatal one. Audit, 5 Sept 2026: the\n            # first version reused SEASON_MISSING_MESSAGE, so the log read\n            # "NOT FAILING THE RUN: TOURNAMENT NOT FOUND ... that is a wrong\n            # or retired ID" — telling the reader it is definitely broken in\n            # the same breath as declining to act. Useless at 7am.\n            soft = (\n                f"{label} holds no questions yet ({tournament_id!r}). Normal "\n                "in the gap between MiniBench rounds, so the run is NOT being "\n                "failed. If this persists past the advertised start date of "\n                "the round, the slug has changed — check the MiniBench "\n                "tournament page and the Metaculus Discord."\n            )\n            logger.warning(soft)\n            RUN_WARNINGS.append(soft)\n            # A python warning produces no GitHub annotation, so on a green run\n            # it is invisible unless somebody opens the log and scrolls. This\n            # is now the ONLY detector for a dead MiniBench slug, so it gets a\n            # yellow flag on the run page for the price of one print.\n            print(f"::warning title={label} holds no questions::{soft}")\n            return [], None\n        logger.info(\n            "%r holds %d questions, none open right now. Normal between windows.",\n            tournament_id,\n            len(sample),\n        )\n    return questions, tournament_slug_problem(tournament_id, sample, label)\n\n\ndef resolve_seasonal_tournament():\n    """Return (tournament_id_or_None, problem_or_None). Three outcomes, not two.\n\n    (id, None)     a real tournament to forecast.\n    (None, None)   the NO_SEASON_VALUES sentinel: no season right now, by\n                   explicit declaration. Expires at NO_SEASON_EXPIRY.\n    (None, problem) unset, blank, or an expired sentinel. The run goes red.\n\n    AIB_TOURNAMENT_ID is REQUIRED: there is no inferred default.\n\n    Returns a problem rather than raising, so the caller can still forecast\n    MiniBench before failing the run. The first version raised here, which meant\n    an unset variable forfeited MiniBench too — a scored series, keyed by a slug\n    that survives the rollover, that was working perfectly. Audit, 2 Sept 2026.\n\n    This used to fall back to the SDK\'s CURRENT_AI_COMPETITION_ID. That fallback\n    was removed on 2 Sept 2026 because it is a silent trap: poetry.lock pins\n    forecasting-tools 0.2.92, where the constant is frozen at Summer 2026, so\n    the fallback quietly aims a whole Fall season at a finished tournament.\n\n    The question-count check downstream catches a WRONG id — a typo has no\n    questions at all — but it cannot catch a RETIRED one. Summer still holds\n    328 questions; they are simply all closed, so the probe would report the\n    tournament as healthy. The fallback had to go rather than be guarded.\n\n    Requiring the variable closes both failures with no dates, no ID lists and\n    no maintenance, and stays correct at every future rollover. The cost is one\n    repository variable that has to be set before a season starts, which was\n    always true anyway.\n    """\n    override = os.environ.get("AIB_TOURNAMENT_ID", "").strip()\n    if not override:\n        return None, (\n            "AIB_TOURNAMENT_ID is not set. Tournament mode has "\n            "to be told which seasonal tournament to forecast — the SDK\'s "\n            "built-in constant is pinned to Summer 2026 and would forecast a "\n            "finished tournament without complaining. Set the repository "\n            "variable (Settings > Secrets and variables > Actions > Variables) "\n            "to the current seasonal tournament ID or slug."\n        )\n    if override.lower() in NO_SEASON_VALUES:\n        now = datetime.now(timezone.utc)\n        if now >= NO_SEASON_EXPIRY:\n            return None, (\n                f"AIB_TOURNAMENT_ID is still {override!r} on "\n                f"{now:%Y-%m-%d}, past the declared no-season window that "\n                f"ended {NO_SEASON_EXPIRY:%Y-%m-%d}. The seasonal tournament "\n                "has opened and this bot is forecasting MiniBench ONLY. Set "\n                "AIB_TOURNAMENT_ID to the Fall 2026 tournament ID (33121, "\n                "fall-futureeval-2026), or move NO_SEASON_EXPIRY deliberately."\n            )\n        logger.warning(\n            "AIB_TOURNAMENT_ID=%r: NO SEASONAL TOURNAMENT this run, by "\n            "explicit configuration. MiniBench only. Set the real tournament "\n            "ID when the season opens. Fall 2026 is 33121 "\n            "(fall-futureeval-2026), opening 28 Sept 2026.",\n            override,\n        )\n        return None, None\n    resolved = int(override) if override.isdigit() else override\n    logger.info("Seasonal tournament %r (from AIB_TOURNAMENT_ID)", resolved)\n    return resolved, None',
    "configuration block",
)

# ---------------------------------------------------------------------------
# 5. WIRE THE MODEL CONFIG IN  (this is the actual fix for the 404: without an
#    explicit llms= block the bot reaches for a model OpenRouter doesn't serve.)
# ---------------------------------------------------------------------------
replace(
    """        extra_metadata_in_explanation=True,
        # llms={
        #     "default": GeneralLlm(
        #         model="openrouter/openai/gpt-4o",
        #         temperature=0.3,
        #         timeout=40,
        #         allowed_tries=2,
        #     ),
        #     "summarizer": "openai/gpt-4o-mini",
        #     "researcher": "asknews/news-summaries",
        #     "parser": "openai/gpt-4o-mini",
        # },
    )""",
    """        extra_metadata_in_explanation=True,
        # Found by tracing every outbound call on paper, 1 Sept 2026, rather
        # than by paying for another run. enable_summarize_research defaults to
        # True, so the SDK was making one summariser call per question whose
        # output we then discarded: line 469 of forecast_bot.py forecasts from
        # `summary_report if self.use_research_summary_to_forecast else
        # research`, and ours is False. It cost money, it spent rate-limit
        # budget on the same model as the parser, and it produced the
        # "Could not summarize research" errors in the trial run.
        # The only loss is a summary paragraph in the private note; the full
        # reasoning for every prediction is still there.
        enable_summarize_research=False,
        llms=build_llm_config(),
    )
    template_bot.predictions_per_research_report = predictions_per_report()
    assert_tier_matches_mode(run_mode)
    preflight_check_free_models()
    preflight_check_balance()""",
    "wire explicit model config into the bot",
)

# ---------------------------------------------------------------------------
# 7. FREE-TIER REQUEST BUDGET  (OpenRouter's free tier allows 50 requests per
#    DAY in total, which three test runs exhausted. Every parse of a model's
#    output is validated with N extra samples, so N is a direct multiplier on
#    request count. Originally 1 on the free tier only; an audit on 31 Aug 2026
#    found the template's default of 2 can forfeit whole questions, so it is
#    now 1 everywhere. See the comment inserted below.)
# ---------------------------------------------------------------------------
replace(
    "    _structure_output_validation_samples = 2",
    """    # Parse each reasoning text ONCE, not twice.
    #
    # The template's default of 2 re-parses the same text and raises if the two
    # parses are not exactly equal (structure_output() compares the parsed
    # objects with `!=` — verified in forecasting-tools 0.2.92). That kills the
    # prediction sample, and the SDK forfeits the WHOLE question if fewer than
    # required_successful_predictions (default 0.5) of the five samples
    # survive. So three unlucky parses lose the question outright and it scores
    # nothing — the exact failure the top open-source bot blamed for ~150
    # forfeited peer points.
    #
    # The check is close to redundant here: five independent predictions are
    # aggregated by median, which already outvotes one bad parse. It also
    # doubles parser calls and latency inside a 90-minute window. The risk is
    # worst precisely where we least want it — multiple choice, where the
    # parser is instructed to emit 0% options and two parses of a long option
    # list can differ by a digit.
    _structure_output_validation_samples = 1

    # One bucket per MODEL, shared by every question in the run, because
    # OpenRouter's throttle is per model per account. See PER_MODEL_RPM.
    _default_model_limiter = RefreshingBucketRateLimiter(
        capacity=PER_MODEL_BURST, refresh_rate=PER_MODEL_RPM / 60
    )
    _parser_model_limiter = RefreshingBucketRateLimiter(
        capacity=PER_MODEL_BURST, refresh_rate=PER_MODEL_RPM / 60
    )

    async def _invoke_default_llm(self, prompt: str) -> str:
        \"\"\"The single door every default-model call goes through.

        Centralised deliberately. The failure this fixes was four separate call
        sites each firing as fast as asyncio would allow, with nothing in the
        bot aware of the others. A rate limit is a property of the account, so
        the gate has to be shared, not per-question.
        \"\"\"
        await self._default_model_limiter.wait_till_able_to_acquire_resources(1)
        return await self.get_llm(\"default\", \"llm\").invoke(prompt)

    async def _structure_output_paced(self, *args, **kwargs):
        \"\"\"structure_output, paced against the parser model's own throttle.

        Parsing happens once per prediction, so it is not a minor side channel:
        it is the same call volume as forecasting. The first rate-limited run
        paced the default model and left this untouched, which is why it still
        failed.
        \"\"\"
        await self._parser_model_limiter.wait_till_able_to_acquire_resources(1)
        # allowed_tries is structure_output's OWN outer retry loop, separate
        # from the parser LLM's. Left at its default of 3 it multiplies with
        # PARSER_ALLOWED_TRIES; stated here so the worst case is visible in one
        # place rather than inherited from a default we did not choose.
        kwargs.setdefault("allowed_tries", STRUCTURE_OUTPUT_ALLOWED_TRIES)
        return await structure_output(*args, **kwargs)""",
    "parse validation samples: 1, never 2; plus the shared rate limiter",
)

# ---------------------------------------------------------------------------
# 13. RATE LIMITING  (observed live on 31 Aug 2026 — see the PER_MODEL_RPM
#     note in the config block. Four call sites fired independently and blew
#     through OpenRouter's 20/min new-account limit on the default model.)
# ---------------------------------------------------------------------------
replace(
    "    ReasonedPrediction,\n    SmartSearcher,",
    "    ReasonedPrediction,\n    RefreshingBucketRateLimiter,\n    SmartSearcher,",
    "import the rate limiter",
)

replace_all(
    '        reasoning = await self.get_llm("default", "llm").invoke(prompt)',
    "        reasoning = await self._invoke_default_llm(prompt)",
    4,
    "route every default-model call through the rate limiter",
)

# Parsing runs once per prediction, so it is the same call volume as
# forecasting. Gating only the default model left half the traffic un-paced,
# which is why the first trial run still hit 63 rejections.
#
# Anchored on "= await" rather than bare "await structure_output(" on purpose:
# the _structure_output_paced helper inserted above ends with
# "return await structure_output(...)", and a looser anchor would rewrite the
# helper into a call to itself. Four assignments, never the return.
replace_all(
    "= await structure_output(",
    "= await self._structure_output_paced(",
    4,
    "route every parser call through the rate limiter",
)

# ===========================================================================
# EDIT 8 — REVERTED 31 Aug 2026.  DO NOT REINSTATE.
#
# I added a patch forcing the reasoning comment public, on the reasoning that
# the rules require "a comment response under every single question" and our
# bot's profile showed 0 comments after a successful run.
#
# That was WRONG. Metaculus's own resources notebook (38928) says:
#     "We request that bots use private notes as their comment type."
#     "For reference, the template bot here leaves private notes."
#     "We will convert these private notes into public comments after
#      questions close weekly."
#
# Private notes ARE the compliant comment. Metaculus converts them itself,
# after close, so that bots cannot read each other's reasoning while a
# question is still open. The stock template was correct; run #4 was already
# fully compliant; the "0 comments" figure was a public counter that does not
# count private notes.
#
# The mistake: I verified the RULE and never checked the IMPLEMENTATION
# GUIDANCE, then treated one other bot's public comment as the standard rather
# than the outlier. Cost: two wasted runs and a broken import.
# ===========================================================================

# ---------------------------------------------------------------------------
# 8. (removed — see the note above)
#
# The tournament rules state: "In order to be eligible for the prize, the
# participating bot needs to have written a comment response under every
# single question that it is forecasting."
#
# forecasting-tools posts that comment with is_private=True (see
# forecast_helpers/metaculus_api.py, post_question_comment). A private note is
# visible only to the bot's own account, so a stock template bot forecasts
# perfectly and leaves nothing under the question. Verified 30 Aug 2026: after
# a successful run our bot showed 9 predictions and 0 comments, while another
# entrant's bot had a visible public comment on the same question.
#
# We do not know for certain that Metaculus counts only public comments. We do
# know that making it public costs nothing and that the downside of being
# wrong is a whole season's prize eligibility, so this is not a close call.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 11. MULTIPLE-CHOICE FLOOR  (an audit on 31 Aug 2026 found this: binary was
#     floored on day one, multiple choice never was, and MC is where Metaculus
#     says bots lose most ground. A 0% option that resolves scores about -691.)
#
#     Probabilities are mutated in place rather than rebuilding the model:
#     PredictedOptionList validates on construction that the options sum to
#     within 0.99-1.01, so flooring without renormalising would raise.
# ---------------------------------------------------------------------------
replace(
    """        logger.info(
            f"Forecasted URL {question.page_url} with prediction: {predicted_option_list}."
        )
        return ReasonedPrediction(
            prediction_value=predicted_option_list, reasoning=reasoning
        )""",
    """        # NO FLOOR APPLIED HERE, DELIBERATELY. We used to floor and renormalise
        # the options at MC_OPTION_FLOOR (0.01). An audit on 1 Sept 2026 showed
        # it was dead code: PredictedOptionList has a model_validator that runs
        # on every construction and already clamps every option to
        # [0.01, 0.99] — the same value — before structure_output returns it.
        # Our version could only ever move a probability by ~2e-4. It read like
        # protection and provided none, which is worse than nothing.
        #
        # The real multiple-choice risk is the opposite one, and it IS now
        # handled — in the parsing instruction, not here. That same validator
        # RAISES if the parsed probabilities sum outside [0.99, 1.01], or if
        # clamping moves any option by more than 0.05, and three rejections out
        # of five samples forfeit the question. It is a prompt problem: the
        # parser is now told never to emit a literal zero and to sum to exactly
        # 1.00, which makes the clamp a no-op. Fixed 2 Sept 2026; measured, the
        # rejection needs roughly six or more literal zeros AND a concentrated
        # forecast, not merely a wide option list as first claimed.

        # FAIL A SHORT OPTION LIST AS A SAMPLE, NOT AS THE QUESTION. Nothing
        # here checked that the parser returned every option. A truncated list
        # passes PredictedOptionList's validator happily — one option at 1.0
        # clamps to 0.99 and sums to 1 — and then raises in
        # MultipleChoiceReport.aggregate_predictions ("All predictions must
        # have the same option names"), which runs OUTSIDE the per-sample
        # gather. One bad sample would forfeit the whole question even when the
        # other four were perfect. Exactly the failure already closed on the
        # numeric path by forcing get_cdf() per sample. Audit, 21 Sept 2026.
        #
        # See _reject_mismatched_options for why this is a sorted LIST
        # comparison and why both sides are read inside its try.
        _reject_mismatched_options(predicted_option_list, question)

        logger.info(
            f"Forecasted URL {question.page_url} with prediction: {predicted_option_list}."
        )
        _telemetry(
            q=question.id_of_question,
            url=question.page_url,
            kind=type(question).__name__,
            sample={
                option.option_name: option.probability
                for option in predicted_option_list.predicted_options
            },
            chars=len(reasoning or ""),
        )
        return ReasonedPrediction(
            prediction_value=predicted_option_list, reasoning=reasoning
        )""",
    "multiple-choice option floor, plus the multiple-choice sample telemetry",
)

# ---------------------------------------------------------------------------
# 14. EMPTY-RESEARCH ALARM  (audit, 1 Sept 2026: nothing anywhere checked that
#     research returned anything. An empty string forecasts from model weights
#     and exits green. Logged rather than raised — see the note in the block.)
# ---------------------------------------------------------------------------
replace(
    '            logger.info(f"Found Research for URL {question.page_url}:\\n{research}")',
    '            # Nothing anywhere checked that research actually returned\n            # anything. If Sonar returns an empty string or a refusal rather\n            # than raising, the prompt reads "Your research assistant says:"\n            # followed by nothing, the bot forecasts from model weights against\n            # a training cutoff, publishes, and exits green. Metaculus\'s own\n            # evidence puts removing search at 3.6x Brier.\n            #\n            # Logged, not raised — deliberately, for now. Raising would discard\n            # the sample, and three discarded samples forfeit the question, so\n            # the fix could cost more than the fault. Whether an unresearched\n            # forecast is worse than no forecast is a judgement about the\n            # scoring rule, and it is recorded as an open decision rather than\n            # settled quietly here.\n            if self.get_llm("researcher") not in (None, "", "None", "no_research"):\n                if base_research_chars < 200:\n                    global EMPTY_RESEARCH_COUNT\n                    EMPTY_RESEARCH_COUNT += 1\n                    logger.error(\n                        "RESEARCH LOOKS EMPTY for %s (%d chars). This forecast is "\n                        "coming from model weights, not from search.",\n                        question.page_url,\n                        len(research.strip()),\n                    )\n            logger.info(f"Found Research for URL {question.page_url}:\\n{research}")',
    "alarm when research comes back empty",
)

# ---------------------------------------------------------------------------
# 15. SEASON GUARD REPLACED  (audit 1 Sept 2026: the date-and-denylist guard
#     tested what the tournament ID IS, not what it DOES. Replaced by a
#     question-count check, which needs ApiFilter.)
# ---------------------------------------------------------------------------
replace(
    "from forecasting_tools import (\n    AskNewsSearcher,",
    "from forecasting_tools import (\n    ApiFilter,\n    AskNewsSearcher,",
    "import ApiFilter for the tournament-existence probe",
)

# ---------------------------------------------------------------------------
# 16. MULTIPLE-CHOICE PARSING  (audit 2 Sept 2026: the upstream instruction to
#     emit 0% options manufactures the very rejection that forfeits questions.
#     PredictedOptionList clamps to [0.01, 0.99] and then RAISES if clamping
#     moved any option by more than 0.05 — which happens at 7+ options. With
#     STRUCTURE_OUTPUT_ALLOWED_TRIES = 1 there is no retry, and three lost
#     samples forfeit the question.)
# ---------------------------------------------------------------------------
replace(
    '            Additionally, you may sometimes need to parse a 0% probability. Please do not skip options with 0% but rather make it an entry in your final list with 0% probability.',
    '            Do not skip options. Every option above must appear in your final list.\n            NEVER emit exactly 0 for an option. Use 0.01 as the minimum for any\n            option you consider negligible, and make the probabilities sum to\n            exactly 1.00.\n\n            (Both rules exist because the library validates this list before we\n            ever see it: it rejects the whole sample if the probabilities sum\n            outside 0.99-1.01, and it clamps every option into 0.01-0.99 and\n            then rejects the sample if that clamping moved any option by more\n            than 0.05. A confident forecast across eight or more options with\n            literal zeros trips the second rule every time. A rejected sample is\n            not a smaller forecast, it is a lost one, and three lost samples\n            forfeit the question entirely.)',
    "multiple-choice parsing: never emit a literal zero",
)

# ---------------------------------------------------------------------------
# 17. DATE PATH CDF  (same reasoning as edit 3, applied to the date path,
#     which the numeric anchor does not reach.)
# ---------------------------------------------------------------------------
replace(
    '        prediction = NumericDistribution.from_question(percentile_list, question)\n        logger.info(\n            f"Forecasted URL {question.page_url} with prediction: {prediction.declared_percentiles}."\n        )\n        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)\n\n    def _create_upper_and_lower_bound_messages(',
    '        prediction = NumericDistribution.from_question(percentile_list, question)\n        # Same reasoning as the numeric path: fail a bad sample as a sample.\n        prediction.get_cdf()\n        logger.info(\n            f"Forecasted URL {question.page_url} with prediction: {prediction.declared_percentiles}."\n        )\n        _telemetry(\n            q=question.id_of_question,\n            url=question.page_url,\n            kind=type(question).__name__,\n            sample=[(pc.percentile, pc.value) for pc in prediction.declared_percentiles],\n            chars=len(reasoning or ""),\n        )\n        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)\n\n    def _create_upper_and_lower_bound_messages(',
    "force CDF expansion per sample on the date path, plus the date sample telemetry",
)

# ---------------------------------------------------------------------------
# 12. TOURNAMENT DISPATCH  (season guard, MiniBench guard, and the order that
#     stops an unset AIB_TOURNAMENT_ID forfeiting MiniBench. See the SEASON
#     ROLLOVER block in the config for the full reasoning.)
# ---------------------------------------------------------------------------
replace(
    '    # Per-mode tournament URL shown in the summary banner footer. These\n    # piggyback on the forecasting_tools SDK constants and need updating\n    # whenever those rotate seasons.\n    TOURNAMENT_URLS = {\n        "tournament": "https://www.metaculus.com/tournament/summer-futureeval-2026/",\n        "metaculus_cup": "https://www.metaculus.com/tournament/metaculus-cup-summer-2025/",\n        "test_questions": "https://www.metaculus.com/tournament/bot-testing-area/",\n    }\n\n    # Dispatch on mode. Each branch produces a list of ForecastReport (or\n    # exceptions, since return_exceptions=True) which then flows into the\n    # summary printers below.\n    client = MetaculusClient()\n    if run_mode == "tournament":\n        seasonal_tournament_reports = asyncio.run(\n            template_bot.forecast_on_tournament(\n                client.CURRENT_AI_COMPETITION_ID, return_exceptions=True\n            )\n        )\n        minibench_reports = asyncio.run(\n            template_bot.forecast_on_tournament(\n                client.CURRENT_MINIBENCH_ID, return_exceptions=True\n            )\n        )\n        forecast_reports = seasonal_tournament_reports + minibench_reports',
    '    # Dispatch on mode. Each branch produces a list of ForecastReport (or\n    # exceptions, since return_exceptions=True) which then flows into the\n    # summary printers below.\n    seasonal_id = None\n    problems: list[str] = []\n    if run_mode == "tournament":\n        # MINIBENCH FIRST, and the seasonal ID resolved after it. The previous\n        # order resolved the seasonal ID up front and raised on an unset\n        # AIB_TOURNAMENT_ID — which forfeited MiniBench on every run of the\n        # rollover window, a scored series keyed by a slug that survives the\n        # rollover and was working perfectly. Audit, 2 Sept 2026.\n        #\n        # MiniBench gets the same fetch-and-verify as the seasonal half. It had\n        # no guard at all before: forecast_on_tournament discards the count, so\n        # if the "minibench" slug ever changes it would forecast nothing,\n        # silently, green, for as long as nobody looked.\n        minibench_questions, minibench_problem = fetch_and_verify_tournament(\n            client,\n            client.CURRENT_MINIBENCH_ID,\n            "MiniBench",\n            # Empty between rounds is normal for MiniBench, not broken.\n            empty_is_fatal=False,\n        )\n        if minibench_problem:\n            logger.error(minibench_problem)\n            problems.append(minibench_problem)\n            minibench_reports = []\n        else:\n            minibench_reports = asyncio.run(\n                template_bot.forecast_questions(\n                    minibench_questions, return_exceptions=True\n                )\n            )\n\n        seasonal_id, seasonal_problem = resolve_seasonal_tournament()\n        # seasonal_id is None with NO problem when the NO_SEASON_VALUES\n        # sentinel is set: there is nothing to fetch, and nothing wrong.\n        if seasonal_problem is None and seasonal_id is not None:\n            seasonal_questions, seasonal_problem = fetch_and_verify_tournament(\n                client, seasonal_id, "Seasonal"\n            )\n        else:\n            seasonal_questions = []\n        if seasonal_problem:\n            logger.error(seasonal_problem)\n            problems.append(seasonal_problem)\n            seasonal_tournament_reports = []\n        else:\n            seasonal_tournament_reports = asyncio.run(\n                template_bot.forecast_questions(\n                    seasonal_questions, return_exceptions=True\n                )\n            )\n\n        forecast_reports = seasonal_tournament_reports + minibench_reports',
    "tournament dispatch: fetch, verify, and order MiniBench first",
)

replace(
    '    template_bot.log_report_summary(forecast_reports)\n    print_run_summary_banner(\n        forecast_reports,\n        will_publish=publish_to_metaculus,\n        tournament_url=TOURNAMENT_URLS.get(run_mode),\n    )',
    '    # Per-mode tournament URL shown in the summary banner footer. The seasonal\n    # entry is built from the ID actually forecast rather than hard-coded, so\n    # the link cannot drift away from what the bot really did. Metaculus\n    # redirects /tournament/<numeric id>/ to the slug (checked 31 Aug 2026).\n    TOURNAMENT_URLS = {\n        "tournament": (\n            f"https://www.metaculus.com/tournament/{seasonal_id}/"\n            if seasonal_id is not None\n            else "https://www.metaculus.com/tournament/minibench/"\n        ),\n        "metaculus_cup": "https://www.metaculus.com/tournament/metaculus-cup-summer-2025/",\n        "test_questions": "https://www.metaculus.com/tournament/bot-testing-area/",\n    }\n\n    # raise_errors=False, deliberately. The SDK\'s default is True and it raises\n    # RuntimeError if ANY question errored — which skipped everything below it,\n    # including the banner and the season-rollover message. Two independent\n    # auditors found this on 1 Sept 2026: the season-rollover message written that\n    # morning was unreachable on any run with a single failed question, which\n    # on recent evidence is most runs. We decide the exit code ourselves below.\n    template_bot.log_report_summary(forecast_reports, raise_errors=False)\n    print_run_summary_banner(\n        forecast_reports,\n        will_publish=publish_to_metaculus,\n        tournament_url=TOURNAMENT_URLS.get(run_mode),\n    )\n\n    # Exit code, decided here rather than inherited from log_report_summary.\n    #\n    # The bar for red is deliberately NOT "any question failed". At a run every\n    # ten minutes, one flaky question turning the whole run red trains whoever\n    # is watching to ignore red — and red is exactly what the season-rollover\n    # guard depends on being noticed. So partial failure warns loudly, and only\n    # a run that achieved nothing, or a stale season, fails the workflow.\n    successes = [r for r in forecast_reports if not isinstance(r, BaseException)]\n    failures = [r for r in forecast_reports if isinstance(r, BaseException)]\n    if failures:\n        logger.warning(\n            "%d of %d questions failed; %d forecast successfully.",\n            len(failures),\n            len(forecast_reports),\n            len(successes),\n        )\n\n    # Empty research fails the run on a RATE, not on a single instance.\n    #\n    # Logging alone was not enough: over four months nobody reads an INFO line\n    # on a green run, and forecasting from model weights against a training\n    # cutoff is worth 3.6x Brier by Metaculus\'s own evidence. But failing on ONE\n    # short research string was worse — Sonar answering "I could not find\n    # relevant information" on a niche question is forty characters, and at 144\n    # runs a day that manufactures exactly the red-fatigue this file spends a\n    # paragraph warning about. A minority of thin research is life; a majority\n    # means the researcher is broken. Audit, 2 Sept 2026.\n    attempted = len(forecast_reports)\n    if EMPTY_RESEARCH_COUNT and attempted:\n        rate = EMPTY_RESEARCH_COUNT / attempted\n        if EMPTY_RESEARCH_COUNT >= EMPTY_RESEARCH_MIN_TO_FAIL and rate > EMPTY_RESEARCH_FAIL_RATE:\n            problems.append(\n                f"{EMPTY_RESEARCH_COUNT} of {attempted} questions were forecast "\n                "with little or no research. Check the researcher model and the "\n                "OpenRouter balance."\n            )\n        else:\n            logger.warning(\n                "%d of %d questions had thin research. Below the failure "\n                "threshold, so not failing the run.",\n                EMPTY_RESEARCH_COUNT,\n                attempted,\n            )\n\n    write_step_summary(\n        run_mode=run_mode,\n        submitted=len(successes),\n        failed=len(failures),\n        attempted=attempted,\n        thin_research=EMPTY_RESEARCH_COUNT,\n        problems=problems,\n        seasonal_id=seasonal_id,\n        tournament_url=TOURNAMENT_URLS.get(run_mode),\n    )\n\n    if problems:\n        raise SystemExit("REFUSING TO PASS: " + " | ".join(problems))\n    if forecast_reports and not successes:\n        raise SystemExit(\n            f"REFUSING TO PASS: all {len(failures)} questions failed and nothing "\n            "was submitted. A green tick here would be a lie."\n        )',
    "banner URL from the resolved id, and the exit code",
)

# ---------------------------------------------------------------------------
# 18. ONE METACULUS CLIENT  (ForecastBot builds its own unless handed one, so
#     nothing we set ever reached the publish path — where the blocking
#     time.sleep lives. Audit, 2 Sept 2026.)
# ---------------------------------------------------------------------------
replace(
    '    template_bot = SummerTemplateBot2026(\n        research_reports_per_question=1,',
    "    # ONE client, built before the bot and handed to it. ForecastBot otherwise\n    # constructs its own, so anything set here never reached the publish path —\n    # which is where the blocking sleeps live. Audit, 2 Sept 2026.\n    #\n    # sleep_seconds_between_requests defaults to 3.5 and is a BLOCKING\n    # time.sleep() inside an async publish method, so it freezes the whole event\n    # loop, not just the calling question. Two requests per published question\n    # is ~8s of frozen loop each. Metaculus's API is not tight enough at two\n    # requests a question for 3.5s to be load-bearing.\n    client = MetaculusClient(\n        sleep_seconds_between_requests=1.0,\n        sleep_jitter_seconds=0.5,\n    )\n\n    template_bot = SummerTemplateBot2026(\n        metaculus_client=client,\n        research_reports_per_question=1,",
    "one shared MetaculusClient, with a shorter inter-request sleep",
)

# ---------------------------------------------------------------------------
# 20. INTEGER-VALUED NUMERIC QUESTIONS  (measured loss, 21 Sept 2026)
#
#     MiniBench q45541 asked how many SpaceX orbital launches would occur on a
#     given day. The outcome can only be a whole number. All five of our samples
#     answered with percentiles like 0.0 / 0.1 / 0.3 / 0.6 / 1.0 / 1.5 —
#     spreading probability across 0.1, 0.3, 0.6 and 1.5, none of which the
#     outcome can ever take. The question RESOLVED AT 1, which sat at our 80th
#     percentile where the density is thinnest. We scored badly on a question
#     whose research had actually told us the right shape.
#
#     Independently corroborated: the maker of Laertes (top-10, Spring) posted in
#     the Metaculus Discord on 19 Sept that his bot "bombed this minibench
#     question because there were 201 bins instead of one for each integer".
#     Metaculus publishes integer-valued quantities as continuous 201-bin
#     questions, so this hits every bot that does not handle it — and MiniBench
#     is roughly 45% numeric/discrete.
#
#     WHY THIS IS A PROMPT FIX AND NOT CODE. This project has already lost
#     points once to its own clever percentile surgery (_sorted_percentiles, a
#     self-inflicted own-goal, retracted 31 Aug). That precedent alone is the
#     reason: the instruction goes to the model and no post-processing is added.
#
#     WHY STRADDLE RATHER THAN REPEAT, stated honestly because this file is the
#     disclosure document. An audit on 21 Sept 2026 ran the pinned SDK and
#     measured it. Repeating a value actually scores BETTER than straddling
#     (0.19 of the mass in the resolution bin against 0.102), because
#     _check_and_update_repeating_values nudges in-bounds repeats DOWN — not up,
#     as an earlier draft of this comment wrongly claimed. But that helper is the
#     subject of open SDK issue #212, whose fix (PR #166) has been unmerged since
#     December 2025, so the direction of that nudge could reverse under us
#     mid-season. Straddling is therefore the ROBUST choice, not the optimal one:
#     splitting the atom across the bin edge means we cannot lose all of it to a
#     convention we have read wrongly or that changes beneath us. The measured
#     price of that insurance is about 0.6 nats per integer question.
#
#     This is a mitigation, not a discrete pipeline. The full fix is CDF-space
#     construction of the kind nostreambot documents, and that is a bigger job
#     than the week before the season allows.
# ---------------------------------------------------------------------------
replace(
    "            - Always start with a smaller number (more negative if negative) and then increase from there. The value for percentile 10 should always be less than the value for percentile 20, and so on.",
    "            - Always start with a smaller number (more negative if negative) and then increase from there. The value for percentile 10 should always be less than the value for percentile 20, and so on.\n"
    "            {grid_message}\n"
    "            - CONCENTRATING PROBABILITY. Only when this question's scoring bins are FINER than the grid the resolution source publishes on is it worth concentrating. For example, if the source reports whole numbers but each bin covers a fraction of one, place a close pair of percentiles either side of the one or two values you think most likely (such as 41.99 and 42.01) so the probability lands in the bins that can actually occur. Keep the values strictly increasing.\n"
    "            - Never concentrate on more than two values, and never spend percentiles 10 and 90 on it \u2014 those stay ordinary wide tail values. A distribution that spends all six percentiles on spikes has no tails left, which loses far more when the answer falls outside them than the spikes gain when it does not. If the bins are as wide as the published grid or wider, do none of this: forecast smoothly.",
    "integer-valued numeric questions: keep the mass on achievable values",
)

# ---------------------------------------------------------------------------
# 21. THE NUMERIC PARSER MUST NOT ROUND THE STRADDLE AWAY  (audit, 21 Sept 2026)
#
#     Edit 20 asks the forecaster to emit close pairs like 0.99 and 1.01. The
#     parser is a separate, cheaper model, and nothing told it to preserve small
#     decimal offsets. Rounding both to 1 would collapse the pair into a repeat.
#
#     That particular failure is benign — a repeat measures BETTER than a
#     straddle on the pinned SDK — but it silently discards the insurance edit 20
#     was buying, and it would do so without any signal. One line closes it.
# ---------------------------------------------------------------------------
replace(
    "            - When parsing the text, please make sure to give the values (the ones assigned to percentiles) in terms of the correct units.",
    "            - When parsing the text, please make sure to give the values (the ones assigned to percentiles) in terms of the correct units.\n"
    "            - Preserve the values exactly as written, including small decimal offsets such as 0.99 or 1.01. Do NOT round them to whole numbers.\n"
    "            - Parse ONLY the final \"Percentile NN: value\" block. Ignore every other number in the text, including base rates, reference-class figures, scoring-grid widths and interpretation notes.",
    "numeric parser: preserve small decimal offsets, do not round",
)

# ---------------------------------------------------------------------------
# 24. THE GUARD HAS TO COME FIRST, AND THE FLAG NEEDS ITS OWN NAME  (21 Sept 2026)
#
#     Edit 22 anchored the still-open guard and the adversarial criteria read on
#     the upstream "Before answering you write:" block. Edit 20 had already
#     inserted four formatting bullets ABOVE that point, so the built prompt read
#     "FIRST, before anything else..." in NINTH position, after a page of
#     instructions about scientific notation and bin widths. An instruction that
#     says "first" and arrives ninth is not merely untidy - it is a direct
#     contradiction, and the model resolves it by ignoring one half.
#
#     So the guard is anchored here instead, on upstream text that sits BEFORE
#     the formatting block, and edit 22 keeps only the lettered list. Nothing in
#     the wording changed except the flag's name.
#
#     THE NAME. The numeric flag reused the binary literal, AMBIGUITY, which is
#     the string caps_for_reasoning() matches to clamp a binary probability to
#     [0.10, 0.90]. Two different meanings behind one token in one codebase is a
#     trap for whoever reads it next, and it made the numeric flag impossible to
#     grep for. It is now FIGURE AMBIGUITY - accurate, since on a numeric
#     question the ambiguity is about WHICH PUBLISHED FIGURE is being asked for -
#     and caps_for_reasoning cannot match it, because its pattern anchors the
#     line at AMBIGUITY and a line beginning FIGURE fails the anchor.
#
#     It is also now LOGGED (see _figure_ambiguity_flag). It remains ADVISORY:
#     the model is asked to widen its own interval and nothing enforces it.
# ---------------------------------------------------------------------------
replace(
    """            {lower_bound_message}
            {upper_bound_message}

            Formatting Instructions:
            - Please notice the units requested and give your answer in these units (e.g. whether you represent a number as 1,000,000 or 1 million).""",
    """            {lower_bound_message}
            {upper_bound_message}

            This question is STILL OPEN and has NOT yet resolved. If your research
            appears to show the figure is already settled, treat that as a warning
            sign rather than a conclusion: re-read the resolution criteria and the
            resolution date, and check whether the number you have found is really
            the one being asked for. A figure that resembles the answer, from a
            different source or a different date, is not the answer.

            FIRST, before anything else, read the resolution criteria adversarially.
            You are forecasting a specific published number, not the general topic.
            Which source publishes it, as of which date, in which units, rounded
            how, and cumulative or per-period - each of those changes the answer,
            and a forecaster who gets the world right and the definition wrong
            loses anyway.

            Write:
            1. The strictest reasonable reading of the resolution criteria,
               stated as a precise test: which published figure, from which
               source, as of which date, in which units.
            2. Any OTHER reading a careful person might take. If a different
               reading would produce a materially different number, say so.

            Then, on its own line, exactly one of:
            FIGURE AMBIGUITY: LOW
            FIGURE AMBIGUITY: HIGH
            Use HIGH only when competing readings would genuinely produce
            different numbers - not merely because the future is uncertain.
            Uncertainty about the world is normal and belongs in the spread of
            your distribution. Uncertainty about WHICH QUANTITY is being asked for
            is different: if you write HIGH, widen your 10 to 90 interval
            materially, because you are not entitled to a sharp distribution when
            you are unsure what is being measured.

            Formatting Instructions:
            - Please notice the units requested and give your answer in these units (e.g. whether you represent a number as 1,000,000 or 1 million).""",
    "numeric prompt: still-open guard and adversarial read, ABOVE the formatting bullets",
)

# ---------------------------------------------------------------------------
# 22. THE NUMERIC PROMPT GETS THE BINARY PROMPT'S SAFEGUARDS  (21 Sept 2026)
#
#     Edit 2 built three things and wired all of them into the BINARY prompt
#     only: the still-open guard, the adversarial resolution-criteria reading
#     with its AMBIGUITY flag, and an explicit base-rate step. The numeric prompt
#     got none of them, and nobody noticed for three weeks.
#
#     That is not a small gap. Numeric and discrete questions were roughly 45% of
#     the 7-25 Sept MiniBench round and 31% of the Spring seasonal tournament. So
#     close to half of every round was being forecast with no adversarial read of
#     the criteria and no reference class — by a bot whose entire stated first
#     pillar is adversarial reading of the criteria. Found by audit, 21 Sept 2026.
#
#     The numeric versions are adapted, not copied. On a binary question the
#     criteria decide WHETHER something counts; on a numeric one they decide
#     WHICH PUBLISHED FIGURE counts — which source, as of which date, in which
#     units, rounded how. That is the failure this step has to catch, and the
#     Spring advice notebook prices the related one: bot maker #45 reported
#     losing 90 peer points purely for not telling the model about the
#     "assume it has not happened yet" convention.
#
#     ⚠️ ONE HONEST DIFFERENCE FROM THE BINARY PATH. On binary, AMBIGUITY: HIGH is
#     ENFORCED in code — caps_for_reasoning() clamps the probability to
#     [0.10, 0.90]. Here it is ADVISORY only: the model is asked to widen its own
#     interval. Enforcing it would mean rewriting percentiles after the fact, and
#     this project has already lost points once to exactly that
#     (_sorted_percentiles, retracted 31 Aug). Do not read the flag on a numeric
#     question as a guarantee that the distribution was widened.
#
#     The lettered list is reordered rather than appended to, so the base rate
#     anchors the reasoning BEFORE the scenarios are generated rather than after
#     them. It also gains an explicit slot for the whole-number judgement that
#     edit 20 asks for, which until now had nowhere to go.
# ---------------------------------------------------------------------------
replace(
    """            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The outcome if nothing changed.
            (c) The outcome if the current trend continued.
            (d) The expectations of experts and markets.
            (e) A brief description of an unexpected scenario that results in a low outcome.
            (f) A brief description of an unexpected scenario that results in a high outcome.

            {self._get_conditional_disclaimer_if_necessary(question)}
            You remind yourself that good forecasters are humble and set wide 90/10 confidence intervals to account for unknown unknowns.

            The last thing you write is your final answer as:
            "
            Percentile 10: XX (lowest number value)""",
    """            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The base rate or reference class: how this quantity has behaved
                over comparable past periods. State the numbers behind it and
                treat that as your starting anchor before adjusting for anything
                current.
            (c) The outcome if nothing changed.
            (d) The outcome if the current trend continued.
            (e) The expectations of experts and markets.
            (f) The grid the resolution source publishes on - whole numbers, one
                decimal place, two decimals - and, ONLY if the scoring bins are
                finer than that grid, which values you consider most likely.
            (g) A brief description of an unexpected scenario that results in a low outcome.
            (h) A brief description of an unexpected scenario that results in a high outcome.

            {self._get_conditional_disclaimer_if_necessary(question)}
            You remind yourself that good forecasters are humble and set wide 90/10 confidence intervals to account for unknown unknowns.

            The last thing you write is your final answer as:
            "
            Percentile 10: XX (lowest number value)""",
    "numeric prompt: still-open guard, adversarial criteria read, base rate",
)

# ---------------------------------------------------------------------------
# 23. THE SCORING GRID, AND THE RETRACTION OF EDIT 20'S PREMISE (21 Sept 2026)
#
#     ⚠️ EDIT 20 WAS BUILT ON A CLAIM THAT IS FALSE. It told the model that
#     probability placed on 0.3 or 1.5 for a count question "cannot happen and is
#     simply thrown away". An audit pulled the REAL parameters of every numeric
#     and discrete question in the 7-25 Sept MiniBench round and measured them
#     against the pinned SDK. Metaculus publishes q45541 - the very question edit
#     20 cites - as a DISCRETE question with range -0.5 to 4.5 and five bins of
#     width 1.0, each centred on an integer. Probability at 0.3 lands in the "0"
#     bin. Probability at 0.6 lands in the "1" bin. Nothing is thrown away.
#
#     Measured on q45541, which resolved at 1: our actual output scored 0.365 of
#     probability on the outcome, not the 0.0124 an earlier audit computed
#     against assumed 201-bin continuous parameters. Straddling there is worth
#     +0.18 nats when right and -0.31 when wrong - a coin flip - and it spends
#     two of six percentiles inside a single bin.
#
#     WHERE IT GENUINELY WINS is the opposite case: a NUMERIC-typed integer
#     quantity whose bins are FINER than the grid the source publishes on.
#     q45561 (Brazil measles, 29-70 over 200 bins, width 0.205) measured +2.84
#     nats when right against -0.39 when wrong. That is about one question in
#     thirty, not the twenty-five in thirty edit 20 and edit 22 between them were
#     about to fire on.
#
#     So the test is not "is it a whole number" - it is "are the scoring bins
#     finer than the publication grid". That is computable, so the bot is told
#     rather than asked to guess. It also catches cases edit 20's framing missed
#     entirely, such as a source publishing to one decimal place.
#
#     This edit supersedes edit 20's bullets. Edit 20 is left in the file with
#     its comment intact rather than deleted, because the disclosure record
#     should show the retraction and not just the corrected version.
# ---------------------------------------------------------------------------
replace(
    """    async def _run_forecast_on_numeric(
        self, question: NumericQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_bound_message, lower_bound_message = (
            self._create_upper_and_lower_bound_messages(question)
        )""",
    """    async def _run_forecast_on_numeric(
        self, question: NumericQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_bound_message, lower_bound_message = (
            self._create_upper_and_lower_bound_messages(question)
        )
        grid_message = _scoring_grid_message(question)""",
    "numeric path: compute the scoring grid before building the prompt",
)

replace(
    """def caps_for_reasoning(reasoning: str) -> tuple[float, float]:""",
    """def _scoring_grid_message(question) -> str:
    \"\"\"Describe the scoring grid so the model can judge when to concentrate.

    Metaculus scores a numeric question over a fixed number of bins. Whether it
    is worth concentrating probability on particular values depends entirely on
    how that grid compares with the grid the resolution source publishes on:

      bins WIDER than the published grid  -> a smooth distribution already puts
                                             the mass in the right bin, and
                                             spiking wastes percentiles
      bins FINER than the published grid  -> concentration is worth a great deal
                                             (+2.84 nats measured on q45561)

    Discrete questions arrive with one bin per achievable outcome, so they fall
    in the first case and need no special handling at all - which is the
    opposite of what edit 20 assumed.

    Never raises: a missing or odd attribute yields an empty string and the
    prompt simply omits the line. An advisory line is not worth a dead sample.
    \"\"\"
    try:
        # A log-scaled question has no single bin width. Metaculus maps CDF
        # position to value geometrically when zero_point is set, so the bins
        # at the bottom of the range can be a hundred times narrower than the
        # ones at the top, and any single figure stated here would be a lie -
        # feeding the one input the concentrate-or-smooth decision rests on.
        # Measured on a 1-1000 log question: stated 4.995, true range 0.035 to
        # 33.95. Silence is correct. Second audit pass, 21 Sept 2026.
        if getattr(question, "zero_point", None) is not None:
            return ""
        cdf_size = getattr(question, "cdf_size", None) or 0
        lower = question.lower_bound
        upper = question.upper_bound
        if not cdf_size or cdf_size < 2 or upper is None or lower is None:
            return ""
        # cdf_size is the number of CDF POINTS, which is one more than the
        # number of bins between them. Calling a 201-point grid "201 bins"
        # overstates it by one; the width was always right.
        bins = cdf_size - 1
        width = (upper - lower) / bins
        if width <= 0:
            return ""
        # The leading "- " belongs to the MESSAGE, not the prompt, so that an
        # empty return leaves no orphan bullet behind. Audit, 21 Sept 2026.
        return (
            f"- This question is scored over {bins} bins, each about {width:.4g} "
            "wide. Concentrating probability on particular values only helps if "
            "those bins are FINER than the grid the resolution source itself "
            "publishes on. If the source reports whole numbers and a bin is a "
            "whole number wide or wider, a smooth distribution already places "
            "your probability in the correct bin and you should not try to "
            "spike it."
        )
    except Exception:  # an advisory line must never kill a forecast
        return ""


TELEMETRY_MARKER = "IBJ-TELEMETRY"


def _subquestion_arm(question_id) -> bool:
    \"\"\"Deterministically assign a question to the subquestion-research arm.

    Half the season gets subquestion research, half does not, and the season is
    then a true A/B rather than a before-and-after against a different set of
    questions. Spring 2026 put "researches subquestions" at r = +0.24 on n = 41,
    q = 0.475 - a hypothesis, not a finding, which is exactly what deserves a
    controlled test rather than adoption on faith.

    STABLE BY CONSTRUCTION. sha256 of the question id, not Python's builtin
    hash(), which is salted per process: the same question retried in a later
    run would land in the other arm and both arms would be polluted. It also
    means the assignment can be recomputed months later from the id alone,
    without trusting the logs.

    Fails to the CONTROL arm, never to the treatment: an unreadable id must not
    quietly spend money on an experiment it cannot record.
    \"\"\"
    try:
        if not isinstance(question_id, (int, str)) or question_id == "":
            return False
        digest = hashlib.sha256(f"subq:{question_id}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % 2 == 1
    except Exception:
        return False


def _telemetry(**fields) -> None:
    \"\"\"Emit one greppable JSON object per sample. NEVER raises.

    WHY THIS EXISTS. The bot is about to run a season as a MEASUREMENT rather
    than a bet, with several changes shipping together. Without a per-sample
    record the post-season analysis is archaeology: one total, several causes,
    no way to separate them. With one, the questions become arithmetic.

    What it makes computable after the fact, for free:
      - THE AGGREGATION RULE. Every sample value is here, so median-of-3,
        median-of-1, the arithmetic mean, the geometric mean of odds and a
        trimmed mean can each be scored against the published outcome. The
        counterfactual DIFFERENCE in peer score is 100*ln(p'/p) for binary and
        multiple choice, 50*ln(p'/p) for continuous - the field's geometric mean
        cancels, so it never has to be reconstructed. Established by audit on
        22 Sept 2026, correcting an earlier method of mine that held the field
        term fixed and was biased by ~1.3 points a question at n=40.
      - WHETHER THE STATUS QUO CHECK FIRES, and by how much it moves the number,
        from the (f)/(g) pair carried on every binary sample.
      - THE PARSE FAILURE RATE per question, by subtraction: a sample that fails
        to parse never reaches this line, so expected-minus-logged is the count.
        That matters because a forfeited question scores zero, which is ABOVE our
        current average - so any median-of-1 reconstruction from these lines is
        conditioned on survival and overstates it until that rate is applied as
        a correction.

    Logged rather than written to a file on purpose: Actions keeps the logs, the
    workflow keeps no artefacts, and a file would need a new step and a
    retention policy. One marker, one grep, one json.loads per line.
    \"\"\"
    try:
        logger.info("%s %s", TELEMETRY_MARKER, json.dumps(fields, default=str, sort_keys=True))
    except Exception:  # telemetry must never cost a forecast
        pass


def _status_quo_pair(reasoning: str):
    \"\"\"Return (anchor, final) from a binary reasoning text, or (None, None).

    The binary prompt asks for (f) the probability implied by the status quo
    continuing and (g) the final probability, both as numbers. This reads them
    back, so the fix can be measured rather than believed.

    TELEMETRY ONLY, and wrapped: nothing downstream reads the return value, and
    no forecast may ever be lost to a logging regex. Takes the LAST match for
    each letter, like caps_for_reasoning - a model that restates the instruction
    before answering it must not fool this.

    Every backslash below is doubled in patch_phase1.py.
    \"\"\"
    try:
        def grab(letter):
            found = re.findall(
                r"^[ \\t\\r>#*_-]*\\(" + letter + r"\\)[^0-9\\n]{0,100}?([0-9]+(?:\\.[0-9]+)?)\\s*%?",
                reasoning or "",
                re.MULTILINE,
            )
            return float(found[-1]) if found else None
        return grab("f"), grab("g")
    except Exception:
        return None, None


def _reject_mismatched_options(predicted_option_list, question) -> None:
    \"\"\"Raise unless the parser returned exactly the question's options.

    FAIL A BAD OPTION LIST AS A SAMPLE, NOT AS THE QUESTION. Nothing checked
    this. A truncated or duplicated list passes PredictedOptionList's own
    validator happily - one option at 1.0 clamps to 0.99 and sums to 1 - and
    then raises in MultipleChoiceReport.aggregate_predictions, which runs
    OUTSIDE the per-sample gather. One bad sample forfeited the whole question
    even when the other four were perfect. Exactly the failure already closed on
    the numeric path by forcing get_cdf() per sample. Audit, 21 Sept 2026.

    Compares SORTED LISTS, not sets. A set comparison passes [A, A, B, C]
    against [A, B, C], and the library's separate length check then raises
    outside the gather - worse still, if the duplicating sample happens to be
    predictions[0] it sets the expected length and every GOOD sample fails.
    Found by the second audit pass, which is why this is a list.

    Not over-strict: publish_report_to_metaculus posts option_name verbatim as
    the Metaculus payload key, so a name that differs from question.options in
    any way could never have published. Every case rejected here was already
    lost; it is now lost one sample at a time instead of a question at a time.

    Degrades to silence, never to noise: if the library renames either
    attribute this must go back to the old behaviour rather than raise on every
    sample of every multiple-choice question for four unattended months. Both
    sides of the comparison are read inside the try for that reason.
    \"\"\"
    try:
        parsed = sorted(
            option.option_name
            for option in predicted_option_list.predicted_options
        )
        expected = sorted(question.options)
    except Exception:
        logger.warning(
            "Option guard could not read this question's options - skipping it. "
            "If this appears on every multiple-choice question, the library has "
            "renamed a field and the guard is no longer protecting anything."
        )
        return
    if parsed != expected:
        raise ValueError(
            f"Parser returned {parsed} but the question asks for {expected} "
            "- rejecting this sample."
        )


def caps_for_reasoning(reasoning: str) -> tuple[float, float]:""",
    "two module-level helpers, ABOVE the __main__ block",
)

# ---------------------------------------------------------------------------
# 25. PARSER GUARDS PORTED BY HAND FROM forecasting-tools v0.3.0  (21 Sept 2026)
#
#     v0.3.0 (released 7 Sept) added two guards to ITS copy of the template:
#     _create_resolved_question_parsing_message and
#     _create_single_distribution_parsing_message. Bumping the dependency
#     delivers NEITHER, because this file vendors its own SummerTemplateBot2026
#     and every parse call below is ours. Verified against the pinned 0.2.92 and
#     the 0.3.0 source. So they are ported by hand.
#
#     Condensed to two bullets and shared by all four parse paths. The binary
#     path had no parsing instructions at all until now.
# ---------------------------------------------------------------------------
replace(
    """    ##################################### RESEARCH #####################################

    async def run_research(self, question: MetaculusQuestion) -> str:""",
    """    # -------------------------------------------------------------------
    # PARSER GUARDS (see edit 25 in patch_phase1.py for provenance).
    #
    # Guard one. The parser is a cheaper model reading a long reasoning text.
    # If the forecaster discusses a figure as though the matter were settled,
    # the parser can lift that figure instead of the forecast. Every FutureEval
    # question is open at forecast time, so a "known outcome" in the text is
    # always the forecaster's framing and never a resolution.
    #
    # Guard two. Drafts, worked examples and sensitivity checks all look like
    # final answers. Merging two of them produces a forecast nobody wrote, and
    # averaging them produces one nobody would defend. Last complete answer
    # wins, which is also how caps_for_reasoning reads the binary flag.
    #
    # Written as long single lines to match the bullet style of the blocks they
    # join. The twelve-space continuation indent is for READABILITY of the
    # rendered prompt only - it is not load-bearing. clean_indents() is not
    # textwrap.dedent: it takes the deeper of the first two lines' indents and
    # lstrips anything shallower, so a flush-left line cannot poison the dedent
    # of the surrounding prompt. Checked against the library, 21 Sept 2026.
    # -------------------------------------------------------------------
    _PARSER_GUARDS = (
        '- The question is STILL OPEN and has NOT resolved. The text may discuss a figure or an event as though the matter were already settled; that is the forecaster weighing evidence, not a resolution. Parse the forecast the text actually gives. Never parse a value the text presents as an already-known outcome, and never supply one yourself.\\n'
        '            - The text may contain MORE THAN ONE candidate answer: an early draft, a worked example, a sensitivity check, or the instruction restated. Use only the LAST complete final answer in the text, and use it IN FULL. Never merge two candidate answers and never average them.'
    )

    ##################################### RESEARCH #####################################

    async def run_research(self, question: MetaculusQuestion) -> str:""",
    "parser guards: the shared constant",
)

replace(
    "            - Turn any values that are in scientific notation into regular numbers.",
    "            - Turn any values that are in scientific notation into regular numbers.\n"
    "            {self._PARSER_GUARDS}",
    "parser guards: numeric path",
)

replace(
    "            - The output is given as dates/times please format it into a valid datetime parsable string. Assume midnight UTC if no hour is given.",
    "            - The output is given as dates/times please format it into a valid datetime parsable string. Assume midnight UTC if no hour is given.\n"
    "            - Parse ONLY the final \"Percentile NN: YYYY-MM-DD\" block. Ignore every other date in the text, including reference-class dates and interpretation notes.\n"
    "            {self._PARSER_GUARDS}",
    "parser guards: date path",
)

replace(
    '            The text you are parsing may prepend these options with some variation of "Option" which you should remove if not part of the option names I just gave you.',
    '            The text you are parsing may prepend these options with some variation of "Option" which you should remove if not part of the option names I just gave you.\n'
    '            {self._PARSER_GUARDS}',
    "parser guards: multiple-choice path",
)

# ---------------------------------------------------------------------------
# 26. THE BINARY PATH GETS PARSING INSTRUCTIONS AT ALL  (21 Sept 2026)
#
#     Binary was the one path calling structure_output with no
#     additional_instructions whatsoever. The parser was handed a page of
#     reasoning and the bare BinaryPrediction schema, and left to work out which
#     of the several percentages in the text was the answer. It has been getting
#     that right, but on inference alone.
#
#     Binary is roughly half of every round and it is the path where the caps
#     live, so a parser that lifts the wrong percentage produces a confident
#     wrong number rather than a failed sample. Two bullets and the shared
#     guards close it.
# ---------------------------------------------------------------------------
replace(
    """    async def _binary_prompt_to_forecast(
        self,
        question: BinaryQuestion,
        prompt: str,
    ) -> ReasonedPrediction[float]:""",
    """    async def _binary_prompt_to_forecast(
        self,
        question: BinaryQuestion,
        prompt: str,
    ) -> ReasonedPrediction[float]:
        parsing_instructions = clean_indents(
            f\"\"\"
            The text given to you is a forecast of the probability that a binary question resolves YES.
            - This text is trying to answer the question: "{question.question_text}".
            - The text states its answer as a PERCENTAGE, for example "Probability: 73%". The field you are filling, prediction_in_decimal, is a DECIMAL BETWEEN 0 AND 1. Divide by one hundred: 73% becomes 0.73, 4% becomes 0.04, 99% becomes 0.99. A value above 1 in that field is always wrong and will be rejected.
            - The answer is ALWAYS the final "Probability:" line, and nothing else. Ignore every other percentage in the text: base rates, reference-class figures, the probabilities inside scenarios, the STATUS QUO ANCHOR at (f), and the working figure at (g). The text may state a figure at (g) and then REVISE it at (h) — in that case the revision is what reaches the final line, and the final line is what you parse.
            - If the text writes its final answer WITHOUT a percent sign as a decimal below 1, such as "Probability: 0.73", use that value unchanged. A bare number of 1 or more is on the 0-100 scale: "Probability: 73" is 0.73, and "Probability: 1" is 0.01. A number followed by "%" is ALWAYS a percentage however small it is: 1% is 0.01, and 0.5% is 0.005. Never read "1%" as 1.
            {self._PARSER_GUARDS}
            \"\"\"
        )""",
    "binary path: build parsing instructions",
)

replace(
    """            BinaryPrediction,
            model=self.get_llm("parser", "llm"),
            num_validation_samples=self._structure_output_validation_samples,
        )""",
    """            BinaryPrediction,
            model=self.get_llm("parser", "llm"),
            additional_instructions=parsing_instructions,
            num_validation_samples=self._structure_output_validation_samples,
        )""",
    "binary path: wire the parsing instructions in",
)

# ---------------------------------------------------------------------------
# 27. LABEL THE RESOLUTION CRITERIA ON THE NUMERIC PROMPT  (21 Sept 2026)
#
#     The binary prompt introduces the criteria with a sentence that does two
#     jobs: it says what the block IS, and it states the convention that the
#     criteria have not yet been satisfied. The numeric prompt dropped the
#     criteria in as an unlabelled block between the background and the fine
#     print, so the model had to infer what it was reading.
#
#     That matters more here than on binary, because edit 24 then asks for a
#     strict reading of a block the prompt never named. The Spring advice
#     notebook prices the missing convention directly: bot maker #45 reported
#     losing 90 peer points for not telling the model to assume the event has
#     not happened yet.
#
#     Same sentence as binary, verbatim, so the two prompts cannot drift.
# ---------------------------------------------------------------------------
replace(
    """            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}

            Units for answer:""",
    """            Background:
            {question.background_info}

            This question's outcome will be determined by the specific criteria below. These criteria have not yet been satisfied:
            {question.resolution_criteria}

            {question.fine_print}

            Units for answer:""",
    "numeric prompt: label the resolution criteria",
)

# ---------------------------------------------------------------------------
# 28. LOG THE FLAG BEFORE THE PARSE, NOT AFTER IT  (third audit pass, 21 Sept)
#
#     The FIGURE AMBIGUITY telemetry sat after _structure_output_paced. A parser
#     that raises kills the sample before the log line is reached, so the log
#     went quiet on exactly the samples worth inspecting. It belongs immediately
#     after the reasoning arrives, where nothing can come between them.
# ---------------------------------------------------------------------------
replace(
    """        parsing_instructions = clean_indents(
            f\"\"\"
            The text given to you is trying to give a forecast distribution for a numeric question.""",
    """        logger.info(
            "FIGURE AMBIGUITY on %s: %s",
            question.page_url,
            _figure_ambiguity_flag(reasoning) or "not declared",
        )
        parsing_instructions = clean_indents(
            f\"\"\"
            The text given to you is trying to give a forecast distribution for a numeric question.""",
    "numeric path: log the ambiguity flag before a parse failure can hide it",
)

# ---------------------------------------------------------------------------
# 29. THE CRITERIA LABEL BELONGS ON EVERY PATH  (third audit pass, 21 Sept 2026)
#
#     Edit 27 gave the numeric prompt binary's labelled criteria sentence and
#     justified it with evidence that applies to all four question types - bot
#     maker #45 losing 90 peer points for not stating the "assume it has not
#     happened yet" convention. Multiple choice and date were left with an
#     unlabelled block, so the rationale stopped one path short of its own
#     conclusion. Same sentence, verbatim, on both.
# ---------------------------------------------------------------------------
replace(
    """            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}


            Your research assistant says:""",
    """            Background:
            {question.background_info}

            This question's outcome will be determined by the specific criteria below. These criteria have not yet been satisfied:
            {question.resolution_criteria}

            {question.fine_print}


            Your research assistant says:""",
    "multiple-choice prompt: label the resolution criteria",
)

replace(
    """            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}

            Your research assistant says:""",
    """            Background:
            {question.background_info}

            This question's outcome will be determined by the specific criteria below. These criteria have not yet been satisfied:
            {question.resolution_criteria}

            {question.fine_print}

            Your research assistant says:""",
    "date prompt: label the resolution criteria",
)

# ---------------------------------------------------------------------------
# 30. THE STATUS-QUO CHECK  (measured, 22 Sept 2026 — the largest single defect
#     found in this project, and the one that cost us the round)
#
#     MEASURED, not reasoned. Per-question peer scores for all 59 scored
#     questions of the 7-25 Sept MiniBench round, split by type and direction:
#
#       binary  25 q  -196.0  avg -7.84   53% of the loss
#       MC       4 q  -139.6  avg -34.91  38% of the loss
#       numeric 30 q   -35.9  avg -1.20   10% of the loss
#
#     And within binary, the whole story:
#
#       we said <=50%   15 q   mean forecast 24%   actually resolved YES  20%
#       we said  >50%   10 q   mean forecast 71%   actually resolved YES  50%
#
#     The low half of the book is well calibrated. The high half is not: at 71%
#     stated, reality is a coin flip. Because the scoring rule is asymmetric,
#     those ten confident-YES calls produced -240.5 from the five that failed
#     against only +67.0 from the five that landed. That net -173.5 is 88% of the
#     entire binary loss, from 40% of the binary questions.
#
#     THE MECHANISM IS VISIBLE IN THE BOT'S OWN PUBLISHED COMMENTS. On every one
#     of the three worst questions it states the status quo correctly and then
#     talks itself out of it. Verbatim, q45527: "(c) Status quo outcome: NO. As
#     of September 7, 2026, no bill with an assigned S. or H.R. number ...
#     appears on Congress.gov." Forecast: 86%. Resolved NO. Score -80.9. The
#     override: "press rollouts of this magnitude - complete with detailed
#     statutory penalties, explicit bill titles, and joint bicameral leadership -
#     typically i[ndicate]". Same shape on q45533 (status quo NO, forecast 72%)
#     and q45576 (status quo NO, forecast 65%).
#
#     AND THERE IS A DOMAIN PATTERN. The five confident calls that failed are all
#     institutional: a bill formally introduced, coalition talks publicly begun,
#     an agency announcing a specific date, a reporting threshold crossed. The
#     five that landed are market and price questions. The bot treats an
#     announcement of intent as evidence of completion.
#
#     WHY THE PROMPT AND NOT THE CODE. The obvious alternative is to shrink the
#     over-50% book toward the base rate in code. Re-scoring the round against a
#     reconstructed field geometric mean says that is worth about +1.2 to +1.7 a
#     question - real, but it is SELF-FITTED RECALIBRATION on 25 points from one
#     round, which this project already examined and killed as a trap, and which
#     would have been fitted on the very data used to justify it. It also treats
#     the symptom. The defect is that the model names the status quo and then
#     abandons it without completed evidence, so that is what is addressed.
#
#     DELIBERATELY MINIMAL. Nothing else in the binary prompt moves - not the
#     lettered list, not the scenario order, not the caps. If this is ever
#     measured, the change that moved it must be identifiable.
# ---------------------------------------------------------------------------
replace(
    """            The last thing you write is your final answer as: "Probability: ZZ%", 0-100""",
    """            Then run the STATUS QUO CHECK. It is the last thing you do before
            answering, and nothing you write after it may move your number. Write:
            (f) The probability implied by the status quo simply continuing,
                as a number. That is your anchor.
            (g) Your final probability, as a number.
            (h) If (g) is ABOVE 50% and more than 20 points above (f), name the
                specific COMPLETED, DATED, VERIFIABLE step that has ALREADY
                happened and that justifies the move. An announcement, a stated intention, a
                draft, a scheduled meeting, an expert expectation, press coverage
                and elapsed time are NOT completed steps. If you cannot name one,
                move (g) back toward (f) and say that you have done so. Whatever
                number you end on after (h) is the one you state below.

            Institutions miss deadlines. Where the question asks whether a
            legislature, an agency, a company or an official will have COMPLETED a
            formal step by a given date - introduced a bill, published a filing,
            announced a specific date, begun formal talks, crossed a reporting
            threshold - that step slips past short deadlines far more often than
            the surrounding reporting suggests. Momentum in the news is not the
            step being taken.

            The last thing you write is your final answer as: "Probability: ZZ%", 0-100""",
    "binary prompt: the status quo check, and institutional slippage",
)

# ---------------------------------------------------------------------------
# 31. THE MULTIPLE-CHOICE FLOOR WENT ON THE OPTION THAT RESOLVED  (22 Sept 2026)
#
#     Multiple choice is 7% of the questions and 38% of the loss: four questions,
#     -139.6, an average of -34.91 each. Both large losses are the SAME failure as
#     edit 30, wearing a different hat - the option describing "nothing happened"
#     got our 0.01 floor and then resolved:
#
#       q45555  "Where will GPT-6 Astra rank..."   resolved "Rank 21 or lower"  -129.9
#       q45539  "If Anthropic files a public IPO..." resolved "No public S-1 filed yet"  -31.8
#
#     The two we won were a positive event and a null option we happened to like.
#
#     Edit 16 told the parser never to emit a literal zero and to use 0.01 as the
#     minimum. That was correct for its own purpose - the library's validator
#     rejects a sample outright if clamping moves an option by more than 0.05 -
#     but 0.01 is a floor for options that are IMPOSSIBLE, and it was being
#     applied to options that were merely dull. Nothing in the prompt asked the
#     model to identify the status-quo option at all.
#
#     n = 4. Small, and stated plainly. But the mechanism is identical to the
#     binary one measured over 25 questions, the fix is two lines, and the
#     downside of holding 10% on a status-quo option that does not resolve is
#     roughly a tenth of what one of these costs.
# ---------------------------------------------------------------------------
replace(
    """            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The status quo outcome if nothing changed.
            (c) A description of an scenario that results in an unexpected outcome.""",
    """            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The status quo outcome if nothing changed.
            (c) A description of an scenario that results in an unexpected outcome.
            (d) Restate (b) as ONE OF THE LISTED OPTIONS - the status quo option,
                the one that wins if nothing changes, nothing is announced and no
                threshold is crossed. Name it exactly as it appears in the list. It
                is often the lowest band, "no change", "none of the above", or the
                option that simply describes the present state.
            (e) Give that option at least 0.10 unless a completed, dated,
                verifiable event has ALREADY ruled it out. A probability of 0.01 is
                for an option that is genuinely impossible, not for one that is
                merely dull.""",
    "multiple-choice prompt: name and protect the status quo option",
)

# ---------------------------------------------------------------------------
# 32. THE RESEARCHER WAS TOLD TO PRE-JUDGE THE ANSWER  (22 Sept 2026)
#
#     Found by an audit asked to attack a DIFFERENT decision, which is how the
#     best findings arrive.
#
#     Upstream's research prompt asks for a rundown of the news "including if the
#     question would resolve Yes or No based on current information". That verdict
#     is then handed to the forecaster under the heading "Your research assistant
#     says:" - an authoritative framing, delivered BEFORE any reasoning step runs.
#
#     Now put that next to the measured defect. Of 25 scored binary questions, the
#     bot's <=50% book was well calibrated (24% stated, 20% resolved YES) and its
#     >50% book was not (71% stated, 50% resolved). All five confident failures
#     were INSTITUTIONAL - a bill introduced, coalition talks begun, an agency
#     announcing a date, a reporting threshold crossed - and each had fresh news
#     coverage of an announcement. A news-search model asked "would this resolve
#     Yes?" on the day a bill is announced says yes. The forecaster then spends
#     the rest of the prompt arguing with an assistant it has been told to trust.
#
#     The bot already carried a patch for the SYMPTOM: edit 2 added "If your
#     research appears to show the outcome is already settled, treat that as a
#     warning sign rather than a conclusion" to the binary prompt. A warning was
#     bolted on downstream while the instruction generating the problem was left
#     in place upstream. This removes the cause.
#
#     It also asks for the thing the status quo check needs and nobody was
#     providing: what has NOT yet happened, as of today, with dates.
#
#     Applies to every question type, since run_research is shared.
# ---------------------------------------------------------------------------
replace(
    """                To be a great assistant, you generate a concise but detailed rundown of the most relevant news, including if the question would resolve Yes or No based on current information.
                You do not produce forecasts yourself.""",
    """                To be a great assistant, you generate a concise but detailed rundown of the most relevant news. Report DATED, SOURCED facts and give the date of each one.
                State explicitly which of the steps this question depends on have NOT yet happened as of today, and keep an announcement, a plan, a draft, a scheduled meeting or a stated intention clearly separate from the thing itself having been done.
                You do not produce forecasts yourself, and you do not say whether the question would resolve Yes or No. That judgement belongs to the forecaster, and stating it here biases them.""",
    "researcher: report dated facts and what has NOT happened, never a verdict",
)

# ---------------------------------------------------------------------------
# 33. ONE TELEMETRY LINE PER SAMPLE  (22 Sept 2026)
#
#     See _telemetry for what this buys. In short: the season is being entered
#     to measure, several changes ship together, and a single season total
#     cannot separate them. These lines can.
#
#     Placed on the SUCCESS path only, immediately before each return, and
#     deliberately not wrapped around the parse in a try/except. A sample that
#     fails never reaches the line, so the failure count falls out by
#     subtraction without any exception handling to get wrong - and exception
#     handling around a forecast is exactly where this project has hurt itself
#     before.
#
#     kind comes from type(question).__name__ so numeric, discrete and date
#     label themselves correctly without a second edit or a guess.
# ---------------------------------------------------------------------------
replace(
    """        return ReasonedPrediction(prediction_value=decimal_pred, reasoning=reasoning)""",
    """        _anchor, _final = _status_quo_pair(reasoning)
        _telemetry(
            q=question.id_of_question,
            url=question.page_url,
            kind=type(question).__name__,
            sample=decimal_pred,
            raw=binary_prediction.prediction_in_decimal,
            floor=floor,
            ceiling=ceiling,
            sq_anchor=_anchor,
            sq_final=_final,
            chars=len(reasoning or ""),
        )
        return ReasonedPrediction(prediction_value=decimal_pred, reasoning=reasoning)""",
    "telemetry: binary sample",
)

# ---------------------------------------------------------------------------
# 34. FACTOR THE RESEARCHER DISPATCH OUT OF run_research  (22 Sept 2026)
#
#     Upstream inlines a five-branch if/elif that picks between a GeneralLlm,
#     AskNews, SmartSearcher, no research at all, and a plain model string. The
#     subquestion pass needs to reach the SAME researcher, and duplicating five
#     branches to do it would guarantee they drift apart. One method, two
#     callers, no behaviour change.
# ---------------------------------------------------------------------------
replace(
    """            if isinstance(researcher, GeneralLlm):
                research = await researcher.invoke(prompt)
            elif (
                researcher == "asknews/news-summaries"
                or researcher == "asknews/deep-research/low-depth"
                or researcher == "asknews/deep-research/medium-depth"
                or researcher == "asknews/deep-research/high-depth"
            ):
                research = await AskNewsSearcher().call_preconfigured_version(
                    researcher, prompt
                )
            elif researcher.startswith("smart-searcher"):
                model_name = researcher.removeprefix("smart-searcher/")
                searcher = SmartSearcher(
                    model=model_name,
                    temperature=0,
                    num_searches_to_run=2,
                    num_sites_per_search=10,
                    use_advanced_filters=False,
                )
                research = await searcher.invoke(prompt)
            elif not researcher or researcher == "None" or researcher == "no_research":
                research = ""
            else:
                research = await self.get_llm("researcher", "llm").invoke(prompt)""",
    """            research = await self._invoke_researcher(researcher, prompt)
            # Measured BEFORE enrichment. The subquestion block's own headers
            # clear the emptiness threshold on their own, so checking the
            # enriched string would mean half the season could never raise the
            # alarm at all.
            base_research_chars = len(research.strip())
            research = await self._add_subquestion_research(question, research)""",
    "run_research: dispatch via a method, then the subquestion pass",
)

# ---------------------------------------------------------------------------
# 35. SUBQUESTION RESEARCH, RANDOMISED  (22 Sept 2026)
#
#     Spring 2026: "Researches subquestions" r = +0.24, q = 0.475, n = 41, on a
#     self-selected sample where 62% of respondents won a prize against 28% of
#     the field. That is a hypothesis. It is being tested, not adopted.
#
#     HALF the season gets it, assigned by sha256 of the question id, so the
#     result is a within-season A/B rather than a comparison against a different
#     set of questions in a different round. Randomising also halves the extra
#     research spend, which is the reason the estimate stays inside budget.
#
#     Failure here returns the ORIGINAL research untouched. This is additive
#     enrichment, not a forecast, so a broken subquestion pass must cost nothing
#     more than the subquestions.
# ---------------------------------------------------------------------------
replace(
    """    ##################################### BINARY QUESTIONS #####################################""",
    """    async def _invoke_researcher(self, researcher, prompt: str) -> str:
        \"\"\"Upstream's dispatch, lifted verbatim so two callers can share it.\"\"\"
        if isinstance(researcher, GeneralLlm):
            return await researcher.invoke(prompt)
        if researcher in (
            "asknews/news-summaries",
            "asknews/deep-research/low-depth",
            "asknews/deep-research/medium-depth",
            "asknews/deep-research/high-depth",
        ):
            return await AskNewsSearcher().call_preconfigured_version(researcher, prompt)
        if isinstance(researcher, str) and researcher.startswith("smart-searcher"):
            searcher = SmartSearcher(
                model=researcher.removeprefix("smart-searcher/"),
                temperature=0,
                num_searches_to_run=2,
                num_sites_per_search=10,
                use_advanced_filters=False,
            )
            return await searcher.invoke(prompt)
        if not researcher or researcher in ("None", "no_research"):
            return ""
        return await self.get_llm("researcher", "llm").invoke(prompt)

    async def _add_subquestion_research(self, question, research: str) -> str:
        \"\"\"Half the questions get their subquestions researched. See
        _subquestion_arm for why the assignment is a hash and not a coin.\"\"\"
        in_arm = _subquestion_arm(getattr(question, "id_of_question", None))
        _telemetry(
            q=getattr(question, "id_of_question", None),
            url=getattr(question, "page_url", None),
            kind=type(question).__name__,
            phase="research",
            subquestion_arm=in_arm,
            base_chars=len(research or ""),
        )
        if not in_arm:
            return research
        try:
            asked = await self._invoke_default_llm(
                clean_indents(
                    f\"\"\"
                    A superforecaster is about to forecast the question below.
                    Name the 2 to 3 SUBQUESTIONS whose answers would most change
                    that forecast - the things that must be true, the steps that
                    must have been completed, the figures the outcome turns on.

                    Each must be answerable from news or public data, and must be
                    narrower than the question itself. Do not answer them. Do not
                    say what you think the outcome will be.

                    Write one per line, nothing else, no numbering.

                    Question:
                    {question.question_text}

                    Resolution criteria:
                    {question.resolution_criteria}
                    \"\"\"
                )
            )
            subquestions = [
                line.strip(" -*\\t")
                for line in (asked or "").splitlines()
                if len(line.strip(" -*\\t")) > 15
                and not line.strip().endswith(":")
            ][:2]
            if not subquestions:
                logger.info("Subquestion arm: none generated for %s", question.page_url)
                return research
            researcher = self.get_llm("researcher")
            findings = []
            for sub in subquestions:
                answer = await self._invoke_researcher(
                    researcher,
                    clean_indents(
                        f\"\"\"
                        Report DATED, SOURCED facts bearing on this question, with
                        the date of each. Say plainly what has NOT yet happened.
                        Do not forecast and do not state an expected outcome.

                        {sub}
                        \"\"\"
                    ),
                )
                findings.append(f"### {sub}\\n{answer}")
            _telemetry(
                q=getattr(question, "id_of_question", None),
                kind=type(question).__name__,
                phase="subquestions",
                asked=subquestions,
                added_chars=sum(len(f) for f in findings),
            )
            return (
                f"{research}\\n\\n"
                "## Subquestion research\\n"
                "The following were researched separately because their answers "
                "bear on the question above.\\n\\n" + "\\n\\n".join(findings)
            )
        except Exception as exc:  # enrichment must never cost the main research
            logger.warning("Subquestion research failed for %s: %s", question.page_url, exc)
            return research

    ##################################### BINARY QUESTIONS #####################################""",
    "the researcher dispatch method and the subquestion pass",
)

DST.write_text(text, encoding="utf-8")
print(f"\n{edits} edits applied cleanly -> {DST}")
