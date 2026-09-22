"""Test the bot against the community prediction, before a season scores it.

WHY THIS EXISTS
---------------
Seven changes shipped on 22 September 2026 and not one of them has been shown to
improve a forecast. The Fall season is the real measurement, but it does not
report until January. This gives a signal in an afternoon, for about a pound.

It is a DEVELOPMENT PRACTICE, not a bot feature. Nothing here runs during a
tournament, nothing is published to Metaculus, and `main.py` is imported rather
than modified — a broken harness costs a couple of pounds and an hour, and
cannot touch the season. Spring 2026 rates "tests vs community prediction" at
r = +0.17 (q = 0.614, n = 41), but the reason to do it is not the correlation.
It is that we are otherwise flying blind until January.

WHAT IT MEASURES
----------------
Two things, and the second matters more than the first.

1. The SDK's own expected baseline score against the community prediction,
   averaged over the sample. Useful as a single number to watch across runs.

2. THE DIRECTIONAL SPLIT. Of the questions where we came down above 50%, how
   does our probability compare with the crowd's? The measured defect of the
   7-25 September round was that our >50% book said 71% where reality delivered
   50%, and every one of the five confident failures was an institutional
   question where an announcement had been read as completion. If the status quo
   check works, our confident book should now sit CLOSER to the crowd, not
   further from it. That is the number to read.

Beating the community prediction is NOT the goal and should not be read as one.
Peer score is measured against other bots, not against the crowd, and the crowd
on main-site questions is a different and generally stronger population than the
bot field. This is a bias detector, not a league table.

⚠️ THE RULE THIS FILE MUST NOT BREAK
------------------------------------
Metaculus forbids testing on OPEN or UPCOMING tournament questions. Testing on
questions that have already closed, or on the main site, is explicitly allowed.

`MetaculusClient.get_benchmark_questions` filters for open binary questions with
a visible community prediction and thirty or more forecasters — but it does NOT
filter by tournament, so its results can include live AIB questions. Every
question is therefore screened against BOT_TOURNAMENT_SLUG_MARKERS, the same
markers `main.py` already uses to verify a tournament, and anything carrying one
is dropped. A question with no slug metadata at all is ALSO dropped: here,
unlike in the live path, refusing on absent data costs nothing and guessing
could cost prize eligibility.

Run:  python benchmark_vs_cp.py --questions 25
"""

import argparse
import asyncio
import logging
import os
import statistics
import sys

from forecasting_tools import Benchmarker, MetaculusClient

# main.py guards its own entry point, so importing it is safe and is proved so
# by the import-check job in tests.yaml on every push.
from main import (
    BOT_TOURNAMENT_SLUG_MARKERS,
    SummerTemplateBot2026,
    build_llm_config,
    preflight_check_balance,
)

logger = logging.getLogger(__name__)

# Over-fetch, because the tournament screen below throws some away and the API
# returns fewer than asked for more often than not.
OVERFETCH_FACTOR = 3


def is_safe_to_test_on(question) -> bool:
    """True only if this question is provably NOT a live bot-tournament question.

    Fails CLOSED, unlike the equivalent check in main.py. There, refusing on
    missing metadata would turn an API change into a self-inflicted four-month
    outage. Here the cost of refusing is one fewer test question and the cost of
    guessing wrong is prize eligibility, so the asymmetry runs the other way.
    """
    slugs = [s.lower() for s in (getattr(question, "tournament_slugs", None) or [])]
    if not slugs:
        return False
    return not any(marker in slug for slug in slugs for marker in BOT_TOURNAMENT_SLUG_MARKERS)


