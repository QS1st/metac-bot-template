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

text = SRC.read_text()
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
            (b) The base rate: how often outcomes of this general kind occur over a
                comparable period. State the reference class you are using and the
                numbers behind it, then treat that rate as your starting anchor.
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
        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)

    ##################################### DATE QUESTIONS #####################################""",
    "wire monotonicity guard into numeric path",
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
    'dotenv.load_dotenv()\nlogger = logging.getLogger(__name__)\n\n# Used by caps_for_reasoning(). The upstream template does not import re, and\n# a missing import here would raise on EVERY binary question — caught by the\n# build check added 31 Aug 2026, which is why that check exists.\nimport re  # noqa: E402\n\n# Used by resolve_seasonal_tournament() to read the AIB_TOURNAMENT_ID\n# repository variable. Not imported upstream either; same failure mode.\nimport os  # noqa: E402\n\n# Counts questions forecast with little or no research, so the run can be\n# failed at the end rather than only logged. See the check near the bottom of\n# the file. Module-level because run_research is a method on the bot and the\n# exit decision is made in __main__.\nEMPTY_RESEARCH_COUNT = 0\n\n# The run fails only if BOTH are exceeded: a minority of thin research is\n# normal on niche questions, a majority means the researcher is broken. Failing\n# on a single instance manufactures red-fatigue, which costs more than it saves\n# when red is the only alarm this project has.\nEMPTY_RESEARCH_MIN_TO_FAIL = 3\nEMPTY_RESEARCH_FAIL_RATE = 0.5\n\n# =============================================================================\n# PHASE 1 CONFIGURATION  —  all tunables live here, nowhere else.\n#\n# Evidence grades below refer to Metaculus, "AI Forecasting in 2026: What 11\n# Analyses Say" (8 Jul 2026), which synthesises 11 analyses plus the Fall 2025\n# survey of 39 bot makers (29 prize winners, 10 non-winners).\n# =============================================================================\n\n# Binary prediction caps. MODERATE evidence, and the strongest single\n# differentiator measured among winners (r = +0.48, p = 0.005). 38% of Fall\n# 2025 winners cap; 47% of the top fifteen do, against 29% of the bottom half.\nBINARY_FLOOR = 0.02\nBINARY_CEILING = 0.98\n\n# AMBIGUITY-BOUNDED CONFIDENCE.\n#\n# Peer score = 100 x (ln(p) - ln(geometric mean of other bots)). It is brutally\n# asymmetric: moving 99% -> 99.9% gains 0.009 when right and costs 2.3 when\n# wrong. The expensive error is confident-and-wrong.\n#\n# A systematic source of confident-and-wrong is not misjudging the world but\n# answering a different question from the one asked. Observed live in the\n# Metaculus bot Discord, 29 Aug 2026: "a lot of bots including mine\n# misinterpreted this question... interpreting as \'July is the annual max\'\n# instead of \'July is a NEW annual max\'." A whole cohort, one word.\n#\n# Other entrants enumerate interpretations to INFORM the forecast. We\n# additionally let interpretation ambiguity BOUND it: where competing readings\n# of the criteria would resolve differently, the bot is not entitled to\n# confidence however sure it is about the world. Uncertainty about the world\n# belongs in the probability; uncertainty about the QUESTION is handled here.\nAMBIGUOUS_FLOOR = 0.10\nAMBIGUOUS_CEILING = 0.90\n\n# MULTIPLE-CHOICE FLOOR — REMOVED 1 Sept 2026. Kept as a note, not as code.\n#\n# We floored multiple-choice options at 0.01 and renormalised, on the reasoning\n# that a 0% option which then RESOLVES scores about -691 and erases thirty good\n# questions. The reasoning about the scoring rule was right. The code was\n# pointless: PredictedOptionList carries a model_validator that runs on every\n# construction and already clamps each option to [0.01, 0.99] — the identical\n# value — before structure_output hands it back. Ours could only ever move a\n# probability by about 2e-4.\n#\n# It survived a week because the unit tests fed it (name, probability) tuples\n# directly, bypassing the SDK model. Data the SDK cannot produce, and in one\n# case actively rejects: an all-zero list raises on the sum check. The test\n# agreed with itself and never asked what the SDK does to the value afterwards\n# — the very failure this project had already diagnosed once, in the numeric\n# path, and written up as a lesson.\n#\n# Recorded rather than deleted silently, because the disclosure document should\n# show the retraction as well as the change.\n\n# Number of independent forecasts aggregated per question. STRONG evidence for\n# ensembling (86% of winners aggregate). Phase 2 will widen this across model\n# families; for now it is repeated sampling of one model.\n#\n# On the free tier this is forced to 1. Free models are served from a shared\n# upstream pool and rate-limit hard (a 429 killed our second test run); five\n# predictions per question means five forecast calls plus five parse calls,\n# which trips the limit within a couple of questions. One prediction is enough\n# to prove the plumbing, which is all the free tier is for.\nRESEARCH_REPORTS_PER_QUESTION = 1\n\n# -----------------------------------------------------------------------------\n# MODELS\n#\n# The template ships with no llms= block, so forecasting-tools picks defaults.\n# One of those defaults is openai/gpt-4o-search-preview, which OpenRouter does\n# not serve — it 404s on every question. So we name every model explicitly.\n#\n# All IDs below were verified against https://openrouter.ai/api/v1/models on\n# 29 Aug 2026. OpenRouter\'s free tier rotates with little notice, so if the bot\n# starts returning "No endpoints found", re-check that endpoint first.\n# -----------------------------------------------------------------------------\n\n# FOUR TIERS.\n#\n#   "free"   — :free models, zero balance. KEPT FOR REFERENCE, NOT RECOMMENDED.\n#              Four runs died on it across three providers. Every :free model\n#              has exactly ONE serving endpoint on one provider\'s shared pool,\n#              so there is no failover and the pool rate-limits under any load.\n#   "test"   — cheap paid models, ONE prediction a question. Development only.\n#   "trial"  — cheap paid models, FIVE predictions and live research. A real\n#              forecasting configuration we can afford to fund ourselves, for\n#              scored MiniBench rounds before the Metaculus credits arrive.\n#   "season" — frontier models, on Metaculus\'s credits, for the tournament.\n#\n# The difference is structural, not a matter of picking a better free model\n# (checked 1 Sept 2026 via the /endpoints API):\n#   z-ai/glm-5.2:free .......  1 endpoint   (Decart)          -> 429\'d us\n#   nvidia/nemotron:free ....  1 endpoint   (Nvidia)          -> 404\'d us\n#   google/gemma-4-31b:free .  1 endpoint   (Google AI Studio)-> 429\'d us\n#   openai/gpt-5-nano ....... 4 endpoints   (OpenAI, Azure)\n#   openai/gpt-oss-120b ..... 20 endpoints  (AkashML, CoreWeave, DeepInfra,\n#                                            Novita, SiliconFlow, Google, ...)\n# OpenRouter routes around dead endpoints automatically, so a paid model\n# tolerates a provider outage that kills a free one outright.\n#\n# The tier can be overridden by an environment variable, so a run can be\n# re-pointed without a commit and a CI cycle — the same reasoning as\n# AIB_TOURNAMENT_ID further down. Set a GitHub repository variable named\n# MODEL_TIER, and delete it to fall back to the default below. Note this does\n# NOT weaken assert_tier_matches_mode: a scored tournament still refuses to run\n# on anything outside TOURNAMENT_READY_TIERS, wherever the value came from.\n# (Changing the variable needs a GitHub password re-prompt, so it is a\n# deliberate human act by construction.)\nVALID_MODEL_TIERS = ("free", "test", "trial", "season")\nMODEL_TIER = (os.environ.get("MODEL_TIER") or "test").strip().lower()\nif MODEL_TIER not in VALID_MODEL_TIERS:\n    # Fail here rather than three steps later inside build_llm_config, so a\n    # typo in the repository variable names itself instead of surfacing as a\n    # confusing model error after the questions have already been fetched.\n    raise SystemExit(\n        f"MODEL_TIER must be one of {VALID_MODEL_TIERS}, got {MODEL_TIER!r}. "\n        "Check the MODEL_TIER repository variable."\n    )\n\n# Back-compat: several helpers below still ask "are we on the cheap tier?"\nUSE_FREE_MODELS = MODEL_TIER in ("free", "test")\n\n# Free tier. Not frontier, not competitive — these exist to prove the bot can\n# read a question, form a forecast and post it. "no_research" skips the search\n# step entirely, which removes a dependency we don\'t need while smoke-testing.\n# Parser and summarizer deliberately sit on a DIFFERENT upstream provider from\n# the default model. One provider must not be a single point of failure.\n#\n# EVERY free model on OpenRouter has exactly ONE serving endpoint — a single\n# provider, with no failover. That is why free-tier outages are total rather\n# than degraded. Checked 1 Sept 2026 via\n#   https://openrouter.ai/api/v1/models/<id>:free/endpoints\n# which exposes a per-endpoint `status` (0 = normal, negative = degraded).\n#\n# Providers that have already failed us, and are avoided here:\n#   Nvidia           — nemotron-3-ultra 404\'d mid-run on 30 Aug ("Provider\n#                      returned error"), despite still being listed and still\n#                      reporting status 0. Listing is not availability.\n#   Google AI Studio — gemma-4-31b 429\'d on 29 Aug from its shared free pool.\n#\n# So: default on Decart, parser/summarizer on GMICloud. Neither has failed us,\n# and they are independent of each other.\nFREE_MODELS = {\n    "default": "openrouter/z-ai/glm-5.2:free",\n    "summarizer": "openrouter/minimax/minimax-m3:free",\n    "parser": "openrouter/minimax/minimax-m3:free",\n    "researcher": "no_research",\n}\n\n# TEST tier — cheap paid models, chosen for endpoint COUNT as much as price.\n# Prices verified against OpenRouter\'s live model list, 1 Sept 2026, per 1M\n# tokens (input / output):\n#   openai/gpt-5-nano    $0.050 / $0.400   400k ctx,  4 endpoints\n#   openai/gpt-oss-120b  $0.037 / $0.170   131k ctx, 20 endpoints\n# A 7-question smoke test at one prediction each is roughly 14 calls and well\n# under 100k tokens total — comfortably under two pence a run. The $10 balance\n# should therefore cover several hundred test runs, not several.\nTEST_MODELS = {\n    "default": "openrouter/openai/gpt-5-nano",\n    "summarizer": "openrouter/openai/gpt-oss-120b",\n    "parser": "openrouter/openai/gpt-oss-120b",\n    # Test with research ON, so we are testing what we will actually run.\n    "researcher": "openrouter/perplexity/sonar",\n}\n\n# Endpoint-status preflight. Logs the health of each configured free model\n# before forecasting starts, so a provider outage appears at the top of the run\n# log as a warning rather than as a wall of 404s two minutes in. Deliberately\n# WARN-ONLY: a status check is not worth turning into a new way for the run to\n# die, and status 0 has already proved not to guarantee availability.\nPREFLIGHT_FREE_MODELS = True\n\n# -----------------------------------------------------------------------------\n# RUN-TIME BUDGET\n#\n# Tournament questions are open for only 1.5 hours (temporarily 3), launch at\n# random hours, and arrive up to FIVE at a time. A run that overruns the window\n# scores zero on every question it did not reach — and a missed question is a\n# zero in a total that is then squared, so misses compound.\n#\n# The top open-source bot\'s author attributes ~150 forfeited peer points, most\n# of a placing tier, to missed questions. None of it was a forecasting problem.\n# -----------------------------------------------------------------------------\n\n# How many questions to work on at once. The template ships 1, which is right\n# for a rate-limited free tier and wrong for a 90-minute window with five\n# questions in it. Serial worst case in-season is roughly 5 questions x 5\n# predictions x ~30s = ~12 minutes; at 3 concurrent that is ~4-5 minutes.\n# Paid models have many endpoints and real capacity, so concurrency is safe\n# here in a way it never was on a shared free pool.\nMAX_CONCURRENT_QUESTIONS = 1 if MODEL_TIER == "free" else 3\n\n# Retries per LLM call. This is the "never retry a slow failure" rule.\n# At timeout=120s, the old value of 6 meant one stubborn call could burn TWELVE\n# MINUTES on its own — retrying a timeout multiplies the wait rather than\n# fixing anything. Free-tier 429s are transient and worth retrying; paid\n# failures usually are not, and OpenRouter already fails over between endpoints.\nLLM_ALLOWED_TRIES = 6 if MODEL_TIER == "free" else 2\nLLM_TIMEOUT_SECONDS = 120 if MODEL_TIER == "free" else 90\n\n# Season tier. claude-fable-5 is the default because it currently sits top of\n# Metaculus\'s own FutureEval model leaderboard (13.23, ahead of Claude Opus 4.8\n# on 13.06 and GPT-5.5 Instant on 12.81). Phase 2 will spread the ensemble\n# across families for decorrelation — see ENSEMBLE_MODELS below.\nSEASON_MODELS = {\n    "default": "openrouter/anthropic/claude-fable-5",\n    "summarizer": "openrouter/google/gemini-3.7-flash",\n    "parser": "openrouter/google/gemini-3.7-flash",\n    # LIVE WEB SEARCH. This was "no_research" until 31 Aug 2026, which would\n    # have entered a tournament of 300-500 near-term news questions with the\n    # bot forecasting from model weights alone, against a training cutoff.\n    # The prompt would still have said "Your research assistant says:" followed\n    # by nothing. Metaculus\'s own evidence: removing search degrades Brier by\n    # 3.6x. The likely result was a NEGATIVE total peer score — and since the\n    # prize is max(total, 0) squared, negative pays nothing at all.\n    #\n    # perplexity/sonar searches the live web and runs on the OpenRouter key we\n    # already hold — no extra registration. $1/$1 per 1M tokens plus $0.005 per\n    # search, so a 400-question season costs roughly $3 in research.\n    "researcher": "openrouter/perplexity/sonar",\n}\n\n# TRIAL TIER. Strong-but-cheap, for scored runs we are paying for ourselves.\n#\n# CORRECTED 5 Sept 2026. The earlier note here read "around 8 percent off the\n# pace", taken from a model leaderboard. The Summer 2026 final leaderboard,\n# read per question rather than by tournament total, puts the gap far wider:\n# claude-fable-5-high averaged 6.94 peer points a question against\n# gemini-3.5-flash on 5.03 — about a quarter below, not 8 percent. Ranking\n# those bots by tournament TOTAL is what produced the wrong figure: total is\n# average score times questions answered, and the reference bots joined on\n# different dates, so totals mostly measure coverage.\n# OpenRouter prices them, verified live against /api/v1/models the same day, at\n# $0.75/$3.75 and $10/$50 per million tokens: Fable is 13.3x dearer for that 8\n# percent.\n#\n# Measured cost on the season tier was about $0.10 a prediction, so this tier\n# should land nearer $0.0075 — a 60-question MiniBench round for a few pounds\n# rather than about $30. Frontier models are the right call on Metaculus\'s\n# credits and the wrong one on ours.\n#\n# Research stays on Sonar. Cutting search is the one economy that reliably\n# loses more than it saves.\n# Parser and summariser are a DIFFERENT model from the default, and that is the\n# whole point rather than a detail. OpenRouter\'s new-account throttle is per\n# model, so putting every role on one model makes them share one 20/minute\n# budget. The first trial run did exactly that — 63 rejections, including\n# "Could not summarize research" — because pacing the default model to 15/min\n# is worthless if the parser is spending the same allowance un-paced.\n# gpt-oss-120b is $0.037/$0.17 per million, 20x cheaper again, and has 20\n# serving endpoints. Both prices verified live against /api/v1/models.\nTRIAL_MODELS = {\n    "default": "openrouter/google/gemini-3.6-flash",\n    "summarizer": "openrouter/openai/gpt-oss-120b",\n    "parser": "openrouter/openai/gpt-oss-120b",\n    "researcher": "openrouter/perplexity/sonar",\n}\n\n# PACING THE DEFAULT MODEL.\n#\n# Measured, not guessed. The first ever season-tier run, 31 Aug 2026, returned:\n#   "Rate limit exceeded: new-account-rpm/anthropic/claude-5-fable-20260609.\n#    Rate limit reached: new accounts are limited to 20 requests per minute"\n# with X-RateLimit-Limit: 20 and limit_source: openrouter_new_account. Out of\n# that run: 87 calls rejected, 18 of 45 predictions landed, and 13 questions\n# forfeited outright by the SDK\'s "at least half the samples must succeed"\n# rule. Nothing in the bot noticed anything was wrong with its own design.\n#\n# The cause is burstiness, not volume. _max_concurrent_questions bounds\n# run_research ONLY; once questions clear research their predictions all fire\n# together, so nine questions at five predictions each puts dozens of calls at\n# one model in the same second. Throttling questions would not have fixed it.\n#\n# 15 a minute against a limit of 20 leaves headroom for the retries GeneralLlm\n# makes underneath this gate — those do not re-acquire, so they are invisible\n# to the limiter and must simply be left room for. Capacity equals the\n# per-minute figure, which is the library\'s intended "requests per minute"\n# shape: a minute\'s worth may burst, then the bucket refills over the next\n# minute before more is allowed.\n#\n# ONE BUCKET PER MODEL, not one per bot. The throttle is keyed on the model, so\n# there are two buckets below: one for the default model and one for the\n# parser. Parsing runs once per prediction, so it generates the SAME volume as\n# forecasting — gating only the default model, as the first version of this did,\n# leaves half the traffic un-paced. The two roles must also BE different models,\n# or the two buckets simply share one real budget and neither is honoured.\n#\n# Cost of the pacing: a five-question tournament pass is 25 default calls, so\n# under three minutes of the 90-minute window. Not the binding constraint.\n#\n# REVISED 1 Sept 2026 after audit, from 15 to 10. Two reasons, both measured\n# rather than assumed:\n#\n#   1. Retries live BELOW this gate and never re-acquire, so they are invisible\n#      to the bucket. Capping the parser at PARSER_ALLOWED_TRIES brings the\n#      worst-case multiplier down from 6 to 2, but 2 x 15 = 30 still breaches a\n#      limit of 20. At 10 a minute, even every single call retrying once stays\n#      inside 20. Headroom of 100% is the point: we are buying certainty with\n#      time, and time is the thing we have.\n#   2. Capacity is now 1, not PER_MODEL_RPM. An audit simulated the library\'s\n#      actual behaviour: with capacity=15 the bucket fires all fifteen requests\n#      in the same instant and then stalls for a full 60 seconds, because\n#      RefreshingBucketRateLimiter refills to FULL once emptied rather than one\n#      unit at a time. The 60-second average held while the instantaneous rate\n#      was ~15 per second — the very burst shape that triggered the throttle.\n#      Capacity 1 gives one request every six seconds and no burst at all.\n#\n# REVISED AGAIN 2 Sept 2026, and made tier-aware. Two reasons:\n#   1. 10 x LLM_ALLOWED_TRIES(2) = 20 against a limit of 20 is a boundary, not\n#      a margin, and retries are CORRELATED with being at the limit — the thing\n#      that triggers a retry is usually the 429 itself. 8 x 2 = 16 leaves room.\n#   2. The free tier keeps LLM_ALLOWED_TRIES = 6, because free-tier 429s are\n#      transient and retrying is the right response there. At 10/min that is a\n#      worst case of 60 against a limit of 20 — the unit test caught it, having\n#      been extended to check BOTH buckets rather than only the parser. The\n#      free tier was unsafe by our own stated standard and nobody had noticed,\n#      because the arithmetic was only ever checked at the default tier.\n#      3 x 6 = 18 holds.\nPER_MODEL_RPM = 3 if MODEL_TIER == "free" else 8\nPER_MODEL_BURST = 1\n\n# Retries inside the parser. The SDK default is 2, and structure_output wraps\n# it in its own loop, so this is one half of a multiplier we cannot see from\n# the rate limiter. One retry is worth having — a lost parse costs a whole\n# sample, and three lost samples forfeit the question.\nPARSER_ALLOWED_TRIES = 2\n\n# structure_output\'s own outer retry loop, which wraps the parser LLM\'s. The\n# SDK default is 3; combined with PARSER_ALLOWED_TRIES that is a 6x multiplier\n# on every acquisition. At 1 the worst case is 2, which the 10/min pace covers.\nSTRUCTURE_OUTPUT_ALLOWED_TRIES = 1\n\n# Phase 2 ensemble members, kept here so the intent is recorded even though\n# nothing reads this yet. Chosen across four families deliberately: the\n# published research says decorrelation is what makes an ensemble worth having.\nENSEMBLE_MODELS = [\n    "openrouter/anthropic/claude-fable-5",\n    "openrouter/openai/gpt-5.5",\n    "openrouter/google/gemini-3.1-pro-preview",\n    "openrouter/x-ai/grok-4.6",\n]\n\n\n# Tiers that constitute a real forecasting configuration: five predictions a\n# question and live research. "trial" qualifies on both counts — it is simply\n# cheaper, and it is what we can afford for scored MiniBench rounds until the\n# Metaculus credits arrive. "test" and "free" do not qualify, and never should.\nTOURNAMENT_READY_TIERS = ("season", "trial")\n\n\ndef assert_tier_matches_mode(run_mode: str) -> None:\n    """Refuse to forecast a scored tournament on a testing configuration.\n\n    MODEL_TIER lives in this file as a single string. Left on "test", the\n    season would run on a nano model at ONE prediction per question instead of\n    five, and would still exit green — the exact class of silent failure that\n    costs a season. A comment is not a safeguard; this is.\n    """\n    if run_mode != "tournament":\n        return\n    if MODEL_TIER not in TOURNAMENT_READY_TIERS:\n        raise SystemExit(\n            f"REFUSING TO RUN: mode=tournament but MODEL_TIER={MODEL_TIER!r}. "\n            f"The tournament is scored. Set MODEL_TIER to one of "\n            f"{TOURNAMENT_READY_TIERS}, or run --mode test_questions against "\n            "the bot testing area instead."\n        )\n    models = {"season": SEASON_MODELS, "trial": TRIAL_MODELS}[MODEL_TIER]\n    if models.get("researcher") in (None, "", "no_research", "None"):\n        raise SystemExit(\n            f"REFUSING TO RUN: the {MODEL_TIER} configuration has no researcher. "\n            "Forecasting news questions with no search produced a negative "\n            "expected score in Metaculus\'s own evidence. Set a researcher in "\n            f"{MODEL_TIER.upper()}_MODELS."\n        )\n    if MODEL_TIER == "trial":\n        logger.warning(\n            "Forecasting a SCORED tournament on the TRIAL tier: cheaper models, "\n            "roughly a quarter below the best model on the Metaculus reference "\n            "bots per question (5.03 v 6.94 average peer score, 2 Sept 2026). "\n            "Deliberate while we are paying for inference ourselves."\n        )\n\n\ndef preflight_check_free_models() -> None:\n    """Log the serving status of each configured free model. Never raises.\n\n    Free models have a single endpoint each, so when a provider goes down the\n    failure is total. This surfaces that at the top of the log instead of\n    leaving us to infer it from a wall of 404s.\n    """\n    # Only meaningful on the free tier: paid models have many endpoints and\n    # OpenRouter routes around dead ones, so a single status is not the story.\n    if not (MODEL_TIER == "free" and PREFLIGHT_FREE_MODELS):\n        return\n    # Imported locally: main.py does not import these at module level, and a\n    # NameError here would be swallowed by the except below — leaving a\n    # preflight that silently checks nothing, which is worse than none.\n    import json\n    import urllib.request\n\n    checked = set()\n    for role, model in FREE_MODELS.items():\n        if not model.startswith("openrouter/"):\n            continue\n        model_id = model[len("openrouter/") :]\n        if model_id in checked:\n            continue\n        checked.add(model_id)\n        try:\n            url = f"https://openrouter.ai/api/v1/models/{model_id}/endpoints"\n            with urllib.request.urlopen(url, timeout=15) as resp:\n                payload = json.loads(resp.read().decode())\n            endpoints = (payload.get("data") or {}).get("endpoints") or []\n            if not endpoints:\n                logger.warning("PREFLIGHT: %s has NO serving endpoints", model_id)\n                continue\n            for ep in endpoints:\n                status = ep.get("status", 0)\n                provider = ep.get("provider_name", "?")\n                if status == 0:\n                    logger.info("PREFLIGHT: %s ok via %s", model_id, provider)\n                else:\n                    logger.warning(\n                        "PREFLIGHT: %s reports status %s via %s — expect failures",\n                        model_id,\n                        status,\n                        provider,\n                    )\n        except Exception as exc:  # never let a health check break the run\n            logger.warning("PREFLIGHT: could not check %s (%s)", model_id, exc)\n\n\ndef predictions_per_report():\n    """Free tier gets 1; the season gets the full ensemble. See note above."""\n    return 1 if USE_FREE_MODELS else 5\n\n\ndef build_llm_config():\n    """Return the llms= mapping for the bot, per MODEL_TIER."""\n    tiers = {\n        "free": FREE_MODELS,\n        "test": TEST_MODELS,\n        "trial": TRIAL_MODELS,\n        "season": SEASON_MODELS,\n    }\n    if MODEL_TIER not in tiers:\n        raise ValueError(\n            f"MODEL_TIER must be one of {sorted(tiers)}, got {MODEL_TIER!r}"\n        )\n    chosen = tiers[MODEL_TIER]\n    logger.info(\n        "Model tier: %s | default=%s | predictions/question=%d",\n        MODEL_TIER.upper(),\n        chosen["default"],\n        predictions_per_report(),\n    )\n    return {\n        # allowed_tries is deliberately generous on the free tier: upstream\n        # 429s there are transient and shared-pool, so retrying is the correct\n        # response rather than failing the run.\n        "default": GeneralLlm(\n            model=chosen["default"],\n            temperature=0.3,\n            timeout=LLM_TIMEOUT_SECONDS,\n            allowed_tries=LLM_ALLOWED_TRIES,\n        ),\n        "summarizer": chosen["summarizer"],\n        # The parser is a GeneralLlm rather than a bare model string so we can\n        # set allowed_tries. Passed as a string, the SDK wraps it in a\n        # GeneralLlm with _DEFAULT_ALLOWED_TRIES = 2, and structure_output adds\n        # its own outer loop of allowed_tries=3 — so ONE trip through our rate\n        # limiter could become SIX requests on the wire. Retries happen below\n        # the gate and never re-acquire, so the limiter cannot see them. Audit,\n        # 1 Sept 2026: this is the better explanation for 87 rejections against\n        # 45 acquisitions than the burstiness we first blamed.\n        "parser": GeneralLlm(\n            model=chosen["parser"],\n            temperature=0.0,\n            timeout=LLM_TIMEOUT_SECONDS,\n            allowed_tries=PARSER_ALLOWED_TRIES,\n        ),\n        "researcher": chosen["researcher"],\n    }\n\n\n# =============================================================================\n# SEASON ROLLOVER\n#\n# The seasonal tournament ID is not ours to set. It arrives from the\n# forecasting-tools SDK as MetaculusClient.CURRENT_AI_COMPETITION_ID, and\n# poetry.lock pins that SDK at 0.2.92 while the workflow installs with\n# `poetry install`, which honours the lock. Read at the 0.2.92 version-bump\n# commit AND at upstream main on 31 Aug 2026, the constants are:\n#\n#     FE_SUMMER_2026_ID         = 33022   # summer-futureeval-2026\n#     CURRENT_AI_COMPETITION_ID = FE_SUMMER_2026_ID\n#     CURRENT_MINIBENCH_ID      = "minibench"   <- a slug, season-independent\n#\n# Metaculus has published no Fall 2026 ID, in the SDK or on the site. The\n# pinned value will therefore still say Summer when the Fall season opens.\n#\n# Why that is dangerous rather than merely wrong: the SDK fetches questions via\n# get_all_open_questions_from_tournament(), which filters on\n# allowed_tournaments=[id] with status "open" and returns whatever comes back.\n# A finished tournament returns ZERO questions — no exception, no warning — and\n# the run exits GREEN. Seasons run about four months. Left alone this bot would\n# forecast on nothing for an entire season while every scheduled run showed a\n# tick, which is the most expensive failure available to us.\n#\n# MiniBench is unaffected: "minibench" is a slug that survives the rollover.\n#\n# Rolling the season over needs no code change:\n#   GitHub -> Settings -> Secrets and variables -> Actions -> Variables -> New\n#   Name:  AIB_TOURNAMENT_ID\n#   Value: the Fall 2026 project ID (33121) or its slug\n# Until that is set, MiniBench is forecast as normal, the seasonal half is\n# skipped, and the run fails so the workflow turns red. MiniBench runs FIRST in\n# the dispatch precisely so an unset variable cannot forfeit it.\n# =============================================================================\n\n# The old guard was a fixed date plus a three-item denylist of known-stale IDs.\n# An audit on 1 Sept 2026 showed it tested the wrong thing: it asked what the\n# tournament ID *is*, not what it *does*. A typo in AIB_TOURNAMENT_ID sailed\n# straight past it — zero questions, green tick, every ten minutes for four\n# months. It also expired: once the Fall ID was set the guard was spent, and\n# the Winter 2027 rollover would have repeated the original failure with no\n# alarm at all.\n#\n# Replaced by a question COUNT check at the point of use. It needs no dates, no\n# ID list and no maintenance, and it is correct at every future rollover.\n# Explicit "there is no seasonal tournament right now" sentinel.\n#\n# Between seasons the seasonal half has nothing to point at, and BOTH of the\n# other options turn every run red: leaving AIB_TOURNAMENT_ID unset raises\n# REFUSING TO PASS, and setting it to a season that has not opened yet trips\n# SEASON_MISSING, because a tournament holding no questions is\n# indistinguishable from a retired one. At a run every ten minutes that is\n# about 144 failure emails a day, drowning the single alarm channel this\n# project has.\n#\n# So the gap is DECLARED, not inferred. No dates and no silent fallback:\n# somebody has to type it, and every run then says loudly that the seasonal\n# half is deliberately off. Added 5 Sept 2026, three days before the MiniBench\n# round it would otherwise have blocked.\nNO_SEASON_VALUES = ("none", "off", "skip", "-")\n\n# THE SENTINEL EXPIRES, and that is the whole point of this line.\n#\n# Audit, 5 Sept 2026, found the flaw in the first version: every detector on\n# the seasonal side (the question COUNT check, the slug check, SEASON_MISSING)\n# sits behind the sentinel, so declaring the gap removes them all. Left set to\n# "none" through the season opening, the bot would forecast MiniBench only,\n# GREEN, every ten minutes for four months, and nothing in the code could\n# notice. That is the ~150-peer-point failure this whole block exists to\n# prevent, faithfully reproduced by the guard written to prevent it.\n#\n# A date was rejected once before, for good reason: the old guard expired into\n# SILENCE, so once it was spent the next rollover had no alarm at all. This\n# one expires into NOISE, which is the safe direction. Firing late costs a\n# little wasted attention; not firing costs a season. The red it produces is\n# actionable and self-clearing — it stops the moment the variable is set — and\n# it is raised at the END of the run, so MiniBench is still forecast first.\n# 30 September, not the 28th, and the two days are deliberate. Audit, 5 Sept\n# 2026: on the 28th BOTH settings would have been red if Metaculus creates the\n# Fall project before populating it — "none" because the sentinel had expired,\n# and 33121 because an empty seasonal tournament is fatal. We have direct\n# evidence they do exactly that: "minibench" held zero questions on 5 Sept for\n# a round starting on the 7th. A guaranteed red morning is not a safety\n# feature — it is 144 emails, and the obvious way to make them stop is to\n# disarm the seasonal guard, permanently. Two days of grace buys a\n# configuration that is green while we wait for the first Fall question.\nNO_SEASON_EXPIRY = datetime(2026, 9, 30, tzinfo=timezone.utc)\n\n# The remedy differs by half, so it is not baked into the message. Telling an\n# operator to set AIB_TOURNAMENT_ID when it is MINIBENCH that broke sends\n# them to a variable with nothing to do with the fault. Audit, 5 Sept 2026.\nSEASON_MISSING_FIXES = {\n    "Seasonal": (\n        "Fix: set the repository variable AIB_TOURNAMENT_ID (Settings > "\n        "Secrets and variables > Actions > Variables) to the current "\n        "seasonal tournament ID or slug."\n    ),\n    "MiniBench": (\n        "Fix: MiniBench is keyed by the slug \'minibench\', which Metaculus "\n        "says is always the currently active round. If it now holds "\n        "nothing, the slug has changed — check the MiniBench tournament "\n        "page and the Metaculus Discord before changing anything here."\n    ),\n}\n\nSEASON_MISSING_MESSAGE = (\n    "{label} TOURNAMENT NOT FOUND: {tournament!r} contains no questions at "\n    "all. That is a wrong or retired tournament ID, not a quiet hour — a live "\n    "tournament always has questions even when none are currently open. That "\n    "half of this run forecast NOTHING. {fix}"\n)\n\n\n# Slug fragments that identify a Metaculus BOT tournament. Every question the\n# API returns carries the slugs of the tournaments it belongs to\n# (MetaculusQuestion.tournament_slugs, filled from projects.tournament[].slug),\n# so checking this costs no extra request.\n#\n# Why it exists: the question-count check alone cannot tell a typo from a\n# correct ID. Metaculus project IDs are dense — 32916, 33021, 33022 — so a\n# transposed digit usually lands on ANOTHER REAL PROJECT, which has questions,\n# passes the count check, and would have us forecasting into a tournament we\n# are not entered in. Green, every ten minutes, for four months. Found by\n# audit on 2 Sept 2026, in the guard written to prevent exactly that.\n#\n# Fragments rather than names because Metaculus has renamed the series over\n# time: aibq3, aibq4, fall-aib-2025, spring-aib-2026, summer-futureeval-2026,\n# minibench. Every one contains one of these.\nBOT_TOURNAMENT_SLUG_MARKERS = ("aib", "futureeval", "minibench")\n\n\ndef tournament_slug_problem(tournament_id, questions, label: str) -> str | None:\n    """None if these questions belong to a bot tournament, else why not.\n\n    Fails SAFE on missing metadata: if no question carries a slug we warn and\n    allow the run, because refusing on absent data would turn an API change\n    into a four-month outage of our own making.\n    """\n    slugs = {\n        s.lower()\n        for q in questions\n        for s in (getattr(q, "tournament_slugs", None) or [])\n    }\n    if not slugs:\n        logger.warning(\n            "%s tournament %r returned questions carrying no tournament slugs, "\n            "so it could not be verified as a bot tournament. Allowing the run.",\n            label,\n            tournament_id,\n        )\n        return None\n    if any(marker in s for s in slugs for marker in BOT_TOURNAMENT_SLUG_MARKERS):\n        logger.info(\n            "%s tournament %r verified as a bot tournament: %s",\n            label,\n            tournament_id,\n            sorted(slugs),\n        )\n        return None\n    return (\n        f"WRONG TOURNAMENT: {label} target {tournament_id!r} resolves to "\n        f"{sorted(slugs)}, none of which looks like a Metaculus bot tournament "\n        f"(expected a slug containing one of {BOT_TOURNAMENT_SLUG_MARKERS}). "\n        "That is almost certainly a mistyped AIB_TOURNAMENT_ID landing on a "\n        "real but unrelated project. Forecasting was skipped."\n    )\n\n\n# GROUP QUESTIONS: ON. Skipping was PROVEN to work on them, 2 Sept 2026.\n#\n# The worry was that skip_previously_forecasted_questions — the only thing\n# stopping a 10-minute cron re-forecasting the same question all season — reads\n# question.already_forecasted, which the SDK fills from\n# question_json["my_forecasts"]["history"]. For an unpacked GROUP subquestion\n# that json is deep-copied from the group payload, so the field is only there\n# if Metaculus puts my_forecasts on each subquestion. The SDK explicitly\n# patches this for CONDITIONAL questions and does nothing for groups, which\n# read like the case had never been considered. If it failed open we would\n# re-forecast group subquestions 144 times a day — wasted spend, and a breach\n# of the one-forecast-per-question rule for bot-only tournaments.\n#\n# Settled by running check_group_questions.py against the bot-testing-area,\n# where earlier Test Bot runs had already forecast the group questions:\n#\n#     9 open question(s): 4 in groups, 5 standalone.\n#     43329  in group  already forecast: True   (post 43325)\n#     43330  in group  already forecast: True   (post 43325)\n#     43323  in group  already forecast: True   (post 43322)\n#     43324  in group  already forecast: True   (post 43322)\n#\n# Four for four. Metaculus does populate my_forecasts per subquestion, so\n# skipping holds and group questions are back in play. The switch stays so the\n# decision is reversible if that ever stops being true — re-run the check\n# rather than assuming.\nSKIP_GROUP_QUESTIONS = False\n\n\ndef drop_group_questions(questions, label: str):\n    """Remove unpacked group subquestions. See SKIP_GROUP_QUESTIONS."""\n    if not SKIP_GROUP_QUESTIONS:\n        return questions\n    kept = [q for q in questions if getattr(q, "question_ids_of_group", None) is None]\n    dropped = len(questions) - len(kept)\n    if dropped:\n        logger.warning(\n            "%s: skipping %d group subquestion(s). Deliberate — see "\n            "SKIP_GROUP_QUESTIONS. We cannot yet prove the SDK reports them as "\n            "already forecast, and re-forecasting one would breach the "\n            "one-forecast-per-question rule.",\n            label,\n            dropped,\n        )\n    return kept\n\n\ndef fetch_and_verify_tournament(\n    client, tournament_id, label: str, *, empty_is_fatal: bool = True\n):\n    """Return (open_questions, problem_message_or_None).\n\n    empty_is_fatal says what "this tournament holds no questions at all"\n    MEANS, and it is not the same thing for both halves.\n\n    For the SEASON it means broken. A season runs continuously for about four\n    months, so an empty seasonal tournament is a wrong or retired ID and the\n    run must go red.\n\n    For MINIBENCH it is normal. MiniBench is a chain of back-to-back two-week\n    rounds, and "minibench" is a slug that repoints to whichever round is\n    active. Between the slug repointing and the first question of the new\n    round being created, it legitimately holds nothing.\n\n    Found the hard way on 5 Sept 2026: the first live run of the tournament\n    workflow failed with "MiniBench TOURNAMENT NOT FOUND", two days before a\n    round we were entering. Treating that as fatal would have reddened every\n    run for two days, and then again in the gap after every future round —\n    permanently, every fortnight. Red that always fires is the same as no red\n    at all, and this project has exactly one alarm channel.\n\n    The cost of the softer treatment is honest: if the MiniBench slug ever\n    really did change, we would see a warning rather than a failure. That is\n    accepted deliberately, because the alternative guarantees alarm fatigue in\n    exchange for detecting something Metaculus documents as fixed ("the\n    project ID for the currently active minibench is always minibench").\n\n    Fetches explicitly rather than letting forecast_on_tournament do it,\n    because that discards the question COUNT, and the count is what separates a\n    working tournament from a dead one.\n    """\n    questions = client.get_all_open_questions_from_tournament(tournament_id)\n    logger.info(\n        "%s tournament %r: %d open questions", label, tournament_id, len(questions)\n    )\n    questions = drop_group_questions(questions, label)\n    sample = questions\n    if not questions:\n        # Zero OPEN questions is normal. Questions accept forecasts for about\n        # 90 minutes and this runs every 10, so most runs legitimately find\n        # nothing. Zero questions AT ALL is not normal. Only pay for the second\n        # query on the runs that would otherwise have said nothing at all.\n        sample = asyncio.run(\n            client.get_questions_matching_filter(\n                # unpack_subquestions to match what the fetch above uses.\n                # ApiFilter defaults to "exclude", which drops group posts both\n                # server-side and locally — so the probe looked at a different\n                # population from the forecast set, and a tournament whose\n                # newest posts were all groups would have produced a false\n                # "NOT FOUND". Audit, 2 Sept 2026.\n                ApiFilter(\n                    allowed_tournaments=[tournament_id],\n                    group_question_mode="unpack_subquestions",\n                )\n            )\n        )\n        if not sample:\n            message = SEASON_MISSING_MESSAGE.format(\n                tournament=tournament_id,\n                label=label,\n                fix=SEASON_MISSING_FIXES.get(\n                    label, SEASON_MISSING_FIXES["Seasonal"]\n                ),\n            )\n            if empty_is_fatal:\n                return [], message\n            # A SEPARATE message, not the fatal one. Audit, 5 Sept 2026: the\n            # first version reused SEASON_MISSING_MESSAGE, so the log read\n            # "NOT FAILING THE RUN: TOURNAMENT NOT FOUND ... that is a wrong\n            # or retired ID" — telling the reader it is definitely broken in\n            # the same breath as declining to act. Useless at 7am.\n            soft = (\n                f"{label} holds no questions yet ({tournament_id!r}). Normal "\n                "in the gap between MiniBench rounds, so the run is NOT being "\n                "failed. If this persists past the advertised start date of "\n                "the round, the slug has changed — check the MiniBench "\n                "tournament page and the Metaculus Discord."\n            )\n            logger.warning(soft)\n            # A python warning produces no GitHub annotation, so on a green run\n            # it is invisible unless somebody opens the log and scrolls. This\n            # is now the ONLY detector for a dead MiniBench slug, so it gets a\n            # yellow flag on the run page for the price of one print.\n            print(f"::warning title={label} holds no questions::{soft}")\n            return [], None\n        logger.info(\n            "%r holds %d questions, none open right now. Normal between windows.",\n            tournament_id,\n            len(sample),\n        )\n    return questions, tournament_slug_problem(tournament_id, sample, label)\n\n\ndef resolve_seasonal_tournament():\n    """Return (tournament_id_or_None, problem_or_None). Three outcomes, not two.\n\n    (id, None)     a real tournament to forecast.\n    (None, None)   the NO_SEASON_VALUES sentinel: no season right now, by\n                   explicit declaration. Expires at NO_SEASON_EXPIRY.\n    (None, problem) unset, blank, or an expired sentinel. The run goes red.\n\n    AIB_TOURNAMENT_ID is REQUIRED: there is no inferred default.\n\n    Returns a problem rather than raising, so the caller can still forecast\n    MiniBench before failing the run. The first version raised here, which meant\n    an unset variable forfeited MiniBench too — a scored series, keyed by a slug\n    that survives the rollover, that was working perfectly. Audit, 2 Sept 2026.\n\n    This used to fall back to the SDK\'s CURRENT_AI_COMPETITION_ID. That fallback\n    was removed on 2 Sept 2026 because it is a silent trap: poetry.lock pins\n    forecasting-tools 0.2.92, where the constant is frozen at Summer 2026, so\n    the fallback quietly aims a whole Fall season at a finished tournament.\n\n    The question-count check downstream catches a WRONG id — a typo has no\n    questions at all — but it cannot catch a RETIRED one. Summer still holds\n    328 questions; they are simply all closed, so the probe would report the\n    tournament as healthy. The fallback had to go rather than be guarded.\n\n    Requiring the variable closes both failures with no dates, no ID lists and\n    no maintenance, and stays correct at every future rollover. The cost is one\n    repository variable that has to be set before a season starts, which was\n    always true anyway.\n    """\n    override = os.environ.get("AIB_TOURNAMENT_ID", "").strip()\n    if not override:\n        return None, (\n            "AIB_TOURNAMENT_ID is not set. Tournament mode has "\n            "to be told which seasonal tournament to forecast — the SDK\'s "\n            "built-in constant is pinned to Summer 2026 and would forecast a "\n            "finished tournament without complaining. Set the repository "\n            "variable (Settings > Secrets and variables > Actions > Variables) "\n            "to the current seasonal tournament ID or slug."\n        )\n    if override.lower() in NO_SEASON_VALUES:\n        now = datetime.now(timezone.utc)\n        if now >= NO_SEASON_EXPIRY:\n            return None, (\n                f"AIB_TOURNAMENT_ID is still {override!r} on "\n                f"{now:%Y-%m-%d}, past the declared no-season window that "\n                f"ended {NO_SEASON_EXPIRY:%Y-%m-%d}. The seasonal tournament "\n                "has opened and this bot is forecasting MiniBench ONLY. Set "\n                "AIB_TOURNAMENT_ID to the Fall 2026 tournament ID (33121, "\n                "fall-futureeval-2026), or move NO_SEASON_EXPIRY deliberately."\n            )\n        logger.warning(\n            "AIB_TOURNAMENT_ID=%r: NO SEASONAL TOURNAMENT this run, by "\n            "explicit configuration. MiniBench only. Set the real tournament "\n            "ID when the season opens. Fall 2026 is 33121 "\n            "(fall-futureeval-2026), opening 28 Sept 2026.",\n            override,\n        )\n        return None, None\n    resolved = int(override) if override.isdigit() else override\n    logger.info("Seasonal tournament %r (from AIB_TOURNAMENT_ID)", resolved)\n    return resolved, None',
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
    preflight_check_free_models()""",
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

        logger.info(
            f"Forecasted URL {question.page_url} with prediction: {predicted_option_list}."
        )
        return ReasonedPrediction(
            prediction_value=predicted_option_list, reasoning=reasoning
        )""",
    "multiple-choice option floor",
)

