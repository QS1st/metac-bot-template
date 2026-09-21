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

## 2026-09-21 (same day, third pass) — the parse paths get guards, and "FIRST" actually comes first

Four changes, batched deliberately so that one paid test run covers the lot.

### 1. Parser guards, ported by hand from forecasting-tools v0.3.0

v0.3.0 (released 7 September) added two guards to *its* copy of the template:
`_create_resolved_question_parsing_message` and
`_create_single_distribution_parsing_message`. Bumping the pinned dependency
would deliver **neither**, because this file vendors its own
`SummerTemplateBot2026` and every `structure_output` call is ours. So they are
ported by hand, condensed to two bullets in a shared `_PARSER_GUARDS` constant,
and wired into all four parse paths.

* **Guard one.** The parser is a cheaper model reading a long reasoning text. If
  the forecaster discusses a figure as though the matter were settled, the parser
  can lift *that* figure instead of the forecast. Every FutureEval question is
  open at forecast time, so a "known outcome" in the text is always the
  forecaster's framing and never a resolution.
* **Guard two.** Drafts, worked examples and sensitivity checks all look like
  final answers. Merging two produces a forecast nobody wrote; averaging them
  produces one nobody would defend. Last complete answer wins — the same rule
  `caps_for_reasoning` already applies to the binary flag.

**The binary path had no parsing instructions at all.** It was the one path
calling `structure_output` with the bare schema and a page of prose, left to
infer which of several percentages was the answer. It has been getting that
right, but on inference alone, on roughly half of every round, and on the path
where the caps live — so a wrong lift produces a confident wrong number rather
than a failed sample. It now gets two bullets of its own plus the shared guards.

### 2. "FIRST, before anything else" was arriving ninth

The 21 September second pass anchored the still-open guard and the adversarial
criteria read on the upstream `Before answering you write:` block. The edit
before it had already inserted four formatting bullets **above** that point, so
the built numeric prompt opened with scientific notation and bin widths, then
said "FIRST, before anything else". An instruction that says *first* and arrives
ninth is not untidy, it is a contradiction, and a model resolves a contradiction
by ignoring one half of it.

The guard is now anchored on upstream text that sits above the formatting block.
No wording changed. A test asserts the ordering, and three more assert that the
bound messages, the formatting block and the guard each appear exactly once —
because the first build of this change emitted the bound messages twice.

### 3. The numeric flag has its own name, and is now logged

The numeric prompt reused the binary literal, `AMBIGUITY`. That is the exact
token `caps_for_reasoning()` matches to clamp a binary probability to
[0.10, 0.90]. Two meanings behind one string in one codebase is a trap for
whoever reads it next, and it made the numeric flag impossible to grep for.

It is now **`FIGURE AMBIGUITY`** — accurate, because on a numeric question the
ambiguity is about *which published figure* is being asked for. The two patterns
cannot match each other: `caps_for_reasoning` anchors its line at `AMBIGUITY`, so
a line beginning `FIGURE` fails the anchor, and the new
`_figure_ambiguity_flag()` requires the word `FIGURE` first. Both directions are
tested.

`_figure_ambiguity_flag()` is **telemetry only**. Nothing acts on its return
value. It exists because the prompt has been asking for the flag while no line of
any run log ever mentioned whether one came back, so there was no way to tell a
model doing the adversarial read from one silently skipping it. The flag remains
**advisory** on numeric questions: the model is asked to widen its own interval
and nothing enforces it. Enforcing it would mean rewriting percentiles after the
fact, which is the `_sorted_percentiles` mistake this project already made once.

### 4. The numeric prompt now labels its resolution criteria

The binary prompt introduces the criteria with a sentence that does two jobs: it
says what the block is, and it states the convention that the criteria have not
yet been satisfied. The numeric prompt dropped the criteria in unlabelled,
between the background and the fine print — and then change 2 above asks for a
strict reading of a block the prompt never named. Binary's sentence is now used
verbatim on numeric, so the two cannot drift. The Spring advice notebook prices
the missing convention directly: bot maker #45 reported losing 90 peer points for
not telling the model to assume the event had not happened yet.

### 5. What the audit of the above caught, before any of it ran

