"""Offline tests against trimmed captures of the real feeds (taken 2026-11-04 ~00:35 UTC)."""
import json
from pathlib import Path

from tracker import markets as M
from tracker import model
from tracker.nyt import _caucus, parse_feed

FX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text())


def test_nyt_senate_parse():
    feed = parse_feed(load("nyt_senate.json"), "S")
    races = feed["races"]
    assert set(races) == {"S-VA", "S-FL-special", "S-TX"}
    va = races["S-VA"]
    assert va["called"] and va["winner"] == "Mark Warner" and va["winner_party"] == "D"
    fl = races["S-FL-special"]
    assert fl["special"] and not fl["called"] and fl["eevp"] > 0
    assert fl["candidates"][0]["votes"] >= fl["candidates"][1]["votes"]
    assert fl["dem_margin_pct"] is not None
    ch = feed["chamber"]
    assert ch["not_up"] == {"D": 34, "R": 31, "I": 0}  # methodology: 34 Dem caucus, 31 Rep
    assert ch["to_win"]["R"] == 50 and ch["to_win"]["D"] == 51
    assert va["poll_close"]


def test_nyt_house_skips_national_row():
    feed = parse_feed(load("nyt_house.json"), "H")
    assert "H-AK-01" in feed["races"] and "H-VA-02" in feed["races"]
    assert not any(k.startswith("H-US") for k in feed["races"])


def test_caucus_shapes():
    assert _caucus("DEM") == "D"
    assert _caucus({"nyt_id": "GOP"}) == "R"
    assert _caucus(None) is None


def test_display_price_rule():
    assert M.display_price(0.82, 0.83, 0.5) == 0.825
    assert M.display_price(0.40, 0.60, 0.55) == 0.55   # spread > 10c -> last trade
    assert M.display_price(None, 0.6, 0.58) == 0.58


def test_pm_event_and_party_probs():
    ev = M.parse_pm_event(load("pm_senate_control.json")[0])
    assert len(ev["outcomes"]) == 2  # placeholder "Party A".."Other" rows dropped
    probs = M.party_probs(ev["outcomes"])
    assert set(probs) == {"D", "R"} and abs(sum(probs.values()) - 1) < 0.05


def test_kalshi_parse():
    outs = M.parse_kalshi_markets(load("kalshi_controls.json")["markets"])
    assert {o["party"] for o in outs} >= {"R"}
    assert all(0 <= o["p"] <= 1 for o in outs)


def test_slug_and_ticker_mapping():
    assert M.race_key_from_pm_slug("alabama-senate-election-winner-154") == "S-AL"
    assert M.race_key_from_pm_slug("new-hampshire-governor-winner-2026") == "G-NH"
    assert M.race_key_from_pm_slug("alaska-governor-election-winner") == "G-AK"
    assert M.race_key_from_pm_slug("ak-al-house-election-winner") == "H-AK-01"
    assert M.race_key_from_pm_slug("which-party-will-win-the-house-race-for-the-ca-22-seat") == "H-CA-22"
    assert M.race_key_from_pm_slug("texas-senate-margin-of-victory-2026") is None
    assert M.race_key_from_kalshi_house("KXHOUSERACE-WV02-26") == "H-WV-02"
    assert M.race_key_from_kalshi_house("KXHOUSERACE-WYAL-26") == "H-WY-01"
    assert M.kalshi_candidates({"office": "senate", "state": "OH", "special": True})[0] == "SENATEOHS-26"
    assert M.kalshi_candidates({"office": "senate", "state": "LA", "special": False})[0] == "KXSENATELA-26NOV"
    assert M.party_of("James Talarico (D)") == "D" and M.party_of("Dan Osborn (I)") == "I"


def test_blend_and_classify():
    assert model.blend({"D": 0.8, "R": 0.2}, {"D": 0.7}) == {"D": 0.75, "R": 0.2}
    assert model.blend(None, {"R": 0.9}) == {"R": 0.9}
    assert model.classify({"blend": {"D": 0.95}})["tier"] == "safe"
    assert model.classify({"blend": {"D": 0.8}})["tier"] == "likely"
    assert model.classify({"blend": {"R": 0.65, "D": 0.35}})["tier"] == "lean"
    assert model.classify({"blend": {"R": 0.55, "D": 0.45}})["party"] is None
    # a call replaces the market, so nothing is double counted
    c = model.classify({"called": True, "winner_party": "R", "blend": {"D": 0.99}})
    assert c == {"party": "R", "tier": "called", "p": 1.0}
    assert model.classify({"blend": {}})["tier"] == "unpriced"


def test_projection_and_declaration():
    races = [{"key": f"S-{i}", "classification": model.classify({"blend": {"D": 0.95}})} for i in range(10)]
    races += [{"key": "S-X", "classification": model.classify({"called": True, "winner_party": "D"})}]
    races += [{"key": "S-Y", "classification": model.classify({"blend": {"D": 0.5, "R": 0.5}})}]
    p = model.project(races, {"D": 34, "R": 31}, {"D": 51, "R": 50}, {"D": 0.8, "R": 0.2})
    assert p["parties"]["D"]["projected"] == 45 and p["parties"]["D"]["locked"] == 35
    assert p["tossups"] == ["S-Y"] and p["declared"] is None
    p = model.project(races, {"D": 50, "R": 31}, {"D": 51, "R": 50}, None)
    assert p["declared"]["party"] == "D"
    p = model.project([], {"D": 34}, {"D": 51}, {"D": 0.995})
    assert p["declared"]["reason"].startswith("control market")


