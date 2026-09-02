# IBJonny-bot — changelog

Metaculus requires prize-winning bot makers to describe how the bot works **and
any significant updates made during the competition**, and to agree to an
inspection. This file is that record. Every behavioural change goes here, with
the reason and the evidence behind it.

Upstream base: `Metaculus/metac-bot-template`. Our changes are applied by
`patch_phase1.py`, which asserts that each edit matches exactly once, so the
build fails loudly if upstream moves rather than silently producing a
different bot.

---

## 2026-09-02 (fifth audit round) — the deferred list, and one thing above it

An audit of the items previously noted-but-not-fixed. Its first finding was not
on the list, and outranks everything that was.

**The alarm channel had never been tested.** Every guard in this file is a
`SystemExit` that turns one run red. Nothing established that red reaches a
human — and GitHub sends scheduled-workflow notifications to whoever *created*
the workflow, with ownership moving if someone edits the cron or re-enables a
disabled workflow. Confirmed empirically: failure emails do arrive in the
inbox. Recorded because the whole design rests on it, and because it will need
re-checking after the September rollover, when re-enabling the tournament
workflow is exactly the kind of act that reassigns the recipient.

**GROUP QUESTIONS ARE NOW SKIPPED, deliberately, until skipping is proven.**
`skip_previously_forecasted_questions` reads `already_forecasted`, which the
SDK fills from `question_json["my_forecasts"]["history"]`. For an unpacked group
subquestion that json is deep-copied from the group payload, so the field
exists only if Metaculus puts `my_forecasts` on each subquestion. The SDK
patches exactly this for CONDITIONAL questions and does nothing for groups,
which reads like the case was never considered. If it fails open the bot
re-forecasts group subquestions 144 times a day — wasted spend, and a breach of
"only one forecast per question" in bot-only tournaments. A rule breach is not
worth a few extra questions. `check_group_questions.py` settles it in one
read-only run against a live token; `SKIP_GROUP_QUESTIONS` flips back when it
does.

**The heartbeat no longer depends on an undocumented answer.** GitHub documents
the 60-day inactivity rule, and separately documents that `GITHUB_TOKEN` events
"will not create a new workflow run" — but never says whether such a push counts
as repository activity for the clock. Rather than bet on it for a season twice
the length of the clock, the heartbeat now also calls the API to re-enable both
scheduled workflows. Idempotent, free, and correct either way.

**One `MetaculusClient`, shared with the publish path.** `ForecastBot` builds
its own unless handed one, so nothing we set ever reached publishing — which is
where `sleep_seconds_between_requests` lives, as a **blocking** `time.sleep()`
inside an async method that freezes the whole event loop. Now 1.0s rather than
3.5, cutting routine blocking roughly fourfold.

**The ambiguity guard matched no decorated output.** Proven: `**AMBIGUITY:
HIGH**`, `- AMBIGUITY: HIGH`, `### AMBIGUITY: HIGH`, a trailing full stop and
italics all failed to match, and the SEASON tier runs claude-fable-5, which
bolds by habit — so the guard could have sat inert for four months on the only
tier that scores. The character classes now tolerate decoration while keeping
line-anchoring and last-match-wins, so the restated-instruction failure they
were built to defeat still does not trigger. Thirteen cases verified, eleven of
them new tests.

Honestly weighted, though: a miss only fails to *tighten* a forecast, and all
18 observed flags were LOW. This was a tail risk, not the steady bleed the
earlier note implied.

**Also:** `timeout-minutes` 20 → 30, which raises the per-run ceiling from about
16 questions to 32 — pacing at 8/min with no burst is ~37s a question, and a
timeout kill loses most of a batch rather than a tail, because questions are
gathered and finish together. And the existence probe now uses
`group_question_mode="unpack_subquestions"`, so it looks at the same population
as the fetch; `ApiFilter` defaults to `"exclude"`, which could have produced a
false "NOT FOUND" on a tournament whose newest posts were all groups.

**Explicitly left alone:** the `nest_asyncio` dependency. It cannot be removed —
the SDK's own `forecast_on_tournament` calls a synchronous method that runs
`asyncio.run` inside a running loop, which is impossible without the
monkeypatch. Two standing constraints instead of code: do not unpin
`nest-asyncio`, and do not move off Python 3.11 without re-testing.

117 unit tests.

## 2026-09-02 (fourth audit round) — the guard that let a typo through

An independent verification pass on the morning's fixes. It found that the
season guard, twice rewritten, still had the hole it was written to close, and
that requiring `AIB_TOURNAMENT_ID` had quietly created a new one.

**The guard could not tell a typo from a correct ID.** The existence probe asks
"does this tournament have any questions", with no status filter — so any ID
resolving to any real Metaculus project with any posts came back healthy.
Project IDs are dense (32916, 33021, 33022), so a transposed digit usually
lands on *another real project*: green tick, zero seasonal forecasts, four
months. Or worse, forecasts published into a tournament we are not entered in.

- **The tournament must now verify as a BOT tournament.** Every question the API
  returns carries the slugs of the tournaments it belongs to
  (`MetaculusQuestion.tournament_slugs`), so this costs no extra request. A slug
  must contain one of `aib`, `futureeval` or `minibench` — fragments rather than
  names, because Metaculus has renamed the series over time (aibq3, fall-aib-2025,
  summer-futureeval-2026). Fails SAFE on absent metadata: refusing when no slug
  is present would turn an API change into an outage of our own making.

**A regression from the morning: MiniBench was being forfeited.** Requiring
`AIB_TOURNAMENT_ID` meant `resolve_seasonal_tournament()` raised *before* the
MiniBench dispatch, so every run of the rollover window skipped a scored series
that was working perfectly — and `main.py` still carried a comment claiming the
opposite. Now: MiniBench is dispatched **first**, the resolver returns a problem
rather than raising, and problems are collected and raised together at the end.

**MiniBench had no count guard at all.** It went through
`forecast_on_tournament`, which discards the question count — the exact thing
this file calls "the most expensive failure available to us". Both halves now
use the same fetch-and-verify path.

**Empty research now fails on a RATE, not one instance.** The morning's version
reddened the whole run for a single short research string. Sonar answering "I
could not find relevant information" is forty characters; at 144 runs a day
that manufactures precisely the red-fatigue this file spends a paragraph
warning about, and red is the only alarm the project has. Now: three or more
*and* a majority.

