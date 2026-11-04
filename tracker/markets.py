"""Market discovery and parsing for Polymarket (Gamma + CLOB) and Kalshi (Trade API v2)."""
from __future__ import annotations

import json
import re

from .config import STATES, YEAR, YY

PARTY_RE = re.compile(r"\((D|R|I|L|G)\)\s*$")
PARTY_WORDS = {"democrat": "D", "democratic": "D", "republican": "R", "independent": "I"}


def display_price(bid: float | None, ask: float | None, last: float | None) -> float | None:
    """Polymarket's display rule, used for both books: the bid/ask midpoint, or the last trade
    when the spread is wider than 10 cents (thin book) or a side is missing."""
    if bid is not None and ask is not None and ask > 0 and ask - bid <= 0.10:
        return round((bid + ask) / 2, 4)
    return last if last is not None else (bid if bid is not None else None)


def _num(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def party_of(label: str) -> str | None:
    if not label:
        return None
    m = PARTY_RE.search(label.strip())
    if m:
        return m.group(1)
    low = label.lower()
    for w, p in PARTY_WORDS.items():
        if w in low:
            return p
    return None


# ---------------------------------------------------------------- Polymarket

AT_LARGE = {"AK", "DE", "ND", "SD", "VT", "WY"}
_NAME_TO_ST = {v.lower().replace(" ", "-"): k for k, v in STATES.items()}
_SEN_RE = re.compile(r"^([a-z-]+?)-senate-election-winner(?:-\d+)?$")
_GOV_RE = re.compile(rf"^([a-z-]+?)-governor-(?:winner-{YEAR}|election-winner|election-{YEAR})$")
_HOUSE_RE = re.compile(r"^([a-z]{2})-(\d{2}|al)-house-election-winner$")
_HOUSE_RE2 = re.compile(r"^which-party-will-win-the-house-race-for-the-([a-z]{2})-(\d{2}|al)-seat$")


def house_district(st: str, district: str) -> str:
    return "AL" if st in AT_LARGE else district


def pm_slug(race: dict) -> str | None:
    """Conventional slug, used as a fallback probe when tag discovery misses a race."""
    st = race["state"]
    name = STATES.get(st, "").lower().replace(" ", "-")
    if race["office"] == "senate":
        return f"{name}-senate-election-winner"
    if race["office"] == "governor":
        return f"{name}-governor-winner-{YEAR}"
    if race["office"] == "house":
        return f"{st.lower()}-{house_district(st, race['district']).lower()}-house-election-winner"
    return None


def race_key_from_pm_slug(slug: str) -> str | None:
    """Map a Polymarket event slug to our race key (special races are resolved by the caller)."""
    if m := _SEN_RE.match(slug):
        st = _NAME_TO_ST.get(m.group(1))
        return f"S-{st}" if st else None
    if m := _GOV_RE.match(slug):
        st = _NAME_TO_ST.get(m.group(1))
        return f"G-{st}" if st else None
    if m := (_HOUSE_RE.match(slug) or _HOUSE_RE2.match(slug)):
        st, d = m.group(1).upper(), m.group(2)
        return f"H-{st}-{'01' if d == 'al' else d}"
    return None


PM_CONTROL = {
    "senate": f"which-party-will-win-the-senate-in-{YEAR}",
    "house": f"which-party-will-win-the-house-in-{YEAR}",
    "balance": f"balance-of-power-{YEAR}-midterms",  # midterm-only name; check the slug in a presidential year
}


def parse_pm_event(ev: dict) -> dict:
    """Return {'slug','title','volume','closed','outcomes':[{label,party,token,bid,ask,last,p,closed,resolved}]}"""
    outs = []
    for m in ev.get("markets") or []:
        label = m.get("groupItemTitle") or m.get("question") or ""
        prices = m.get("outcomePrices")
        if prices is None:
            continue  # placeholder outcomes ("Person A", "Party C") have no prices
        try:
            tokens = json.loads(m.get("clobTokenIds") or "[]")
        except ValueError:
            tokens = []
        try:
            yes_px = _num(json.loads(prices)[0]) if prices else None
        except (ValueError, IndexError):
            yes_px = None
        bid, ask, last = _num(m.get("bestBid")), _num(m.get("bestAsk")), _num(m.get("lastTradePrice"))
        closed = bool(m.get("closed"))
        p = yes_px if closed else (display_price(bid, ask, last) if (bid or ask or last) else yes_px)
        outs.append({
            "label": label,
            "party": party_of(label),
            "token": tokens[0] if tokens else None,
            "bid": bid, "ask": ask, "last": last, "p": p,
            "volume": _num(m.get("volumeNum") or m.get("volume")),
            "closed": closed,
        })
    return {
        "slug": ev.get("slug"),
        "title": ev.get("title"),
        "volume": _num(ev.get("volume")),
        "closed": bool(ev.get("closed")),
        "outcomes": outs,
    }


# ---------------------------------------------------------------- Kalshi

KALSHI_CONTROL = {"senate": f"CONTROLS-{YEAR}", "house": f"CONTROLH-{YEAR}"}


def kalshi_candidates(race: dict) -> list[str]:
    """Event tickers to try, in order, for a race. First one that exists wins."""
    st = race["state"]
    if race["office"] == "senate":
        if race["special"]:
            return [f"SENATE{st}S-{YY}", f"KXSENATE{st}S-{YY}"]
        if st == "LA":
            return [f"KXSENATELA-{YY}NOV", f"SENATELA-{YY}"]
        return [f"SENATE{st}-{YY}", f"KXSENATE{st}-{YY}"]
    if race["office"] == "governor":
        return [f"GOVPARTY{st}-{YY}", f"KXGOVPARTY{st}-{YY}"]
    if race["office"] == "house":
        # Most districts come from the KXHOUSERACE series listing; older per-district series
        # (HOUSETX15-26, KXHOUSENC11-26) are probed only for competitive seats, see engine.
        d = int(race["district"])
        return [f"HOUSE{st}{d}-{YY}", f"KXHOUSE{st}{d}-{YY}"]
    return []


def race_key_from_kalshi_house(event_ticker: str) -> str | None:
    """KXHOUSERACE-WV02-26 -> H-WV-02 ; KXHOUSERACE-WYAL-26 -> H-WY-01"""
    m = re.match(rf"^KXHOUSERACE-([A-Z]{{2}})(\d{{2}}|AL)-{YY}$", event_ticker)
    if not m:
        return None
    return f"H-{m.group(1)}-{'01' if m.group(2) == 'AL' else m.group(2)}"


def _rules_party(rules: str) -> str | None:
    m = re.search(r"(Democratic|Republican) Party", rules)
    return {"Democratic": "D", "Republican": "R"}[m.group(1)] if m else None


def parse_kalshi_markets(markets: list[dict]) -> list[dict]:
    outs = []
    for m in markets:
        ticker = m.get("ticker", "")
        suffix = ticker.rsplit("-", 1)[-1]
        label = m.get("yes_sub_title") or m.get("subtitle") or suffix
        party = suffix if suffix in ("D", "R", "I") else (
            _rules_party(m.get("rules_primary") or "") or party_of(label))
        bid, ask = _num(m.get("yes_bid_dollars")), _num(m.get("yes_ask_dollars"))
        last = _num(m.get("last_price_dollars"))
        result = (m.get("result") or "").lower()
        if result in ("yes", "no"):
            p = 1.0 if result == "yes" else 0.0
        else:
            p = display_price(bid, ask, last)
        outs.append({
            "label": label, "party": party, "ticker": ticker,
            "bid": bid, "ask": ask, "last": last, "p": p,
            "volume": _num(m.get("volume_fp") or m.get("volume")),
            "status": m.get("status"), "result": result or None,
        })
    return outs


def party_probs(outcomes: list[dict]) -> dict[str, float]:
    """Collapse a book's outcomes to {party: probability}. Several candidates from one party
    (top-two / jungle races) are summed. Unknown-party outcomes are dropped."""
    agg: dict[str, float] = {}
    for o in outcomes:
        if o.get("party") and o.get("p") is not None:
            agg[o["party"]] = agg.get(o["party"], 0.0) + o["p"]
    return {k: round(min(v, 1.0), 4) for k, v in agg.items()}