def test_pills_sum_to_100():
    pills = model.normalize_pills({"D": 0.825, "R": 0.185})
    assert sum(pills.values()) == 100 and pills["D"] > pills["R"]
    assert model.normalize_pills({"D": 0.999, "R": 0.001}) == {"D": 99, "R": 1}


# ---------------------------------------------------------------- Kalshi auth + websocket

def _pem(key):
    from cryptography.hazmat.primitives import serialization
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def test_kalshi_signer_ed25519_and_rsa():
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
    from tracker.kalshi_auth import KalshiSigner

    ek = ed25519.Ed25519PrivateKey.generate()
    h = KalshiSigner("kid", _pem(ek)).headers("GET", "/trade-api/ws/v2?x=1", ts_ms=1700000000000)
    assert h["KALSHI-ACCESS-KEY"] == "kid" and h["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"
    ek.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), b"1700000000000GET/trade-api/ws/v2")

    rk = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    h = KalshiSigner("kid", _pem(rk)).headers("GET", "/trade-api/ws/v2", ts_ms=1)
    rk.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), b"1GET/trade-api/ws/v2",
                           padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                           hashes.SHA256())


def test_kalshi_key_env_formats(monkeypatch):
    import base64
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from tracker.kalshi_auth import KalshiSigner

    import pytest
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "kid")
    for k in (ed25519.Ed25519PrivateKey.generate(), rsa.generate_private_key(public_exponent=65537, key_size=2048)):
        pem = _pem(k).decode()
        der = k.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
        pem_body = "".join(pem.strip().splitlines()[1:-1])  # the PEM with its BEGIN/END lines dropped
        for form in (pem, pem.replace("\n", "\\n"), base64.b64encode(pem.encode()).decode(),
                     base64.b64encode(der).decode(), pem_body):
            monkeypatch.setenv("KALSHI_PRIVATE_KEY", form)
            assert KalshiSigner.from_env() is not None

    # A key that is set but unreadable must raise (reported as "key not usable"), not look unset
    for bad in ("not base64 !!", base64.b64encode(b"\x00" * 48).decode()):
        monkeypatch.setenv("KALSHI_PRIVATE_KEY", bad)
        with pytest.raises(ValueError):
            KalshiSigner.from_env()

    monkeypatch.delenv("KALSHI_PRIVATE_KEY")
    assert KalshiSigner.from_env() is None


def test_kalshi_ws_ticker_updates_price():
    from tracker.engine import Engine
    e = Engine()
    e.kx_events["SENATETX-26"] = M.parse_kalshi_markets(load("kalshi_controls.json")["markets"])
    t = e.kx_events["SENATETX-26"][0]["ticker"]
    e._on_kx_ws({"type": "ticker", "sid": 1, "msg": {"market_ticker": t, "yes_bid_dollars": "0.6100",
                                                     "yes_ask_dollars": "0.6300", "price_dollars": "0.6200"}})
    o = e.kx_events["SENATETX-26"][0]
    assert (o["bid"], o["ask"], o["p"]) == (0.61, 0.63, 0.62)
    assert e.status["kalshi_ws"]["last_msg"]


def test_live_payload_is_compact_and_complete():
    from tracker.live import live_payload, slim_race
    race = {"key": "S-TX", "office": "senate", "state": "TX", "district": None, "special": False, "called": False,
            "winner": None, "winner_party": None, "flip": None, "runoff": False, "eevp": 23.0, "total_votes": 100,
            "expected_votes": 400, "est_votes_outstanding": 300, "uncontested": False,
            "poll_close": "2026-11-04T01:00:00Z", "call_status": "too_early", "called_at": None,
            "blend": {"D": 0.9, "R": 0.1}, "rating": {"normalized": "TOSS_UP", "battleground": True},
            "classification": {"party": "D", "tier": "likely", "p": 0.9},
            "candidates": [{"name": "A", "party": "D", "caucus": "D", "votes": 60, "pct": 60, "winner": False,
                            "incumbent": False, "img": "x"}],
            "markets": {"polymarket": {"outcomes": ["big"]}}, "provenance": {"results": "AP"}}
    s = slim_race(race)
    assert s["tier"] == "likely" and s["rating"] == "TOSS_UP" and s["battleground"] is True
    assert "markets" not in s and "provenance" not in s and "img" not in s["candidates"][0]
    out = live_payload({"version": 3, "races": [race], "calls": []}, {"nyt": {"ok": True, "age_seconds": 2, "stale": False, "x": 1}})
    assert out["version"] == 3 and out["sources"]["nyt"] == {"ok": True, "age_seconds": 2, "stale": False}


def test_page_and_map_are_served():
    from fastapi.testclient import TestClient
    from tracker.app import app
    c = TestClient(app)  # no lifespan: the engine (and its network pollers) never starts
    r = c.get("/")
    assert r.status_code == 200 and "/static/app.js" in r.text
    assert c.get("/static/app.js").status_code == 200
    m = c.get("/static/us-states.json").json()
    assert len(m["states"]) == 51 and m["states"]["TX"]["d"].startswith("M")