**The rate limit had no margin, and the free tier was unsafe.** 10 × 2 = 20
against a limit of 20 is a boundary, not headroom — and retries are correlated
with being at the limit, since a 429 is what triggers one. Worse, the free tier
keeps six retries, so 10 × 6 = 60. The unit test caught it only because it had
been extended that morning to check *both* buckets instead of just the parser.
`PER_MODEL_RPM` is now tier-aware: 3 on free (18 worst case), 8 elsewhere (16).
Every tier now satisfies the invariant, where before it was accidentally true
on one.

**Two overstated claims corrected in place.** The multiple-choice rejection
needs roughly six or more literal zeros *and* a concentrated forecast, not
"seven options every time"; and a comment still described that risk as
unhandled thirty lines below the instruction that handles it.

97 unit tests with the cached upstream, 95 without. The real build check earned
itself twice today: it caught two orphaned edits, and then caught me corrupting
the patch file while mirroring these changes.

## 2026-09-02 (third attempt) — stop guessing at the build, actually run it

The second commit failed the same step for the same reason: the date path's
`get_cdf()` was in `main.py` with no patch entry. The heuristic added an hour
earlier did not catch it, because it is a hand-maintained list of markers and
neither orphaned block happened to contain one. Two of Iain's commits went on
discovering what a real diff shows in a second.

- **The date path now has its own patch edit.** The numeric anchor does not
  reach it; they are separate call sites and need separate entries. 18 edits.
- **The local check now runs the real build.** A copy of upstream `main.py` is
  cached outside the repository, and when `UPSTREAM_MAIN` points at it the test
  suite runs `patch_phase1.py` against it and asserts the output is
  byte-identical to the committed `main.py` — the same thing CI does, minus the
  clone. It reports 18 edits applied and an empty diff.
- The marker heuristic stays as a fallback for CI, where no cached upstream
  exists and the workflow does its own authoritative rebuild a step later.

The lesson is the one this project keeps relearning in different costumes: a
check that approximates the real thing will eventually approximate it wrongly.
Reading the SDK rather than guessing at it found the researcher bug; running
the patch rather than pattern-matching it finds this class. 82 unit tests.

## 2026-09-02 (later) — the build check caught an edit with no patch entry

The commit went red on "The committed main.py must match what the patch
produces". Two divergences, both mine:

- **The multiple-choice parsing instruction had no patch entry.** It was
  rewritten directly in `main.py` and never mirrored, so CI rebuilt a `main.py`
  without it. That is not a cosmetic mismatch: the rebuilt bot would still have
  been telling the parser to emit 0% options — the exact instruction that
  manufactures the forfeit the rewrite was meant to stop. The disclosure
  document would also have been describing a build nobody was running.
- **One blank line too many**, left behind when `season_is_stale` was deleted.

**The real fault was the local check, and that is now fixed.** It verified that
every patch replacement appears in `main.py` — one direction only. It could
never see an edit that exists in `main.py` with nothing in the patch to produce
it, which is precisely what happened. It now also reverse-applies every
replacement and asserts none of our markers survive in the residue: anything
still standing is an edit with no patch entry. Verified by deleting the
multiple-choice edit and watching it fail with `['NEVER emit exactly 0']`, then
restoring it.

A heuristic rather than a proof — CI's rebuild-and-diff remains the authority,
since it clones real upstream — but it runs in a second and catches this class
before a commit rather than after one. Three of the last few cycles were spent
discovering things a local check could have found.

17 patch replacements, 80 unit tests.

## 2026-09-02 (morning) — AIB_TOURNAMENT_ID is now required, closing a hole I had just made

The count-based guard written last night had a gap, found while researching
something else. It probes whether the seasonal tournament contains any
questions, which distinguishes a typo'd ID from a quiet hour — but **not** a
tournament that has finished. Summer still holds 328 questions; they are simply
all closed, so the probe would have reported it healthy. Forgetting to set
`AIB_TOURNAMENT_ID` at all would have produced exactly the four-month silent
failure the old date guard existed to prevent. One gap traded for another.

- **The SDK fallback is removed.** `resolve_seasonal_tournament` no longer
  returns `client.CURRENT_AI_COMPETITION_ID` when the variable is unset; it
  refuses to run and says why. That constant is pinned to Summer 2026 by
  poetry.lock, so the fallback was never a safe default — it was a trap wearing
  a convenience's clothes.
- Between them the two checks now cover both failures with no dates, no ID
  lists and no maintenance: **unset** refuses, **wrong** returns zero questions
  and refuses, **correct** runs. Still true at the Winter 2027 rollover.
- `raises()` in the test harness now catches `SystemExit` as well as
  `Exception`. It inherits from `BaseException`, so the old helper let it
  through and killed the run instead of recording a pass — which is how a guard
  that works can look like a test suite that doesn't.

**Verified, not assumed:** the resources page states bot makers should submit
only one forecast per question in the bot-only tournaments. Our dispatch now
calls `forecast_questions` directly rather than `forecast_on_tournament`, so it
was worth checking the filter survived the change. It does —
`skip_previously_forecasted_questions` is applied inside `forecast_questions`
itself, filtering on `question.already_forecasted`.

**Also settled from the resources page (updated 31 Aug 2026), which closes an
open unknown:** questions are released "at a rate of up to 5 questions at a
time **from either series**… open for 1.5 hours each", and MiniBench rounds run
~60 questions across a fortnight. So a round does **not** open 60 questions at
once. Worst case in any run is about ten questions, roughly seven minutes —
inside both the 10-minute cadence and the 20-minute timeout. The throughput
ceiling flagged in the last audit is not a live problem, and a MiniBench round
costs around $0.25 a day rather than needing the daily cap raised. Questions are
also currently open for **3 hours**, not 1.5, while GitHub Actions latency is
poor.

79 unit tests.

## 2026-09-02 — third audit round: the season guard retired, and three real forfeits closed

Two auditors on the post-fix code. They found that three of my own fixes were
not what this file claimed, and two faults nobody had raised. Corrections
first, since they matter more than the additions.

**RETIRED: the season guard, entirely.** `SEASON_GUARD_DATE`,
`STALE_SEASON_IDS`, `SEASON_STALE_MESSAGE` and `season_is_stale` are gone. The
guard tested what the tournament ID **is** — a fixed date plus a three-item
denylist — rather than what it **does**. A typo in `AIB_TOURNAMENT_ID` walked
straight past it: zero questions, green tick, every ten minutes for four
months. It also expired, so the Winter 2027 rollover had no alarm at all.

