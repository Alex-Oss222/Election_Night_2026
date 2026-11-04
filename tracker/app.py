"""HTTP API. Run: uvicorn tracker.app:app --port 8000"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config as C
from .engine import Engine, age_seconds
from .live import live_payload

STATIC = Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
engine = Engine()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(title=f"Election Night {C.YEAR} Tracker", version="0.1.0", lifespan=lifespan,
              description="AP results (via NYT JSON) blended with Polymarket and Kalshi prices.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1024)  # Starlette leaves text/event-stream uncompressed
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def page():
    """The results page. It polls /api/live every few seconds."""
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


# A source is stale when it hasn't succeeded for 3x its poll interval.
STALE_AFTER = {"nyt": 3 * C.NYT_INTERVAL, "polymarket": 3 * C.POLYMARKET_INTERVAL,
               "kalshi": max(60, 3 * C.KALSHI_INTERVAL), "early_vote": 3 * 3600}


@app.get("/api/health")
def health():
    """ok=false when any core feed (AP/NYT, Polymarket, Kalshi) is failing or stale."""
    out, ok = {}, True
    for k, st in engine.status.items():
        age = age_seconds(st.get("last_ok") if k != "polymarket_ws" else st.get("last_msg"))
        if k == "kalshi_ws" and not engine.kx_signer:
            out[k] = {**st, "stale": None}
            continue
        if k == "kalshi_ws":
            age = age_seconds(st.get("last_msg"))
        stale = age is None or (k in STALE_AFTER and age > STALE_AFTER[k]) or (k.endswith("_ws") and age > 60)
        out[k] = {**st, "age_seconds": None if age is None else round(age, 1), "stale": stale}
        if k in ("nyt", "polymarket", "kalshi") and (stale or not st.get("ok")):
            ok = False
    return {"ok": ok, "version": engine.version, "sources": out}


@app.get("/api/sources/status")
def sources_status():
    return health()["sources"]


@app.get("/api/early-vote")
def early_vote():
    """UF Election Lab statewide early vote. Context only, never add it to vote totals."""
    return engine.early_vote


@app.get("/api/states/{state}/early-vote")
def state_early_vote(state: str):
    ev = engine.early_vote.get(state.upper())
    if not ev:
        raise HTTPException(404, f"no early-vote data for {state}")
    return ev


@app.get("/api/snapshot")
def snapshot():
    """Everything in one payload: control, projections, calls, every race."""
    return engine.snapshot()


_live: tuple[int, bytes] | None = None


@app.get("/api/live")
def live():
    """What the results page draws: seats, calls and a compact row per race (no odds).
    Serialised once per data version, so many viewers cost one build."""
    global _live
    if not _live or _live[0] != engine.version:
        snap = engine.snapshot()
        election = {"date": C.ELECTION_DATE, "year": C.YEAR}
        _live = (snap["version"], json.dumps(live_payload(snap, health()["sources"], election),
                                             separators=(",", ":")).encode())
    return Response(_live[1], media_type="application/json", headers={"Cache-Control": "no-store"})


@app.get("/api/control")
def control():
    """Chamber control odds: blended market, each book, NYT needle, change since 6 pm ET."""
    return engine.control()


@app.get("/api/projection/{office}")
def projection(office: str):
    if office not in ("senate", "house", "governor"):
        raise HTTPException(404, "office must be senate, house or governor")
    return engine.projection(office)


@app.get("/api/calls")
def calls(office: str | None = None):
    """AP calls, newest first."""
    return [c for c in engine.snapshot()["calls"] if not office or c["key"].startswith(office[0].upper())]


@app.get("/api/races")
def races(office: str | None = None, state: str | None = None,
          status: str | None = Query(None, description="called | uncalled | tossup | reporting"),
          priced: bool | None = None):
    out = []
    for r in engine.snapshot()["races"]:
        if office and r["office"] != office:
            continue
        if state and r["state"] != state.upper():
            continue
        if status == "called" and not r["called"]:
            continue
        if status == "uncalled" and r["called"]:
            continue
        if status == "tossup" and r["classification"]["tier"] != "tossup":
            continue
        if status == "reporting" and not r["eevp"]:
            continue
        if priced is not None and bool(r["blend"]) != priced:
            continue
        out.append(r)
    return out


@app.get("/api/races/{key}")
def race(key: str):
    """Race detail with every candidate and each book's raw quotes. Key like S-TX, H-PA-07, G-GA."""
    r = engine.race(key.upper().replace("SPECIAL", "special"), detail=True)
    if not r:
        raise HTTPException(404, f"unknown race {key}")
    return r


@app.get("/api/races/{key}/history")
async def race_history(key: str, since: str = C.BASELINE_TS, fidelity: int = 5):
    """Polymarket price history per candidate since `since` (default 6 pm ET)."""
    r = engine.race(key.upper().replace("SPECIAL", "special"), detail=True)
    if not r or not r["markets"]["polymarket"]:
        raise HTTPException(404, "no Polymarket market for this race")
    start = int(datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp())
    end = int(datetime.now(timezone.utc).timestamp())
    outs = [o for o in r["markets"]["polymarket"].get("outcomes", []) if o["token"]]
    series = await asyncio.gather(*(engine.price_history(o["token"], start, end, fidelity) for o in outs))
    return {o["label"]: s for o, s in zip(outs, series)}


@app.get("/api/history")
def history(limit: int = 500):
    """Recorded control odds and seat projections (one row every HISTORY_EVERY seconds)."""
    return engine.history[-limit:]


@app.get("/api/stream")
async def stream(request: Request, races: bool = False):
    """Server-sent events: pushes control, projections and calls (optionally all races) on every change."""
    async def gen():
        seen = -1
        while not await request.is_disconnected():
            if engine.version != seen:
                seen = engine.version
                s = engine.snapshot()
                body = {k: s[k] for k in ("updated", "version", "control", "projection", "calls")}
                if races:
                    body["races"] = s["races"]
                yield f"event: snapshot\ndata: {json.dumps(body)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
