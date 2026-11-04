# Election Night Tracker

A live results site and API, built for the 2026 midterms. It reads AP votes and calls from the New York Times results JSON and serves a results page modeled on Google's election results. The API also blends Polymarket and Kalshi prices into seat projections, following the methodology at [election.offgramercy.com/methodology](https://election.offgramercy.com/methodology). Nothing here is a model: odds are market prices, and calls are AP calls.

The 2026 site runs at https://electionnight2026-production.up.railway.app. To run it for another election, set `ELECTION_DATE` and follow [docs/NEXT_CYCLE.md](docs/NEXT_CYCLE.md).

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/uvicorn tracker.app:app --host 0.0.0.0 --port 8000
```

Interactive docs are served at `http://localhost:8000/docs`. On startup the first NYT pull takes about 2 seconds, Polymarket discovery about 5, and Kalshi discovery 1 to 2 minutes, because Kalshi rate-limits aggressively and discovery probes one event per request. Once discovery is done, Kalshi is refreshed in batches of 100 markets.

## Results page

Open `http://localhost:8000/` for the live results page. It shows results only, no odds:

- **Office tabs:** Senate, House and governor, each with the number of races called.
- **Seats bar:** seats held or won, seats where a party leads in votes, the number needed to win, and seats gained or lost.
- **Map:** each state is colored by who leads in votes and by how much, and labeled with the leader's vote total. Hover for the full count; click to open that race's NYT results page. The House tab shows seats won or led per state, plus a grid of all 435 seats.
- **Status board:** every race placed by the leader's vote margin, from projected through within 1 point.
- **Key races:** a card per battleground with each candidate's vote % and vote count, and links to the full results.
- **All races:** a table you can filter and sort.
- **Live feed:** AP calls and poll closings.

Projected winners get a yellow check everywhere, and flipped seats a gold outline. Clicking a race opens its NYT results page, falling back to a Google search. On a phone, the first tap shows the details and a second tap opens the page.

The page polls `GET /api/live` every 3 seconds. That endpoint is a compact version of the snapshot, serialised once per data change and gzipped to about 40 KB. Results reach the page within about 10 to 15 seconds of NYT publishing them. The page has no build step and no dependencies apart from Google Fonts. The map shapes in `tracker/static/us-states.json` come from us-atlas (US Census boundaries) via `scripts/build_map.py`.

## Put it on the web

The included `Dockerfile` runs on any host that builds a Dockerfile, such as Render, Railway or Fly.io. To deploy:

1. Create a web service from this repository's `main` branch.
2. Leave the start command alone. The image reads the host's `$PORT`.
3. Optionally, set `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY` in the host's environment settings for real-time Kalshi prices.
4. Pick a plan that does not sleep when idle. A cold start restarts market discovery, which takes 1 to 2 minutes.

Run exactly one instance with one worker, because all state is in memory. The page and the API share one origin, so the site needs no other configuration.

Before making the site public, read the licensing notes at the end of `CLAUDE.md`. The NYT results JSON is not licensed for redistribution.

## Endpoints

| Endpoint | What it returns |
|---|---|
| `GET /` | The live results page |
| `GET /api/live` | What the page draws: seats, calls and a compact row per race, no odds (gzipped, about 40 KB) |
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
| AP votes and calls | `static01.nyt.com/elections-assets/pages/data/{ELECTION_DATE}/results-{senate,house,governor}.json` | 10 s, conditional GET with ETag (the CDN's max-age is 5 s) |
| Polymarket prices | CLOB websocket `ws-subscriptions-clob.polymarket.com` | real time |
| Polymarket markets | Gamma API, tag discovery (`POLYMARKET_TAG`, default `midterms`) every 10 min, batched slug refresh | 20 s |
| Kalshi prices | Trade API v2 `?tickers=` batches | 5 s (30 s once the websocket streams) |
| Kalshi prices, optional | Websocket `ticker` channel, needs an API key | real time |
| 6 pm ET baseline | Polymarket `prices-history` | every 30 min |

Each interval can be overridden with an environment variable: `NYT_INTERVAL`, `POLYMARKET_INTERVAL`, `KALSHI_INTERVAL`, `KALSHI_MIN_GAP`, `HISTORY_EVERY`, `HISTORY_FILE`, `POLYMARKET_WS=0`.

The election itself is set by `ELECTION_DATE` (default `2026-11-03`). The year in feed URLs, market slugs and tickers, and the 6 pm ET baseline all follow from it. `POLYMARKET_TAG` and `BASELINE_TS` can be overridden separately.

## Kalshi API key (optional, makes Kalshi real time)

Create the key on Kalshi under Account Settings, API Keys, Create New API Key. Ed25519 and RSA keys both work. Kalshi shows the private key once, so save the downloaded file. Then set two environment variables:

- `KALSHI_API_KEY_ID`: the key ID
- `KALSHI_PRIVATE_KEY`: the PEM text. Literal `\n` escapes, base64 of the whole PEM, or just the base64 body between the BEGIN and END lines also work. Alternatively, set `KALSHI_PRIVATE_KEY_PATH` to the file.

The tracker only reads market data with it: it never calls an order or portfolio endpoint. A read-only key is enough. Without the variables it falls back to REST polling, and `/api/health` shows `kalshi_ws` as disabled. A key that is set but can't be read shows as `key not usable`, and a rejected key as `handshake rejected: ... 401`.

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