The four changes were written, then audited adversarially against the real
library source. The audit found one blocker of mine and one serious gap, both in
the work above. They are recorded here because this file is the disclosure
record, not a highlights reel.

**Blocker — the binary parsing instruction was wrong.** The first version told
the parser to give the probability "as a percentage from 0 to 100". The field it
fills is `BinaryPrediction.prediction_in_decimal`, whose validator accepts only
[0, 1]; and `STRUCTURE_OUTPUT_ALLOWED_TRIES` is 1, so a rejected parse is a dead
sample rather than a retry. Two failure modes, both new, both on ~half of every
round:

* any obedient value above 1 raises, and because all five samples share one
  prompt and one model the error is correlated — closer to forfeiting the
  question than to losing one sample in five;
* a 1% forecast parsed as `1` is coerced by the validator to 0.999 and then
  clamped by our own caps to 0.98. **A 1% belief published at 98%**, silently,
  with a single warning line in a four-month log.

Upstream deliberately passed *no* instructions here and let the field name do the
work. The fix names the unit the schema wants and gives the conversion. Four
tests now cover it, including one asserting the old wording is absent.

**Serious — "Parse exactly one" is a trap on multiple choice.** On binary and
numeric the answer is one value; on multiple choice it is a list of N, and the
guard sat two lines above "Every option above must appear in your final list."
The wording is now "use only the LAST complete final answer, and use it IN
FULL". More importantly the *code* now checks it: a parsed option set that does
not match the question's options raises, so a truncated list costs a **sample**.
It previously cost the **question**, because
`MultipleChoiceReport.aggregate_predictions` raises on mismatched option names
outside the per-sample gather — the same failure already closed on the numeric
path by forcing `get_cdf()` per sample. The check is wrapped so that a future
library rename degrades to the old behaviour rather than raising on every sample
of every multiple-choice question for four unattended months.

**Also fixed from the same audit:** an empty scoring-grid message left an orphan
`- ` bullet with the line below it pointing at a grid that was not there (the
message now carries its own bullet and the bullet below is self-contained); the
date parser gained the numeric path's "final block only" scoping; the numeric
flag now also matches a hyphenated `FIGURE-AMBIGUITY`; the `(i)`/`(ii)` list no
longer collides with the `(a)`–`(h)` list below it; one vacuous test was replaced
with a real one; and a comment claiming the guards' indentation was load-bearing
was corrected — `clean_indents` takes the deeper of the first two lines' indents
and lstrips anything shallower, so it is readability only.

**Known and accepted:** the numeric prompt now states the still-open convention
twice, once in the labelled criteria sentence and once in the guard. That is
reinforcement of the single convention a bot maker publicly measured at 90 peer
points, and it is left deliberately.

### 6. The second audit found a bot that did nothing at all

**This is the most serious defect this project has produced, and it was already
committed.** It is recorded here in full because that is what this file is for.

The scoring-grid helper added earlier today was inserted into `main.py` by
anchoring on the line `    parser = argparse.ArgumentParser(`. That line sits
**inside** the `if __name__ == "__main__":` suite. Inserting a column-zero `def`
above it ended the suite. Everything from `argparse` to the end of the file —
`check_environment`, the startup banner, the `MetaculusClient`, the bot itself,
the tournament dispatch, the step summary, every `raise SystemExit` — became
unreachable code sitting in the helper's body, after its `return` statements.

`python main.py` would have configured logging and **exited zero**. A green tick
every ten minutes for four months and not one forecast: precisely the "a green
tick here would be a lie" failure this codebase is built to prevent, in code that
could no longer run.

**Nothing caught it.** The patch matched `main.py` byte for byte. The rebuild
diff was clean. All 255 checks passed, because `load()` lifts individual function
nodes and `exec`s them, so dead code compiles perfectly and is never inspected.
The test suite had grown to 255 checks over a bot that did nothing.

**No live run was affected.** The workflow has been disabled since 20 September,
so the broken build was never executed.

The helper is now anchored on a column-zero line. Six new checks read the
module's AST directly: the `__main__` guard must be found exactly once, must run
to the last line of the file, must be the last node, and must still contain
`argparse`, `check_environment`, `MetaculusClient`, `forecast_on_tournament`,
`write_step_summary` and a `raise SystemExit`.