Replaced by a count check at the point of use. The seasonal questions are now
fetched explicitly, so the count is visible; and because zero *open* questions
is normal between windows — the cron runs every 10 minutes against a ~90 minute
window — a second query without a status filter distinguishes "quiet hour" from
"dead tournament ID". That probe only runs on the runs that would otherwise
have said nothing. No dates, no ID list, no maintenance, and correct at every
future rollover.

**CORRECTED: the "100% headroom" claim was false for half the traffic.** I
fixed the parser's retry multiplier and left the default model's.
`LLM_ALLOWED_TRIES = 3` meant a worst case of 10 × 3 = **30 requests a minute
against a limit of 20**, on the exact model that produced 87 rejections. Now 2.
And the unit test computed the arithmetic for the parser bucket only — it
asserted the half that passed and was silent on the half that failed. It now
checks both.

**CORRECTED: my reason for not raising on empty research was wrong.** I wrote
that raising would discard samples and forfeit the question.
`research_reports_per_question = 1`, so research runs once per question, not
five times. Empty research is now counted and fails the run at the end. The
forecasts are still published — a partial forecast beats none — but the run
cannot pass unseen.

**NEW: our own parsing instruction was manufacturing forfeits.** The prompt
told the parser to emit 0% options. `PredictedOptionList`'s validator clamps
every option into [0.01, 0.99] and then **rejects the sample** if that clamping
moved anything by more than 0.05 — which happens once a question has seven or
more options, and Metaculus multiple-choice questions routinely have eight to
twelve. Worse, `STRUCTURE_OUTPUT_ALLOWED_TRIES = 1`, set two entries ago,
removed the retry that used to absorb it. Three rejected samples forfeit the
question. The instruction now asks for a 0.01 minimum and a sum of exactly
1.00, and says why. Prompt only, no code — and it is what the retracted
`MC_OPTION_FLOOR` was reaching for and could never have achieved, because the
validator fires before our code sees the object.

**NEW: one bad numeric sample was killing the whole question.**
`NumericReport.aggregate_predictions` expands every sample's CDF in a list
comprehension, so a single raise takes the question down even when four of five
samples were sound — exactly defeating the design recorded here as "discards
one sample and keeps the others". Several checks (CDF spacing, distance from
bounds, log-scale zero point) fire only at expansion, not construction.
`prediction.get_cdf()` is now called inside the per-sample coroutine on both
the numeric and date paths, so a bad sample fails as a sample and the 3-of-5
tolerance works as intended.

**Held up under verification:** the two-bucket architecture, `capacity=1`, the
dead-code removal, `enable_summarize_research`, the workflow variable split,
and no new escaping bugs. One auditor independently confirmed the ambiguity
regex was right to use `[ \t\r]` rather than the `\s` an earlier auditor
suggested, since `\s` matches newlines.

78 unit tests. The caps tests now lift their values from `main.py` instead of
restating them.

**Still open, recorded not fixed:** the ambiguity regex does not match
decorated output (`**AMBIGUITY: HIGH**`, `- AMBIGUITY: HIGH`), and the season
tier runs Claude Fable 5, which formats that way by habit — the guard could sit
inert all season on the tier that matters. The publish path blocks the event
loop with `time.sleep` and retries 400s that can never clear. The bot builds a
second `MetaculusClient`, so client settings do not reach the publish path.
Pacing caps a run at roughly 17 questions before it exceeds its own 10-minute
cadence. `run_bot_on_metaculus_cup.yaml` must be deleted — the tier guard does
not fire for that mode, so enabling it would run nano models on a scored public
board.

## 2026-09-01 (night) — the first clean run, and a measured cost

Test Bot #13 on commit b5491e4, nine questions in the bot-testing-area at full
five-prediction strength. **45 of 45 predictions landed. Zero rate-limit
rejections, zero errors, zero forfeited questions.** The two previous runs
managed 18/45 with 87 rejections and 29/45 with 63.

The ambiguity guard is demonstrably alive rather than silently failing open:
18 flag lines emitted, all `AMBIGUITY: LOW`, and no "flag not found" warnings.
The HIGH path remains unobserved in the wild — the testing area's questions are
genuinely unambiguous — but the machinery works.

Pacing cost 6m16s against 2.5–4.5 minutes before, well inside the 20-minute
timeout. That is the trade we chose.

**Cost, measured rather than extrapolated.** $7.51 → $7.00, and the per-model
ledger splits the roles for the first time now they are on different models:

    Gemini 3.6 Flash (45 forecasts) .... $0.45    88%
    Sonar (9 research calls) ........... $0.05    10%
    gpt-oss-120b (45 parses) ........... $0.01     2%
                                         -----
    9 questions ........................ $0.51

**$0.057 a question.** Forecasting is essentially the whole bill; moving the
parser to gpt-oss-120b was worth it twice over, since 45 parses now cost a
penny. Note the efficiency gain as well as the total: the previous run spent
$0.54 for 29 landed predictions, this one $0.51 for 45 — about 45% cheaper per
prediction that actually counted, because nothing was spent on work that was
then rejected.

What that implies, with the caveat that testing-area questions may be simpler
than tournament ones:

    60-question MiniBench round ........ ~$3.40
    400-question season, trial tier .... ~$23
    400-question season, season tier ... ~$200

The last figure is why the Metaculus credits matter. The middle one is the more
interesting: a full season on the trial configuration is affordable without
them, at roughly 8 percent off the frontier on Metaculus's own leaderboard.
That is a fallback we did not have this morning.

## 2026-09-01 (evening) — two independent audits, and three retractions

Two auditors were briefed separately — one adversarial on cost and rate limits,
one on correctness and silent failure — and told to assume the author was
wrong. They were. Three things recorded in this file as working do not work as
described. The retractions matter more than the fixes, so they come first.

**RETRACTED: the multiple-choice floor never did anything.** `PredictedOptionList`
carries a `model_validator` that runs on every construction and already clamps
each option to `[0.01, 0.99]` — the identical value to our `MC_OPTION_FLOOR` —
before `structure_output` returns. Ours could move a probability by at most
~2e-4. The entry above dated 31 Aug claiming it prevented a −691 event is
wrong; that protection was always in the SDK.

