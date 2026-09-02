import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal

import dotenv

# Runtime helpers (env validation, banners, dependency-warning suppression).
from bot_helpers import (
    check_environment,
    print_run_summary_banner,
    print_startup_banner,
    silence_noisy_dependencies,
)

silence_noisy_dependencies()

from forecasting_tools import (
    ApiFilter,
    AskNewsSearcher,
    BinaryQuestion,
    ForecastBot,
    GeneralLlm,
    MetaculusClient,
    MetaculusQuestion,
    MultipleChoiceQuestion,
    NumericDistribution,
    NumericQuestion,
    DateQuestion,
    DatePercentile,
    Percentile,
    ConditionalQuestion,
    ConditionalPrediction,
    PredictionTypes,
    PredictionAffirmed,
    BinaryPrediction,
    PredictedOptionList,
    ReasonedPrediction,
    RefreshingBucketRateLimiter,
    SmartSearcher,
    clean_indents,
    structure_output,
)

dotenv.load_dotenv()
logger = logging.getLogger(__name__)

# Used by caps_for_reasoning(). The upstream template does not import re, and
# a missing import here would raise on EVERY binary question — caught by the
# build check added 31 Aug 2026, which is why that check exists.
import re  # noqa: E402

# Used by resolve_seasonal_tournament() to read the AIB_TOURNAMENT_ID
# repository variable. Not imported upstream either; same failure mode.
import os  # noqa: E402

# Counts questions forecast with little or no research, so the run can be
# failed at the end rather than only logged. See the check near the bottom of
# the file. Module-level because run_research is a method on the bot and the
# exit decision is made in __main__.
EMPTY_RESEARCH_COUNT = 0

# The run fails only if BOTH are exceeded: a minority of thin research is
# normal on niche questions, a majority means the researcher is broken. Failing
# on a single instance manufactures red-fatigue, which costs more than it saves
# when red is the only alarm this project has.
EMPTY_RESEARCH_MIN_TO_FAIL = 3
EMPTY_RESEARCH_FAIL_RATE = 0.5

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

# MULTIPLE-CHOICE FLOOR — REMOVED 1 Sept 2026. Kept as a note, not as code.
#
# We floored multiple-choice options at 0.01 and renormalised, on the reasoning
# that a 0% option which then RESOLVES scores about -691 and erases thirty good
# questions. The reasoning about the scoring rule was right. The code was
# pointless: PredictedOptionList carries a model_validator that runs on every
# construction and already clamps each option to [0.01, 0.99] — the identical
# value — before structure_output hands it back. Ours could only ever move a
# probability by about 2e-4.
#
# It survived a week because the unit tests fed it (name, probability) tuples
# directly, bypassing the SDK model. Data the SDK cannot produce, and in one
# case actively rejects: an all-zero list raises on the sum check. The test
# agreed with itself and never asked what the SDK does to the value afterwards
# — the very failure this project had already diagnosed once, in the numeric
# path, and written up as a lesson.
#
# Recorded rather than deleted silently, because the disclosure document should
# show the retraction as well as the change.

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

# FOUR TIERS.
#
#   "free"   — :free models, zero balance. KEPT FOR REFERENCE, NOT RECOMMENDED.
#              Four runs died on it across three providers. Every :free model
#              has exactly ONE serving endpoint on one provider's shared pool,
#              so there is no failover and the pool rate-limits under any load.
#   "test"   — cheap paid models, ONE prediction a question. Development only.
#   "trial"  — cheap paid models, FIVE predictions and live research. A real
#              forecasting configuration we can afford to fund ourselves, for
#              scored MiniBench rounds before the Metaculus credits arrive.
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
#
# The tier can be overridden by an environment variable, so a run can be
# re-pointed without a commit and a CI cycle — the same reasoning as
# AIB_TOURNAMENT_ID further down. Set a GitHub repository variable named
# MODEL_TIER, and delete it to fall back to the default below. Note this does
# NOT weaken assert_tier_matches_mode: a scored tournament still refuses to run
# on anything outside TOURNAMENT_READY_TIERS, wherever the value came from.
# (Changing the variable needs a GitHub password re-prompt, so it is a
# deliberate human act by construction.)
VALID_MODEL_TIERS = ("free", "test", "trial", "season")
MODEL_TIER = (os.environ.get("MODEL_TIER") or "test").strip().lower()
if MODEL_TIER not in VALID_MODEL_TIERS:
    # Fail here rather than three steps later inside build_llm_config, so a
    # typo in the repository variable names itself instead of surfacing as a
    # confusing model error after the questions have already been fetched.
    raise SystemExit(
        f"MODEL_TIER must be one of {VALID_MODEL_TIERS}, got {MODEL_TIER!r}. "
        "Check the MODEL_TIER repository variable."
    )

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
LLM_ALLOWED_TRIES = 6 if MODEL_TIER == "free" else 2
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

# TRIAL TIER. Strong-but-cheap, for scored runs we are paying for ourselves.
#
# Metaculus's own model leaderboard (read 31 Aug 2026) puts Gemini 3.6 Flash at
# 12.70 against Claude Fable 5 High at 13.77 — around 8 percent off the pace.
# OpenRouter prices them, verified live against /api/v1/models the same day, at
# $0.75/$3.75 and $10/$50 per million tokens: Fable is 13.3x dearer for that 8
# percent.
#
# Measured cost on the season tier was about $0.10 a prediction, so this tier
# should land nearer $0.0075 — a 60-question MiniBench round for a few pounds
# rather than about $30. Frontier models are the right call on Metaculus's
# credits and the wrong one on ours.
#
# Research stays on Sonar. Cutting search is the one economy that reliably
# loses more than it saves.
# Parser and summariser are a DIFFERENT model from the default, and that is the
# whole point rather than a detail. OpenRouter's new-account throttle is per
# model, so putting every role on one model makes them share one 20/minute
# budget. The first trial run did exactly that — 63 rejections, including
# "Could not summarize research" — because pacing the default model to 15/min
# is worthless if the parser is spending the same allowance un-paced.
# gpt-oss-120b is $0.037/$0.17 per million, 20x cheaper again, and has 20
# serving endpoints. Both prices verified live against /api/v1/models.
TRIAL_MODELS = {
    "default": "openrouter/google/gemini-3.6-flash",
    "summarizer": "openrouter/openai/gpt-oss-120b",
    "parser": "openrouter/openai/gpt-oss-120b",
    "researcher": "openrouter/perplexity/sonar",
}

