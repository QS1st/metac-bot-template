"""
Settle ONE open question: does the SDK report group subquestions as already
forecast?

WHY THIS MATTERS
----------------
`skip_previously_forecasted_questions=True` is the only thing stopping the
10-minute cron re-forecasting the same question all season. It reads
`question.already_forecasted`, which the SDK fills from

    question_json["my_forecasts"]["history"]

For an unpacked GROUP subquestion, that json is deep-copied straight out of the
group payload, so the field is only present if Metaculus puts `my_forecasts` on
each subquestion. Nobody knows whether it does. The SDK explicitly patches this
for CONDITIONAL questions and does nothing equivalent for groups, which reads
like the case was never considered.

If it fails open, the bot re-forecasts group subquestions 144 times a day —
wasted money, and a breach of Metaculus's "only one forecast per question" rule
for bot-only tournaments. So until this is settled, `main.py` skips group
questions entirely (`SKIP_GROUP_QUESTIONS = True`).

HOW TO RUN
----------
This reads only. It posts nothing and forecasts nothing.

It needs a Metaculus token in the environment, and it is only meaningful AFTER
the bot has forecast at least once on the tournament you point it at.

    cd <the repo>
    export METACULUS_TOKEN=...        # or put it in .env
    poetry run python check_group_questions.py                  # bot-testing-area
    poetry run python check_group_questions.py minibench        # or a tournament

READING THE RESULT
------------------
The bot-testing-area contains group questions by design, which is why it is the
default: you do not have to wait for a tournament to have one.

  * "SKIPPING WORKS"  — every group subquestion you have already forecast is
    reported as already_forecasted. Set SKIP_GROUP_QUESTIONS = False in main.py
    and group questions come back into play.

  * "SKIPPING FAILS OPEN" — at least one group subquestion you have forecast is
    reported as NOT already forecast. Leave SKIP_GROUP_QUESTIONS = True. Do not
    be tempted: this is the rule-breach case.

  * "INCONCLUSIVE" — no group subquestions were found, or none had been
    forecast yet. Run the bot once against this tournament, then try again.
"""

import sys

import dotenv

dotenv.load_dotenv()

from forecasting_tools import MetaculusClient  # noqa: E402


def main() -> int:
    tournament = sys.argv[1] if len(sys.argv) > 1 else "bot-testing-area"
    print(f"Reading open questions from {tournament!r}...\n")

    questions = MetaculusClient().get_all_open_questions_from_tournament(tournament)
    if not questions:
        print("No open questions returned. Nothing to check.")
        return 2

    groups, singles = [], []
    for q in questions:
        (groups if getattr(q, "question_ids_of_group", None) is not None else singles).append(q)

    print(f"{len(questions)} open question(s): {len(groups)} in groups, {len(singles)} standalone.\n")
    print(f"{'id':>10}  {'in group':>8}  {'already forecast':>16}  url")
    for q in questions:
        in_group = getattr(q, "question_ids_of_group", None) is not None
        print(
            f"{str(q.id_of_question):>10}  {str(in_group):>8}  "
            f"{str(bool(q.already_forecasted)):>16}  {q.page_url}"
        )

    print()
    if not groups:
        print("INCONCLUSIVE — no group subquestions here. Try another tournament.")
        return 2

    forecasted_singles = [q for q in singles if q.already_forecasted]
    forecasted_groups = [q for q in groups if q.already_forecasted]

    if not forecasted_singles and not forecasted_groups:
        print("INCONCLUSIVE — nothing here has been forecast yet.")
        print("Run the bot once against this tournament, then run this again.")
        return 2

    if forecasted_groups:
        print("SKIPPING WORKS.")
        print(
            f"{len(forecasted_groups)} group subquestion(s) report already_forecasted=True, "
            "so the SDK does populate my_forecasts for them."
        )
        print("You can set SKIP_GROUP_QUESTIONS = False in main.py.")
        return 0

    print("SKIPPING FAILS OPEN — or is still inconclusive. Read carefully:")
    print(
        f"  {len(forecasted_singles)} standalone question(s) report already_forecasted=True, "
        f"but ZERO of the {len(groups)} group subquestion(s) do."
    )
    print(
        "  If the bot HAS forecast those group subquestions, this is the "
        "rule-breach case: leave SKIP_GROUP_QUESTIONS = True."
    )
    print(
        "  If it has not (because they were skipped, which is the current "
        "behaviour), this proves nothing yet — set SKIP_GROUP_QUESTIONS = False "
        "temporarily, run the bot once against this tournament, set it back, "
        "and run this check again."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
