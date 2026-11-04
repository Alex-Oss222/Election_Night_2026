"""The compact payload behind the results page (GET /api/live).

The full /api/snapshot is about 800 KB because every race carries provenance and market detail. The
page polls every few seconds, so it gets only the fields it draws: about a third of the size, and
gzip takes it to roughly 40 KB.
"""
from __future__ import annotations

from typing import Any

RACE_FIELDS = ("key", "office", "state", "district", "special", "called", "winner", "winner_party", "flip",
               "runoff", "eevp", "total_votes", "expected_votes", "est_votes_outstanding", "uncontested",
               "poll_close", "call_status", "called_at", "blend")
CANDIDATE_FIELDS = ("name", "party", "caucus", "votes", "pct", "winner", "incumbent")


def slim_race(r: dict) -> dict:
    out = {k: r.get(k) for k in RACE_FIELDS}
    rating = r.get("rating") or {}
    out["rating"] = rating.get("normalized")
    out["battleground"] = bool(rating.get("battleground"))
    cls = r.get("classification") or {}
    out["tier"], out["tier_party"], out["tier_p"] = cls.get("tier"), cls.get("party"), cls.get("p")
    out["candidates"] = [{k: c.get(k) for k in CANDIDATE_FIELDS} for c in r.get("candidates") or []]
    return out


def live_payload(snap: dict, sources: dict[str, Any]) -> dict:
    return {
        "updated": snap.get("updated"),
        "version": snap.get("version"),
        "control": snap.get("control"),
        "projection": snap.get("projection"),
        "chambers": snap.get("chambers"),
        "calls": snap.get("calls"),
        "races": [slim_race(r) for r in snap.get("races") or []],
        "sources": {k: {"ok": v.get("ok"), "age_seconds": v.get("age_seconds"), "stale": v.get("stale")}
                    for k, v in sources.items()},
    }