# PACING THE DEFAULT MODEL.
#
# Measured, not guessed. The first ever season-tier run, 31 Aug 2026, returned:
#   "Rate limit exceeded: new-account-rpm/anthropic/claude-5-fable-20260609.
#    Rate limit reached: new accounts are limited to 20 requests per minute"
# with X-RateLimit-Limit: 20 and limit_source: openrouter_new_account. Out of
# that run: 87 calls rejected, 18 of 45 predictions landed, and 13 questions
# forfeited outright by the SDK's "at least half the samples must succeed"
# rule. Nothing in the bot noticed anything was wrong with its own design.
#
# The cause is burstiness, not volume. _max_concurrent_questions bounds
# run_research ONLY; once questions clear research their predictions all fire
# together, so nine questions at five predictions each puts dozens of calls at
# one model in the same second. Throttling questions would not have fixed it.
#
# 15 a minute against a limit of 20 leaves headroom for the retries GeneralLlm
# makes underneath this gate — those do not re-acquire, so they are invisible
# to the limiter and must simply be left room for. Capacity equals the
# per-minute figure, which is the library's intended "requests per minute"
# shape: a minute's worth may burst, then the bucket refills over the next
# minute before more is allowed.
#
# ONE BUCKET PER MODEL, not one per bot. The throttle is keyed on the model, so
# there are two buckets below: one for the default model and one for the
# parser. Parsing runs once per prediction, so it generates the SAME volume as
# forecasting — gating only the default model, as the first version of this did,
# leaves half the traffic un-paced. The two roles must also BE different models,
# or the two buckets simply share one real budget and neither is honoured.
#
# Cost of the pacing: a five-question tournament pass is 25 default calls, so
# under three minutes of the 90-minute window. Not the binding constraint.
#
# REVISED 1 Sept 2026 after audit, from 15 to 10. Two reasons, both measured
# rather than assumed:
#
#   1. Retries live BELOW this gate and never re-acquire, so they are invisible
#      to the bucket. Capping the parser at PARSER_ALLOWED_TRIES brings the
#      worst-case multiplier down from 6 to 2, but 2 x 15 = 30 still breaches a
#      limit of 20. At 10 a minute, even every single call retrying once stays
#      inside 20. Headroom of 100% is the point: we are buying certainty with
#      time, and time is the thing we have.
#   2. Capacity is now 1, not PER_MODEL_RPM. An audit simulated the library's
#      actual behaviour: with capacity=15 the bucket fires all fifteen requests
#      in the same instant and then stalls for a full 60 seconds, because
#      RefreshingBucketRateLimiter refills to FULL once emptied rather than one
#      unit at a time. The 60-second average held while the instantaneous rate
#      was ~15 per second — the very burst shape that triggered the throttle.
#      Capacity 1 gives one request every six seconds and no burst at all.
#
# REVISED AGAIN 2 Sept 2026, and made tier-aware. Two reasons:
#   1. 10 x LLM_ALLOWED_TRIES(2) = 20 against a limit of 20 is a boundary, not
#      a margin, and retries are CORRELATED with being at the limit — the thing
#      that triggers a retry is usually the 429 itself. 8 x 2 = 16 leaves room.
#   2. The free tier keeps LLM_ALLOWED_TRIES = 6, because free-tier 429s are
#      transient and retrying is the right response there. At 10/min that is a
#      worst case of 60 against a limit of 20 — the unit test caught it, having
#      been extended to check BOTH buckets rather than only the parser. The
#      free tier was unsafe by our own stated standard and nobody had noticed,
#      because the arithmetic was only ever checked at the default tier.
#      3 x 6 = 18 holds.
PER_MODEL_RPM = 3 if MODEL_TIER == "free" else 8
PER_MODEL_BURST = 1

# Retries inside the parser. The SDK default is 2, and structure_output wraps
# it in its own loop, so this is one half of a multiplier we cannot see from
# the rate limiter. One retry is worth having — a lost parse costs a whole
# sample, and three lost samples forfeit the question.
PARSER_ALLOWED_TRIES = 2

# structure_output's own outer retry loop, which wraps the parser LLM's. The
# SDK default is 3; combined with PARSER_ALLOWED_TRIES that is a 6x multiplier
# on every acquisition. At 1 the worst case is 2, which the 10/min pace covers.
STRUCTURE_OUTPUT_ALLOWED_TRIES = 1

# Phase 2 ensemble members, kept here so the intent is recorded even though
# nothing reads this yet. Chosen across four families deliberately: the
# published research says decorrelation is what makes an ensemble worth having.
ENSEMBLE_MODELS = [
    "openrouter/anthropic/claude-fable-5",
    "openrouter/openai/gpt-5.5",
    "openrouter/google/gemini-3.1-pro-preview",
    "openrouter/x-ai/grok-4.6",
]


# Tiers that constitute a real forecasting configuration: five predictions a
# question and live research. "trial" qualifies on both counts — it is simply
# cheaper, and it is what we can afford for scored MiniBench rounds until the
# Metaculus credits arrive. "test" and "free" do not qualify, and never should.
TOURNAMENT_READY_TIERS = ("season", "trial")