The same pass also found, and this batch fixes:

* **The multiple-choice check compared sets.** `[A, A, B, C]` against `[A, B, C]`
  passed, and the library's separate *length* check then raised outside the
  gather — and if the duplicating sample happened to be `predictions[0]`, every
  good sample failed against it. It is now a sorted-list comparison in a
  module-level helper, and both sides are read inside the same `try` so a library
  rename degrades to silence rather than raising on every sample.
* **The scoring grid lied on log-scaled questions.** `width = range / bins`
  assumes a uniform grid; when `zero_point` is set the bins are geometric. On a
  1–1000 log question the helper stated 4.995 where the true widths ran from
  0.035 to 33.95 — a wrong number feeding the one input the concentrate-or-smooth
  decision rests on. It now says nothing at all on those questions.
* **The bin count was off by one.** `cdf_size` counts CDF *points*; the bins are
  the gaps between them. A 201-point grid is 200 bins. The width was always
  right; only the stated count was wrong.
* **Two tests were passing over dead features.** A mutation test turned the
  multiple-choice `raise` into a `logger.warning` and neutered the binary
  parsing instruction to `additional_instructions=None`; the suite stayed green
  both times, because both checks only grepped the source. The guards check now
  pins the argument's *value*, and the option check is lifted and **executed**
  against correct, truncated, extra, duplicated and renamed option lists. Seven
  mutations were run against the finished batch and all seven were caught.

### 7. Passes three and four: the same bullet, wrong twice more

The batch was audited four times. Each pass found something the one before it
had introduced, which is the honest reason for reporting all four.

**Third pass — the escape hatch reopened the blocker it was written to close.**
Fixing the binary parser meant telling it to divide by a hundred; a bullet was
then added so it would not divide an *already*-decimal answer twice. It read "if
the text already states its final answer as a value between 0 and 1, use that
value unchanged". `1%` and `0.5%` are values between 0 and 1. The bullet meant to
protect the fix reopened the 1%-published-at-98% inversion on exactly the
forecasts where it does most damage.

**Fourth pass — the rewrite still let one input through.** The second version
keyed on the percent sign instead of the size. A model that drops the sign and
writes `Probability: 1` still satisfied it: the library's validator maps exactly
`1` to 0.999 rather than raising, and our caps then publish 0.98. The bullet is
now bounded to decimals *below* 1 and spells out the two bare cases — 73 is 0.73,
1 is 0.01.

Also from those two passes: the `FIGURE AMBIGUITY` telemetry moved above the
parse, because a parser that raises kills the sample before the log line runs and
the log went quiet on exactly the samples worth reading; the flag reader is now
wrapped, since telemetry must never kill a forecast; the multiple-choice and date
prompts got the labelled criteria sentence, because edit 27's evidence was never
numeric-specific; and the option guard now says so in the log when it cannot read
a question's options, rather than opting out in silence.

**Nineteen deliberate mutations** were run across the whole batch. Six survived
the third pass and three the fourth — every one a case where a check asserted
that a line *existed* without asserting what it *said*, so reversing its meaning
stayed green. Reversing "the LAST complete final answer" to "FIRST" — the entire
point of the ported guard — passed 276 checks. All surviving mutations are now
caught.

### Verification

The upstream template was reconstructed locally and confirmed byte-identical
through the existing patch, so this batch was checked with the **real**
rebuild-and-diff rather than the residue heuristic that stands in for it when
upstream is absent. 35 patch edits apply cleanly; `main.py` is byte-identical to
the patch output; **298 checks pass, up from 197**; and nineteen deliberate mutations of today's changes are each caught by them. Nothing here has been run
against a live question yet.

## 2026-09-21 (same day, second pass) — RETRACTION, and the numeric prompt catches up

**The entry below is wrong and this one supersedes it.** It is left in place
because this file is the disclosure record and the retraction is part of it.