# ---------------------------------------------------------------------------
# 14. EMPTY-RESEARCH ALARM  (audit, 1 Sept 2026: nothing anywhere checked that
#     research returned anything. An empty string forecasts from model weights
#     and exits green. Logged rather than raised — see the note in the block.)
# ---------------------------------------------------------------------------
replace(
    '            logger.info(f"Found Research for URL {question.page_url}:\\n{research}")',
    '            # Nothing anywhere checked that research actually returned\n            # anything. If Sonar returns an empty string or a refusal rather\n            # than raising, the prompt reads "Your research assistant says:"\n            # followed by nothing, the bot forecasts from model weights against\n            # a training cutoff, publishes, and exits green. Metaculus\'s own\n            # evidence puts removing search at 3.6x Brier.\n            #\n            # Logged, not raised — deliberately, for now. Raising would discard\n            # the sample, and three discarded samples forfeit the question, so\n            # the fix could cost more than the fault. Whether an unresearched\n            # forecast is worse than no forecast is a judgement about the\n            # scoring rule, and it is recorded as an open decision rather than\n            # settled quietly here.\n            if self.get_llm("researcher") not in (None, "", "None", "no_research"):\n                if len(research.strip()) < 200:\n                    global EMPTY_RESEARCH_COUNT\n                    EMPTY_RESEARCH_COUNT += 1\n                    logger.error(\n                        "RESEARCH LOOKS EMPTY for %s (%d chars). This forecast is "\n                        "coming from model weights, not from search.",\n                        question.page_url,\n                        len(research.strip()),\n                    )\n            logger.info(f"Found Research for URL {question.page_url}:\\n{research}")',
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
    '        prediction = NumericDistribution.from_question(percentile_list, question)\n        # Same reasoning as the numeric path: fail a bad sample as a sample.\n        prediction.get_cdf()\n        logger.info(\n            f"Forecasted URL {question.page_url} with prediction: {prediction.declared_percentiles}."\n        )\n        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)\n\n    def _create_upper_and_lower_bound_messages(',
    "force CDF expansion per sample on the date path",
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
    '    # Per-mode tournament URL shown in the summary banner footer. The seasonal\n    # entry is built from the ID actually forecast rather than hard-coded, so\n    # the link cannot drift away from what the bot really did. Metaculus\n    # redirects /tournament/<numeric id>/ to the slug (checked 31 Aug 2026).\n    TOURNAMENT_URLS = {\n        "tournament": (\n            f"https://www.metaculus.com/tournament/{seasonal_id}/"\n            if seasonal_id is not None\n            else "https://www.metaculus.com/tournament/minibench/"\n        ),\n        "metaculus_cup": "https://www.metaculus.com/tournament/metaculus-cup-summer-2025/",\n        "test_questions": "https://www.metaculus.com/tournament/bot-testing-area/",\n    }\n\n    # raise_errors=False, deliberately. The SDK\'s default is True and it raises\n    # RuntimeError if ANY question errored — which skipped everything below it,\n    # including the banner and the season-rollover message. Two independent\n    # auditors found this on 1 Sept 2026: the season-rollover message written that\n    # morning was unreachable on any run with a single failed question, which\n    # on recent evidence is most runs. We decide the exit code ourselves below.\n    template_bot.log_report_summary(forecast_reports, raise_errors=False)\n    print_run_summary_banner(\n        forecast_reports,\n        will_publish=publish_to_metaculus,\n        tournament_url=TOURNAMENT_URLS.get(run_mode),\n    )\n\n    # Exit code, decided here rather than inherited from log_report_summary.\n    #\n    # The bar for red is deliberately NOT "any question failed". At a run every\n    # ten minutes, one flaky question turning the whole run red trains whoever\n    # is watching to ignore red — and red is exactly what the season-rollover\n    # guard depends on being noticed. So partial failure warns loudly, and only\n    # a run that achieved nothing, or a stale season, fails the workflow.\n    successes = [r for r in forecast_reports if not isinstance(r, BaseException)]\n    failures = [r for r in forecast_reports if isinstance(r, BaseException)]\n    if failures:\n        logger.warning(\n            "%d of %d questions failed; %d forecast successfully.",\n            len(failures),\n            len(forecast_reports),\n            len(successes),\n        )\n\n    # Empty research fails the run on a RATE, not on a single instance.\n    #\n    # Logging alone was not enough: over four months nobody reads an INFO line\n    # on a green run, and forecasting from model weights against a training\n    # cutoff is worth 3.6x Brier by Metaculus\'s own evidence. But failing on ONE\n    # short research string was worse — Sonar answering "I could not find\n    # relevant information" on a niche question is forty characters, and at 144\n    # runs a day that manufactures exactly the red-fatigue this file spends a\n    # paragraph warning about. A minority of thin research is life; a majority\n    # means the researcher is broken. Audit, 2 Sept 2026.\n    attempted = len(forecast_reports)\n    if EMPTY_RESEARCH_COUNT and attempted:\n        rate = EMPTY_RESEARCH_COUNT / attempted\n        if EMPTY_RESEARCH_COUNT >= EMPTY_RESEARCH_MIN_TO_FAIL and rate > EMPTY_RESEARCH_FAIL_RATE:\n            problems.append(\n                f"{EMPTY_RESEARCH_COUNT} of {attempted} questions were forecast "\n                "with little or no research. Check the researcher model and the "\n                "OpenRouter balance."\n            )\n        else:\n            logger.warning(\n                "%d of %d questions had thin research. Below the failure "\n                "threshold, so not failing the run.",\n                EMPTY_RESEARCH_COUNT,\n                attempted,\n            )\n\n    if problems:\n        raise SystemExit("REFUSING TO PASS: " + " | ".join(problems))\n    if forecast_reports and not successes:\n        raise SystemExit(\n            f"REFUSING TO PASS: all {len(failures)} questions failed and nothing "\n            "was submitted. A green tick here would be a lie."\n        )',
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

DST.write_text(text)
print(f"\n{edits} edits applied cleanly -> {DST}")