def assert_tier_matches_mode(run_mode: str) -> None:
    """Refuse to forecast a scored tournament on a testing configuration.

    MODEL_TIER lives in this file as a single string. Left on "test", the
    season would run on a nano model at ONE prediction per question instead of
    five, and would still exit green — the exact class of silent failure that
    costs a season. A comment is not a safeguard; this is.
    """
    if run_mode != "tournament":
        return
    if MODEL_TIER not in TOURNAMENT_READY_TIERS:
        raise SystemExit(
            f"REFUSING TO RUN: mode=tournament but MODEL_TIER={MODEL_TIER!r}. "
            f"The tournament is scored. Set MODEL_TIER to one of "
            f"{TOURNAMENT_READY_TIERS}, or run --mode test_questions against "
            "the bot testing area instead."
        )
    models = {"season": SEASON_MODELS, "trial": TRIAL_MODELS}[MODEL_TIER]
    if models.get("researcher") in (None, "", "no_research", "None"):
        raise SystemExit(
            f"REFUSING TO RUN: the {MODEL_TIER} configuration has no researcher. "
            "Forecasting news questions with no search produced a negative "
            "expected score in Metaculus's own evidence. Set a researcher in "
            f"{MODEL_TIER.upper()}_MODELS."
        )
    if MODEL_TIER == "trial":
        logger.warning(
            "Forecasting a SCORED tournament on the TRIAL tier: cheaper models, "
            "about 8 percent off the frontier on Metaculus's own leaderboard. "
            "Deliberate while we are paying for inference ourselves."
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
    tiers = {
        "free": FREE_MODELS,
        "test": TEST_MODELS,
        "trial": TRIAL_MODELS,
        "season": SEASON_MODELS,
    }
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
        # The parser is a GeneralLlm rather than a bare model string so we can
        # set allowed_tries. Passed as a string, the SDK wraps it in a
        # GeneralLlm with _DEFAULT_ALLOWED_TRIES = 2, and structure_output adds
        # its own outer loop of allowed_tries=3 — so ONE trip through our rate
        # limiter could become SIX requests on the wire. Retries happen below
        # the gate and never re-acquire, so the limiter cannot see them. Audit,
        # 1 Sept 2026: this is the better explanation for 87 rejections against
        # 45 acquisitions than the burstiness we first blamed.
        "parser": GeneralLlm(
            model=chosen["parser"],
            temperature=0.0,
            timeout=LLM_TIMEOUT_SECONDS,
            allowed_tries=PARSER_ALLOWED_TRIES,
        ),
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
# Until that is set, MiniBench is forecast as normal, the seasonal half is
# skipped, and the run fails so the workflow turns red. MiniBench runs FIRST in
# the dispatch precisely so an unset variable cannot forfeit it.
# =============================================================================

# The old guard was a fixed date plus a three-item denylist of known-stale IDs.
# An audit on 1 Sept 2026 showed it tested the wrong thing: it asked what the
# tournament ID *is*, not what it *does*. A typo in AIB_TOURNAMENT_ID sailed
# straight past it — zero questions, green tick, every ten minutes for four
# months. It also expired: once the Fall ID was set the guard was spent, and
# the Winter 2027 rollover would have repeated the original failure with no
# alarm at all.
#
# Replaced by a question COUNT check at the point of use. It needs no dates, no
# ID list and no maintenance, and it is correct at every future rollover.
SEASON_MISSING_MESSAGE = (
    "{label} TOURNAMENT NOT FOUND: {tournament!r} contains no questions at "
    "all. That is a wrong or retired tournament ID, not a quiet hour — a live "
    "tournament always has questions even when none are currently open. That "
    "half of this run forecast NOTHING. Fix: set the repository variable "
    "AIB_TOURNAMENT_ID (Settings > Secrets and variables > Actions > "
    "Variables) to the current seasonal tournament ID or slug."
)


# Slug fragments that identify a Metaculus BOT tournament. Every question the
# API returns carries the slugs of the tournaments it belongs to
# (MetaculusQuestion.tournament_slugs, filled from projects.tournament[].slug),
# so checking this costs no extra request.
#
# Why it exists: the question-count check alone cannot tell a typo from a
# correct ID. Metaculus project IDs are dense — 32916, 33021, 33022 — so a
# transposed digit usually lands on ANOTHER REAL PROJECT, which has questions,
# passes the count check, and would have us forecasting into a tournament we
# are not entered in. Green, every ten minutes, for four months. Found by
# audit on 2 Sept 2026, in the guard written to prevent exactly that.
#
# Fragments rather than names because Metaculus has renamed the series over
# time: aibq3, aibq4, fall-aib-2025, spring-aib-2026, summer-futureeval-2026,
# minibench. Every one contains one of these.
BOT_TOURNAMENT_SLUG_MARKERS = ("aib", "futureeval", "minibench")


def tournament_slug_problem(tournament_id, questions, label: str) -> str | None:
    """None if these questions belong to a bot tournament, else why not.

    Fails SAFE on missing metadata: if no question carries a slug we warn and
    allow the run, because refusing on absent data would turn an API change
    into a four-month outage of our own making.
    """
    slugs = {
        s.lower()
        for q in questions
        for s in (getattr(q, "tournament_slugs", None) or [])
    }
    if not slugs:
        logger.warning(
            "%s tournament %r returned questions carrying no tournament slugs, "
            "so it could not be verified as a bot tournament. Allowing the run.",
            label,
            tournament_id,
        )
        return None
    if any(marker in s for s in slugs for marker in BOT_TOURNAMENT_SLUG_MARKERS):
        logger.info(
            "%s tournament %r verified as a bot tournament: %s",
            label,
            tournament_id,
            sorted(slugs),
        )
        return None
    return (
        f"WRONG TOURNAMENT: {label} target {tournament_id!r} resolves to "
        f"{sorted(slugs)}, none of which looks like a Metaculus bot tournament "
        f"(expected a slug containing one of {BOT_TOURNAMENT_SLUG_MARKERS}). "
        "That is almost certainly a mistyped AIB_TOURNAMENT_ID landing on a "
        "real but unrelated project. Forecasting was skipped."
    )


# GROUP QUESTIONS: ON. Skipping was PROVEN to work on them, 2 Sept 2026.
#
# The worry was that skip_previously_forecasted_questions — the only thing
# stopping a 10-minute cron re-forecasting the same question all season — reads
# question.already_forecasted, which the SDK fills from
# question_json["my_forecasts"]["history"]. For an unpacked GROUP subquestion
# that json is deep-copied from the group payload, so the field is only there
# if Metaculus puts my_forecasts on each subquestion. The SDK explicitly
# patches this for CONDITIONAL questions and does nothing for groups, which
# read like the case had never been considered. If it failed open we would
# re-forecast group subquestions 144 times a day — wasted spend, and a breach
# of the one-forecast-per-question rule for bot-only tournaments.
#
# Settled by running check_group_questions.py against the bot-testing-area,
# where earlier Test Bot runs had already forecast the group questions:
#
#     9 open question(s): 4 in groups, 5 standalone.
#     43329  in group  already forecast: True   (post 43325)
#     43330  in group  already forecast: True   (post 43325)
#     43323  in group  already forecast: True   (post 43322)
#     43324  in group  already forecast: True   (post 43322)
#
# Four for four. Metaculus does populate my_forecasts per subquestion, so
# skipping holds and group questions are back in play. The switch stays so the
# decision is reversible if that ever stops being true — re-run the check
# rather than assuming.
SKIP_GROUP_QUESTIONS = False


def drop_group_questions(questions, label: str):
    """Remove unpacked group subquestions. See SKIP_GROUP_QUESTIONS."""
    if not SKIP_GROUP_QUESTIONS:
        return questions
    kept = [q for q in questions if getattr(q, "question_ids_of_group", None) is None]
    dropped = len(questions) - len(kept)
    if dropped:
        logger.warning(
            "%s: skipping %d group subquestion(s). Deliberate — see "
            "SKIP_GROUP_QUESTIONS. We cannot yet prove the SDK reports them as "
            "already forecast, and re-forecasting one would breach the "
            "one-forecast-per-question rule.",
            label,
            dropped,
        )
    return kept


def fetch_and_verify_tournament(client, tournament_id, label: str):
    """Return (open_questions, problem_message_or_None).

    Fetches explicitly rather than letting forecast_on_tournament do it,
    because that discards the question COUNT, and the count is what separates a
    working tournament from a dead one.
    """
    questions = client.get_all_open_questions_from_tournament(tournament_id)
    logger.info(
        "%s tournament %r: %d open questions", label, tournament_id, len(questions)
    )
    questions = drop_group_questions(questions, label)
    sample = questions
    if not questions:
        # Zero OPEN questions is normal. Questions accept forecasts for about
        # 90 minutes and this runs every 10, so most runs legitimately find
        # nothing. Zero questions AT ALL is not normal. Only pay for the second
        # query on the runs that would otherwise have said nothing at all.
        sample = asyncio.run(
            client.get_questions_matching_filter(
                # unpack_subquestions to match what the fetch above uses.
                # ApiFilter defaults to "exclude", which drops group posts both
                # server-side and locally — so the probe looked at a different
                # population from the forecast set, and a tournament whose
                # newest posts were all groups would have produced a false
                # "NOT FOUND". Audit, 2 Sept 2026.
                ApiFilter(
                    allowed_tournaments=[tournament_id],
                    group_question_mode="unpack_subquestions",
                )
            )
        )
        if not sample:
            return [], SEASON_MISSING_MESSAGE.format(
                tournament=tournament_id, label=label
            )
        logger.info(
            "%r holds %d questions, none open right now. Normal between windows.",
            tournament_id,
            len(sample),
        )
    return questions, tournament_slug_problem(tournament_id, sample, label)


def resolve_seasonal_tournament():
    """Return (tournament_id_or_None, problem_or_None). AIB_TOURNAMENT_ID is REQUIRED.

    Returns a problem rather than raising, so the caller can still forecast
    MiniBench before failing the run. The first version raised here, which meant
    an unset variable forfeited MiniBench too — a scored series, keyed by a slug
    that survives the rollover, that was working perfectly. Audit, 2 Sept 2026.

    This used to fall back to the SDK's CURRENT_AI_COMPETITION_ID. That fallback
    was removed on 2 Sept 2026 because it is a silent trap: poetry.lock pins
    forecasting-tools 0.2.92, where the constant is frozen at Summer 2026, so
    the fallback quietly aims a whole Fall season at a finished tournament.

    The question-count check downstream catches a WRONG id — a typo has no
    questions at all — but it cannot catch a RETIRED one. Summer still holds
    328 questions; they are simply all closed, so the probe would report the
    tournament as healthy. The fallback had to go rather than be guarded.

    Requiring the variable closes both failures with no dates, no ID lists and
    no maintenance, and stays correct at every future rollover. The cost is one
    repository variable that has to be set before a season starts, which was
    always true anyway.
    """
    override = os.environ.get("AIB_TOURNAMENT_ID", "").strip()
    if not override:
        return None, (
            "REFUSING TO PASS: AIB_TOURNAMENT_ID is not set. Tournament mode has "
            "to be told which seasonal tournament to forecast — the SDK's "
            "built-in constant is pinned to Summer 2026 and would forecast a "
            "finished tournament without complaining. Set the repository "
            "variable (Settings > Secrets and variables > Actions > Variables) "
            "to the current seasonal tournament ID or slug."
        )
    resolved = int(override) if override.isdigit() else override
    logger.info("Seasonal tournament %r (from AIB_TOURNAMENT_ID)", resolved)
    return resolved, None


class SummerTemplateBot2026(ForecastBot):
    """
    This is the template bot for Summer 2026 Metaculus AI Tournament.
    This is a copy of what is used by Metaculus to run the Metac Bots in our benchmark, provided as a template for new bot makers.
    This template is given as-is, and is use-at-your-own-risk.
    We have covered most test cases in forecasting-tools it may be worth double checking key components locally.
    So far our track record has been 1 mentionable bug per season (affecting forecasts for 1-2% of total questions)

    Main changes since Fall:
    - Additional prompting has been added to numeric questions to emphasize putting pecentile values in the correct order.
    - Support for conditional and date questions has been added
    - Note: Summer AIB will not use date/conditional questions, so these are only for forecasting on the main site as you wish.

    The main entry point of this bot is `bot.forecast_on_tournament(tournament_id)` in the parent class.
    See the script at the bottom of the file for more details on how to run the bot.
    Ignoring the finer details, the general flow is:
    - Load questions from Metaculus
    - For each question
        - Execute run_research a number of times equal to research_reports_per_question
        - Execute respective run_forecast function `predictions_per_research_report * research_reports_per_question` times
        - Aggregate the predictions
        - Submit prediction (if publish_reports_to_metaculus is True)
    - Return a list of ForecastReport objects

    Alternatively, you can use the MetaculusClient to make a custom filter of questions to forecast on
    and forecast them with `bot.forecast_questions(questions)`

    Only the research and forecast functions need to be implemented in ForecastBot subclasses,
    though you may want to override other ForecastBot functions.
    In this example, you can change the prompts to be whatever you want since,
    structure_output uses an LLM to intelligently reformat the output into the needed structure.

    By default (i.e. 'tournament' mode), when you run this script, it will forecast on any open questions in the
    primary bot tournament and MiniBench. If you want to forecast on only one or the other, you can remove one
    of them from the 'tournament' mode code at the bottom of the file.

    You can experiment with what models work best with your bot by using the `llms` parameter when initializing the bot.
    You can initialize the bot with any number of models. For example,
    ```python
    my_bot = MyBot(
        ...
        llms={  # choose your model names or GeneralLlm llms here, otherwise defaults will be chosen for you
            "default": GeneralLlm(
                model="openrouter/openai/gpt-4o", # "anthropic/claude-sonnet-4-20250514", etc (see docs for litellm)
                temperature=0.3,
                timeout=40,
                allowed_tries=2,
            ),
            "summarizer": "openai/gpt-4o-mini",
            "researcher": "asknews/news-summaries",
            "parser": "openai/gpt-4o-mini",
        },
    )
    ```

    Then you can access the model in custom functions like this:
    ```python
    research_strategy = self.get_llm("researcher", "model_name"
    if research_strategy == "asknews/news-summaries":
        ...
    # OR
    summarizer = await self.get_llm("summarizer", "llm").invoke(prompt)
    # OR
    reasoning = await self.get_llm("default", "llm").invoke(prompt)
    ```

    If you end up having trouble with rate limits and want to try a more sophisticated rate limiter try:
    ```python
    from forecasting_tools import RefreshingBucketRateLimiter
    rate_limiter = RefreshingBucketRateLimiter(
        capacity=2,
        refresh_rate=1,
    ) # Allows 1 request per second on average with a burst of 2 requests initially. Set this as a class variable
    await self.rate_limiter.wait_till_able_to_acquire_resources(1) # 1 because it's consuming 1 request (use more if you are adding a token limit)
    ```
    Additionally OpenRouter has large rate limits immediately on account creation
    """

    _max_concurrent_questions = MAX_CONCURRENT_QUESTIONS
    _concurrency_limiter = asyncio.Semaphore(_max_concurrent_questions)
    # Parse each reasoning text ONCE, not twice.
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
        """The single door every default-model call goes through.

        Centralised deliberately. The failure this fixes was four separate call
        sites each firing as fast as asyncio would allow, with nothing in the
        bot aware of the others. A rate limit is a property of the account, so
        the gate has to be shared, not per-question.
        """
        await self._default_model_limiter.wait_till_able_to_acquire_resources(1)
        return await self.get_llm("default", "llm").invoke(prompt)

    async def _structure_output_paced(self, *args, **kwargs):
        """structure_output, paced against the parser model's own throttle.

        Parsing happens once per prediction, so it is not a minor side channel:
        it is the same call volume as forecasting. The first rate-limited run
        paced the default model and left this untouched, which is why it still
        failed.
        """
        await self._parser_model_limiter.wait_till_able_to_acquire_resources(1)
        # allowed_tries is structure_output's OWN outer retry loop, separate
        # from the parser LLM's. Left at its default of 3 it multiplies with
        # PARSER_ALLOWED_TRIES; stated here so the worst case is visible in one
        # place rather than inherited from a default we did not choose.
        kwargs.setdefault("allowed_tries", STRUCTURE_OUTPUT_ALLOWED_TRIES)
        return await structure_output(*args, **kwargs)

    ##################################### RESEARCH #####################################

    async def run_research(self, question: MetaculusQuestion) -> str:
        async with self._concurrency_limiter:
            research = ""
            researcher = self.get_llm("researcher")

            prompt = clean_indents(
                f"""
                You are an assistant to a superforecaster.
                The superforecaster will give you a question they intend to forecast on.
                To be a great assistant, you generate a concise but detailed rundown of the most relevant news, including if the question would resolve Yes or No based on current information.
                You do not produce forecasts yourself.

                Question:
                {question.question_text}

                This question's outcome will be determined by the specific criteria below:
                {question.resolution_criteria}

                {question.fine_print}
                """
            )

            if isinstance(researcher, GeneralLlm):
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
                research = await self.get_llm("researcher", "llm").invoke(prompt)
            # Nothing anywhere checked that research actually returned
            # anything. If Sonar returns an empty string or a refusal rather
            # than raising, the prompt reads "Your research assistant says:"
            # followed by nothing, the bot forecasts from model weights against
            # a training cutoff, publishes, and exits green. Metaculus's own
            # evidence puts removing search at 3.6x Brier.
            #
            # Logged, not raised — deliberately, for now. Raising would discard
            # the sample, and three discarded samples forfeit the question, so
            # the fix could cost more than the fault. Whether an unresearched
            # forecast is worse than no forecast is a judgement about the
            # scoring rule, and it is recorded as an open decision rather than
            # settled quietly here.
            if self.get_llm("researcher") not in (None, "", "None", "no_research"):
                if len(research.strip()) < 200:
                    global EMPTY_RESEARCH_COUNT
                    EMPTY_RESEARCH_COUNT += 1
                    logger.error(
                        "RESEARCH LOOKS EMPTY for %s (%d chars). This forecast is "
                        "coming from model weights, not from search.",
                        question.page_url,
                        len(research.strip()),
                    )
            logger.info(f"Found Research for URL {question.page_url}:\n{research}")
            return research

    ##################################### BINARY QUESTIONS #####################################

    async def _run_forecast_on_binary(
        self, question: BinaryQuestion, research: str
    ) -> ReasonedPrediction[float]:
        prompt = clean_indents(
            f"""
            You are a professional forecaster interviewing for a job.

            Your interview question is:
            {question.question_text}

            Question background:
            {question.background_info}


            This question's outcome will be determined by the specific criteria below. These criteria have not yet been satisfied:
            {question.resolution_criteria}

            {question.fine_print}


            Your research assistant says:
            {research}

            Today is {datetime.now().strftime("%Y-%m-%d")}.

            This question is STILL OPEN and has NOT yet resolved. If your research
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

            You write your rationale remembering that good forecasters put extra weight on the status quo outcome since the world changes slowly most of the time.
            {self._get_conditional_disclaimer_if_necessary(question)}

            The last thing you write is your final answer as: "Probability: ZZ%", 0-100
            """
        )

        return await self._binary_prompt_to_forecast(question, prompt)

    async def _binary_prompt_to_forecast(
        self,
        question: BinaryQuestion,
        prompt: str,
    ) -> ReasonedPrediction[float]:
        reasoning = await self._invoke_default_llm(prompt)
        logger.info(f"Reasoning for URL {question.page_url}: {reasoning}")
        binary_prediction: BinaryPrediction = await self._structure_output_paced(
            reasoning,
            BinaryPrediction,
            model=self.get_llm("parser", "llm"),
            num_validation_samples=self._structure_output_validation_samples,
        )
        floor, ceiling = caps_for_reasoning(reasoning)
        decimal_pred = max(floor, min(ceiling, binary_prediction.prediction_in_decimal))

        logger.info(
            f"Forecasted URL {question.page_url} with prediction: {decimal_pred}."
        )
        return ReasonedPrediction(prediction_value=decimal_pred, reasoning=reasoning)

    ##################################### MULTIPLE CHOICE QUESTIONS #####################################

    async def _run_forecast_on_multiple_choice(
        self, question: MultipleChoiceQuestion, research: str
    ) -> ReasonedPrediction[PredictedOptionList]:
        prompt = clean_indents(
            f"""
            You are a professional forecaster interviewing for a job.

            Your interview question is:
            {question.question_text}

            The options are: {question.options}


            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}


            Your research assistant says:
            {research}

            Today is {datetime.now().strftime("%Y-%m-%d")}.

            Before answering you write:
            (a) The time left until the outcome to the question is known.
            (b) The status quo outcome if nothing changed.
            (c) A description of an scenario that results in an unexpected outcome.

            {self._get_conditional_disclaimer_if_necessary(question)}
            You write your rationale remembering that (1) good forecasters put extra weight on the status quo outcome since the world changes slowly most of the time, and (2) good forecasters leave some moderate probability on most options to account for unexpected outcomes.

            The last thing you write is your final probabilities for the N options in this order {question.options} as:
            Option_A: Probability_A
            Option_B: Probability_B
            ...
            Option_N: Probability_N
            """
        )
        return await self._multiple_choice_prompt_to_forecast(question, prompt)

    async def _multiple_choice_prompt_to_forecast(
        self,
        question: MultipleChoiceQuestion,
        prompt: str,
    ) -> ReasonedPrediction[PredictedOptionList]:
        parsing_instructions = clean_indents(
            f"""
            Make sure that all option names are one of the following:
            {question.options}

            The text you are parsing may prepend these options with some variation of "Option" which you should remove if not part of the option names I just gave you.
            Do not skip options. Every option above must appear in your final list.
            NEVER emit exactly 0 for an option. Use 0.01 as the minimum for any
            option you consider negligible, and make the probabilities sum to
            exactly 1.00.

            (Both rules exist because the library validates this list before we
            ever see it: it rejects the whole sample if the probabilities sum
            outside 0.99-1.01, and it clamps every option into 0.01-0.99 and
            then rejects the sample if that clamping moved any option by more
            than 0.05. A confident forecast across eight or more options with
            literal zeros trips the second rule every time. A rejected sample is
            not a smaller forecast, it is a lost one, and three lost samples
            forfeit the question entirely.)
            """
        )
        reasoning = await self._invoke_default_llm(prompt)
        logger.info(f"Reasoning for URL {question.page_url}: {reasoning}")
        predicted_option_list: PredictedOptionList = await self._structure_output_paced(
            text_to_structure=reasoning,
            output_type=PredictedOptionList,
            model=self.get_llm("parser", "llm"),
            num_validation_samples=self._structure_output_validation_samples,
            additional_instructions=parsing_instructions,
        )

        # NO FLOOR APPLIED HERE, DELIBERATELY. We used to floor and renormalise
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
        )

    ##################################### NUMERIC QUESTIONS #####################################

    async def _run_forecast_on_numeric(
        self, question: NumericQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_bound_message, lower_bound_message = (
            self._create_upper_and_lower_bound_messages(question)
        )
        prompt = clean_indents(
            f"""
            You are a professional forecaster interviewing for a job.

            Your interview question is:
            {question.question_text}

            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}

            Units for answer: {question.unit_of_measure if question.unit_of_measure else "Not stated (please infer this)"}

            Your research assistant says:
            {research}

            Today is {datetime.now().strftime("%Y-%m-%d")}.

            {lower_bound_message}
            {upper_bound_message}

            Formatting Instructions:
            - Please notice the units requested and give your answer in these units (e.g. whether you represent a number as 1,000,000 or 1 million).
            - Never use scientific notation.
            - Always start with a smaller number (more negative if negative) and then increase from there. The value for percentile 10 should always be less than the value for percentile 20, and so on.

            Before answering you write:
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
            Percentile 10: XX (lowest number value)
            Percentile 20: XX
            Percentile 40: XX
            Percentile 60: XX
            Percentile 80: XX
            Percentile 90: XX (highest number value)
            "
            """
        )
        return await self._numeric_prompt_to_forecast(question, prompt)

    async def _numeric_prompt_to_forecast(
        self,
        question: NumericQuestion,
        prompt: str,
    ) -> ReasonedPrediction[NumericDistribution]:
        reasoning = await self._invoke_default_llm(prompt)
        logger.info(f"Reasoning for URL {question.page_url}: {reasoning}")
        parsing_instructions = clean_indents(
            f"""
            The text given to you is trying to give a forecast distribution for a numeric question.
            - This text is trying to answer the numeric question: "{question.question_text}".
            - When parsing the text, please make sure to give the values (the ones assigned to percentiles) in terms of the correct units.
            - The units for the forecast are: {question.unit_of_measure}
            - Your work will be shown publicly with these units stated verbatim after the numbers your parse.
            - As an example, someone else guessed that the answer will be between {question.lower_bound} {question.unit_of_measure} and {question.upper_bound} {question.unit_of_measure}, so the numbers parsed from an answer like this would be verbatim "{question.lower_bound}" and "{question.upper_bound}".
            - If the answer doesn't give the answer in the correct units, you should parse it in the right units. For instance if the answer gives numbers as $500,000,000 and units are "B $" then you should parse the answer as 0.5 (since $500,000,000 is $0.5 billion).
            - If percentiles are not explicitly given (e.g. only a single value is given) please don't return a parsed output, but rather indicate that the answer is not explicitly given in the text.
            - Turn any values that are in scientific notation into regular numbers.
            """
        )
        percentile_list: list[Percentile] = await self._structure_output_paced(
            reasoning,
            list[Percentile],
            model=self.get_llm("parser", "llm"),
            additional_instructions=parsing_instructions,
            num_validation_samples=self._structure_output_validation_samples,
        )
        percentile_list = _sorted_percentiles(percentile_list)
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

    ##################################### DATE QUESTIONS #####################################

    async def _run_forecast_on_date(
        self, question: DateQuestion, research: str
    ) -> ReasonedPrediction[NumericDistribution]:
        upper_bound_message, lower_bound_message = (
            self._create_upper_and_lower_bound_messages(question)
        )
        prompt = clean_indents(
            f"""
            You are a professional forecaster interviewing for a job.

            Your interview question is:
            {question.question_text}

            Background:
            {question.background_info}

            {question.resolution_criteria}

            {question.fine_print}

            Your research assistant says:
            {research}

            Today is {datetime.now().strftime("%Y-%m-%d")}.

            {lower_bound_message}
            {upper_bound_message}

            Formatting Instructions:
            - This is a date question, and as such, the answer must be expressed in terms of dates.
            - The dates must be written in the format of YYYY-MM-DD. If hours matter, please append the date with the hour in UTC and military time: YYYY-MM-DDTHH:MM:SSZ.No other formatting is allowed.
            - Always start with a lower date chronologically and then increase from there.
            - Do NOT forget this. The dates must be written in chronological order starting at the earliest time at percentile 10 and increasing from there.

            Before answering you write:
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
            Percentile 10: YYYY-MM-DD (oldest date)
            Percentile 20: YYYY-MM-DD
            Percentile 40: YYYY-MM-DD
            Percentile 60: YYYY-MM-DD
            Percentile 80: YYYY-MM-DD
            Percentile 90: YYYY-MM-DD (newest date)
            "
            """
        )
        forecast = await self._date_prompt_to_forecast(question, prompt)
        return forecast

    async def _date_prompt_to_forecast(
        self,
        question: DateQuestion,
        prompt: str,
    ) -> ReasonedPrediction[NumericDistribution]:
        reasoning = await self._invoke_default_llm(prompt)
        logger.info(f"Reasoning for URL {question.page_url}: {reasoning}")
        parsing_instructions = clean_indents(
            f"""
            The text given to you is trying to give a forecast distribution for a date question.
            - This text is trying to answer the question: "{question.question_text}".
            - As an example, someone else guessed that the answer will be between {question.lower_bound} and {question.upper_bound}, so the numbers parsed from an answer like this would be verbatim "{question.lower_bound}" and "{question.upper_bound}".
            - The output is given as dates/times please format it into a valid datetime parsable string. Assume midnight UTC if no hour is given.
            - If percentiles are not explicitly given (e.g. only a single value is given) please don't return a parsed output, but rather indicate that the answer is not explicitly given in the text.
            """
        )
        date_percentile_list: list[DatePercentile] = await self._structure_output_paced(
            reasoning,
            list[DatePercentile],
            model=self.get_llm("parser", "llm"),
            additional_instructions=parsing_instructions,
            num_validation_samples=self._structure_output_validation_samples,
        )

        percentile_list = [
            Percentile(
                percentile=percentile.percentile,
                value=percentile.value.timestamp(),
            )
            for percentile in date_percentile_list
        ]
        prediction = NumericDistribution.from_question(percentile_list, question)
        # Same reasoning as the numeric path: fail a bad sample as a sample.
        prediction.get_cdf()
        logger.info(
            f"Forecasted URL {question.page_url} with prediction: {prediction.declared_percentiles}."
        )
        return ReasonedPrediction(prediction_value=prediction, reasoning=reasoning)

    def _create_upper_and_lower_bound_messages(
        self, question: NumericQuestion | DateQuestion
    ) -> tuple[str, str]:
        if isinstance(question, NumericQuestion):
            if question.nominal_upper_bound is not None:
                upper_bound_number = question.nominal_upper_bound
            else:
                upper_bound_number = question.upper_bound
            if question.nominal_lower_bound is not None:
                lower_bound_number = question.nominal_lower_bound
            else:
                lower_bound_number = question.lower_bound
            unit_of_measure = question.unit_of_measure
        elif isinstance(question, DateQuestion):
            upper_bound_number = question.upper_bound.date().isoformat()
            lower_bound_number = question.lower_bound.date().isoformat()
            unit_of_measure = ""
        else:
            raise ValueError()

        if question.open_upper_bound:
            upper_bound_message = f"The question creator thinks the number is likely not higher than {upper_bound_number} {unit_of_measure}."
        else:
            upper_bound_message = f"The outcome can not be higher than {upper_bound_number} {unit_of_measure}."

        if question.open_lower_bound:
            lower_bound_message = f"The question creator thinks the number is likely not lower than {lower_bound_number} {unit_of_measure}."
        else:
            lower_bound_message = f"The outcome can not be lower than {lower_bound_number} {unit_of_measure}."
        return upper_bound_message, lower_bound_message

    ##################################### CONDITIONAL QUESTIONS #####################################

    async def _run_forecast_on_conditional(
        self, question: ConditionalQuestion, research: str
    ) -> ReasonedPrediction[ConditionalPrediction]:
        parent_info, full_research = await self._get_question_prediction_info(
            question.parent, research, "parent"
        )
        child_info, full_research = await self._get_question_prediction_info(
            question.child, research, "child"
        )
        yes_info, full_research = await self._get_question_prediction_info(
            question.question_yes, full_research, "yes"
        )
        no_info, full_research = await self._get_question_prediction_info(
            question.question_no, full_research, "no"
        )
        full_reasoning = clean_indents(
            f"""
            ## Parent Question Reasoning
            {parent_info.reasoning}
            ## Child Question Reasoning
            {child_info.reasoning}
            ## Yes Question Reasoning
            {yes_info.reasoning}
            ## No Question Reasoning
            {no_info.reasoning}
        """
        )
        full_prediction = ConditionalPrediction(
            parent=parent_info.prediction_value,  # type: ignore
            child=child_info.prediction_value,  # type: ignore
            prediction_yes=yes_info.prediction_value,  # type: ignore
            prediction_no=no_info.prediction_value,  # type: ignore
        )
        return ReasonedPrediction(
            reasoning=full_reasoning, prediction_value=full_prediction
        )

    async def _get_question_prediction_info(
        self, question: MetaculusQuestion, research: str, question_type: str
    ) -> tuple[ReasonedPrediction[PredictionTypes | PredictionAffirmed], str]:
        from forecasting_tools.data_models.data_organizer import DataOrganizer

        previous_forecasts = question.previous_forecasts
        if (
            question_type in ["parent", "child"]
            and previous_forecasts
            and question_type not in self.force_reforecast_in_conditional
        ):
            # TODO: add option to not affirm current parent/child forecasts, create new forecast
            previous_forecast = previous_forecasts[-1]
            current_utc_time = datetime.now(timezone.utc)
            if (
                previous_forecast.timestamp_end is None
                or previous_forecast.timestamp_end > current_utc_time
            ):
                pretty_value = DataOrganizer.get_readable_prediction(previous_forecast)  # type: ignore
                prediction = ReasonedPrediction(
                    prediction_value=PredictionAffirmed(),
                    reasoning=f"Already existing forecast reaffirmed at {pretty_value}.",
                )
                return (prediction, research)  # type: ignore
        info = await self._make_prediction(question, research)
        full_research = self._add_reasoning_to_research(research, info, question_type)
        return info, full_research  # type: ignore

    def _add_reasoning_to_research(
        self,
        research: str,
        reasoning: ReasonedPrediction[PredictionTypes],
        question_type: str,
    ) -> str:
        from forecasting_tools.data_models.data_organizer import DataOrganizer

        question_type = question_type.title()
        return clean_indents(
            f"""
            {research}
            ---
            ## {question_type} Question Information
            You have previously forecasted the {question_type} Question to the value: {DataOrganizer.get_readable_prediction(reasoning.prediction_value)}
            This is relevant information for your current forecast, but it is NOT your current forecast, but previous forecasting information that is relevant to your current forecast.
            The reasoning for the {question_type} Question was as such:
            ```
            {reasoning.reasoning}
            ```
            This is absolutely essential: do NOT use this reasoning to re-forecast the {question_type} question.
            """
        )

    def _get_conditional_disclaimer_if_necessary(
        self, question: MetaculusQuestion
    ) -> str:
        if question.conditional_type not in ["yes", "no"]:
            return ""
        return clean_indents(
            """
            As you are given a conditional question with a parent and child, you are to only forecast the **CHILD** question, given the parent question's resolution.
            You never re-forecast the parent question under any circumstances, but you use probabilistic reasoning, strongly considering the parent question's resolution, to forecast the child question.
            """
        )


def caps_for_reasoning(reasoning: str) -> tuple[float, float]:
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
        r"^[ \t\r>#*_-]*AMBIGUITY[ \t\r*_]*:[ \t\r*_]*(HIGH|LOW)[ \t\r*_.!:]*$",
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


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(description="Run the template forecasting bot")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["tournament", "metaculus_cup", "test_questions"],
        default="tournament",
        help="What to forecast on (default: tournament)",
    )
    args = parser.parse_args()
    run_mode: Literal["tournament", "metaculus_cup", "test_questions"] = args.mode

    check_environment(strict=True)
    publish_to_metaculus = True
    print_startup_banner(run_mode, will_publish=publish_to_metaculus)

    # Configure the bot. The `llms=` block below is commented out to use
    # whichever default models forecasting-tools picks based on your env vars;
    # uncomment and edit to pin specific models.
    # ONE client, built before the bot and handed to it. ForecastBot otherwise
    # constructs its own, so anything set here never reached the publish path —
    # which is where the blocking sleeps live. Audit, 2 Sept 2026.
    #
    # sleep_seconds_between_requests defaults to 3.5 and is a BLOCKING
    # time.sleep() inside an async publish method, so it freezes the whole event
    # loop, not just the calling question. Two requests per published question
    # is ~8s of frozen loop each. Metaculus's API is not tight enough at two
    # requests a question for 3.5s to be load-bearing.
    client = MetaculusClient(
        sleep_seconds_between_requests=1.0,
        sleep_jitter_seconds=0.5,
    )

    template_bot = SummerTemplateBot2026(
        metaculus_client=client,
        research_reports_per_question=1,
        predictions_per_research_report=5,
        use_research_summary_to_forecast=False,
        publish_reports_to_metaculus=publish_to_metaculus,
        folder_to_save_reports_to=None,
        skip_previously_forecasted_questions=True,
        extra_metadata_in_explanation=True,
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

    # Dispatch on mode. Each branch produces a list of ForecastReport (or
    # exceptions, since return_exceptions=True) which then flows into the
    # summary printers below.
    seasonal_id = None
    problems: list[str] = []
    if run_mode == "tournament":
        # MINIBENCH FIRST, and the seasonal ID resolved after it. The previous
        # order resolved the seasonal ID up front and raised on an unset
        # AIB_TOURNAMENT_ID — which forfeited MiniBench on every run of the
        # rollover window, a scored series keyed by a slug that survives the
        # rollover and was working perfectly. Audit, 2 Sept 2026.
        #
        # MiniBench gets the same fetch-and-verify as the seasonal half. It had
        # no guard at all before: forecast_on_tournament discards the count, so
        # if the "minibench" slug ever changes it would forecast nothing,
        # silently, green, for as long as nobody looked.
        minibench_questions, minibench_problem = fetch_and_verify_tournament(
            client, client.CURRENT_MINIBENCH_ID, "MiniBench"
        )
        if minibench_problem:
            logger.error(minibench_problem)
            problems.append(minibench_problem)
            minibench_reports = []
        else:
            minibench_reports = asyncio.run(
                template_bot.forecast_questions(
                    minibench_questions, return_exceptions=True
                )
            )

        seasonal_id, seasonal_problem = resolve_seasonal_tournament()
        if seasonal_problem is None:
            seasonal_questions, seasonal_problem = fetch_and_verify_tournament(
                client, seasonal_id, "Seasonal"
            )
        else:
            seasonal_questions = []
        if seasonal_problem:
            logger.error(seasonal_problem)
            problems.append(seasonal_problem)
            seasonal_tournament_reports = []
        else:
            seasonal_tournament_reports = asyncio.run(
                template_bot.forecast_questions(
                    seasonal_questions, return_exceptions=True
                )
            )

        forecast_reports = seasonal_tournament_reports + minibench_reports
    elif run_mode == "metaculus_cup":
        # The Metaculus Cup may be uninitialized near the start of a season
        # (Jan/May/Sep). AXC_2025_TOURNAMENT_ID = 32564 and
        # AI_2027_TOURNAMENT_ID = "ai-2027" are also valid targets here.
        template_bot.skip_previously_forecasted_questions = False
        forecast_reports = asyncio.run(
            template_bot.forecast_on_tournament(
                client.CURRENT_METACULUS_CUP_ID, return_exceptions=True
            )
        )
    elif run_mode == "test_questions":
        # The bot-testing-area tournament contains all question types and is
        # the recommended target for smoke-testing your bot.
        # https://www.metaculus.com/tournament/bot-testing-area/
        template_bot.skip_previously_forecasted_questions = False
        forecast_reports = asyncio.run(
            template_bot.forecast_on_tournament(
                "bot-testing-area", return_exceptions=True
            )
        )

    # Per-mode tournament URL shown in the summary banner footer. The seasonal
    # entry is built from the ID actually forecast rather than hard-coded, so
    # the link cannot drift away from what the bot really did. Metaculus
    # redirects /tournament/<numeric id>/ to the slug (checked 31 Aug 2026).
    TOURNAMENT_URLS = {
        "tournament": f"https://www.metaculus.com/tournament/{seasonal_id}/",
        "metaculus_cup": "https://www.metaculus.com/tournament/metaculus-cup-summer-2025/",
        "test_questions": "https://www.metaculus.com/tournament/bot-testing-area/",
    }

    # raise_errors=False, deliberately. The SDK's default is True and it raises
    # RuntimeError if ANY question errored — which skipped everything below it,
    # including the banner and the season-rollover message. Two independent
    # auditors found this on 1 Sept 2026: the season-rollover message written that
    # morning was unreachable on any run with a single failed question, which
    # on recent evidence is most runs. We decide the exit code ourselves below.
    template_bot.log_report_summary(forecast_reports, raise_errors=False)
    print_run_summary_banner(
        forecast_reports,
        will_publish=publish_to_metaculus,
        tournament_url=TOURNAMENT_URLS.get(run_mode),
    )

    # Exit code, decided here rather than inherited from log_report_summary.
    #
    # The bar for red is deliberately NOT "any question failed". At a run every
    # ten minutes, one flaky question turning the whole run red trains whoever
    # is watching to ignore red — and red is exactly what the season-rollover
    # guard depends on being noticed. So partial failure warns loudly, and only
    # a run that achieved nothing, or a stale season, fails the workflow.
    successes = [r for r in forecast_reports if not isinstance(r, BaseException)]
    failures = [r for r in forecast_reports if isinstance(r, BaseException)]
    if failures:
        logger.warning(
            "%d of %d questions failed; %d forecast successfully.",
            len(failures),
            len(forecast_reports),
            len(successes),
        )

    # Empty research fails the run on a RATE, not on a single instance.
    #
    # Logging alone was not enough: over four months nobody reads an INFO line
    # on a green run, and forecasting from model weights against a training
    # cutoff is worth 3.6x Brier by Metaculus's own evidence. But failing on ONE
    # short research string was worse — Sonar answering "I could not find
    # relevant information" on a niche question is forty characters, and at 144
    # runs a day that manufactures exactly the red-fatigue this file spends a
    # paragraph warning about. A minority of thin research is life; a majority
    # means the researcher is broken. Audit, 2 Sept 2026.
    attempted = len(forecast_reports)
    if EMPTY_RESEARCH_COUNT and attempted:
        rate = EMPTY_RESEARCH_COUNT / attempted
        if EMPTY_RESEARCH_COUNT >= EMPTY_RESEARCH_MIN_TO_FAIL and rate > EMPTY_RESEARCH_FAIL_RATE:
            problems.append(
                f"{EMPTY_RESEARCH_COUNT} of {attempted} questions were forecast "
                "with little or no research. Check the researcher model and the "
                "OpenRouter balance."
            )
        else:
            logger.warning(
                "%d of %d questions had thin research. Below the failure "
                "threshold, so not failing the run.",
                EMPTY_RESEARCH_COUNT,
                attempted,
            )

    if problems:
        raise SystemExit("REFUSING TO PASS: " + " | ".join(problems))
    if forecast_reports and not successes:
        raise SystemExit(
            f"REFUSING TO PASS: all {len(failures)} questions failed and nothing "
            "was submitted. A green tick here would be a lie."
        )