**What was wrong.** The entry below claims that probability placed on 0.3 or 1.5
for a count question sits on outcomes that "cannot take" and is thrown away. An
adversarial audit pulled the real parameters of every numeric and discrete
question in the 7–25 September MiniBench round and measured them against the
pinned SDK. Metaculus publishes q45541 — the very question the entry cites — as a
**discrete** question with range −0.5 to 4.5 over five bins of width 1.0, each
centred on an integer. Probability at 0.3 lands in the "0" bin. Probability at
0.6 lands in the "1" bin. Nothing is thrown away.

Measured on q45541, which resolved at 1: our actual output placed **0.365** of
its probability on the outcome, not the 0.0124 an earlier audit had computed
against assumed 201-bin continuous parameters. Straddling there is worth about
**+0.18 nats when right and −0.31 when wrong** — a coin flip that also spends two
of six percentiles inside a single bin.

**Where concentration genuinely wins** is the opposite case: a *numeric*-typed
integer quantity whose bins are **finer** than the grid its source publishes on.
Measured on q45561 (Brazil measles, 29–70 over 200 bins, width 0.205):
**+2.84 nats when right against −0.39 when wrong.** That is roughly one question
in thirty — not the twenty-five in thirty the previous version was about to fire
on, since its new mandatory reasoning step would have answered "yes, whole
numbers" on every discrete question in the round.

**The corrected rule.** The test is not "is it a whole number" but "are the
scoring bins finer than the publication grid". That is computable, so the bot is
now told rather than asked to guess: `_scoring_grid_message()` derives the bin
count and width from the question and states them in the prompt, and the
concentration instruction is explicitly conditional on that comparison. If the
bins are as wide as the published grid or wider, the instruction is to forecast
smoothly. The helper never raises; a missing or odd attribute yields an empty
string and the line is simply omitted.

This also catches a case the whole-number framing missed entirely — a source
that publishes to one decimal place.

**A literal `\n` was shipped into the numeric prompt.** The previous entry's
third bullet ended with a doubled backslash in a non-raw string, so the
cap-at-two-values guardrail rendered mid-sentence behind a stray escape instead
of as its own bullet — in the prompt that runs on about half of every round.
This is the same class as the newline bug of 1 September, mirrored, and every
existing guard was blind to it: the patch and `main.py` agreed, and the tests
asserted the text was *present* rather than correctly *structured*. There is now
a test asserting no literal backslash-n appears in any prompt span.

**The numeric prompt gained the binary prompt's safeguards.** Edit 2 built a
still-open guard, an adversarial resolution-criteria reading with an AMBIGUITY
flag, and an explicit base-rate step — and wired all three into the binary prompt
only. Numeric and discrete were **30 of 59 questions (51%)** in the September
round and 31% of the Spring seasonal tournament, and were being forecast with
none of them. The numeric versions are adapted rather than copied: on a binary
question the criteria decide whether something counts, on a numeric one they
decide *which published figure* counts. Audit evidence for that framing, from
the round's 30 numeric questions: 28 name an explicit source, 27 specify units or
a worked conversion, 23 are cumulative or per-period.

The lettered reasoning list is reordered from (a)–(f) to (a)–(h) so the base rate
anchors before the scenarios rather than after them.

**One honest asymmetry, recorded so nobody assumes parity.** On binary,
`AMBIGUITY: HIGH` is *enforced* in code by `caps_for_reasoning()`. On numeric it
is *advisory* — the model is asked to widen its own interval. Enforcing it would
mean rewriting percentiles after the fact, which this project retracted once
already. Two tests assert the asymmetry deliberately.

**Also fixed:** the numeric parser is now scoped to the final percentile block
and told to ignore base rates, reference-class figures and grid widths, since
those now sit upstream of it in the reasoning; and the date prompt is
deliberately left untouched, since FutureEval uses only binary, numeric, discrete
and multiple-choice, and the round contained no date questions.

---

## 2026-09-21 — whole-number outcomes keep their probability *(SUPERSEDED — see above)*

**The measured loss.** MiniBench question 45541 asked how many SpaceX orbital
launches would occur on 17 September. The answer can only be a whole number. All
five of our samples returned percentiles of the shape `0.0, 0.1, 0.3, 0.6, 1.0,
1.5`, spreading probability across 0.1, 0.3, 0.6 and 1.5 — values the outcome
cannot take. **The question resolved at 1**, which sat at roughly our 80th
percentile where the density is thinnest. Measured against the pinned SDK, our
distribution placed 0.0124 of its mass in the resolution bin; a straddled one
places 0.1021. About 2.1 nats on a single question, under a log-based score.