Worse is why it survived. The unit tests fed `(name, probability)` tuples
straight in, which the SDK never produces, and one case (`[("a",0.0),("b",0.0)]`)
the SDK rejects outright on its sum check. The test agreed with itself and
never asked what the library does to the value afterwards — **the exact failure
this project diagnosed in the numeric path on 31 Aug and wrote up as a lesson.**
Both the function and its tests are deleted, with the reasoning kept in place of
the code so the retraction is visible to an inspector.

**RETRACTED: the ambiguity cap was close to inert.** `caps_for_reasoning` used
to require HIGH present and LOW absent anywhere in the text. But the prompt
hands the model both literal strings, and models routinely restate an
instruction before answering it — "I must output either AMBIGUITY: LOW or
AMBIGUITY: HIGH … AMBIGUITY: HIGH". Both present meant normal caps, so the
guard failed OPEN on precisely the questions it exists for, and the old test
asserted that as correct. It now matches a flag on its own line and takes the
last one, which separates the restated instruction from the answer. Six tests
cover the realistic patterns.

**RETRACTED: the season-rollover message was unreachable.** Found independently
by both auditors. `log_report_summary` defaults to `raise_errors=True` and
raises on any failed question, so the banner and the `SEASON_STALE_MESSAGE`
written that morning never executed on a run with a single failure — which,
on recent evidence, is most runs. Now called with `raise_errors=False` and the
exit code decided explicitly.

The bar for a red run is deliberately **not** "any question failed". At a run
every ten minutes, one flaky question turning everything red trains whoever is
watching to ignore red — and red is what the rollover guard depends on being
noticed. Partial failure now warns; only a run that achieved nothing, or a
stale season, fails the workflow.

**Rate limiting, corrected twice over.**

- **Retries live below the gate.** `structure_output` defaults to
  `allowed_tries=3`, and a parser passed as a bare string is wrapped in a
  `GeneralLlm` with `_DEFAULT_ALLOWED_TRIES=2`. One trip through our limiter
  could be **six** requests on the wire, invisible to the bucket. That is a
  better explanation for 87 rejections against 45 acquisitions than the
  burstiness first blamed. The parser is now an explicit `GeneralLlm` with
  `allowed_tries=2`, and `structure_output`'s own loop is pinned at 1.
- **`capacity` is the burst size, not the budget.** At `capacity=15` the library
  fires a full minute's allowance in one instant, then stalls 60 seconds —
  `RefreshingBucketRateLimiter` refills to FULL once emptied. The 60-second
  average held while the instantaneous rate was ~15/second: the very burst shape
  that triggered the throttle. Simulated against the library's own algorithm,
  old versus new: 15 sends at t=0 becomes one send every 6 seconds, worst
  60-second window 10 against a limit of 20.
- **`PER_MODEL_RPM` 15 → 10, `PER_MODEL_BURST` = 1.** Half the observed limit, so
  even every call retrying once stays inside it. 45 calls take 264 seconds
  rather than 120. Time is the thing we have; credit is not.

**A probe can no longer reconfigure the live bot.** Both workflows read the same
`MODEL_TIER` variable, so setting it to `season` for a cost probe would also
have put the 10-minute scheduled tournament on frontier models at ~$0.50 a
question. `test_bot.yaml` now reads `TEST_MODEL_TIER`. Caught before the
tournament workflow was ever enabled.

**An alarm for empty research.** Nothing checked that research returned
anything. If Sonar returns an empty string rather than raising, the prompt
reads "Your research assistant says:" followed by nothing and the bot forecasts
from model weights — 3.6× Brier by Metaculus's own evidence — then exits green.
Now logged as an ERROR. Deliberately not raised: discarding the sample could
forfeit the question, and whether an unresearched forecast beats no forecast is
a judgement about the scoring rule that is recorded as open rather than settled
quietly.

**Also corrected:** an earlier entry said 13 questions were forfeited out of 9.
That counted log lines, not questions, and 13 of 9 is impossible. The measured
trial-tier cost is ~$0.019 a prediction, so a 60-question MiniBench round is
about $5.70 — not the "few dollars" estimated from a price ratio.

67 unit tests. Still open and NOT fixed here, recorded so they are not lost:
the multiple-choice prompt still invites 0% options that the SDK's validator
then rejects; `SEASON_GUARD_DATE` is one-shot and the Winter 2027 rollover has
no alarm; group questions unpack into N independent questions, which no cost
estimate models.

## 2026-09-01 (later) — a wasted call, found by reading instead of paying

Two paid runs had found two errors that were both visible in the source. So
before spending again, every outbound call was traced on paper. That found a
third error, at no cost.

- **`enable_summarize_research=False`.** The SDK summarises research on every
  question — the flag defaults to True — and we then discard the result:
  `forecast_bot.py` line 469 forecasts from
  `summary_report if self.use_research_summary_to_forecast else research`, and
  ours is False. So every question was paying for a summary that was written,
  logged, and ignored. Worse, it was **un-paced** and pointed at the same model
  as the parser, so that model was taking six calls a question against a bucket
  sized for five. It is also the source of the "Could not summarize research"
  warnings. The only thing lost is a summary paragraph in the private note; the
  full reasoning for every prediction is unaffected.
- **Two tests** pin both summariser settings so neither drifts back on.

The full call inventory now reads, per question: one research call (Sonar, its
own model), five forecasts (paced, bucket A), five parses (paced, bucket B),
and nothing else. Each paced model sees exactly 15 a minute against a limit of
20, and the two buckets sit on genuinely different models.

Also checked and recorded, since it had never been examined: the repository is
public and every workflow uses a standard runner, so GitHub Actions is free
with no minute cap — our ~4,300 runs a month cost nothing. GitHub's Actions
terms permit use for "production… of the software project associated with the
repository", which is what this is, and Metaculus ships the template with a
scheduled workflow. Noted honestly: we run a 10-minute cron where the template
ships 20, which is twice the sanctioned burden.

## 2026-09-01 — the second half of the traffic, which the first fix ignored

The trial tier ran and was still rate-limited: 63 rejections, this time on
`new-account-rpm/google/gemini-3.6-flash`. My error, and an instructive one.

