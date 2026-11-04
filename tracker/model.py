"""Blending and seat projection, following election.offgramercy.com/methodology."""
from __future__ import annotations

from .config import TIERS, TOSSUP_HIGH, TOSSUP_LOW


def blend(pm: dict[str, float] | None, kx: dict[str, float] | None) -> dict[str, float]:
    """Simple mean of the two books where both price a party, otherwise whichever exists."""
    pm, kx = pm or {}, kx or {}
    out = {}
    for party in set(pm) | set(kx):
        vals = [b[party] for b in (pm, kx) if party in b]
        out[party] = round(sum(vals) / len(vals), 4)
    return out


def tier(p: float) -> str:
    for name, lo in TIERS:
        if p >= lo:
            return name
    return "tossup"


def classify(race: dict) -> dict:
    """Decide who a race counts for. A call always beats the market."""
    if race.get("called") and race.get("winner_party"):
        return {"party": race["winner_party"], "tier": "called", "p": 1.0}
    b = race.get("blend") or {}
    if not b:
        return {"party": None, "tier": "unpriced", "p": None}
    party, p = max(b.items(), key=lambda kv: kv[1])
    if p < TOSSUP_HIGH:
        # 40-60 is a toss-up for both sides; anything under 40 for the leader is a multi-way field
        return {"party": None, "tier": "tossup", "p": p, "leader": party if p > TOSSUP_LOW else None}
    return {"party": party, "tier": tier(p), "p": p}


def project(races: list[dict], not_up: dict[str, int], to_win: dict[str, int] | None,
            control_blend: dict[str, float] | None) -> dict:
    by_party: dict[str, dict[str, int]] = {}
    tossups, unpriced = [], []
    for r in races:
        c = r["classification"]
        if c["party"] is None:
            (unpriced if c["tier"] == "unpriced" else tossups).append(r["key"])
            continue
        by_party.setdefault(c["party"], {}).setdefault(c["tier"], 0)
        by_party[c["party"]][c["tier"]] += 1

    parties = sorted(set(by_party) | set(not_up))
    summary = {}
    for p in parties:
        tiers = by_party.get(p, {})
        base = not_up.get(p, 0)
        called = tiers.get("called", 0)
        summary[p] = {
            "not_up": base,
            "called": called,
            "projected": base + sum(tiers.values()),
            "locked": base + called,
            "tiers": {t: tiers.get(t, 0) for t in ("called", "safe", "likely", "lean")},
        }

    declared = None
    for p, s in summary.items():
        need = (to_win or {}).get(p)
        if need and s["locked"] >= need:
            declared = {"party": p, "reason": f"locked seats plus AP calls reached {need}"}
    for p, prob in (control_blend or {}).items():
        if prob > 0.99 and not declared:
            declared = {"party": p, "reason": "control market above 99%"}

    return {
        "parties": summary,
        "tossups": sorted(tossups),
        "unpriced": sorted(unpriced),
        "to_win": to_win,
        "control_blend": control_blend,
        "declared": declared,
    }


def normalize_pills(probs: dict[str, float]) -> dict[str, int]:
    """Rounded display odds that sum to 100 and sit in 1-99 (largest-remainder rounding)."""
    tot = sum(probs.values())
    if not tot:
        return {}
    raw = {k: 100 * v / tot for k, v in probs.items()}
    out = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[: 100 - sum(out.values())]:
        out[k] += 1
    return {k: min(99, max(1, v)) for k, v in out.items()}
