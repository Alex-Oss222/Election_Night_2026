# Election Night 2026 Tracker API

A small live API for the 2026 midterms. It merges AP votes and calls (read from the New York Times results JSON) with Polymarket and Kalshi prices, then projects Senate, House and governor seats. The data sources and projection rules follow the published methodology at [election.offgramercy.com/methodology](https://election.offgramercy.com/methodology). Nothing here is a model: odds are market prices, and calls are AP calls.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/uvicorn tracker.app:app --host 0.0.0.0 --port 8000
```

Interactive docs are served at `http://localhost:8000/docs`. On startup the first NYT pull takes about 2 seconds, Polymarket discovery about 5, and Kalshi discovery 1 to 2 minutes, because Kalshi rate-limits aggressively and discovery probes one event per request. Once discovery is done, Kalshi is refreshed in batches of 100 markets.

## Endpoints

| Endpoint | What it returns |
|---|---|
| `GET /api/health` | Per-source status, last success, last error |
| `GET /api/snapshot` | Everything: control, projections, calls, all 506 races |
| `GET /api/control` | Senate/House control: blended, each book, rounded pills, change since 6 pm ET, NYT needle, AP winner |
| `GET /api/projection/{senate,house,governor}` | Seats by party and tier, toss-ups, unpriced races, whether control can be declared |
| `GET /api/calls?office=senate` | AP calls, newest first |
| `GET /api/races?office=&state=&status=called\|uncalled\|tossup\|reporting&priced=` | Filtered race list |
| `GET /api/races/{key}` | One race with every candidate and each book's raw bid/ask/last. Keys: `S-TX`, `S-OH-special`, `G-GA`, `H-PA-07` |
| `GET /api/races/{key}/history?since=&fidelity=` | Polymarket price history per candidate (default since 6 pm ET) |
| `GET /api/history?limit=` | Recorded control odds and seat projections, one row every 20 s, persisted to `data/history.jsonl` |
| `GET /api/stream?races=false` | Server-sent events, pushed on every change (about once a second during live trading) |

```bash
curl -N localhost:8000/api/stream
```

## Sources and cadence

| Source | Endpoint | Cadence |
|---|---|---|
| AP votes and calls | `static01.nyt.com/elections-assets/pages/data/2026-11-03/results-{senate,house,governor}.json` | 10 s, conditional GET with ETag (the CDN's max-age is 5 s) |
| Polymarket prices | CLOB websocket `ws-subscriptions-clob.polymarket.com` | real time |
| Polymarket markets | Gamma API, `midterms` tag discovery every 10 min, batched slug refresh | 20 s |
| Kalshi prices | Trade API v2 `?tickers=` batches | 5 s |
| 6 pm ET baseline | Polymarket `prices-history` | every 30 min |

Each interval can be overridden with an environment variable: `NYT_INTERVAL`, `POLYMARKET_INTERVAL`, `KALSHI_INTERVAL`, `KALSHI_MIN_GAP`, `HISTORY_EVERY`, `HISTORY_FILE`, `POLYMARKET_WS=0`.

## Rules

These follow the methodology page.

- **Price per book.** Midpoint of bid and ask; last trade if the spread is wider than 10 cents. A settled Kalshi market is 1 or 0.
- **Blend.** Simple mean of Polymarket and Kalshi where both price a party, otherwise whichever exists. Multiple candidates from the same party are summed per book.
- **Tiers.** Safe at 90% and above, likely 75 to 90, lean 60 to 75, toss-up 40 to 60 (toss-ups count for neither side).
- **AP calls.** An AP call replaces the market estimate, so no race is counted twice.
- **Baseline.** The baseline is seats not up tonight, from NYT's `no_election` balance (Senate: 34 Democratic caucus, 31 Republican). The House baseline is 0.
- **Declaring control.** A party is declared only when locked seats plus calls reach NYT's `to_win` figure (Senate D 51, R 50 with the VP; House 218), or when the blended control market is above 99%.
- **Pills.** Largest-remainder rounding to 100, clamped to 1 to 99.

## Known gaps

- **House coverage.** Polymarket lists about 90 House districts. Kalshi's `KXHOUSERACE` series covers most others. The older per-district Kalshi series (`HOUSETX15-26`) are only probed for seats NYT does not rate "solid", to protect the rate limit. Districts with no market show up as `unpriced` and are counted for nobody.
- **Change since 6 pm.** This is computed for chamber control from Polymarket history only, because Kalshi history needs an authenticated key.
- **NYT needle.** It sits idle (`model_status: inactive`) until enough votes are in. Its numbers before then are pre-election baselines.
- **AP lag.** The AP feed can trail network calls by minutes.
- **Independents.** Senate independents keep party `I` in the market data. When NYT reports a caucus, an AP-called winner is counted for that caucus.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest -q
```

The tests run offline against trimmed captures of the real feeds in `tests/fixtures/`.