It did improve on the season run — 29 predictions landed against 18, four
question errors against nineteen, five forfeitures against thirteen — so the
default-model bucket was working. It just wasn't the whole picture.

**Two mistakes, one cause.** I gated the default model and left `structure_output`
un-paced. Parsing runs **once per prediction**, so it is not a side channel: it
is the same call volume as forecasting. Then I compounded it by putting the
trial tier's default, parser and summariser all on Gemini 3.6 Flash — and since
OpenRouter's throttle is keyed on the *model*, all three roles shared one
20/minute budget. Pacing one of them to 15/min while another spends the same
allowance freely achieves nothing. The log said so plainly: "Could not
summarize research… rate limit exceeded".

- **A second bucket, for the parser**, and a `_structure_output_paced` wrapper
  so all four parse sites go through it. One bucket per model, not one per bot.
- **Trial's parser and summariser moved to `openai/gpt-oss-120b`** —
  $0.037/$0.17 per million, 20x cheaper again, 20 serving endpoints, verified
  live. Now the two buckets map onto two genuinely separate budgets, which is
  what makes them mean anything. The season tier already had this shape by
  accident; it is now deliberate, and tested.
- **`PER_MODEL_RPM` replaces `DEFAULT_MODEL_RPM`.** The old name described the
  limit as belonging to one model, which is exactly the misconception that
  caused this.
- **A near-miss worth recording.** Rewriting `await structure_output(` to the
  paced wrapper also rewrote the wrapper's own body into a call to itself. The
  patch anchor is now `= await structure_output(`, matching the four
  assignments and never the helper's `return`, with a comment saying why.
- **Six more tests**, including the two that would have caught the original
  error: that no un-gated parse sites remain, and that default and parser are
  different models at both paid tiers.

## 2026-08-31 (night, last) — a trial tier, because frontier models are ours to pay for

The measured cost above makes the seasonal configuration unaffordable for any
run Metaculus is not funding: about $30 a MiniBench round, $135–$275 a season,
against a $8.05 balance. Skipping scored rounds entirely until the credits
arrive would mean entering Fall with no scored run behind us, which is the
worse risk. So there is now a fourth tier.

- **`trial`: Gemini 3.6 Flash as default, summariser and parser; Sonar still
  doing the research.** Metaculus's own model leaderboard puts Gemini 3.6 Flash
  at 12.70 against Claude Fable 5 High at 13.77 — about 8 percent off the pace.
  OpenRouter prices them at $0.75/$3.75 and $10/$50 per million tokens, so
  Fable is **13.3x dearer for that 8 percent**. Both figures verified live, the
  pricing against `/api/v1/models` rather than from memory, which is the
  standing rule since the stock template's default researcher 404'd on day one.
- **Research stays on Sonar at every paid tier.** Cutting search is the one
  economy that reliably costs more than it saves — Metaculus's own evidence
  puts it at 3.6x Brier — and a negative total pays nothing under `max(total,0)²`.
- **`trial` counts as tournament-ready; `test` and `free` still do not.**
  `assert_tier_matches_mode` now admits a tier only if it is a genuine
  forecasting configuration: five predictions a question and a live researcher.
  `trial` qualifies on both. A scored run on `trial` logs a warning saying so,
  because it is a deliberate compromise rather than the intended setup.
- **Nine unit tests** cover the tier, including the two that matter most —
  that `test` and `free` remain barred from a scored tournament — plus a guard
  that every tournament-ready tier is actually a valid tier, so the two lists
  cannot drift apart. Verified by running the guard at all five tier values.

Expected effect: a 60-question MiniBench round for a few dollars rather than
about thirty, which brings 7 September within reach of the balance we have.

## 2026-08-31 (night, after the first season-tier run) — rate limiting

The season configuration ran for the first time, against the bot-testing-area.
It had never been exercised before — only reasoned about — and it failed in a
way no amount of reading would have found.

**What happened.** Nine questions retrieved, nine research calls fine (the
`no_research` → `perplexity/sonar` swap works). Then OpenRouter:

    Rate limit exceeded: new-account-rpm/anthropic/claude-5-fable-20260609.
    Rate limit reached: new accounts are limited to 20 requests per minute
    X-RateLimit-Limit: 20   limit_source: openrouter_new_account

87 calls rejected. 18 of 45 predictions landed. 13 questions forfeited by the
SDK's "at least half the samples must succeed" rule — the exact mechanism
documented two entries above, now observed rather than inferred.

**Why, and why the obvious fix would have missed.** The cause is burstiness,
not volume. `_max_concurrent_questions` bounds `run_research` only — noted
earlier today as "fine on paid models with many endpoints", which was true
about endpoints and wrong about rate limits. Once questions clear research
their predictions all fire together, so nine questions at five predictions each
put dozens of calls at one model in the same second. Lowering the question
concurrency would have reduced the burst without bounding it.

- **A shared token bucket now paces every default-model call.** One
  `RefreshingBucketRateLimiter` — the mechanism the upstream template's own
  docstring recommends for this — at `DEFAULT_MODEL_RPM = 15`, capacity 15,
  refresh 0.25/second. Shared across the whole run, because the limit is a
  property of the account, not of a question.
- **All four prompt paths now go through one method**, `_invoke_default_llm`.
  The failure was four call sites each firing as fast as asyncio allowed with
  nothing aware of the others; a gate only works if there is one door.
- **15 a minute against a limit of 20** leaves room for the retries
  `GeneralLlm` makes underneath the gate. Those do not re-acquire, so they are
  invisible to the limiter and can only be left space for.
- **The patch gained `replace_all`**, which states the expected number of call
  sites. If upstream adds a fifth, the build fails rather than quietly pacing
  four of five.
- **Four structural unit tests** assert the invariant directly: no un-gated
  call sites, four gated ones, the acquire happens before the invoke, and the
  pace stays under the observed limit. Behavioural tests would not have caught
  the original bug; this is a property of the file, so the file is what is
  checked. 47 tests now.

**The cost figure, which is the more important result.** $9.93 → $8.05, so
$1.88 for 18 landed predictions and 9 research calls — roughly $0.10 a
prediction, near enough $0.50 a question at five predictions. Rejected calls
are free, so this is a clean unit cost. That extrapolates to about $30 for a
60-question MiniBench round and $135–$275 for a 300–500 question season. The
Metaculus LLM credits are therefore load-bearing, not a convenience, and
Claude Fable 5 as the default is a decision that needs revisiting for any
self-funded run.

