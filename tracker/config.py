"""Static configuration. Every interval can be overridden by an environment variable.

Everything tied to one election derives from ELECTION_DATE: feed URLs, the year in market slugs and
tickers, and the 6 pm ET baseline. To run another cycle, set ELECTION_DATE (see docs/NEXT_CYCLE.md).
"""
import os
from datetime import date

ELECTION_DATE = os.environ.get("ELECTION_DATE", "2026-11-03")
YEAR = ELECTION_DATE[:4]          # "2026": Polymarket slugs, Kalshi control tickers
YY = YEAR[2:]                     # "26": Kalshi race tickers (SENATETX-26)


def _six_pm_et_utc(day: str) -> str:
    """6:00 pm Eastern on election day, in UTC. DST ends on the first Sunday of November, which is
    usually before election day (EST, 23:00Z), but not when November 1 is a Monday (EDT, 22:00Z)."""
    d = date.fromisoformat(day)
    first_sunday = 1 + (6 - date(d.year, 11, 1).weekday()) % 7
    return f"{day}T{23 if d.month == 11 and d.day >= first_sunday else 22}:00:00Z"


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


NYT_BASE = f"https://static01.nyt.com/elections-assets/pages/data/{ELECTION_DATE}"
NYT_FEEDS = {"senate": "results-senate.json", "house": "results-house.json", "governor": "results-governor.json"}
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
UF_EARLY_VOTE = f"https://election.lab.ufl.edu/data-downloads/earlyvote/{YEAR}/US.csv"
POLYMARKET_TAG = os.environ.get("POLYMARKET_TAG", "midterms")  # discovery tag; a presidential year likely uses another

# NYT's CDN sets max-age=5; conditional GETs make 10s polling cheap even for the 9 MB House file.
NYT_INTERVAL = _f("NYT_INTERVAL", 10)
POLYMARKET_INTERVAL = _f("POLYMARKET_INTERVAL", 20)   # REST refresh; websocket is the real-time path
KALSHI_INTERVAL = _f("KALSHI_INTERVAL", 5)     # batched ?tickers= refresh of every known market
KALSHI_MIN_GAP = _f("KALSHI_MIN_GAP", 0.5)      # seconds between Kalshi requests (it 429s readily)
KALSHI_INTERVAL_WITH_WS = _f("KALSHI_INTERVAL_WITH_WS", 30)  # REST sweep once the websocket streams
KALSHI_WS = os.environ.get("KALSHI_WS", "wss://api.elections.kalshi.com/trade-api/ws/v2")
KALSHI_WS_PATH = "/trade-api/ws/v2"  # the path that gets signed
POLYMARKET_WS = os.environ.get("POLYMARKET_WS", "1") != "0"
HISTORY_FILE = os.environ.get("HISTORY_FILE", "data/history.jsonl")
HISTORY_EVERY = _f("HISTORY_EVERY", 20)
USER_AGENT = f"election-night-{YEAR}-tracker/0.1"

# Methodology thresholds (election.offgramercy.com/methodology)
TOSSUP_LOW, TOSSUP_HIGH = 0.40, 0.60
TIERS = [("safe", 0.90), ("likely", 0.75), ("lean", 0.60)]
# 6:00 pm ET on election night, the "since 6 pm" baseline.
BASELINE_TS = os.environ.get("BASELINE_TS") or _six_pm_et_utc(ELECTION_DATE)

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
