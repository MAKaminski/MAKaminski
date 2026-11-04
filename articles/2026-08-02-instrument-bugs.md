# Instrument Bugs: When the Code Works and the Numbers Lie

**2026-08-02 · Engineering Log**

I reviewed a week of commits across my active repositories this morning expecting to write
about features. Two projects shipped real ones — a field-sales census app went from empty
repo to production in a day, and a wholesale channel grew a prospecting pipeline. But the
commits I kept re-reading weren't the feature commits. They were the ones where the code had
been working fine and the *numbers* were wrong.

That's a specific and nasty class of defect. A crash gets reported. A 500 gets paged. An
instrument bug just quietly produces a plausible number, and everything downstream — the
dashboard, the prioritization call, the "self-optimizing" loop — inherits the lie with full
confidence. Four of them turned up in one day's work, and they're worth writing down because
none of them would have been caught by a test suite asserting that the code does what the
code says.

---

## 1. The autosave that erased its own completion flag

[YardLine](https://github.com/MAKaminski/YardLine) is a discovery-call tool: a rep works a
list of heavy-truck salvage yards, fills in an eleven-field intake per call, and hits
*Complete discovery*. A `/census` dashboard aggregates the completed intakes.

The intake form autosaved on a debounce, and the autosave issued a **full-row upsert** — every
column, including `completed_at: null`. Which is correct, right up until a debounce timer
fires *after* the rep clicks Complete. Then the autosave writes back the row it captured
before completion and blanks the timestamp.

Nothing errors. The rep sees a saved form. The call disappears from every `/census` metric and
gets re-offered as unfinished work the next morning.

The fix is small — autosave no longer writes `completed_at` / `completed_by` at all, and
`complete()` cancels any pending timer — but the shape of the bug is the interesting part.
A partial-update discipline (write the fields this handler owns, never the whole row) would
have made it unrepresentable. The full-row upsert is the convenient default in almost every
ORM and client library, and it silently gives every writer authority over every column.

Worth noting for anyone chasing something similar: this was verified by reproducing it against
the database, not by reasoning about it. Old path wiped the timestamp, new path didn't. An
instrument bug deserves an instrument-level proof.

## 2. The growth loop optimizing for crawlers

Over on the retail side, a marketing engine weighted its channel mix on link-click events. The
problem: publishing a post makes every platform fetch the link to build a preview card, and
those fetches hit the redirect endpoint exactly the way a human click does.

The overwhelming majority of recorded "clicks" were link-preview crawlers —
`facebookexternalhit`, `Twitterbot`, and friends. The self-optimizing loop was faithfully
tuning the channel mix on **scraper eagerness**.

Three details in the fix I'd repeat:

- **Tag, don't drop.** The redirect has to keep working or previews break, and a crawler
  pickup is genuine signal that the post was seen and unfurled. It's just not *engagement*.
  Two different facts, two different columns.
- **Filter on the raw attribute, not the derived flag.** The analysis filters on user agent
  rather than the new `is_bot` column, so the correction retroactively repairs the events
  captured before tagging existed. Deriving at read time instead of write time is usually the
  wrong default — here it's what made the backfill free.
- **Refuse to answer.** With the crawler traffic removed, human clicks were near zero, and the
  analyzer now declines to recommend a channel mix rather than ranking a 0-vs-0 tie. A
  recommender that never says "insufficient data" is not a recommender; it's a random number
  generator with a UI.

## 3. The denominator that included O'Reilly

YardLine's seeder builds the census population from OpenStreetMap. The brief's raw keyword
list, run against 929 named elements, matched **249 chain auto-parts retailers** — O'Reilly,
NAPA, Advance. None of them are heavy-truck salvage yards.

Nothing would have crashed. The census would have reported a market of ~250 businesses with a
~4% engagement rate, and every conclusion drawn from it would have been wrong by an order of
magnitude — because the error was in the denominator, which nobody looks at twice.

The seeder now runs a strict classifier plus a retail-chain blocklist, enforces the 60-mile
scope *after* geocoding (so directory sources can't smuggle in out-of-region yards), and drops
non-parts businesses outright — a junk-removal service, a wrecker, a scrap-metal recycler.
Twelve yards survived verification against each company's own website. Five candidates were
rejected on inspection, including one that ranks for Atlanta but operates out of Massachusetts.

Twelve verified records beat 249 plausible ones. That's not a moral position, it's arithmetic:
the census's whole output is a ratio, and inflating the denominator with businesses that
structurally cannot convert doesn't make the sample bigger, it makes the metric meaningless.

## 4. Leaving the column NULL

The best call of the day was a non-decision.

Measuring seller concentration in the incumbent's listing index required interpreting an
opaque URL structure. The first read was wrong — a segment I'd taken for a part code turned
out to be a corporate account, with the next segment identifying a branch. Under that reading
two "unrelated dealers" were actually two branches of the same recycler, which is the
difference between a fragmented market and a consolidated one. Precisely the fact the whole
exercise existed to establish.

While it was ambiguous, `published_listing_count` was left NULL, with the reasoning recorded
in the commit: *a wrong concentration figure is worse than none.*

Once ground truth was pulled from live item pages, the numbers landed: 829,525 listings across
97 corporate sellers and 128 locations, with the top five holding 67% and the top twenty 86%.
Consolidated, decisively — and now defensible, because it's anchored to verified seller
identities rather than to a guess about URL structure.

A NULL is an honest data type. A number you can't defend is a liability that outlives the
sprint, because by the time someone acts on it, nobody remembers it was a guess.

---

## The thread

Four bugs, one shape: **the system was measuring something adjacent to what it claimed to
measure.** A completion flag that meant "not overwritten by a race." A click that meant "a
crawler unfurled a preview." A market size that meant "businesses matching a keyword." A
concentration figure that meant "a guess about URL structure."

Every one of them passes code review. The code does what it says. The gap is between what the
number is called and what it counts, and the only reliable way to close it is to state the
claim out loud — *this column means a human being clicked this link* — and then go looking for
the ways that's false.

One more thing worth stealing, from an unglamorous fifth commit: the login page had been
rendering a bare `{}` to users, because the auth provider returns an empty body when the mail
transport fails. Same failure of legibility, aimed at a person instead of a dashboard. Someone
was looking straight at the error and being told nothing.

Instruments that lie are worse than instruments that break. A broken one gets fixed.

---

*Reviewed for this entry: 8 repositories, ~2026-07-26 → 2026-08-02. Substantive engineering in
two; the rest was automated snapshot and stats refresh traffic. Public commit history for the
YardLine items is at [github.com/MAKaminski/YardLine](https://github.com/MAKaminski/YardLine).
Figures from private repositories are described qualitatively by design.*
