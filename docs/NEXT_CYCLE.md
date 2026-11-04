# Running the tracker for 2028

This tracker was built for the 2026 midterms. Most of it carries over. This file lists what changes on its own, what to check before the night, and what 2028 needs that 2026 didn't.

## 1. Make a 2028 copy

Make a new repository from this one, for example `Election_Night_2028`. On GitHub, the simplest way is to tick **Template repository** under this repo's **Settings → General**, then use **Use this template** to create the new one. Deploy it on Railway the same way as 2026:

1. Create a new project from the new repository.
2. Generate a domain using the port shown in the deploy log line `Uvicorn running on http://0.0.0.0:____`.
3. Keep it at 1 replica.

## 2. Set one variable

Set `ELECTION_DATE=2028-11-07`. That is Tuesday, November 7, 2028. Either set it in Railway's Variables tab or change the default in `tracker/config.py`.

Everything below follows from that date, and `tests/test_tracker.py` checks the time calculation:

| What | 2026 value | 2028 value |
|---|---|---|
| NYT results feed folder | `.../pages/data/2026-11-03/` | `.../pages/data/2028-11-07/` |
| Polymarket slugs | `which-party-will-win-the-senate-in-2026` | `...-in-2028` |
| Kalshi control tickers | `CONTROLS-2026`, `CONTROLH-2026` | `CONTROLS-2028`, `CONTROLH-2028` |
| Kalshi race tickers | `SENATETX-26`, `KXHOUSERACE-WV02-26` | `SENATETX-28`, `KXHOUSERACE-WV02-28` |
| "Since 6 pm ET" baseline | `2026-11-03T23:00:00Z` | `2028-11-07T23:00:00Z` (after DST ends) |
| UF early-vote file | `.../earlyvote/2026/US.csv` | `.../earlyvote/2028/US.csv` |
| Page and API title | Election Night 2026 | Election Night 2028 |

## 3. Check before the night

Each of these was true in 2026. Nothing guarantees it in 2028, so check each one against the live sources a few days ahead.

- **NYT feed URL and shape.** NYT published `results-{senate,house,governor}.json` at the URL above. Confirm the 2028 files exist (they appeared with zero votes before polls closed). Then capture fresh copies into `tests/fixtures/` and run the tests, because the parser in `tracker/nyt.py` depends on field names such as `party_caucus`, `eevp` and `no_election`.
- **Polymarket discovery tag.** Discovery used the `midterms` tag. A presidential year will almost certainly use a different one. Find it on Polymarket and set `POLYMARKET_TAG`.
- **Market slug and ticker patterns.** The year is now filled in automatically, but the naming schemes themselves can change. In particular, the balance-of-power slug ended in `-midterms`. Check `tracker/markets.py` against a few real 2028 markets.
- **Special elections.** The 2026 specials (FL and OH Senate) were one-offs. Specials in 2028 come from whatever vacancies exist then. Race keys get `-special` automatically from the NYT feed, but the Kalshi special tickers (`SENATE{ST}S-28`) need checking.
- **Kalshi key.** Only needed for real-time Kalshi prices. A read-only key worked in 2026. The page doesn't show odds anyway.

## 4. Build what 2028 needs

In order of importance:

1. **The presidential race.** This is the biggest gap: nothing in the tracker handles it yet. It needs:
   - the NYT president feed added to `NYT_FEEDS` in `tracker/config.py` (probably `results-president.json`; check);
   - parsing in `tracker/nyt.py` with electoral votes per state (538 total, 270 to win). Maine and Nebraska also award votes by congressional district;
   - a President tab on the page: a state map colored by vote leader and labeled with electoral votes, an electoral-vote bar with a 270 marker, and calls.

   The 2028 electoral votes use the same 2020-census apportionment as 2024.
2. **House district map.** The House tab maps states, not districts, because 2026 boundaries changed mid-decade in several states. A district map needs a boundary file that matches the 2028 lines.
3. **Senate and governors.** 2028 is a Class 3 Senate year (34 regular seats), with governor races in about 11 states. The code takes whatever races the feed lists, and the seats-not-up baseline comes from NYT's `no_election` counts, so these should need no code changes. Check the bar's numbers on the night anyway.

## 5. Licensing, if the site is public

The NYT results JSON isn't licensed for redistribution. For a public 2028 site, the licensed sources are the AP Elections API and Decision Desk HQ, both paid. Switching means replacing `tracker/nyt.py` only. The rest of the app works from its race dicts (`key`, `candidates`, `total_votes`, `eevp`, `called`, `winner_party`, `flip`), so the new parser just has to produce the same shape.

## 6. Odds are optional

The results page shows no odds. The Polymarket and Kalshi code still runs and still feeds `/api/control`, `/api/projection` and `/api/snapshot`. If 2028 doesn't need odds, removing `tracker/markets.py`, `tracker/model.py`, `tracker/kalshi_auth.py` and their loops in `tracker/engine.py` removes most of the rate-limit and key trouble from 2026.