Independently corroborated: the maker of Laertes (top ten, Spring) posted in the
Metaculus Discord on 19 September that his bot *"bombed this minibench question
because there were 201 bins instead of one for each integer"*. Metaculus
publishes integer-valued quantities as continuous 201-bin questions, and
MiniBench is roughly 45% numeric or discrete.

**The change.** Two bullets added to the numeric forecasting prompt only: name
whole-number quantities explicitly, straddle the one or two most likely whole
numbers with a close pair of percentiles, keep percentiles 10 and 90 as ordinary
wide tails. A third line tells the numeric parser not to round those offsets
away. No code-level percentile processing was added; that decision rests on this
project's own retracted `_sorted_percentiles`, which was a self-inflicted loss.

**On the porcupine question, stated plainly because this file is the disclosure
document.** Metaculus's Spring 2026 analysis describes bots that *"put zero
weight on decimal values and put full weight on integer values, which inflates
their score without being an actual indicator of forecasting accuracy"*, calls
the result a porcupine distribution and a non-skill advantage, and says the
remedy is converting such questions to Discrete. That passage sits in a
methodology section rather than the competition rules, and we could find no
prohibition in the rules themselves.

This change is the same mechanism in a much smaller dose: at most two spikes, on
quantities that genuinely cannot resolve at a fraction, with the tails left
intact. We think that is honest forecasting rather than scoring exploitation — a
human forecaster able to place mass precisely would do the same — but we are not
going to pretend the distinction is invisible from the outside. We will not
scale it toward a full porcupine, and we would welcome these questions being
converted to Discrete, which would make the instruction a no-op.

**Corrected by audit before shipping.** An adversarial pass ran the pinned SDK
and measured the outcomes, which changed two things:

1. The first draft let the model straddle without limit. Measured, spending all
   six percentiles on spikes gains about 1.7 nats when the mode is right and
   loses about 3 when it is not. The instruction is now capped at two whole
   numbers with the tails protected.
2. Our stated reason for avoiding a code fix was **wrong**. We had written that
   SDK issue #212's repair helper nudges repeated percentiles *up*; in the
   pinned 0.2.92 it nudges in-bounds repeats *down*, and repeating actually
   scores better than straddling (0.19 against 0.102). We straddle anyway,
   because that helper has an unmerged fix open since December 2025 and the
   direction could reverse mid-season. Robust rather than optimal, at a measured
   cost of about 0.6 nats per integer question.

Also corrected: a test asserting the instruction was "not in the date prompt"
used `str.index`, which returns the first match, so a stray second copy would
have passed — it now asserts a single occurrence bounded by the numeric
function's span. And `patch_phase1.py` now reads and writes with an explicit
UTF-8 encoding, because this edit introduced the first non-ASCII byte into a
generated file and the build was relying on the runner's locale.

---

## 2026-09-06 — the run verdict on the run page, and a balance preflight

**Observability.** Reading a run meant scrolling roughly two thousand log lines
in a viewer that fights you. `write_step_summary()` now writes a markdown
verdict to `$GITHUB_STEP_SUMMARY`, which renders at the top of the run page:
verdict, mode, model tier, questions found and attempted, submitted, failed,
thin research, balance, and every problem verbatim. It is written *before* the
exit decision, so a red run gets one too — that being the run somebody actually
needs to read. It returns early when the variable is absent and swallows every
exception, because an observability aid must never become a new way to die.

**A balance preflight, which is the one that could have cost a round.** The only
preflight we had returned immediately on every tier except `free` — the one tier
that cannot run out of money. An exhausted OpenRouter balance mid-round means
every prediction 402s, every sample fails, and the questions are forfeited; we
would have learned about it from a red run *after* the three-hour window closed,
and forfeited questions compound because peer scores are summed then squared.
`preflight_check_balance()` now reads `/api/v1/key` once at startup, logs usage
and remaining, and raises a `::warning` below $1. The API key is never logged.
The response shape is not verified against a live key — it lives in GitHub
secrets and is never read locally — so every field is treated as optional and
absence is reported honestly rather than guessed at.

