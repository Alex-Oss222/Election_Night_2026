"""Static configuration. Every interval can be overridden by an environment variable."""
import os

ELECTION_DATE = "2026-11-03"


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
UF_EARLY_VOTE = "https://election.lab.ufl.edu/data-downloads/earlyvote/2026/US.csv"

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
USER_AGENT = "election-night-2026-tracker/0.1"

# Methodology thresholds (election.offgramercy.com/methodology)
TOSSUP_LOW, TOSSUP_HIGH = 0.40, 0.60
TIERS = [("safe", 0.90), ("likely", 0.75), ("lean", 0.60)]
# 6:00 pm ET on election night, the "since 6 pm" baseline.
BASELINE_TS = "2026-11-03T23:00:00Z"

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