## 2026-08-31 (night, last) — the repository variables actually reach the bot

Writing the cost-probe instructions exposed a hole in the rollover fix made
three entries ago. GitHub repository variables are **not** visible to a
workflow's process unless the workflow passes them through explicitly.
`AIB_TOURNAMENT_ID` was being read with `os.environ.get()` by code that would
never have received it: setting the variable would have looked like the fix,
changed nothing, and left the guard failing runs with no way to clear it.

- **`AIB_TOURNAMENT_ID` and `MODEL_TIER` are now passed through** in
  `run_bot_on_tournament.yaml` and `test_bot.yaml` as `${{ vars.NAME }}`. An
  unset variable renders as an empty string, which `main.py` already treats as
  "use the default", so nothing changes until a variable is deliberately set.
- **`MODEL_TIER` can now be set by environment variable** as well as in code,
  validated on the spot against `("free", "test", "season")` so a typo names
  itself at startup rather than surfacing later as a confusing model error.
  This does not weaken `assert_tier_matches_mode`: a scored tournament still
  refuses to run on anything but `season`, whatever the source of the value.
- **`test_bot.yaml` gains `timeout-minutes: 20`**, matching the tournament
  workflow, since a season-tier probe runs five predictions per question
  instead of one.

The reason for the tier override is a cost probe. The Metaculus LLM credits are
tied to the Seasonal tournament and cannot be applied for until Fall is
announced, so any MiniBench trial before then runs on our own OpenRouter
balance. The bot-testing-area holds seven questions, one of each type and
never scored, which makes a bounded way to measure real cost per question at
season-tier models before committing to a round of roughly sixty. Worth noting
that the in-run cost figure cannot be trusted for this: `bot_helpers.py`
suppresses a "does not support cost tracking" warning, so the OpenRouter
account is the only reliable ground truth.

## 2026-08-31 (night, later) — parse validation, and two findings that weren't

Closing out the audit list. One real change; two suspected faults tested and
found harmless, recorded because a cleared suspicion is worth as much as a fix
and stops it being re-investigated later.

**Changed: reasoning is now parsed once, not twice.** The template sets
`_structure_output_validation_samples = 2`, which re-parses the same reasoning
text and raises if the two parses are not exactly equal — `structure_output()`
compares the parsed objects with `!=`. A raise kills that prediction sample,
and the SDK forfeits the **entire question** when fewer than
`required_successful_predictions` (default 0.5) of the five samples survive.
Three unlucky parses therefore lose the question outright, scoring nothing.

The check earns little here: five independent predictions are already
aggregated with `statistics.median`, which outvotes a single bad parse. It also
doubles parser calls and latency inside a 90-minute window, and the failure is
likeliest exactly where it hurts most — multiple choice, where the parser is
told to emit 0% options and two parses of a long option list can differ by one
digit. Set to 1 on every tier.

**Tested and cleared: the two `asyncio.run` calls do not break MiniBench.**
The concern was real in principle. `_concurrency_limiter` is a class-level
`asyncio.Semaphore`, and a semaphore binds itself to the first event loop that
contends it; a second `asyncio.run` creates a second loop, so MiniBench should
have died with `RuntimeError: bound to a different event loop` on every
question past the concurrency limit — silently, since `return_exceptions=True`
swallows it. Reproduced exactly that in isolation: 3 of 60 questions survived.

It does not happen, because `forecasting_tools/__init__.py` calls
`nest_asyncio.apply()` at import, which patches `asyncio.run` to reuse the
running loop rather than create a new one. Re-ran the same reproduction with
`nest_asyncio` applied: one loop across both calls, 60 of 60 questions fine.
Had this been "fixed" on the strength of the first reproduction alone, the
result would have been added risk for no benefit.

**Tested and cleared: the ambiguity cap interacting with median aggregation.**
Each of the five predictions emits its own `AMBIGUITY` flag and has its own
caps applied before the median is taken, so a tightened cap binds only when at
least three of five samples call the question ambiguous. That is a majority
vote, not a bug — and it is more robust than capping after aggregation, since
one flaky flag cannot move the published forecast.

Also noted, not changed: `_max_concurrent_questions` bounds `run_research`
only. The forecasting calls beneath it are unbounded, so peak in-flight LLM
calls at season are nearer 25 than 3. Fine on paid models with many endpoints;
it would not have been on the free tier.

## 2026-08-31 (night) — the season rollover, which nothing would have caught

The last of the audit findings, and the one that could have cost the whole
season without producing a single error.

**What the code actually does.** The seasonal tournament is not something this
repository chooses. It arrives from the forecasting-tools SDK as
`MetaculusClient.CURRENT_AI_COMPETITION_ID`. `poetry.lock` pins that SDK at
0.2.92 and the workflow installs with `poetry install`, which honours the lock,
so the value is frozen. Read at the 0.2.92 version-bump commit and again at
upstream `main`:

    FE_SUMMER_2026_ID         = 33022   # summer-futureeval-2026
    CURRENT_AI_COMPETITION_ID = FE_SUMMER_2026_ID
    CURRENT_MINIBENCH_ID      = "minibench"

Metaculus has published no Fall 2026 ID, in the SDK or on the site. Seasons
start every January, May and September; the Summer tournament stops posting
questions in early September and Fall opens on 28 September.

**Why it is silent.** `get_all_open_questions_from_tournament()` filters on
`allowed_tournaments=[id]` with status `open` and returns whatever comes back.
A finished tournament returns zero questions. No exception, no warning, and the
run exits green. A season runs about four months, so the bot would have
forecast on nothing until Christmas while every scheduled run showed a tick.
Ten green test runs proved nothing about this, because in August the Summer ID
is still correct.

- **The seasonal tournament is now resolved through `AIB_TOURNAMENT_ID`**, a
  repository variable, falling back to the SDK constant. A variable rather than
  a constant because the Fall ID is not knowable today and mid-season is a poor
  time to be editing, testing and redeploying code.
- **A dated guard turns silence into a red workflow.** From 21 September, a
  seasonal target still equal to the Summer tournament is treated as a
  misconfiguration: the seasonal half is skipped, the reason is logged, and the
  run exits non-zero. GitHub then reports a failure every ten minutes until
  somebody sets the variable, which is the intended level of nuisance. The date
  is a week before Fall opens rather than the day Summer ends, because in the
  gap between the two there is no Fall ID to set and an alarm nobody can act on
  is one people learn to ignore. Aiming at a finished tournament during that
  gap is free: zero questions means zero model calls.
