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

## 2026-08-31 — reliability pass

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
