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
    SmartSearcher,
    clean_indents,
    structure_output,
)

dotenv.load_dotenv()
logger = logging.getLogger(__name__)

# Used by caps_for_reasoning(). The upstream template does not import re, and
# a missing import here would raise on EVERY binary question — caught by the
# build check on 1 Sept, which is why that check exists.
import re  # noqa: E402

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
    "researcher": "no_research",
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
    "researcher": "no_research",  # replaced in Phase 2 by real search providers
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
    _structure_output_validation_samples = 1 if USE_FREE_MODELS else 2

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
        reasoning = await self.get_llm("default", "llm").invoke(prompt)
        logger.info(f"Reasoning for URL {question.page_url}: {reasoning}")
        binary_prediction: BinaryPrediction = await structure_output(
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
            Additionally, you may sometimes need to parse a 0% probability. Please do not skip options with 0% but rather make it an entry in your final list with 0% probability.
            """
        )
        reasoning = await self.get_llm("default", "llm").invoke(prompt)
        logger.info(f"Reasoning for URL {question.page_url}: {reasoning}")
        predicted_option_list: PredictedOptionList = await structure_output(
            text_to_structure=reasoning,
            output_type=PredictedOptionList,
            model=self.get_llm("parser", "llm"),
            num_validation_samples=self._structure_output_validation_samples,
            additional_instructions=parsing_instructions,
        )

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
        reasoning = await self.get_llm("default", "llm").invoke(prompt)
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
        percentile_list: list[Percentile] = await structure_output(
            reasoning,
            list[Percentile],
            model=self.get_llm("parser", "llm"),
            additional_instructions=parsing_instructions,
            num_validation_samples=self._structure_output_validation_samples,
        )
        percentile_list = _sorted_percentiles(percentile_list)
        prediction = NumericDistribution.from_question(percentile_list, question)
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
        reasoning = await self.get_llm("default", "llm").invoke(prompt)
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
        date_percentile_list: list[DatePercentile] = await structure_output(
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

    Fails SAFE: a missing, malformed or contradictory flag yields the normal
    caps, so a parsing problem can never silently make the bot MORE confident.
    """
    text = reasoning or ""
    high = re.search(r"AMBIGUITY\s*:\s*HIGH", text, re.IGNORECASE) is not None
    low = re.search(r"AMBIGUITY\s*:\s*LOW", text, re.IGNORECASE) is not None

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


def _sorted_percentiles(percentile_list):
    """Return percentiles sorted by declared percentile, values forced monotonic.

    The Summer template prompts the model to emit percentile values in
    ascending order, but prompting is not a guarantee. A non-monotonic
    distribution is a silent corruption, so we repair it and log loudly.
    """
    ordered = sorted(percentile_list, key=lambda p: p.percentile)
    repaired = False
    running_max = None
    for entry in ordered:
        if running_max is not None and entry.value < running_max:
            entry.value = running_max
            repaired = True
        running_max = entry.value
    if repaired:
        logger.warning(
            "Numeric percentiles arrived non-monotonic and were repaired. "
            "Check the raw reasoning for this question."
        )
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
    template_bot = SummerTemplateBot2026(
        research_reports_per_question=1,
        predictions_per_research_report=5,
        use_research_summary_to_forecast=False,
        publish_reports_to_metaculus=publish_to_metaculus,
        folder_to_save_reports_to=None,
        skip_previously_forecasted_questions=True,
        extra_metadata_in_explanation=True,
        llms=build_llm_config(),
    )
    template_bot.predictions_per_research_report = predictions_per_report()
    preflight_check_free_models()

    # Per-mode tournament URL shown in the summary banner footer. These
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
        minibench_reports = asyncio.run(
            template_bot.forecast_on_tournament(
                client.CURRENT_MINIBENCH_ID, return_exceptions=True
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

    template_bot.log_report_summary(forecast_reports)
    print_run_summary_banner(
        forecast_reports,
        will_publish=publish_to_metaculus,
        tournament_url=TOURNAMENT_URLS.get(run_mode),
    )