**Three faults found by audit of the above, all closed**

1. The summary said `OK - no open questions this run` on the exact run where
   MiniBench had gone empty — talking over the `::warning` that is our only
   detector for a dead slug. Non-fatal warnings are now collected in
   `RUN_WARNINGS`, printed in the summary under their own heading, and the
   verdict becomes `NOTHING FORECAST - see warnings below`.
2. The summary reported only questions *attempted*, which is zero both when a
   tournament is empty and when everything is already forecast — the normal
   steady state for most runs. `QUESTIONS_FOUND` now records what each half
   actually held, so those two states look different at a glance.
3. A crash before the summary left the run page blank. The workflow gained a
   step that runs only on failure, and only when nothing has been written, to
   name the likely causes.

Also: table cells and bullets are escaped against pipes and newlines (not
reachable today, but silent when it becomes so); the workflow comment said the
sentinel expires on 28 September when the code says the 30th; and the test
harness could not lift annotated assignments, reporting a present constant as
"patch did not apply" — a false alarm indistinguishable from a real build
failure.

The tests for this change are behavioural: they write a real file and read it
back, covering the clean, partial, all-failed, refusing, nothing-forecast and
hostile-character cases.

---

## 2026-09-05 (same day, second pass) — an empty MiniBench is not a broken one

The first live run of the tournament workflow failed:

    REFUSING TO PASS: MiniBench TOURNAMENT NOT FOUND: 'minibench' contains no
    questions at all.

Correct behaviour by the guard, wrong assumption underneath it. MiniBench is a
chain of back-to-back two-week rounds and `minibench` is a slug that repoints to
whichever round is active, so between the repoint and the first question of the
new round it legitimately holds nothing. The 7–25 September round had not been
populated. Treating that as fatal would have reddened every run for two days,
and again in the gap after every future round — permanently, every fortnight.

`fetch_and_verify_tournament` now takes a **keyword-only** `empty_is_fatal`,
defaulting to `True`. The MiniBench call alone passes `False`: an empty
tournament there logs a warning, raises a GitHub `::warning` annotation so it is
visible on a green run without opening the log, and does not fail the run. The
seasonal call is unchanged and still fatal, because a season runs continuously
for four months and empty there does mean broken.

The cost is stated plainly: if the MiniBench slug ever really changed we would
now see a warning rather than a failure. Accepted deliberately — the alternative
guarantees alarm fatigue every fortnight, and red that always fires is the same
as no red at all.

**Three faults found by audit of that fix, all now closed**

1. `empty_is_fatal` was positional, so `fetch_and_verify_tournament(client, id,
   "Seasonal", False)` would silently disarm the season for four months. Made
   keyword-only.
2. `NO_SEASON_EXPIRY` moved from 28 to 30 September. On the 28th *both* settings
   would have been red if Metaculus creates the Fall project before populating
   it — `none` because the sentinel had expired, `33121` because an empty
   seasonal tournament is fatal. We now have direct evidence they do exactly
   that, from MiniBench on this very day. A guaranteed red morning is not a
   safety feature; it is 144 emails, and the obvious way to stop them is to
   disarm the seasonal guard permanently.
3. The tests for this change were five regexes, and all five stayed green
   through the precise refactor they existed to catch — including moving the
   opt-out from the MiniBench call to the seasonal one. Replaced with tests that
   call the function against a stub client, including one asserting the
   positional form now raises `TypeError`.

The non-fatal path also got its own message. It previously reused
`SEASON_MISSING_MESSAGE`, so the log read "NOT FAILING THE RUN: TOURNAMENT NOT
FOUND … that is a wrong or retired ID" — telling the reader it was definitely
broken in the same breath as declining to act.

---

## 2026-09-05 — a declared "no season" state, and its expiry date

**Why.** The Fall 2026 tournament exists (`33121`, `fall-futureeval-2026`) but
opens on 28 September and currently holds zero questions. The MiniBench round
starts 7 September. Between those dates the seasonal half of tournament mode has
nothing to point at, and both available states turned every run red:

