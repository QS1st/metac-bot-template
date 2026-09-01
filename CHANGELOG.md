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
