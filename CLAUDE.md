# Election Night 2026 tracker: working notes

Read this first. It is the handoff between sessions: what we're building, where it stands, and what's next.

## Goal

A live API for the 2026 midterms (election day 2026-11-03), built on election night. The priority is data that is as current and as close to streaming as possible. Early vote and history are backend nice-to-haves, not needs. Methodology source: https://election.offgramercy.com/methodology. Reference repo: https://github.com/KeepTheOutliers/margin-of-error.

## Run it

A new container has no venv, so create it first:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

```bash
.venv/bin/uvicorn tracker.app:app --port 8000
```

Tests (offline):

```bash
.venv/bin/pytest -q
```

Check status at `curl -s localhost:8000/api/health`. Kalshi discovery takes 1 to 2 minutes after a start.

## Status (as of 2026-11-04 ~00:55 UTC)

Built, tested (13 tests) and pushed on `claude/laughing-brahmagupta-yyojle`. `/api/health` returned ok against the live feeds:

- **NYT/AP:** votes and calls 5 to 8 s old.
- **Polymarket:** websocket streaming.
- **Kalshi REST:** about 3 s old.
- **Early vote:** loaded.

## Next step: Kalshi websocket key

The user created a Kalshi API key, read-only scope only ("Read all data"), and added it as environment variables:

- `KALSHI_API_KEY_ID`
- `KALSHI_PRIVATE_KEY`: one-line base64 of the PEM file. Raw PEM, `\n`-escaped PEM, or `KALSHI_PRIVATE_KEY_PATH` also work.

To act on it:

1. Check the variables are present without printing their values, e.g. `[ -n "$KALSHI_API_KEY_ID" ] && echo set`.
2. Start the tracker. After about 1 minute, `/api/health` should show `kalshi_ws` ok with a recent `last_msg`.
3. If it shows `handshake rejected: ... 401`, either the key was copied wrong (the ID and key don't match, or the base64 is broken) or a read-only scope isn't enough for the websocket. Tell the user which, and don't guess.
4. If the variables aren't set, the user saved them to a different environment, or this session started before the save.

Never print, log or commit the key values. Never ask the user to paste a key into chat.

The user is deleting the Kalshi account after election night. Remind them to also remove both variables from the environment settings.

## Code map

- **`tracker/nyt.py`:** parses the NYT results JSON (AP data). Race keys are `S-TX`, `S-OH-special`, `G-GA` and `H-PA-07`; at-large districts are `-01`.
- **`tracker/markets.py`:** Polymarket and Kalshi parsing, slug and ticker mapping, and the display-price rule (bid/ask midpoint, or last trade if the spread is over 10 cents).
- **`tracker/model.py`:** blend, tiers and seat projection, following the methodology rules.
- **`tracker/engine.py`:** all pollers and websockets, plus the in-memory store.
- **`tracker/kalshi_auth.py`:** Ed25519 and RSA-PSS signing.
- **`tracker/app.py`:** FastAPI endpoints, an SSE stream at `/api/stream`, and staleness reporting in `/api/health`.

## Hard-won gotchas

- **Kalshi rate limits:** Kalshi returns 429 quickly from this environment's shared IP. Requests are serialised with a 0.5 to 0.6 s gap, and the refresh uses batched `/markets?tickers=a,b,...`. Don't add per-race polling.
- **Kalshi House markets:** most come from the `KXHOUSERACE` series (`KXHOUSERACE-WV02-26`, `-WYAL-26`). Older per-district tickers (`HOUSETX15-26`) are probed only for seats not rated "solid".
- **Polymarket discovery:** use the `midterms` tag plus batched `?slug=a&slug=b`. Placeholder outcomes ("Person A", "Party C") have no `outcomePrices` and are dropped. At-large slugs use `-al-` (`ak-al-house-election-winner`).
- **NYT feed quirks:** the CDN's max-age is 5 s, so polling under 10 s is pointless; the poller uses ETag conditional GETs. `party_caucus` is a string in the Senate feed and an object in the House feed. The House feed has a national `US-...` row, which is skipped.
- **Specials:** FL and OH have only special Senate races this cycle. Polymarket uses the plain state slug for them, and Kalshi uses `SENATEOHS-26` / `SENATEFLS-26`.
- **Restarting the server:** don't run `pkill -f uvicorn` or `kill $(pgrep -f ...uvicorn...)` from a bash command that also contains "uvicorn", because it kills the tool's own shell. Find the PID first, then `kill <pid>` in a separate call.
- **Grepping JSON:** FastAPI returns compact JSON (`"ok":true`, no spaces), so grep patterns must match that.

## Known gaps and backlog (in priority order)

1. Confirm the Kalshi websocket with the real key (above).
2. 64 House districts have no market on either site, so they show as `unpriced`.
3. "Since 6 pm" exists for chamber control only, from Polymarket history. Per-race history is available at `/api/races/{key}/history`.
4. Kalshi seat-count markets (`KXDSENATEWON`, etc.) and Polymarket seat buckets aren't ingested.
5. Optional: Open America (`openamerica.io/elections/results.json?year=2026&office=Senate`). It is state-reported, covers about 22 states and works with no key, but the publisher asks developers to contact them before repackaging. Use it only as a cross-check.
6. Direct state feeds (AZ every 2 min, OH every 3, NC every 5 to 10) are slower than AP via NYT. Use them for verification only.

## Licensing notes, to raise if this goes public

- The NYT results JSON is not licensed for redistribution. AP or Decision Desk HQ is the licensed route.
- The UF Election Lab early-vote data is CC BY-NC-ND.

## User preferences

- Be direct, accuracy over agreement, and separate what's verified from what's inferred.
- No em dashes.
- Keep the user posted in a few words during long work.
- The user granted full autonomy on this repo: commit and push to the branch above. No PR unless asked.