- `AIB_TOURNAMENT_ID` unset → `REFUSING TO PASS`.
- `AIB_TOURNAMENT_ID=33121` → `SEASON_MISSING`, because a tournament holding no
  questions is indistinguishable from a retired one.

At a run every ten minutes that is roughly 144 red runs and failure emails a
day, into the single alarm channel this project has. Red that always fires is
the same as no red at all.

**Change.** `AIB_TOURNAMENT_ID` now accepts `none` / `off` / `skip` / `-`
(case-insensitive). The seasonal half is then skipped with a loud log line and
**no** problem raised, MiniBench is forecast as normal, and the run stays green.
The gap is declared rather than inferred: no dates, no silent fallback, and
somebody has to type it. The caller was also corrected to check the id and not
merely the absence of a problem, and the summary banner now shows the MiniBench
URL rather than `.../tournament/None/`.

**The flaw in that change, and the fix.** An independent audit of the above
found it reproduced the exact failure it was written to prevent. Every detector
on the seasonal side — the question-count check, the slug check,
`SEASON_MISSING` — sits *behind* the sentinel, so declaring the gap switches all
of them off. Left set to `none` through the season opening, the bot would have
forecast MiniBench only, green, every ten minutes for four months, with nothing
in the code able to notice. That is the ~150-peer-point failure mode this
project treats as its worst case.

So the sentinel expires: `NO_SEASON_EXPIRY = 2026-09-28`, after which it is
refused and the run goes red. A date guard was retired from this file once
before, on the grounds that it expired into *silence* and left the next rollover
unprotected. This one expires into *noise*, which is the safe direction — firing
late wastes a little attention, failing to fire costs a season. The red is
actionable and self-clearing, it stops the moment the variable is set, and it is
raised at the end of the run so MiniBench is still forecast first.

**Also in this change**
- `SEASON_MISSING_MESSAGE` now takes a per-half remedy. It previously told the
  operator to set `AIB_TOURNAMENT_ID` even when it was MiniBench that failed —
  a variable with nothing to do with the fault.
- Removed a doubled `REFUSING TO PASS:` prefix in the unset message.
- Corrected a claim in two places that the trial tier is "about 8 percent off
  the frontier". Read per question rather than by tournament total, the Summer
  2026 leaderboard puts `claude-fable-5-high` at 6.94 average peer points
  against `gemini-3.5-flash` on 5.03 — roughly a quarter below, not 8 percent.
  The original figure came from ranking bots by tournament total, which is
  average score times questions answered; since the reference bots joined on
  different dates, those totals largely measure coverage rather than skill.

Tests added for every value of the sentinel, its case- and
whitespace-insensitivity, a collision check against real tournament ids and
slugs, the distinction between the sentinel and an unset variable, and the
expiry either side of 28 September against a frozen clock.

---

## 2026-09-02 (settled) — group questions are back on, because it was tested

`check_group_questions.py`, run against the bot-testing-area as a manual
workflow, answered the one question that was holding group questions out:

    9 open question(s): 4 in groups, 5 standalone.
    43329  in group  already forecast: True   (post 43325)
    43330  in group  already forecast: True   (post 43325)
    43323  in group  already forecast: True   (post 43322)
    43324  in group  already forecast: True   (post 43322)

Four for four. Metaculus does populate `my_forecasts` per subquestion, so
`skip_previously_forecasted_questions` holds for unpacked group subquestions and
there is no risk of breaching the one-forecast-per-question rule.
`SKIP_GROUP_QUESTIONS` is now `False` — the switch stays so the decision is
reversible, and the unit tests exercise it in both positions rather than only
the default.

Two things worth recording about how this was settled.

The evidence already existed: earlier Test Bot runs had forecast those group
questions before skipping was introduced, so the check needed no new forecast
and cost nothing. Worth remembering — a question about past behaviour is often
answerable from what already happened.

And the check was delivered as a **workflow, not a command line**. The first
version told Iain to run `poetry run python …`, which assumed a local
development environment he has never had; everything on this project runs
through the Actions tab. Instructions written for the wrong machine are the
documentation equivalent of a test that agrees with itself.

118 unit tests.

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
