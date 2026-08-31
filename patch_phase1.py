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

    Fails SAFE: a missing, malformed or contradictory flag yields the normal
    caps, so a parsing problem can never silently make the bot MORE confident.
    """
    text = reasoning or ""
    high = re.search(r"AMBIGUITY\\s*:\\s*HIGH", text, re.IGNORECASE) is not None
    low = re.search(r"AMBIGUITY\\s*:\\s*LOW", text, re.IGNORECASE) is not None

    if high and not low:
        logger.info(
            "Resolution criteria flagged AMBIGUOUS — capping to %.2f-%.2f",
            AMBIGUOUS_FLOOR,
            AMBIGUOUS_CEILING,
        )
        return AMBIGUOUS_FLOOR, AMBIGUOUS_CEILING
    if high and low:
        # Both present: the model contradicted itself, so we cannot trust the
        # flag. Treat as not-ambiguous rather than guessing, but say so.
        logger.warning(
            "Both AMBIGUITY: HIGH and LOW present in reasoning — using normal caps"
        )
    return BINARY_FLOOR, BINARY_CEILING


def floor_and_renormalise(option_probabilities):
    """Apply a floor to every multiple-choice option, then renormalise to 1.

    Takes and returns a list of (name, probability) pairs.

    A 0% option that resolves scores about -691 — one of those erases thirty
    good questions. Flooring costs almost nothing: lifting an option from 0%
    to 1% moves the others by roughly a percent in total.

    Renormalising afterwards is not optional. Metaculus validates that the
    options sum to 1, and flooring necessarily pushes the sum above it.
    """
    if not option_probabilities:
        return []
    count = len(option_probabilities)
    uniform = [(name, 1.0 / count) for name, _ in option_probabilities]

    # Too many options for the floor to fit inside a unit total.
    if MC_OPTION_FLOOR * count >= 1.0:
        return uniform

    raw = [max(0.0, float(p)) for _, p in option_probabilities]
    total = sum(raw)
    if total <= 0:
        logger.warning("Multiple-choice probabilities summed to %s — using uniform", total)
        return uniform
    normalised = [p / total for p in raw]

    # Naive flooring then renormalising does NOT work: scaling the whole vector
    # back to 1 pushes the floored options straight back under the floor.
    # Instead, pin the low options AT the floor and share what is left among
    # the rest, in proportion to their existing weights.
    low = [i for i, p in enumerate(normalised) if p < MC_OPTION_FLOOR]
    if not low:
        return [(name, p) for (name, _), p in zip(option_probabilities, normalised)]

    remaining = 1.0 - MC_OPTION_FLOOR * len(low)
    high_total = sum(p for i, p in enumerate(normalised) if i not in set(low))
    if high_total <= 0:
        return uniform

    out = []
    low_set = set(low)
    for i, ((name, _), p) in enumerate(zip(option_probabilities, normalised)):
        out.append((name, MC_OPTION_FLOOR if i in low_set else p / high_total * remaining))
    return out


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
# 4. CONFIGURATION BLOCK  (Strong evidence: use a current frontier reasoning
#    model. Everything tunable is pulled to one place at the top of the file so
#    later phases change constants, not scattered logic.)
# ---------------------------------------------------------------------------
replace(
    "dotenv.load_dotenv()\nlogger = logging.getLogger(__name__)",
    '''dotenv.load_dotenv()
logger = logging.getLogger(__name__)

# Used by caps_for_reasoning(). The upstream template does not import re, and
# a missing import here would raise on EVERY binary question — caught by the
# build check added 31 Aug 2026, which is why that check exists.
import re  # noqa: E402

# Used by resolve_seasonal_tournament() to read the AIB_TOURNAMENT_ID
# repository variable. Not imported upstream either; same failure mode.
import os  # noqa: E402

# =============================================================================
# PHASE 1 CONFIGURATION  —  all tunables live here, nowhere else.
#
# Evidence grades below refer to Metaculus, "AI Forecasting in 2026: What 11
# Analyses Say" (8 Jul 2026), which synthesises 11 analyses plus the Fall 2025
# survey of 39 bot makers (29 prize winners, 10 non-winners).
# =============================================================================

# Binary prediction caps. MODERATE evidence, and the strongest single
# differentiator measured among winners (r = +0.48, p = 0.005). 38% of Fall
# 2025 winners cap; 47% of the top fifteen do, against 29% of the bottom half.
BINARY_FLOOR = 0.02
BINARY_CEILING = 0.98

# AMBIGUITY-BOUNDED CONFIDENCE.
#
# Peer score = 100 x (ln(p) - ln(geometric mean of other bots)). It is brutally
# asymmetric: moving 99% -> 99.9% gains 0.009 when right and costs 2.3 when
# wrong. The expensive error is confident-and-wrong.
#
# A systematic source of confident-and-wrong is not misjudging the world but
# answering a different question from the one asked. Observed live in the
# Metaculus bot Discord, 29 Aug 2026: "a lot of bots including mine
# misinterpreted this question... interpreting as 'July is the annual max'
# instead of 'July is a NEW annual max'." A whole cohort, one word.
#
# Other entrants enumerate interpretations to INFORM the forecast. We
# additionally let interpretation ambiguity BOUND it: where competing readings
# of the criteria would resolve differently, the bot is not entitled to
# confidence however sure it is about the world. Uncertainty about the world
# belongs in the probability; uncertainty about the QUESTION is handled here.
AMBIGUOUS_FLOOR = 0.10
AMBIGUOUS_CEILING = 0.90

# MULTIPLE-CHOICE FLOOR.
#
# The upstream parser is instructed: "you may sometimes need to parse a 0%
# probability. Please do not skip options with 0%." If a 0% option then
# RESOLVES, the peer score for that question is the floor — about -691, against
# typical per-question scores of ±10-30. One such event erases roughly thirty
# good questions, and because the prize is max(total, 0) squared it can take
# the whole season to nothing.
#
# Binary forecasts were floored at 0.02 on day one. Multiple choice — the type
# where Metaculus's own analysis says bots lose most ground to humans — had no
# floor at all until an audit found it on 31 Aug 2026.
MC_OPTION_FLOOR = 0.01

# Number of independent forecasts aggregated per question. STRONG evidence for
# ensembling (86% of winners aggregate). Phase 2 will widen this across model
# families; for now it is repeated sampling of one model.
#
# On the free tier this is forced to 1. Free models are served from a shared
# upstream pool and rate-limit hard (a 429 killed our second test run); five
# predictions per question means five forecast calls plus five parse calls,
# which trips the limit within a couple of questions. One prediction is enough
# to prove the plumbing, which is all the free tier is for.
RESEARCH_REPORTS_PER_QUESTION = 1

# -----------------------------------------------------------------------------
# MODELS
#
# The template ships with no llms= block, so forecasting-tools picks defaults.
# One of those defaults is openai/gpt-4o-search-preview, which OpenRouter does
# not serve — it 404s on every question. So we name every model explicitly.
#
# All IDs below were verified against https://openrouter.ai/api/v1/models on
# 29 Aug 2026. OpenRouter's free tier rotates with little notice, so if the bot
# starts returning "No endpoints found", re-check that endpoint first.
# -----------------------------------------------------------------------------

# THREE TIERS.
#
#   "free"   — :free models, zero balance. KEPT FOR REFERENCE, NOT RECOMMENDED.
#              Four runs died on it across three providers. Every :free model
#              has exactly ONE serving endpoint on one provider's shared pool,
#              so there is no failover and the pool rate-limits under any load.
#   "test"   — cheap paid models. What we develop against.
#   "season" — frontier models, on Metaculus's credits, for the tournament.
#
# The difference is structural, not a matter of picking a better free model
# (checked 1 Sept 2026 via the /endpoints API):
#   z-ai/glm-5.2:free .......  1 endpoint   (Decart)          -> 429'd us
#   nvidia/nemotron:free ....  1 endpoint   (Nvidia)          -> 404'd us
#   google/gemma-4-31b:free .  1 endpoint   (Google AI Studio)-> 429'd us
#   openai/gpt-5-nano ....... 4 endpoints   (OpenAI, Azure)
#   openai/gpt-oss-120b ..... 20 endpoints  (AkashML, CoreWeave, DeepInfra,
#                                            Novita, SiliconFlow, Google, ...)
# OpenRouter routes around dead endpoints automatically, so a paid model
# tolerates a provider outage that kills a free one outright.
MODEL_TIER = "test"

# Back-compat: several helpers below still ask "are we on the cheap tier?"
USE_FREE_MODELS = MODEL_TIER in ("free", "test")

# Free tier. Not frontier, not competitive — these exist to prove the bot can
# read a question, form a forecast and post it. "no_research" skips the search
# step entirely, which removes a dependency we don't need while smoke-testing.
# Parser and summarizer deliberately sit on a DIFFERENT upstream provider from
# the default model. One provider must not be a single point of failure.
#
# EVERY free model on OpenRouter has exactly ONE serving endpoint — a single
# provider, with no failover. That is why free-tier outages are total rather
# than degraded. Checked 1 Sept 2026 via
#   https://openrouter.ai/api/v1/models/<id>:free/endpoints
# which exposes a per-endpoint `status` (0 = normal, negative = degraded).
#
# Providers that have already failed us, and are avoided here:
#   Nvidia           — nemotron-3-ultra 404'd mid-run on 30 Aug ("Provider
#                      returned error"), despite still being listed and still
#                      reporting status 0. Listing is not availability.
#   Google AI Studio — gemma-4-31b 429'd on 29 Aug from its shared free pool.
#
# So: default on Decart, parser/summarizer on GMICloud. Neither has failed us,
# and they are independent of each other.
FREE_MODELS = {
    "default": "openrouter/z-ai/glm-5.2:free",
    "summarizer": "openrouter/minimax/minimax-m3:free",
    "parser": "openrouter/minimax/minimax-m3:free",
    "researcher": "no_research",
}

# TEST tier — cheap paid models, chosen for endpoint COUNT as much as price.
# Prices verified against OpenRouter's live model list, 1 Sept 2026, per 1M
# tokens (input / output):
#   openai/gpt-5-nano    $0.050 / $0.400   400k ctx,  4 endpoints
#   openai/gpt-oss-120b  $0.037 / $0.170   131k ctx, 20 endpoints
# A 7-question smoke test at one prediction each is roughly 14 calls and well
# under 100k tokens total — comfortably under two pence a run. The $10 balance
# should therefore cover several hundred test runs, not several.
TEST_MODELS = {
    "default": "openrouter/openai/gpt-5-nano",
    "summarizer": "openrouter/openai/gpt-oss-120b",
    "parser": "openrouter/openai/gpt-oss-120b",
    # Test with research ON, so we are testing what we will actually run.
    "researcher": "openrouter/perplexity/sonar",
}

# Endpoint-status preflight. Logs the health of each configured free model
# before forecasting starts, so a provider outage appears at the top of the run
# log as a warning rather than as a wall of 404s two minutes in. Deliberately
# WARN-ONLY: a status check is not worth turning into a new way for the run to
# die, and status 0 has already proved not to guarantee availability.
PREFLIGHT_FREE_MODELS = True

# -----------------------------------------------------------------------------
# RUN-TIME BUDGET
#
# Tournament questions are open for only 1.5 hours (temporarily 3), launch at
# random hours, and arrive up to FIVE at a time. A run that overruns the window
# scores zero on every question it did not reach — and a missed question is a
# zero in a total that is then squared, so misses compound.
#
# The top open-source bot's author attributes ~150 forfeited peer points, most
# of a placing tier, to missed questions. None of it was a forecasting problem.
# -----------------------------------------------------------------------------

# How many questions to work on at once. The template ships 1, which is right
# for a rate-limited free tier and wrong for a 90-minute window with five
# questions in it. Serial worst case in-season is roughly 5 questions x 5
# predictions x ~30s = ~12 minutes; at 3 concurrent that is ~4-5 minutes.
# Paid models have many endpoints and real capacity, so concurrency is safe
# here in a way it never was on a shared free pool.
MAX_CONCURRENT_QUESTIONS = 1 if MODEL_TIER == "free" else 3

# Retries per LLM call. This is the "never retry a slow failure" rule.
# At timeout=120s, the old value of 6 meant one stubborn call could burn TWELVE
# MINUTES on its own — retrying a timeout multiplies the wait rather than
# fixing anything. Free-tier 429s are transient and worth retrying; paid
# failures usually are not, and OpenRouter already fails over between endpoints.
LLM_ALLOWED_TRIES = 6 if MODEL_TIER == "free" else 3
LLM_TIMEOUT_SECONDS = 120 if MODEL_TIER == "free" else 90

# Season tier. claude-fable-5 is the default because it currently sits top of
# Metaculus's own FutureEval model leaderboard (13.23, ahead of Claude Opus 4.8
# on 13.06 and GPT-5.5 Instant on 12.81). Phase 2 will spread the ensemble
# across families for decorrelation — see ENSEMBLE_MODELS below.
SEASON_MODELS = {
    "default": "openrouter/anthropic/claude-fable-5",
    "summarizer": "openrouter/google/gemini-3.7-flash",
    "parser": "openrouter/google/gemini-3.7-flash",
    # LIVE WEB SEARCH. This was "no_research" until 31 Aug 2026, which would
    # have entered a tournament of 300-500 near-term news questions with the
    # bot forecasting from model weights alone, against a training cutoff.
    # The prompt would still have said "Your research assistant says:" followed
    # by nothing. Metaculus's own evidence: removing search degrades Brier by
    # 3.6x. The likely result was a NEGATIVE total peer score — and since the
    # prize is max(total, 0) squared, negative pays nothing at all.
    #
    # perplexity/sonar searches the live web and runs on the OpenRouter key we
    # already hold — no extra registration. $1/$1 per 1M tokens plus $0.005 per
    # search, so a 400-question season costs roughly $3 in research.
    "researcher": "openrouter/perplexity/sonar",
}

# Phase 2 ensemble members, kept here so the intent is recorded even though
# nothing reads this yet. Chosen across four families deliberately: the
# published research says decorrelation is what makes an ensemble worth having.
ENSEMBLE_MODELS = [
    "openrouter/anthropic/claude-fable-5",
    "openrouter/openai/gpt-5.5",
    "openrouter/google/gemini-3.1-pro-preview",
    "openrouter/x-ai/grok-4.6",
]


def assert_tier_matches_mode(run_mode: str) -> None:
    """Refuse to forecast a scored tournament on a testing configuration.

    MODEL_TIER lives in this file as a single string. Left on "test", the
    season would run on a nano model at ONE prediction per question instead of
    five, and would still exit green — the exact class of silent failure that
    costs a season. A comment is not a safeguard; this is.
    """
    if run_mode != "tournament":
        return
    if MODEL_TIER != "season":
        raise SystemExit(
            f"REFUSING TO RUN: mode=tournament but MODEL_TIER={MODEL_TIER!r}. "
            "The tournament is scored. Set MODEL_TIER = 'season' in main.py, or "
            "run --mode test_questions against the bot testing area instead."
        )
    if SEASON_MODELS.get("researcher") in (None, "", "no_research", "None"):
        raise SystemExit(
            "REFUSING TO RUN: the season configuration has no researcher. "
            "Forecasting news questions with no search produced a negative "
            "expected score in Metaculus's own evidence. Set a researcher in "
            "SEASON_MODELS."
        )


def preflight_check_free_models() -> None:
    """Log the serving status of each configured free model. Never raises.

    Free models have a single endpoint each, so when a provider goes down the
    failure is total. This surfaces that at the top of the log instead of
    leaving us to infer it from a wall of 404s.
    """
    # Only meaningful on the free tier: paid models have many endpoints and
    # OpenRouter routes around dead ones, so a single status is not the story.
    if not (MODEL_TIER == "free" and PREFLIGHT_FREE_MODELS):
        return
    # Imported locally: main.py does not import these at module level, and a
    # NameError here would be swallowed by the except below — leaving a
    # preflight that silently checks nothing, which is worse than none.
    import json
    import urllib.request

    checked = set()
    for role, model in FREE_MODELS.items():
        if not model.startswith("openrouter/"):
            continue
        model_id = model[len("openrouter/") :]
        if model_id in checked:
            continue
        checked.add(model_id)
        try:
            url = f"https://openrouter.ai/api/v1/models/{model_id}/endpoints"
            with urllib.request.urlopen(url, timeout=15) as resp:
                payload = json.loads(resp.read().decode())
            endpoints = (payload.get("data") or {}).get("endpoints") or []
            if not endpoints:
                logger.warning("PREFLIGHT: %s has NO serving endpoints", model_id)
                continue
            for ep in endpoints:
                status = ep.get("status", 0)
                provider = ep.get("provider_name", "?")
                if status == 0:
                    logger.info("PREFLIGHT: %s ok via %s", model_id, provider)
                else:
                    logger.warning(
                        "PREFLIGHT: %s reports status %s via %s — expect failures",
                        model_id,
                        status,
                        provider,
                    )
        except Exception as exc:  # never let a health check break the run
            logger.warning("PREFLIGHT: could not check %s (%s)", model_id, exc)


def predictions_per_report():
    """Free tier gets 1; the season gets the full ensemble. See note above."""
    return 1 if USE_FREE_MODELS else 5


def build_llm_config():
    """Return the llms= mapping for the bot, per MODEL_TIER."""
    tiers = {"free": FREE_MODELS, "test": TEST_MODELS, "season": SEASON_MODELS}
    if MODEL_TIER not in tiers:
        raise ValueError(
            f"MODEL_TIER must be one of {sorted(tiers)}, got {MODEL_TIER!r}"
        )
    chosen = tiers[MODEL_TIER]
    logger.info(
        "Model tier: %s | default=%s | predictions/question=%d",
        MODEL_TIER.upper(),
        chosen["default"],
        predictions_per_report(),
    )
    return {
        # allowed_tries is deliberately generous on the free tier: upstream
        # 429s there are transient and shared-pool, so retrying is the correct
        # response rather than failing the run.
        "default": GeneralLlm(
            model=chosen["default"],
            temperature=0.3,
            timeout=LLM_TIMEOUT_SECONDS,
            allowed_tries=LLM_ALLOWED_TRIES,
        ),
        "summarizer": chosen["summarizer"],
        "parser": chosen["parser"],
        "researcher": chosen["researcher"],
    }


# =============================================================================
# SEASON ROLLOVER
#
# The seasonal tournament ID is not ours to set. It arrives from the
# forecasting-tools SDK as MetaculusClient.CURRENT_AI_COMPETITION_ID, and
# poetry.lock pins that SDK at 0.2.92 while the workflow installs with
# `poetry install`, which honours the lock. Read at the 0.2.92 version-bump
# commit AND at upstream main on 31 Aug 2026, the constants are:
#
#     FE_SUMMER_2026_ID         = 33022   # summer-futureeval-2026
#     CURRENT_AI_COMPETITION_ID = FE_SUMMER_2026_ID
#     CURRENT_MINIBENCH_ID      = "minibench"   <- a slug, season-independent
#
# Metaculus has published no Fall 2026 ID, in the SDK or on the site. The
# pinned value will therefore still say Summer when the Fall season opens.
#
# Why that is dangerous rather than merely wrong: the SDK fetches questions via
# get_all_open_questions_from_tournament(), which filters on
# allowed_tournaments=[id] with status "open" and returns whatever comes back.
# A finished tournament returns ZERO questions — no exception, no warning — and
# the run exits GREEN. Seasons run about four months. Left alone this bot would
# forecast on nothing for an entire season while every scheduled run showed a
# tick, which is the most expensive failure available to us.
#
# MiniBench is unaffected: "minibench" is a slug that survives the rollover.
#
# Rolling the season over needs no code change:
#   GitHub -> Settings -> Secrets and variables -> Actions -> Variables -> New
#   Name:  AIB_TOURNAMENT_ID
#   Value: the Fall 2026 project ID (e.g. 33099) or its slug
# Until that is set, the guard below skips the seasonal half, still forecasts
# MiniBench, and then fails the run so the workflow turns red.
# =============================================================================

# When the alarm starts sounding. Deliberately NOT the day Summer stops posting
# questions (early September): between then and the Fall launch there is nothing
# anyone could do about it, since Metaculus has not published a Fall ID, and an
# alarm nobody can act on is one people learn to ignore. Aiming at a finished
# tournament in that gap is free — zero questions means zero model calls — so
# the guard waits until a week before Fall opens on 28 September. That is one
# week of red runs to find and set the ID, and no false alarms before it.
SEASON_GUARD_DATE = datetime(2026, 9, 21, tzinfo=timezone.utc)

# What the pinned SDK still points at. Both the numeric and slug forms are
# listed because either can arrive depending on where the value came from.
STALE_SEASON_IDS = frozenset({33022, "33022", "summer-futureeval-2026"})

SEASON_STALE_MESSAGE = (
    "SEASON ROLLOVER NOT DONE. The seasonal target is still Summer 2026, which "
    "has stopped posting questions: forecasting it would return zero questions "
    "and exit green. The seasonal half of this run was SKIPPED; MiniBench was "
    "forecast as normal. Fix: set the repository variable AIB_TOURNAMENT_ID "
    "(Settings > Secrets and variables > Actions > Variables) to the Fall 2026 "
    "tournament ID or slug."
)


def resolve_seasonal_tournament(client) -> int | str:
    """The seasonal tournament to forecast on, environment override first.

    AIB_TOURNAMENT_ID exists because the Fall ID is not knowable today, and a
    repository variable can be set without editing, testing and redeploying
    code mid-season.
    """
    override = os.environ.get("AIB_TOURNAMENT_ID", "").strip()
    if override:
        resolved = int(override) if override.isdigit() else override
        logger.info("Seasonal tournament %r (from AIB_TOURNAMENT_ID)", resolved)
        return resolved
    resolved = client.CURRENT_AI_COMPETITION_ID
    logger.info("Seasonal tournament %r (from the forecasting-tools SDK)", resolved)
    return resolved


def season_is_stale(tournament_id, now=None) -> bool:
    """True when the seasonal target is a tournament that has finished.

    Deliberately returns a bool rather than raising: the caller still needs to
    forecast MiniBench before failing the run. Exiting here would turn one
    misconfiguration into two forfeited tournaments.
    """
    now = now if now is not None else datetime.now(timezone.utc)
    if now < SEASON_GUARD_DATE:
        return False
    return tournament_id in STALE_SEASON_IDS
''',
    "configuration block",
)

# ---------------------------------------------------------------------------
# 12. SEASON ROLLOVER  (an audit on 31 Aug 2026 found that the seasonal
#     tournament ID comes from the SDK, poetry.lock pins the SDK, and a
#     finished tournament returns zero questions rather than an error. See the
#     SEASON ROLLOVER block inserted above for the full reasoning.)
# ---------------------------------------------------------------------------
replace(
    """    # Per-mode tournament URL shown in the summary banner footer. These
    # piggyback on the forecasting_tools SDK constants and need updating
    # whenever those rotate seasons.
    TOURNAMENT_URLS = {
        "tournament": "https://www.metaculus.com/tournament/summer-futureeval-2026/",
        "metaculus_cup": "https://www.metaculus.com/tournament/metaculus-cup-summer-2025/",
        "test_questions": "https://www.metaculus.com/tournament/bot-testing-area/",
    }

    # Dispatch on mode. Each branch produces a list of ForecastReport (or
    # exceptions, since return_exceptions=True) which then flows into the
    # summary printers below.
    client = MetaculusClient()
    if run_mode == "tournament":
        seasonal_tournament_reports = asyncio.run(
            template_bot.forecast_on_tournament(
                client.CURRENT_AI_COMPETITION_ID, return_exceptions=True
            )
        )
        minibench_reports = asyncio.run(""",
    """    # Dispatch on mode. Each branch produces a list of ForecastReport (or
    # exceptions, since return_exceptions=True) which then flows into the
    # summary printers below.
    client = MetaculusClient()
    seasonal_id = None
    stale_season = False
    if run_mode == "tournament":
        seasonal_id = resolve_seasonal_tournament(client)
        stale_season = season_is_stale(seasonal_id)
        if stale_season:
            # Logged here so the reason appears in the run log next to the
            # MiniBench work, and raised again at the very end so the workflow
            # itself goes red. See the SEASON ROLLOVER block above.
            logger.error(SEASON_STALE_MESSAGE)
            seasonal_tournament_reports = []
        else:
            seasonal_tournament_reports = asyncio.run(
                template_bot.forecast_on_tournament(
                    seasonal_id, return_exceptions=True
                )
            )
        # MiniBench is keyed by a slug, so it survives the season rollover and
        # is forecast even when the seasonal half has been skipped.
        minibench_reports = asyncio.run(""",
    "season rollover: resolve the tournament, skip a stale one",
)

replace(
    """    template_bot.log_report_summary(forecast_reports)
    print_run_summary_banner(
        forecast_reports,
        will_publish=publish_to_metaculus,
        tournament_url=TOURNAMENT_URLS.get(run_mode),
    )""",
    """    # Per-mode tournament URL shown in the summary banner footer. The seasonal
    # entry is built from the ID actually forecast rather than hard-coded, so
    # the link cannot drift away from what the bot really did. Metaculus
    # redirects /tournament/<numeric id>/ to the slug (checked 31 Aug 2026).
    TOURNAMENT_URLS = {
        "tournament": f"https://www.metaculus.com/tournament/{seasonal_id}/",
        "metaculus_cup": "https://www.metaculus.com/tournament/metaculus-cup-summer-2025/",
        "test_questions": "https://www.metaculus.com/tournament/bot-testing-area/",
    }

    template_bot.log_report_summary(forecast_reports)
    print_run_summary_banner(
        forecast_reports,
        will_publish=publish_to_metaculus,
        tournament_url=TOURNAMENT_URLS.get(run_mode),
    )

    # Last thing in the file, so the log and banner are complete first. A red
    # workflow run every ten minutes is the point: silence is what costs a
    # season, and nothing else in this repository would notice.
    if stale_season:
        raise SystemExit(SEASON_STALE_MESSAGE)""",
    "season rollover: derive the banner URL and fail the run",
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
    _structure_output_validation_samples = 1""",
    "parse validation samples: 1, never 2",
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
    """        _pairs = [
            (o.option_name, o.probability)
            for o in predicted_option_list.predicted_options
        ]
        for _opt, (_, _p) in zip(
            predicted_option_list.predicted_options, floor_and_renormalise(_pairs)
        ):
            _opt.probability = _p

        logger.info(
            f"Forecasted URL {question.page_url} with prediction: {predicted_option_list}."
        )
        return ReasonedPrediction(
            prediction_value=predicted_option_list, reasoning=reasoning
        )""",
    "multiple-choice option floor",
)

DST.write_text(text)
print(f"\n{edits} edits applied cleanly -> {DST}")