def summarise(reports) -> None:
    """Print the directional split, which is the reason this script exists."""
    scored = [r for r in reports if getattr(r, "community_prediction", None) is not None]
    if not scored:
        print("\nNo report carried a community prediction. Nothing to compare.")
        return

    print(f"\n{'=' * 72}\nBENCHMARK AGAINST THE COMMUNITY PREDICTION\n{'=' * 72}")
    print(f"Questions scored: {len(scored)}")

    baselines = [r.expected_baseline_score for r in scored if r.expected_baseline_score is not None]
    if baselines:
        print(f"Average expected baseline score: {statistics.mean(baselines):+.1f}")

    high = [r for r in scored if r.prediction > 0.5]
    low = [r for r in scored if r.prediction <= 0.5]

    for label, group in (("we said  >50%", high), ("we said <=50%", low)):
        if not group:
            print(f"\n{label}: no questions")
            continue
        ours = statistics.mean(r.prediction for r in group)
        crowd = statistics.mean(r.community_prediction for r in group)
        gaps = [r.prediction - r.community_prediction for r in group]
        print(
            f"\n{label}:  n={len(group):3d}   ours {ours * 100:5.1f}%   "
            f"crowd {crowd * 100:5.1f}%   mean gap {statistics.mean(gaps) * 100:+5.1f} pts"
        )
        print(f"                 median gap {statistics.median(gaps) * 100:+5.1f} pts   "
              f"worst {max(gaps, key=abs) * 100:+5.1f} pts")

    # THE HEADLINE. In the 7-25 Sept round our >50% book claimed 71% where
    # reality delivered 50%. A positive gap here means we are still more
    # confident than the crowd on the half of the book that was broken.
    if high:
        gap = statistics.mean(r.prediction - r.community_prediction for r in high)
        verdict = (
            "still ABOVE the crowd on our confident book — the defect may persist"
            if gap > 0.05 else
            "BELOW the crowd on our confident book — possibly over-corrected"
            if gap < -0.05 else
            "close to the crowd on our confident book"
        )
        print(f"\nVERDICT: {verdict} ({gap * 100:+.1f} pts).")

    print("\nNot a league table. Peer score is measured against other bots, not")
    print("the crowd, and these are main-site questions. Read the direction of")
    print("the gap, not its sign against zero.")
    print("=" * 72)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="Benchmark the bot against the community prediction")
    parser.add_argument("--questions", type=int, default=25, help="how many to test on (default 25)")
    args = parser.parse_args()

    if not os.environ.get("METACULUS_TOKEN"):
        print("::error::METACULUS_TOKEN is not set; the API refuses unauthenticated calls.")
        return 1

    # Say what the balance is BEFORE spending it. Added 22 Sept 2026 after
    # discovering a $1.75 balance would not have covered the default run — the
    # script would have started, burned what there was, and died part way with
    # nothing to show. preflight_check_balance never raises and never logs the
    # key; it is the same check the live bot runs.
    preflight_check_balance()
    print(
        f"\nAbout to forecast {args.questions} questions at roughly $0.081-0.094 "
        f"each: expect ${args.questions * 0.081:.2f}-${args.questions * 0.094:.2f}. "
        "Stop now if the balance above will not cover it.\n"
    )

    client = MetaculusClient()
    wanted = args.questions
    candidates = client.get_benchmark_questions(
        wanted * OVERFETCH_FACTOR,
        error_if_question_target_missed=False,
    )
    logger.info("Fetched %d candidate questions", len(candidates))

    safe = [q for q in candidates if is_safe_to_test_on(q)]
    dropped = len(candidates) - len(safe)
    logger.info("Dropped %d as tournament or unlabelled; %d remain", dropped, len(safe))

    if len(safe) < wanted:
        print(
            f"::error::only {len(safe)} questions passed the tournament screen, "
            f"{wanted} requested. Refusing to run rather than quietly testing on "
            "a smaller or different sample."
        )
        return 1

    questions = safe[:wanted]
    print(f"\nTesting on {len(questions)} main-site binary questions. None are tournament questions.")
    for q in questions[:5]:
        print(f"  - {q.page_url}  {q.tournament_slugs}")
    if len(questions) > 5:
        print(f"  ... and {len(questions) - 5} more")

    bot = SummerTemplateBot2026(
        metaculus_client=client,
        research_reports_per_question=1,
        predictions_per_research_report=5,
        use_research_summary_to_forecast=False,
        # NEVER publish from this script. It is a test harness, and a published
        # forecast on a main-site question is a public artefact we did not
        # intend and cannot retract.
        publish_reports_to_metaculus=False,
        folder_to_save_reports_to=None,
        skip_previously_forecasted_questions=False,
        extra_metadata_in_explanation=True,
        enable_summarize_research=False,
        llms=build_llm_config(),
    )

    benchmarks = asyncio.run(
        Benchmarker(forecast_bots=[bot], questions_to_use=questions).run_benchmark()
    )

    for benchmark in benchmarks:
        if benchmark.total_cost is not None:
            print(f"\nCost: ${benchmark.total_cost:.2f}   failures: {benchmark.num_failed_forecasts}")
        summarise(benchmark.forecast_reports)

    return 0


if __name__ == "__main__":
    sys.exit(main())