- **MiniBench is deliberately still forecast** in that state. Its ID is the
  slug `"minibench"`, which survives the rollover, so failing early would have
  turned one misconfiguration into two forfeited tournaments. The guard returns
  a boolean and the run fails at the very end, after the banner.
- **The banner's tournament link is now derived from the ID actually used**
  rather than hard-coded to the Summer URL, so it cannot describe a tournament
  the bot did not forecast. Metaculus redirects `/tournament/<numeric id>/` to
  the slug, checked live.

Fourteen unit tests cover it, and the harness now lifts `SEASON_GUARD_DATE` and
`STALE_SEASON_IDS` out of `main.py` instead of restating them — a test that
hard-codes what it expects the code to say can agree with itself while
disagreeing with the file, which is precisely how the old `_sorted_percentiles`
test came to assert the wrong behaviour.

Also corrected: a comment dated a build check to "1 Sept", a date that had not
happened yet.

## 2026-08-31 (evening) — adversarial audit, and four season-ending fixes

Two independent auditors were briefed to find ways this bot loses points, and
told to read the rules and scoring before looking at the code. They found
things ten green runs had hidden. Recorded in full because the disclosure
requirement asks for significant changes, and because the errors are more
instructive than the successes.

- **The season configuration did no research at all.** `SEASON_MODELS` carried
  `"researcher": "no_research"` — a placeholder added to simplify smoke-testing
  and then copied into the season tier. It would have entered a tournament of
  300–500 near-term news questions forecasting from model weights alone, with
  the prompt still saying "Your research assistant says:" followed by nothing.
  Metaculus's own evidence puts the cost of removing search at 3.6× Brier.
  The likely outcome was a negative total, which under `max(total, 0)²` pays
  nothing at all. **Now `perplexity/sonar`** — live web search on the existing
  OpenRouter key, about $3 for a full season.
- **A refusal to run the tournament on a testing configuration.** `MODEL_TIER`
  is one string; left on `"test"`, the season would have run a nano model at
  one prediction instead of five, and still exited green. `assert_tier_matches_mode`
  now refuses to forecast a scored tournament unless the tier is `season` and a
  researcher is configured. A comment is not a safeguard.
- **A floor on multiple-choice options.** Binary was floored on day one;
  multiple choice never was, and it is where Metaculus's analysis says bots
  lose most ground to humans. The upstream parser is explicitly instructed to
  emit 0% options — and a 0% option that resolves scores about −691, against
  typical per-question scores of ±10–30. One of those erases thirty good
  questions. Options are now floored at 1%, with the remaining mass shared
  proportionally among the rest. Naive flooring-then-renormalising does not
  work: rescaling pushes the floored options straight back under the floor.
  The unit test caught that on the first attempt.
- **Corrupt numeric samples are now rejected, not "repaired".** The previous
  behaviour forced non-monotonic percentiles into monotonic ones, which on a
  fully reversed parse produced a near point-mass at the wrong end of the
  range — published with confidence. The audit demonstrated it. Rejecting
  discards one sample and keeps the others, which is what the library does
  natively. A safe failure had been converted into a confident wrong answer,
  and the original unit test asserted that as correct.

Also: **`patch_phase1.py` is now actually in the repository.** This changelog
previously described a build process using a file that had never been
committed — a false statement in the document an inspector reads. And
**`.github/workflows/tests.yaml`** now runs the unit tests, rebuilds `main.py`
from the patch, and fails if the committed file does not match. Until today
nothing ran the tests at all.

## 2026-08-31 — resolution forensics, and ambiguity-bounded confidence

The second pillar, replacing the reference-class engine the audit killed. This
one needs no special data access — only careful reading.

**The problem.** Peer score is brutally asymmetric: moving 99% to 99.9% gains
0.009 when right and costs 2.3 when wrong, so the expensive error is
confident-and-wrong. A systematic source of that is not misjudging the world
but answering a different question from the one asked. Seen live in the
Metaculus bot Discord on 29 August: *"a lot of bots including mine
misinterpreted this question... interpreting as 'July is the annual max'
instead of 'July is a NEW annual max'."* A whole cohort of bots, one word.

- **Forensics stage added ahead of everything else in the binary prompt.** The
  model must state the strictest reasonable reading of the criteria as a
  precise test, then any other reading a careful person might take, and say
  explicitly whether those readings would resolve differently.
- **Ambiguity now bounds confidence.** The model emits `AMBIGUITY: LOW|HIGH`;
  HIGH tightens the caps from 0.02–0.98 to 0.10–0.90. Uncertainty about the
  world belongs in the probability. Uncertainty about *the question* is
  different, and the bot is not entitled to confidence in the face of it.
- **Fails safe by construction.** A missing, malformed or self-contradictory
  flag yields the normal caps, so a parsing fault can never make the bot *more*
  confident. Eleven unit tests cover it, including near-misses like the bare
  word "ambiguity" and the phrase "not ambiguous".

Note on novelty: at least one other entrant (Ora) performs resolution forensics
to *inform* its forecast. Using interpretation ambiguity to *bound* confidence
is the part we have not seen described, and it follows directly from the
scoring rule rather than from intuition.

## 2026-08-31 (later still) — run-time budget

Everything here is aimed at one fact: tournament questions are open for
**1.5 hours**, launch at random hours, and arrive **up to five at a time**. A
run that overruns scores zero on every question it didn't reach, and because
the season total is squared, misses compound. The top open-source bot's author
attributes ~150 forfeited points — most of a placing tier — to missed
questions, none of it a forecasting problem.

- **Questions processed concurrently raised from 1 to 3** (still 1 on the free
  tier). One-at-a-time is right for a rate-limited shared pool and wrong for a
  90-minute window containing five questions. Serial worst case in-season is
  roughly five questions × five predictions × ~30s ≈ 12 minutes; at three
  concurrent that becomes 4–5. Safe now only because paid models have real
  capacity and many endpoints.
- **Retries cut from 6 to 3, and the per-call timeout from 120s to 90s** on the
  paid tiers. This is the "never retry a slow failure" rule: retrying a timeout
  multiplies the wait rather than fixing anything. The old settings meant a
  single stubborn call could burn **twelve minutes** on its own; it is now
  4m30s. Free-tier 429s are transient and still get six tries.
