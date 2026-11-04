"""AP votes and calls, read from the New York Times static results JSON."""
from __future__ import annotations

from typing import Any

PARTY = {"DEM": "D", "GOP": "R"}
# NYT publishes one rating per race (solid/likely/lean/tossup). Mapped onto the nine-step scale
# SAFE_D..SAFE_R; NYT never uses TILT, so those two steps simply never appear from this source.
RATING = {"solid-dem": "SAFE_D", "likely-dem": "LIKELY_D", "lean-dem": "LEAN_D", "tossup": "TOSS_UP",
          "lean-rep": "LEAN_R", "likely-rep": "LIKELY_R", "solid-rep": "SAFE_R"}
OFFICE = {"S": "senate", "H": "house", "G": "governor"}


def _party(meta: dict) -> str:
    """Map an NYT candidate to D / R / I. Independents that caucus with a party keep 'I'
    here; `caucus` carries the caucus so seat counts can fold them in."""
    pid = ((meta.get("party") or {}).get("nyt_id")) or ""
    return PARTY.get(pid, "I")


def _caucus(v) -> str | None:
    """party_caucus is a string in the Senate feed and a party object in the House feed."""
    if isinstance(v, dict):
        v = v.get("nyt_id")
    return PARTY.get(v) if isinstance(v, str) else None


def race_key(race: dict) -> str | None:
    tru = race.get("top_reporting_unit") or {}
    st = tru.get("state_postal")
    office = race.get("office_id")
    if not st or st == "US" or office not in OFFICE:
        return None
    special = "Special" in (race.get("type") or "")
    if office == "H":
        try:
            n = int(race.get("seat_number") or 1)
        except ValueError:
            return None
        return f"H-{st}-{n:02d}"
    return f"{office}-{st}" + ("-special" if special else "")


def normalize_race(race: dict) -> dict | None:
    key = race_key(race)
    if key is None:
        return None
    tru = race["top_reporting_unit"]
    meta = race.get("candidate_metadata") or {}
    total = tru.get("total_votes") or 0
    won = set((race.get("outcome") or {}).get("won") or [])
    runoff = set((race.get("outcome") or {}).get("advanced_to_runoff") or [])

    cands = []
    for c in tru.get("candidates") or []:
        cid = c.get("nyt_id")
        m = meta.get(cid) or {}
        votes = (c.get("votes") or {}).get("total") or 0
        est = c.get("nyt_model_estimates") or {}
        cands.append({
            "id": cid,
            "name": " ".join(x for x in [m.get("first_name"), m.get("last_name")] if x) or cid,
            "party": _party(m),
            "caucus": _caucus(m.get("party_caucus") or est.get("party_caucus")),
            "incumbent": bool(m.get("incumbent")),
            "votes": votes,
            "pct": round(100 * votes / total, 2) if total else None,
            "leader": bool(c.get("leader")),
            "winner": cid in won,
            "runoff": cid in runoff,
            "nyt_win_prob": est.get("win_probability"),
            "img": m.get("img_url"),
        })
    cands.sort(key=lambda c: -c["votes"])

    winner = next((c for c in cands if c["winner"]), None)
    dr = [c for c in cands if c["party"] in ("D", "R")]
    margin = None
    if total and len(dr) >= 2:
        d = sum(c["votes"] for c in dr if c["party"] == "D")
        r = sum(c["votes"] for c in dr if c["party"] == "R")
        margin = round(100 * (d - r) / total, 2)  # + = Democratic lead

    st = tru["state_postal"]
    return {
        "key": key,
        "office": OFFICE[race["office_id"]],
        "state": st,
        "district": key.split("-")[2] if race["office_id"] == "H" else None,
        "special": "Special" in (race.get("type") or ""),
        "nyt_id": race.get("nyt_id"),
        "rating": {
            "provider": "nyt",
            "original": race.get("rating"),
            "normalized": RATING.get(race.get("rating") or "", "UNRATED"),
            "battleground": (race.get("rating") or "").startswith(("lean", "tossup")),
        },
        "call_status": race.get("race_call_status"),
        "called": winner is not None,
        "called_by": (race.get("outcome") or {}).get("called_by") or [],
        "called_at": next(iter((race.get("outcome") or {}).get("winner_accepted_at") or []), None),
        "winner": winner["name"] if winner else None,
        "winner_party": (winner["caucus"] or winner["party"]) if winner else None,
        "flip": (race.get("outcome") or {}).get("gained"),
        "runoff": bool(runoff),
        "eevp": tru.get("eevp"),
        "precincts_reporting": tru.get("precincts_reporting"),
        "precincts_total": tru.get("precincts_total"),
        "total_votes": total,
        # AP's expected-vote estimate, not a ballot inventory: a projection that moves as counties report
        "expected_votes": tru.get("total_expected_vote"),
        "est_votes_outstanding": max(0, (tru.get("total_expected_vote") or 0) - total) or None,
        "certified": bool(race.get("certified")),
        "dem_margin_pct": margin,
        "tabulation": race.get("tabulation_method"),
        "uncontested": bool(race.get("uncontested")),
        "candidates": cands,
        "source_updated_at": tru.get("provider_updated_at") or race.get("updated_at"),
        "results_provider": tru.get("provider"),  # "ap"
        "nyt_needle_status": (race.get("model_settings") or {}).get("model_status"),
        "url": race.get("url"),
    }


def parse_feed(doc: dict, office_id: str) -> dict[str, Any]:
    """Return {'races': {key: race}, 'chamber': {...} | None, 'poll_closing': {ST: iso}, 'updated': iso}."""
    races = {}
    for r in doc.get("races") or []:
        n = normalize_race(r)
        if n:
            races[n["key"]] = n

    closing = {}
    for p in doc.get("pollClosingTimes") or []:
        st = (p.get("reporting_unit") or {}).get("state_postal")
        if st:
            closing[st] = p.get("poll_closing_time")
    for r in races.values():
        r["poll_close"] = closing.get(r["state"])

    chamber = None
    for res in (doc.get("partyControlData") or {}).get("results") or []:
        o = (res.get("offices") or {}).get(office_id)
        if not o:
            continue
        bal = o.get("party_balance") or {}
        model = {m.get("nyt_party_id"): m for m in o.get("nyt_model_estimates") or []}
        chamber = {
            "total": o.get("total"),
            "up": o.get("open_seats"),
            "not_up": {PARTY.get(k, "I"): v.get("no_election", 0) for k, v in bal.items()},
            "seats_won": {PARTY.get(k, "I"): v.get("seats", 0) for k, v in bal.items()},
            "flips": {PARTY.get(k, "I"): v.get("flip_count", 0) for k, v in bal.items()},
            "to_win": {PARTY.get(k, "I"): v for k, v in (o.get("to_win") or {}).items()},
            "called": o.get("num_races_called"),
            "winner": next((PARTY.get(k, "I") for k, v in bal.items() if v.get("winner")), None),
            "nyt_needle": {
                PARTY.get(k, "I"): {
                    "win_prob": v.get("win_probability"),
                    "seats": v.get("seats"),
                    "seats_quantile": v.get("seats_quantile"),
                    "updated": v.get("nyt_updated_at"),
                } for k, v in model.items()
            },
        }
    return {
        "races": races,
        "chamber": chamber,
        "poll_closing": closing,
        "updated": (doc.get("resultsMeta") or {}).get("resultsUpdatedAt"),
    }
