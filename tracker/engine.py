"""Pollers and the in-memory store. One Engine instance runs inside the API process."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from . import config as C
from . import markets as M
from . import model
from .nyt import parse_feed

log = logging.getLogger("tracker")
OFFICE_ID = {"senate": "S", "house": "H", "governor": "G"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(ts: float) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def age_seconds(iso: str | None) -> float | None:
    if not iso:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds()


class SourceStatus(dict):
    def ok(self, **kw):
        self.update(ok=True, last_ok=now_iso(), error=None, **kw)

    def fail(self, err: Exception | str):
        self.update(ok=False, last_error_at=now_iso(), error=str(err)[:300])


class Engine:
    def __init__(self) -> None:
        self.client: httpx.AsyncClient | None = None
        self.nyt: dict[str, dict] = {}
        self.nyt_etag: dict[str, str] = {}
        self.pm_events: dict[str, dict] = {}       # slug -> parsed event
        self.pm_race: dict[str, str] = {}          # race key -> slug
        self.pm_token: dict[str, tuple[str, int]] = {}  # token -> (slug, outcome index)
        self.kx_events: dict[str, list[dict]] = {}  # event ticker -> outcomes
        self.kx_race: dict[str, str] = {}          # race key -> event ticker
        self.kx_missing: set[str] = set()
        self.kx_fetched: dict[str, str] = {}
        self.early_vote: dict[str, dict] = {}
        self.history: list[dict] = []
        self.baseline: dict[str, Any] = {}         # 6 pm ET control prices
        self.status = {k: SourceStatus(ok=None) for k in ("nyt", "polymarket", "polymarket_ws", "kalshi", "early_vote")}
        self.version = 0
        self._snap: tuple[int, dict] | None = None
        self._kx_lock = asyncio.Lock()
        self._kx_next = 0.0
        self._tasks: list[asyncio.Task] = []
        self._ws_tokens: set[str] = set()
        self._ws_restart = asyncio.Event()

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.client = httpx.AsyncClient(timeout=20, headers={"User-Agent": C.USER_AGENT},
                                        follow_redirects=True, http2=False)
        self._load_history()
        await self.poll_nyt()  # race list drives market discovery
        loops = [self._loop(self.poll_nyt, C.NYT_INTERVAL),
                 self._loop(self.discover_pm, 600),
                 self._loop(self.poll_pm, C.POLYMARKET_INTERVAL, delay=5),
                 self._kalshi_loop(),
                 self._loop(self.record_history, C.HISTORY_EVERY, delay=15),
                 self._loop(self.load_baseline, 1800, delay=20),
                 self._loop(self.poll_early_vote, 3600, delay=3)]
        if C.POLYMARKET_WS:
            loops.append(self._pm_ws_loop())
        self._tasks = [asyncio.create_task(x) for x in loops]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.client:
            await self.client.aclose()

    async def _loop(self, fn, every: float, delay: float = 0) -> None:
        await asyncio.sleep(delay)
        while True:
            t0 = time.monotonic()
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # keep every loop alive whatever a feed does
                log.exception("%s failed: %s", fn.__name__, e)
            await asyncio.sleep(max(0.5, every - (time.monotonic() - t0)))

    def _bump(self) -> None:
        self.version += 1

    # ------------------------------------------------------------ NYT / AP
    async def poll_nyt(self) -> None:
        changed, errors = False, []
        for office, fname in C.NYT_FEEDS.items():
            url = f"{C.NYT_BASE}/{fname}"
            headers = {"If-None-Match": self.nyt_etag[office]} if office in self.nyt_etag else {}
            try:
                r = await self.client.get(url, headers=headers)
                if r.status_code == 304:
                    continue
                r.raise_for_status()
                self.nyt[office] = parse_feed(r.json(), OFFICE_ID[office])
                self.nyt[office]["fetched_at"] = now_iso()
                self.nyt[office]["source_url"] = url
                if et := r.headers.get("etag"):
                    self.nyt_etag[office] = et
                changed = True
            except Exception as e:  # one bad feed must not block the other two
                log.warning("nyt %s: %s", office, e)
                errors.append(f"{office}: {e}")
        if errors:
            self.status["nyt"].fail("; ".join(errors))
        else:
            self.status["nyt"].ok(updated={k: v["updated"] for k, v in self.nyt.items()})
        if changed:
            self._bump()

    def all_races(self) -> dict[str, dict]:
        out = {}
        for feed in self.nyt.values():
            out.update(feed["races"])
        return out

    # ------------------------------------------------------------ Polymarket
    def _key_for_pm(self, slug: str) -> str | None:
        key = M.race_key_from_pm_slug(slug)
        races = self.all_races()
        if key and key not in races and f"{key}-special" in races:
            key = f"{key}-special"   # FL and OH only have specials this cycle
        return key if key in races else None

    async def discover_pm(self) -> None:
        """Tag discovery (cheap), then probe conventional slugs for anything still missing."""
        found: dict[str, dict] = {}
        for closed in ("false", "true"):
            offset = 0
            while offset < 5000:
                r = await self.client.get(f"{C.GAMMA}/events", params={
                    "tag_slug": "midterms", "closed": closed, "limit": 500, "offset": offset})
                r.raise_for_status()
                page = r.json()
                for ev in page:
                    found[ev["slug"]] = ev
                if len(page) < 500:
                    break
                offset += 500
        missing = [M.pm_slug(r) for k, r in self.all_races().items()
                   if k not in {self._key_for_pm(s) for s in found} and r["office"] != "house"]
        for ev in await self._pm_fetch_slugs([s for s in missing if s]):
            found[ev["slug"]] = ev
        for slug, ev in found.items():
            key = self._key_for_pm(slug)
            if key and (key not in self.pm_race or self.pm_race[key] == slug):
                self.pm_race[key] = slug
                self._store_pm(ev)
            elif slug in M.PM_CONTROL.values():
                self._store_pm(ev)
        self.status["polymarket"].ok(races=len(self.pm_race))
        self._refresh_ws_tokens()
        self._bump()

    async def _pm_fetch_slugs(self, slugs: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(slugs), 25):
            chunk = slugs[i:i + 25]
            r = await self.client.get(f"{C.GAMMA}/events", params=[("slug", s) for s in chunk] + [("limit", 50)])
            r.raise_for_status()
            out.extend(r.json())
        return out

    def _store_pm(self, ev: dict) -> None:
        parsed = M.parse_pm_event(ev)
        old = self.pm_events.get(parsed["slug"])
        if old:  # keep fresher websocket quotes when REST is momentarily behind
            ws = {o["token"]: o for o in old["outcomes"] if o.get("ws_at")}
            for o in parsed["outcomes"]:
                w = ws.get(o["token"])
                if w and time.time() - w["ws_at"] < C.POLYMARKET_INTERVAL and not o["closed"]:
                    o.update({k: w[k] for k in ("bid", "ask", "last", "p", "ws_at")})
        parsed["fetched_at"] = now_iso()
        self.pm_events[parsed["slug"]] = parsed
        for i, o in enumerate(parsed["outcomes"]):
            if o["token"]:
                self.pm_token[o["token"]] = (parsed["slug"], i)

    async def poll_pm(self) -> None:
        slugs = sorted(set(self.pm_race.values()) | set(M.PM_CONTROL.values()))
        try:
            for ev in await self._pm_fetch_slugs(slugs):
                self._store_pm(ev)
            self.status["polymarket"].ok(races=len(self.pm_race))
            self._bump()
        except Exception as e:
            self.status["polymarket"].fail(e)
            raise

    def _refresh_ws_tokens(self) -> None:
        toks = {o["token"] for ev in self.pm_events.values() for o in ev["outcomes"]
                if o["token"] and not o["closed"] and (o["party"] or ev["slug"] in M.PM_CONTROL.values())}
        if toks != self._ws_tokens:
            self._ws_tokens = toks
            self._ws_restart.set()

    async def _pm_ws_loop(self) -> None:
        import websockets
        backoff = 1
        while True:
            await asyncio.sleep(1)
            toks = sorted(self._ws_tokens)
            if not toks:
                continue
            self._ws_restart.clear()
            try:
                async with websockets.connect(C.CLOB_WS, ping_interval=None, max_size=2**24) as ws:
                    await ws.send(json.dumps({"assets_ids": toks, "type": "market"}))
                    self.status["polymarket_ws"].ok(tokens=len(toks))
                    backoff = 1
                    last_ping = time.monotonic()
                    while not self._ws_restart.is_set():
                        if time.monotonic() - last_ping > 10:
                            await ws.send("PING")
                            last_ping = time.monotonic()
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            continue
                        if raw == "PONG":
                            continue
                        self._on_ws(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.status["polymarket_ws"].fail(e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _on_ws(self, msg: Any) -> None:
        msgs = msg if isinstance(msg, list) else [msg]
        touched = False
        for m in msgs:
            kind = m.get("event_type")
            if kind == "book":
                bids = [float(x["price"]) for x in m.get("bids") or []]
                asks = [float(x["price"]) for x in m.get("asks") or []]
                touched |= self._ws_quote(m.get("asset_id"), bid=max(bids, default=None),
                                          ask=min(asks, default=None))
            elif kind == "price_change":
                for pc in m.get("price_changes") or []:
                    touched |= self._ws_quote(pc.get("asset_id"), bid=M._num(pc.get("best_bid")),
                                              ask=M._num(pc.get("best_ask")))
            elif kind == "last_trade_price":
                touched |= self._ws_quote(m.get("asset_id"), last=M._num(m.get("price")))
            elif kind == "best_bid_ask":
                touched |= self._ws_quote(m.get("asset_id"), bid=M._num(m.get("best_bid")),
                                          ask=M._num(m.get("best_ask")))
        if touched:
            self.status["polymarket_ws"]["last_msg"] = now_iso()
            self._bump()

    def _ws_quote(self, token, **kw) -> bool:
        ref = self.pm_token.get(token)
        if not ref:
            return False
        o = self.pm_events[ref[0]]["outcomes"][ref[1]]
        for k, v in kw.items():
            if v is not None:
                o[k] = v
        o["p"] = M.display_price(o["bid"], o["ask"], o["last"])
        o["ws_at"] = time.time()
        return True

    # ------------------------------------------------------------ Kalshi
    async def _kx_get(self, path: str, params: dict | None = None) -> httpx.Response:
        """Serialised, spaced Kalshi requests with 429 backoff (Kalshi rate-limits readily)."""
        async with self._kx_lock:
            for attempt in range(5):
                wait = self._kx_next - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                r = await self.client.get(f"{C.KALSHI}{path}", params=params)
                self._kx_next = time.monotonic() + C.KALSHI_MIN_GAP
                if r.status_code != 429:
                    return r
                delay = float(r.headers.get("retry-after") or 2 ** (attempt + 1))
                self._kx_next = time.monotonic() + delay
            return r

    async def _kx_event(self, ticker: str) -> bool:
        """Discovery: does this event exist? Stores its markets if so."""
        r = await self._kx_get("/markets", {"event_ticker": ticker, "limit": 100})
        if r.status_code == 404:
            return False
        r.raise_for_status()
        mk = r.json().get("markets") or []
        if not mk:
            return False
        self.kx_events[ticker] = M.parse_kalshi_markets(mk)
        self.kx_fetched[ticker] = now_iso()
        return True

    async def _kx_refresh(self) -> None:
        """Refresh every known market in batches via ?tickers= (2-4 requests for the whole night)."""
        by_ticker = {o["ticker"]: ev for ev, outs in self.kx_events.items() for o in outs}
        tickers = sorted(by_ticker)
        fresh: dict[str, list[dict]] = {}
        for i in range(0, len(tickers), 100):
            r = await self._kx_get("/markets", {"tickers": ",".join(tickers[i:i + 100]), "limit": 100})
            r.raise_for_status()
            for m in r.json().get("markets") or []:
                fresh.setdefault(m["event_ticker"], []).append(m)
        t = now_iso()
        for ev, mk in fresh.items():
            self.kx_events[ev] = M.parse_kalshi_markets(mk)
            self.kx_fetched[ev] = t

    async def _kalshi_loop(self) -> None:
        await asyncio.sleep(2)
        last_discovery = 0.0
        while True:
            t0 = time.monotonic()
            try:
                if time.time() - last_discovery >= 600 or not self.kx_events:
                    await self._kx_discover()
                    last_discovery = time.time()
                else:
                    await self._kx_refresh()
                self.status["kalshi"].ok(races=len(self.kx_race), events=len(self.kx_events))
                self._bump()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("kalshi: %s", e)
                self.status["kalshi"].fail(e)
            await asyncio.sleep(max(1, C.KALSHI_INTERVAL - (time.monotonic() - t0)))

    async def _kx_discover(self) -> None:
        probes = 0
        self.status["kalshi"]["discovering"] = True
        for t in M.KALSHI_CONTROL.values():
            await self._kx_event(t)
        await self._kx_house()
        self.status["kalshi"].ok(discovering=True, races=len(self.kx_race))
        self._bump()
        for key, race in self.all_races().items():
            if key in self.kx_race:
                continue
            if race["office"] == "house" and race["rating"]["normalized"] in ("SAFE_D", "SAFE_R", "UNRATED"):
                continue  # don't spend Kalshi's rate limit probing safe seats
            for t in M.kalshi_candidates(race):
                if t in self.kx_missing:
                    continue
                probes += 1
                if probes % 15 == 0:  # keep known prices fresh while discovery crawls
                    await self._kx_refresh()
                    self.status["kalshi"].ok(discovering=True, races=len(self.kx_race))
                    self._bump()
                if await self._kx_event(t):
                    self.kx_race[key] = t
                    break
                self.kx_missing.add(t)
        self.status["kalshi"]["discovering"] = False

    async def _kx_house(self) -> None:
        cursor = None
        for _ in range(10):
            params = {"series_ticker": "KXHOUSERACE", "limit": 200, "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            r = await self._kx_get("/events", params)
            r.raise_for_status()
            d = r.json()
            for ev in d.get("events") or []:
                key = M.race_key_from_kalshi_house(ev.get("event_ticker", ""))
                if key and ev.get("markets"):
                    self.kx_events[ev["event_ticker"]] = M.parse_kalshi_markets(ev["markets"])
                    self.kx_fetched[ev["event_ticker"]] = now_iso()
                    self.kx_race[key] = ev["event_ticker"]
            cursor = d.get("cursor")
            if not cursor:
                break

    # ------------------------------------------------------------ assembly
    def _pm_probs(self, slug: str | None) -> dict[str, float]:
        ev = self.pm_events.get(slug) if slug else None
        return M.party_probs(ev["outcomes"]) if ev else {}

    def _kx_probs(self, ticker: str | None) -> dict[str, float]:
        return M.party_probs(self.kx_events.get(ticker, [])) if ticker else {}

    def _race_blend(self, key: str) -> dict[str, float]:
        return model.blend(self._pm_probs(self.pm_race.get(key)), self._kx_probs(self.kx_race.get(key)))

    def race(self, key: str, detail: bool = False) -> dict | None:
        base = self.all_races().get(key)
        if not base:
            return None
        r = dict(base)
        pm_slug, kx_t = self.pm_race.get(key), self.kx_race.get(key)
        pm_ev = self.pm_events.get(pm_slug) or {}
        r["markets"] = {
            "polymarket": {"slug": pm_slug, "probs": self._pm_probs(pm_slug),
                           "fetched_at": pm_ev.get("fetched_at"),
                           "ws_at": _iso(max((o.get("ws_at") or 0 for o in pm_ev.get("outcomes", [])), default=0)),
                           "url": f"https://polymarket.com/event/{pm_slug}"} if pm_slug else None,
            "kalshi": {"event": kx_t, "probs": self._kx_probs(kx_t),
                       "fetched_at": self.kx_fetched.get(kx_t)} if kx_t else None,
        }
        feed = self.nyt.get(r["office"]) or {}
        r["provenance"] = {
            "results": "AP via NYT; unofficial until certified",
            "results_source_url": feed.get("source_url"),
            "results_fetched_at": feed.get("fetched_at"),
            "odds": "market prices, not a forecast",
        }
        if detail:
            r["early_vote"] = self.early_vote.get(r["state"])
        r["blend"] = self._race_blend(key)
        r["classification"] = model.classify(r)
        if detail:
            if pm_slug in self.pm_events:
                r["markets"]["polymarket"]["outcomes"] = self.pm_events[pm_slug]["outcomes"]
            if kx_t in self.kx_events:
                r["markets"]["kalshi"]["outcomes"] = self.kx_events[kx_t]
        else:
            r["candidates"] = [{k: c[k] for k in ("name", "party", "votes", "pct", "winner", "incumbent")}
                               for c in r["candidates"][:4]]
        return r

    def control(self) -> dict:
        out = {}
        for chamber in ("senate", "house"):
            pm = {}
            ev = self.pm_events.get(M.PM_CONTROL[chamber])
            if ev:
                for o in ev["outcomes"]:
                    p = M.party_of(o["label"])
                    if p and o["p"] is not None:
                        pm[p] = o["p"]
            kx = M.party_probs(self.kx_events.get(M.KALSHI_CONTROL[chamber], []))
            b = model.blend(pm, kx)
            nyt = (self.nyt.get(chamber) or {}).get("chamber") or {}
            base = (self.baseline.get(chamber) or {})
            out[chamber] = {
                "blend": b,
                "pills": model.normalize_pills(b),
                "polymarket": pm, "kalshi": kx,
                "since_6pm": {p: round(b[p] - base[p], 4) for p in b if p in base} or None,
                "nyt_needle": nyt.get("nyt_needle"),
                "ap_winner": nyt.get("winner"),
            }
        bal = self.pm_events.get(M.PM_CONTROL["balance"])
        if bal:
            out["balance_of_power"] = {o["label"]: o["p"] for o in bal["outcomes"] if o["p"] is not None}
        return out

    def projection(self, office: str) -> dict:
        races = [self.race(k) for k, r in self.all_races().items() if r["office"] == office]
        nyt = (self.nyt.get(office) or {}).get("chamber") or {}
        ctrl = self.control().get(office, {}).get("blend") if office in ("senate", "house") else None
        return model.project(races, nyt.get("not_up") or {}, nyt.get("to_win"), ctrl)

    def snapshot(self) -> dict:
        if self._snap and self._snap[0] == self.version:
            return self._snap[1]
        races = [self.race(k) for k in sorted(self.all_races())]
        snap = {
            "updated": now_iso(),
            "version": self.version,
            "control": self.control(),
            "projection": {o: self.projection(o) for o in ("senate", "house", "governor")},
            "chambers": {o: (self.nyt.get(o) or {}).get("chamber") for o in ("senate", "house")},
            "calls": [{"key": r["key"], "winner": r["winner"], "party": r["winner_party"],
                       "flip": r["flip"], "at": r["called_at"]} for r in races if r["called"]],
            "races": races,
            "sources": self.status,
        }
        snap["calls"].sort(key=lambda c: c["at"] or "", reverse=True)
        self._snap = (self.version, snap)
        return snap

    # ------------------------------------------------------------ early vote
    async def poll_early_vote(self) -> None:
        """UF Election Lab statewide early-vote tracker. Turnout context only: these ballots are
        already inside the AP totals as they're tabulated, and party registration is not a vote."""
        import csv
        import io
        r = await self.client.get(C.UF_EARLY_VOTE)
        r.raise_for_status()
        out = {}
        for row in csv.DictReader(io.StringIO(r.text)):
            def n(k):
                try:
                    return int(float(row.get(k) or 0)) or None
                except ValueError:
                    return None
            st = row.get("state_abbv")
            if not st:
                continue
            voted, t22 = n("voted_all"), n("turn_2022")
            out[st] = {
                "mail_requested": n("request_all"), "mail_accepted": n("accept_all"),
                "in_person": n("inperson_all"), "total_voted": voted,
                "by_registration": {"D": n("voted_dem"), "R": n("voted_rep"), "none": n("voted_none")},
                "turnout_2022_total": t22,
                "pct_of_2022_turnout": round(100 * voted / t22, 1) if voted and t22 else None,
                "source_updated": row.get("last_update"), "state_source": row.get("data_source"),
                "notes": None if row.get("notes") in (None, "", "NA") else row["notes"],
                "provider": "UF Election Lab (CC BY-NC-ND)", "source_url": C.UF_EARLY_VOTE,
            }
        self.early_vote = out
        self.status["early_vote"].ok(states=len(out))
        self._bump()

    # ------------------------------------------------------------ history
    def _load_history(self) -> None:
        if not os.path.exists(C.HISTORY_FILE):
            return
        with open(C.HISTORY_FILE) as f:
            for line in f:
                try:
                    self.history.append(json.loads(line))
                except ValueError:
                    pass
        self.history = self.history[-10000:]

    async def record_history(self) -> None:
        ctrl = self.control()
        row = {"t": now_iso(),
               "control": {c: ctrl[c]["blend"] for c in ("senate", "house")},
               "seats": {o: {p: s["projected"] for p, s in self.projection(o)["parties"].items()}
                         for o in ("senate", "house")},
               "called": {o: (self.nyt.get(o) or {}).get("chamber", {}).get("called")
                          for o in ("senate", "house")}}
        self.history.append(row)
        self.history = self.history[-10000:]
        os.makedirs(os.path.dirname(C.HISTORY_FILE) or ".", exist_ok=True)
        with open(C.HISTORY_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")

    async def load_baseline(self) -> None:
        """6 pm ET control prices from Polymarket's price history (Kalshi needs auth for this)."""
        ts = int(datetime.fromisoformat(C.BASELINE_TS.replace("Z", "+00:00")).timestamp())
        for chamber in ("senate", "house"):
            ev = self.pm_events.get(M.PM_CONTROL[chamber])
            if not ev:
                continue
            got = {}
            for o in ev["outcomes"]:
                p = M.party_of(o["label"])
                if not (p and o["token"]):
                    continue
                pts = await self.price_history(o["token"], ts - 600, ts + 600)
                if pts:
                    got[p] = min(pts, key=lambda x: abs(x["t"] - ts))["p"]
            if got:
                self.baseline[chamber] = got
        self._bump()

    async def price_history(self, token: str, start: int, end: int, fidelity: int = 1) -> list[dict]:
        r = await self.client.get(f"{C.CLOB}/prices-history",
                                  params={"market": token, "startTs": start, "endTs": end, "fidelity": fidelity})
        r.raise_for_status()
        return r.json().get("history") or []