- **`timeout-minutes: 20` on the forecasting job.** A hung run would otherwise
  hold the concurrency group for GitHub's six-hour default, silently blocking
  every subsequent run and costing an afternoon of questions. A healthy full
  pass takes about three minutes.

## 2026-08-31 (later) — abandoned the free tier for development

Four runs died on free models across three separate providers: a 429 from
Google AI Studio's pool, a model that 404'd mid-run at Nvidia, OpenRouter's
50-request daily ceiling, and a 429 from Decart. That is structural rather
than unlucky, and the endpoint API shows why:

    z-ai/glm-5.2:free ........  1 endpoint    (Decart)
    nvidia/nemotron:free .....  1 endpoint    (Nvidia)
    google/gemma-4-31b:free ..  1 endpoint    (Google AI Studio)
    openai/gpt-5-nano ........  4 endpoints   (OpenAI, Azure)
    openai/gpt-oss-120b ...... 20 endpoints   (AkashML, CoreWeave, DeepInfra,
                                               Novita, SiliconFlow, Google, …)

Every free model has exactly one serving endpoint on one provider's shared
pool. No failover, and the pool is shared globally, so it rate-limits under
any sustained load. Paid models have many endpoints and OpenRouter routes
around dead ones.

- **Configuration restructured into three tiers** — `free` (kept for
  reference, not recommended), `test` (cheap paid models, what we develop
  against), `season` (frontier models on Metaculus's credits). Selected by a
  single `MODEL_TIER` constant, which raises a clear error if misspelled.
- **Test tier: `openai/gpt-5-nano` as default, `openai/gpt-oss-120b` for
  parsing and summarising.** Chosen for endpoint count as much as price.
  Prices verified live: $0.05/$0.40 and $0.037/$0.17 per million tokens, so a
  seven-question smoke test costs under two pence.
- The endpoint preflight now runs only on the free tier, where a single
  endpoint status is actually the whole story.

## 2026-08-31 — free-model providers swapped, endpoint preflight added

- **Default model moved to `z-ai/glm-5.2:free` (Decart); parser and summariser
  stay on `minimax/minimax-m3:free` (GMICloud).** Two independent providers,
  neither of which has failed us. We have now lost runs to Nvidia (a model that
  404'd mid-run while still listed and still reporting healthy) and to Google AI
  Studio (a 429 from its shared free pool), so both are avoided.
- **Every free model on OpenRouter has exactly one serving endpoint.** There is
  no failover, which is why free-tier outages are total rather than degraded.
  Worth knowing before trusting one for anything that matters.
- **Added a startup preflight** that queries each configured free model's
  endpoint status and logs it. Deliberately warn-only: a health check should not
  become a new way for the run to die, and "healthy" has already proved not to
  guarantee availability. Its value is putting a provider outage at the top of
  the log instead of leaving it to be inferred from a wall of 404s.

## 2026-08-30 (later) — reliability pass

- **Forecast cadence raised from every 20 minutes to every 10.** Tournament
  questions open at random hours and stay open for only 1.5 hours (temporarily
  3 while GitHub Actions latency is poor). GitHub's cron is imprecise and drops
  runs under load. The top open-source bot in the Fall 2025 season attributes
  roughly 150 forfeited peer points — most of a placing tier — to missed
  questions caused by exactly this. Cadence is the cheapest available mitigation.
  Deliberately still a *single* workflow: two overlapping workflows could each
  see a question as unforecast and submit twice, which would breach the
  one-forecast-per-question rule.
- **Added a weekly heartbeat workflow.** GitHub disables scheduled workflows
  after 60 days without repository *activity*, and workflow runs do not count —
  only commits do. A season runs about four months. Without this, a perfectly
  healthy bot would go silent around day 60 with no error and no notification.
- **Started this changelog**, for the prize-eligibility disclosure requirement.

## 2026-08-30 — free-tier tuning

- Free-tier runs reduced to **one prediction per question** (from five) and
  **one parse-validation sample** (from two). OpenRouter's free tier allows 50
  requests per day in total; five predictions plus their parses exhaust it
  within two questions. The season configuration is unchanged at five.
- Parser and summariser moved to a **different upstream provider** from the
  default model, after a run died on a 429 from Google AI Studio's shared free
  pool. One provider should not be a single point of failure.

## 2026-08-29 — initial configuration

- **Every model named explicitly.** With no `llms=` block the library picks
  defaults, one of which is an OpenAI search-preview model that OpenRouter does
  not serve — so the stock template plus an OpenRouter key 404s on every
  question. All model IDs are verified against OpenRouter's live model list
  before use, because its free tier rotates without notice.
- **Binary predictions capped at 0.02–0.98** (template default 0.01–0.99).
  Justified by the scoring rule rather than taste: peer score is logarithmic
  against the geometric mean of other bots, so moving 99% to 99.9% gains 0.009
  when right and costs 2.3 when wrong.
- **Open-question guard added to the binary prompt.** A recurring, expensive
  failure among entrants is a bot reading news that resembles the outcome,
  concluding the question has already resolved, and forecasting near-certainty
  on a question that is still open and can still move.
- **Explicit base-rate step added to the binary prompt.** Rigorous base-rate
  calculation was reported by 40% of the top fifteen bots in the Fall 2025
  survey against 7% of the bottom half.
- **Numeric percentiles forced monotonic in code** (`_sorted_percentiles`), not
  by prompting. Percentiles arriving out of order silently corrupt the
  distribution. Repairs are logged as warnings. Nine unit tests cover it.

---

## Reverted, and why — kept deliberately

- **2026-08-30, reverted 2026-08-31: forcing reasoning comments public.**
  Added on the reasoning that the rules require "a comment response under every
  single question" while the bot's profile showed zero public comments.
  This was wrong. Metaculus's bot resources notebook states: *"We request that
  bots use private notes as their comment type… We will convert these private
  notes into public comments after questions close weekly."* Private notes are
  the compliant comment, and Metaculus publishes them itself after close so
  bots cannot read each other's reasoning while questions are open. The stock
  template was correct throughout.
  The error came from trusting `/aib/contest-rules/`, which is 2024 text, over
  the resources notebook, which is the operative document.
