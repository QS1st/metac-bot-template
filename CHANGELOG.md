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
