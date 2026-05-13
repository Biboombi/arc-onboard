#!/usr/bin/env python3
"""
ArcPredict Terminal — Polymarket Charting + TA + x402 Subscription
═══════════════════════════════════════════════════════════════════
Polymarket prediction market charting terminal with:
  - Real-time Polymarket price data (Gamma + CLOB APIs)
  - Technical analysis: EMA, SMA, RSI, MACD, Bollinger Bands, Fibonacci
  - x402 paywall: one-time payment for 30-day premium access
  - Kraken-inspired dark trading terminal UI

Usage:
    python3 server.py                    # starts on port 8743
    python3 server.py --port 8743        # custom port
    python3 server.py --price 5.0        # $5 USDC/month subscription
"""

import json, time, os, sys, hmac, hashlib, secrets, threading
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from typing import Optional

import uvicorn
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from web3 import Web3
from eth_account.messages import encode_typed_data
from eth_account import Account

import numpy as np

# ─── Constants ──────────────────────────────────────────────────────────
ARC_RPC = "https://rpc.testnet.arc.network"
CHAIN_ID = 5042002
GATEWAY_WALLET = Web3.to_checksum_address("0x0077777d7EBA4688BDeF3E311b846F25870A19B9")
USDC_TOKEN = Web3.to_checksum_address("0x3600000000000000000000000000000000000000")
DEFAULT_PRICE_USD = 5.0  # $5 USDC for 30-day premium
SUBSCRIPTION_DAYS = 30
SUBSCRIPTION_FILE = Path(__file__).parent / "data" / "subscriptions.json"

# EIP-712 domain for GatewayWallet
EIP712_DOMAIN = {
    "name": "GatewayWallet",
    "version": "2",
    "chainId": CHAIN_ID,
    "verifyingContract": GATEWAY_WALLET,
}

EIP712_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}

GW_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "address", "name": "from", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "uint256", "name": "validAfter", "type": "uint256"},
            {"internalType": "uint256", "name": "validBefore", "type": "uint256"},
            {"internalType": "bytes32", "name": "nonce", "type": "bytes32"},
            {"internalType": "uint8", "name": "v", "type": "uint8"},
            {"internalType": "bytes32", "name": "r", "type": "bytes32"},
            {"internalType": "bytes32", "name": "s", "type": "bytes32"},
        ],
        "name": "transferWithAuthorization",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "transferSpecHash", "type": "bytes32"},
        ],
        "name": "isTransferSpecHashUsed",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ─── Init ────────────────────────────────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(ARC_RPC))
gw = w3.eth.contract(address=GATEWAY_WALLET, abi=GW_ABI)
SELLER_KEY = os.environ.get("X402_SELLER_KEY", "")
seller = Account.from_key(SELLER_KEY) if SELLER_KEY else None

app = FastAPI(title="ArcPredict Terminal", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FRONTEND_DIR = Path(__file__).parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# ─── Subscription Manager ───────────────────────────────────────────────
_sub_lock = threading.Lock()
_price_usd = DEFAULT_PRICE_USD


def load_subscriptions() -> dict:
    """Load subscriptions from JSON file."""
    if SUBSCRIPTION_FILE.exists():
        try:
            return json.loads(SUBSCRIPTION_FILE.read_text())
        except Exception:
            pass
    return {}


def save_subscriptions(subs: dict):
    """Save subscriptions to JSON file."""
    SUBSCRIPTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUBSCRIPTION_FILE.write_text(json.dumps(subs, indent=2))


def is_subscribed(address: str) -> dict:
    """Check if an address has an active subscription. Returns {active, expires_at}."""
    address = address.lower()
    subs = load_subscriptions()
    if address not in subs:
        return {"active": False, "expires_at": None}
    entry = subs[address]
    expires = datetime.fromisoformat(entry["expires_at"])
    now = datetime.now(timezone.utc)
    if now > expires:
        return {"active": False, "expires_at": entry["expires_at"]}
    return {"active": True, "expires_at": entry["expires_at"], "since": entry.get("since")}


def activate_subscription(address: str, days: int = SUBSCRIPTION_DAYS) -> dict:
    """Activate or extend a subscription for an address."""
    address = address.lower()
    with _sub_lock:
        subs = load_subscriptions()
        now = datetime.now(timezone.utc)

        # If existing subscription is still active, extend from its expiry
        if address in subs:
            existing = datetime.fromisoformat(subs[address]["expires_at"])
            if existing > now:
                start_from = existing
            else:
                start_from = now
        else:
            start_from = now

        expires = start_from + timedelta(days=days)
        subs[address] = {
            "address": address,
            "since": subs[address]["since"] if address in subs else now.isoformat(),
            "expires_at": expires.isoformat(),
            "last_payment": now.isoformat(),
        }
        save_subscriptions(subs)

    return {"active": True, "expires_at": expires.isoformat(), "days_added": days}


# ─── Payment Helpers ────────────────────────────────────────────────────

def compute_transfer_spec_hash(message: dict) -> bytes:
    from eth_account._utils.encode_typed_data.encoding_and_hashing import hash_domain, hash_eip712_message
    domain_hash = hash_domain(EIP712_DOMAIN)
    msg_hash = hash_eip712_message(EIP712_TYPES, message)
    return Web3.keccak(b"\x19\x01" + domain_hash + msg_hash)


def verify_payment_signature(payload: dict) -> Optional[str]:
    """Verify EIP-712 signature using low-level hash (matches MetaMask v4)."""
    try:
        msg = {
            "from": Web3.to_checksum_address(payload["from"]),
            "to": Web3.to_checksum_address(payload["to"]),
            "value": int(payload["value"]),
            "validAfter": int(payload["validAfter"]),
            "validBefore": int(payload["validBefore"]),
            "nonce": Web3.to_bytes(hexstr=payload["nonce"]),
        }
        sig = payload["signature"]
        r, s, v = (
            Web3.to_bytes(hexstr=sig["r"]),
            Web3.to_bytes(hexstr=sig["s"]),
            int(sig["v"]),
        )

        # Use low-level EIP-712 hash instead of encode_typed_data
        # (encode_typed_data encoding differs from MetaMask eth_signTypedData_v4 in newer eth-account)
        from eth_account._utils.encode_typed_data.encoding_and_hashing import hash_domain, hash_eip712_message
        domain_hash = hash_domain(EIP712_DOMAIN)
        msg_hash = hash_eip712_message(EIP712_TYPES, msg)
        full_hash = Web3.keccak(b"\x19\x01" + domain_hash + msg_hash)
        recovered = Account._recover_hash(full_hash, vrs=(v, r, s))

        if recovered.lower() != msg["from"].lower():
            print(f"[verify] Sig mismatch: recovered={recovered}, from={msg['from']}")
            return None

        # Check timestamps
        now = int(time.time())
        if now < msg["validAfter"]:
            print(f"[verify] Not yet valid: now={now}, validAfter={msg['validAfter']}")
            return None
        if now > msg["validBefore"]:
            print(f"[verify] Expired: now={now}, validBefore={msg['validBefore']}")
            return None

        # Check that value >= price
        price_atomic = int(_price_usd * 1_000_000)
        if msg["value"] < price_atomic:
            print(f"[verify] Insufficient: value={msg['value']}, required={price_atomic}")
            return None

        return recovered
    except Exception as e:
        print(f"[verify] Exception: {e}")
        return None


def settle_payment(payload: dict, seller_addr: str) -> Optional[str]:
    """Submit transferWithAuthorization to GatewayWallet."""
    if not SELLER_KEY:
        return "verified-offchain"
    try:
        msg = payload
        sig = msg["signature"]
        tx = gw.functions.transferWithAuthorization(
            USDC_TOKEN,
            Web3.to_checksum_address(msg["from"]),
            int(msg["value"]),
            int(msg["validAfter"]),
            int(msg["validBefore"]),
            Web3.to_bytes(hexstr=msg["nonce"]),
            int(sig["v"]),
            Web3.to_bytes(hexstr=sig["r"]),
            Web3.to_bytes(hexstr=sig["s"]),
        ).build_transaction({
            "from": seller_addr,
            "nonce": w3.eth.get_transaction_count(seller_addr),
            "gas": 300_000,
            "gasPrice": w3.eth.gas_price,
        })
        signed = w3.eth.account.sign_transaction(tx, SELLER_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()
    except Exception as e:
        print(f"[settle] Failed: {e}")
        return None


# ─── Polymarket Data ────────────────────────────────────────────────────

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
UA = {"User-Agent": "ArcPredict/1.0"}

# Cache for Polymarket market data (refresh every 60s)
_cache = {"markets": None, "events": None, "markets_ts": 0, "events_ts": 0}
CACHE_TTL = 60


def _cached_get(url: str, cache_key: str, ttl: int = CACHE_TTL) -> list:
    """Fetch with simple TTL cache."""
    now = time.time()
    if _cache.get(cache_key) and (now - _cache[f"{cache_key}_ts"]) < ttl:
        return _cache[cache_key]
    try:
        resp = requests.get(url, headers=UA, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _cache[cache_key] = data
        _cache[f"{cache_key}_ts"] = now
        return data
    except Exception as e:
        if _cache.get(cache_key):
            return _cache[cache_key]  # stale cache on error
        raise


def get_top_markets(limit: int = 50) -> list:
    """Get top active prediction markets sorted by volume."""
    markets = _cached_get(
        f"{GAMMA_API}/markets?limit={limit}&closed=false&order=volume24hr&ascending=false",
        "markets", 60
    )
    results = []
    for m in markets[:limit]:
        results.append({
            "id": m.get("id"),
            "question": m.get("question", ""),
            "slug": m.get("slug", ""),
            "volume_24h": float(m.get("volume24hr", 0) or 0),
            "liquidity": float(m.get("liquidity", 0) or 0),
            "outcomes": json.loads(m.get("outcomes", "[]")),
            "outcome_prices": json.loads(m.get("outcomePrices", "[]")),
            "end_date": m.get("endDate", ""),
            "category": m.get("category", ""),
            "active": m.get("active", False),
        })
    return results


def get_popular_events(limit: int = 20) -> list:
    """Get popular events with their markets."""
    events = _cached_get(
        f"{GAMMA_API}/events?limit={limit}&closed=false&order=volume&ascending=false",
        "events", 60
    )
    results = []
    for e in events[:limit]:
        results.append({
            "id": e.get("id"),
            "title": e.get("title", ""),
            "slug": e.get("slug", ""),
            "volume_24h": float(e.get("volume24hr", 0) or 0),
            "liquidity": float(e.get("liquidity", 0) or 0),
            "markets_count": len(e.get("markets", [])),
            "active": e.get("active", False),
        })
    return results


def get_price_history(market_id: str, interval: str = "1h", lookback_hours: int = 168) -> list:
    """Get price history for a market. Uses CLOB token ID (YES outcome).
    Automatically resolves Gamma market ID to CLOB token ID if needed."""
    end_ts = int(time.time())
    start_ts = end_ts - lookback_hours * 3600

    # Resolve CLOB token ID from Gamma market ID if needed
    clob_token_id = market_id
    if not market_id.isdigit() or len(market_id) < 30:
        # Looks like a Gamma market ID — resolve to CLOB token ID
        try:
            resp = requests.get(f"{GAMMA_API}/markets/{market_id}", headers=UA, timeout=10)
            resp.raise_for_status()
            market = resp.json()
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            if token_ids:
                clob_token_id = token_ids[0]  # YES token
        except Exception:
            pass  # Fall through with original ID

    try:
        resp = requests.get(
            f"{CLOB_API}/prices-history",
            params={"market": clob_token_id, "startTs": start_ts, "endTs": end_ts,
                    "interval": interval, "fidelity": 5},
            headers=UA, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        history = data.get("history", [])
        return [{"t": h["t"], "p": h["p"]} for h in history]
    except Exception as e:
        err_str = str(e)
        if "403" in err_str or "Forbidden" in err_str:
            pass  # Resolved or restricted market — silent
        else:
            print(f"[prices-history] Error for {market_id}: {e}")
        return []


def get_order_book(token_id: str) -> dict:
    """Get order book for a specific token."""
    try:
        resp = requests.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            headers=UA, timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"bids": [], "asks": []}


# ─── Technical Analysis (Pure Python) ───────────────────────────────────

def calc_sma(prices: list, period: int = 20) -> list:
    """Simple Moving Average. Returns same-length list with None padding."""
    if len(prices) < period:
        return [None] * len(prices)
    sma = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        sma.append(sum(prices[i - period + 1:i + 1]) / period)
    return sma


def calc_ema(prices: list, period: int = 20) -> list:
    """Exponential Moving Average."""
    if len(prices) < period:
        return [None] * len(prices)
    ema = [None] * (period - 1)
    multiplier = 2 / (period + 1)
    sma_start = sum(prices[:period]) / period
    ema.append(sma_start)
    for price in prices[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


def calc_rsi(prices: list, period: int = 14) -> list:
    """Relative Strength Index."""
    if len(prices) < period + 1:
        return [None] * len(prices)

    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi = [None] * period
    rsi.append(100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss != 0 else 100)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi.append(100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss != 0 else 100)

    return rsi


def calc_macd(prices: list) -> dict:
    """MACD (12, 26, 9). Returns {macd, signal, histogram}."""
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)

    macd_line = [None] * len(prices)
    for i in range(len(prices)):
        if ema12[i] is not None and ema26[i] is not None:
            macd_line[i] = ema12[i] - ema26[i]

    # Signal = 9-period EMA of MACD
    valid_macd = [v for v in macd_line if v is not None]
    if len(valid_macd) >= 9:
        signal_raw = calc_ema(valid_macd, 9)
    else:
        signal_raw = [None] * len(valid_macd)

    # Re-align to original index
    signal = [None] * len(prices)
    offset = len(prices) - len(valid_macd)
    for i, v in enumerate(signal_raw):
        signal[offset + i] = v

    histogram = [None] * len(prices)
    for i in range(len(prices)):
        if macd_line[i] is not None and signal[i] is not None:
            histogram[i] = macd_line[i] - signal[i]

    return {"macd": macd_line, "signal": signal, "histogram": histogram}


def calc_bollinger(prices: list, period: int = 20, std_dev: int = 2) -> dict:
    """Bollinger Bands. Returns {middle, upper, lower}."""
    sma = calc_sma(prices, period)
    upper = [None] * len(prices)
    lower = [None] * len(prices)

    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1:i + 1]
        std = np.std(window)
        upper[i] = sma[i] + std_dev * std
        lower[i] = sma[i] - std_dev * std

    return {"middle": sma, "upper": upper, "lower": lower}


def calc_volume_profile(prices: list, bins: int = 20) -> dict:
    """Volume Profile (simplified — price distribution density)."""
    if not prices:
        return {"levels": [], "poc": None}

    hist, bin_edges = np.histogram(prices, bins=bins)
    poc_idx = int(np.argmax(hist))
    poc_price = (bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2

    levels = []
    max_count = max(hist) if max(hist) > 0 else 1
    for i in range(len(hist)):
        if hist[i] > 0:
            center = (bin_edges[i] + bin_edges[i + 1]) / 2
            levels.append({
                "price": round(float(center), 4),
                "count": int(hist[i]),
                "intensity": round(float(hist[i]) / max_count, 2),
            })

    return {"levels": levels, "poc": round(float(poc_price), 4)}


def calc_fibonacci(prices: list) -> dict:
    """Fibonacci Retracement levels from highest high to lowest low."""
    if len(prices) < 2:
        return {"levels": {}, "trend": "neutral", "high": None, "low": None}

    high = max(prices)
    low = min(prices)
    diff = high - low

    if diff == 0:
        return {"levels": {}, "trend": "neutral", "high": high, "low": low}

    # Check if recent price is closer to high or low to determine trend
    recent = prices[-1]
    trend = "uptrend" if recent > (high + low) / 2 else "downtrend"

    levels = {}
    ratios = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    for r in ratios:
        if trend == "uptrend":
            # Retrace from high down
            levels[f"{r:.3f}"] = round(high - diff * r, 4)
        else:
            # Retrace from low up
            levels[f"{r:.3f}"] = round(low + diff * r, 4)

    return {
        "levels": levels,
        "trend": trend,
        "high": round(high, 4),
        "low": round(low, 4),
        "current": round(recent, 4),
    }


def full_analysis(prices: list) -> dict:
    """Run full technical analysis suite on price data."""
    if len(prices) < 26:
        return {"error": f"Need at least 26 data points, got {len(prices)}"}

    sma20 = calc_sma(prices, 20)
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)
    rsi = calc_rsi(prices, 14)
    macd = calc_macd(prices)
    bb = calc_bollinger(prices, 20, 2)
    vp = calc_volume_profile(prices)
    fib = calc_fibonacci(prices)

    last = len(prices) - 1
    current = prices[-1]

    # Signal summary
    signals = []
    # RSI
    if rsi[last] is not None:
        if rsi[last] < 30:
            signals.append({"indicator": "RSI", "signal": "OVERSOLD", "strength": "STRONG",
                           "value": round(rsi[last], 1)})
        elif rsi[last] > 70:
            signals.append({"indicator": "RSI", "signal": "OVERBOUGHT", "strength": "STRONG",
                           "value": round(rsi[last], 1)})
        elif rsi[last] > 60:
            signals.append({"indicator": "RSI", "signal": "BULLISH", "strength": "MODERATE",
                           "value": round(rsi[last], 1)})
        elif rsi[last] < 40:
            signals.append({"indicator": "RSI", "signal": "BEARISH", "strength": "MODERATE",
                           "value": round(rsi[last], 1)})

    # MACD
    if macd["histogram"][last] is not None:
        h = macd["histogram"][last]
        if h > 0:
            signals.append({"indicator": "MACD", "signal": "BULLISH", "strength": "STRONG" if h > 0.005 else "MODERATE",
                           "value": round(h, 6)})
        else:
            signals.append({"indicator": "MACD", "signal": "BEARISH", "strength": "STRONG" if h < -0.005 else "MODERATE",
                           "value": round(h, 6)})

    # Bollinger
    if bb["upper"][last] and bb["lower"][last]:
        if current > bb["upper"][last]:
            signals.append({"indicator": "Bollinger", "signal": "ABOVE_UPPER", "strength": "STRONG"})
        elif current < bb["lower"][last]:
            signals.append({"indicator": "Bollinger", "signal": "BELOW_LOWER", "strength": "STRONG"})
        bb_width = (bb["upper"][last] - bb["lower"][last]) / bb["middle"][last] * 100
    else:
        bb_width = None

    # Summary score (-100 to +100)
    bullish = sum(1 for s in signals if s["signal"] in ("BULLISH", "OVERSOLD"))
    bearish = sum(1 for s in signals if s["signal"] in ("BEARISH", "OVERBOUGHT", "ABOVE_UPPER", "BELOW_LOWER"))
    score_raw = (bullish - bearish) / max(len(signals), 1) * 100

    return {
        "current_price": round(current, 4),
        "price_change_24h": round(((prices[-1] / prices[0] - 1) * 100) if prices[0] else 0, 2),
        "indicators": {
            "SMA_20": round(sma20[last], 4) if sma20[last] else None,
            "EMA_12": round(ema12[last], 4) if ema12[last] else None,
            "EMA_26": round(ema26[last], 4) if ema26[last] else None,
            "RSI_14": round(rsi[last], 1) if rsi[last] else None,
            "MACD": {
                "line": round(macd["macd"][last], 6) if macd["macd"][last] else None,
                "signal": round(macd["signal"][last], 6) if macd["signal"][last] else None,
                "histogram": round(macd["histogram"][last], 6) if macd["histogram"][last] else None,
            },
            "BB_middle": round(bb["middle"][last], 4) if bb["middle"][last] else None,
            "BB_upper": round(bb["upper"][last], 4) if bb["upper"][last] else None,
            "BB_lower": round(bb["lower"][last], 4) if bb["lower"][last] else None,
            "BB_width_pct": round(bb_width, 2) if bb_width else None,
        },
        "volume_profile": vp,
        "fibonacci": fib,
        "signals": signals,
        "sentiment_score": round(score_raw, 1),
        "sentiment_label": "BULLISH 🔼" if score_raw > 30 else ("BEARISH 🔽" if score_raw < -30 else "NEUTRAL ➡️"),
        "data_points": len(prices),
    }


# ─── Routes ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return index_path.read_text()
    return {
        "service": "ArcPredict Terminal",
        "version": "1.0.0",
        "network": "Arc Testnet",
        "subscription": f"${_price_usd} USDC for {SUBSCRIPTION_DAYS} days",
        "endpoints": [
            "GET /api/markets",
            "GET /api/market/{id}",
            "GET /api/chart/{id}",
            "GET /api/events",
            "POST /api/subscribe (x402 paywall)",
            "GET /api/premium/status?address=0x...",
        ],
        "docs": "/docs",
    }


@app.get("/api/markets")
def api_markets(limit: int = 50, category: str = None):
    """Get top active prediction markets."""
    markets = get_top_markets(limit)
    if category:
        markets = [m for m in markets if m.get("category", "").lower() == category.lower()]
    return {"count": len(markets), "markets": markets}


@app.get("/api/events")
def api_events(limit: int = 20):
    """Get popular prediction market events."""
    events = get_popular_events(limit)
    return {"count": len(events), "events": events}


@app.get("/api/market/{market_id}")
def api_market_detail(market_id: str):
    """Get single market detail with price chart data + basic TA."""
    # Fetch from Gamma
    try:
        resp = requests.get(f"{GAMMA_API}/markets/{market_id}", headers=UA, timeout=10)
        resp.raise_for_status()
        market = resp.json()
    except Exception:
        return JSONResponse(status_code=404, content={"error": "Market not found"})

    # Get price history
    prices = get_price_history(market_id, interval="1h", lookback_hours=168)
    price_values = [p["p"] for p in prices]

    # Basic free indicators
    free_analysis = {}
    if len(price_values) >= 5:
        free_analysis = {
            "current": round(price_values[-1], 4),
            "high_24h": round(max(price_values[-24:]) if len(price_values) >= 24 else max(price_values), 4),
            "low_24h": round(min(price_values[-24:]) if len(price_values) >= 24 else min(price_values), 4),
            "change_24h_pct": round(((price_values[-1] / price_values[-24] - 1) * 100)
                                    if len(price_values) >= 24 and price_values[-24] > 0
                                    else ((price_values[-1] / price_values[0] - 1) * 100 if price_values[0] > 0 else 0), 2),
            "data_points": len(price_values),
        }

    return {
        "id": market.get("id"),
        "question": market.get("question"),
        "slug": market.get("slug"),
        "volume_24h": float(market.get("volume24hr", 0) or 0),
        "liquidity": float(market.get("liquidity", 0) or 0),
        "outcomes": json.loads(market.get("outcomes", "[]")),
        "outcome_prices": json.loads(market.get("outcomePrices", "[]")),
        "end_date": market.get("endDate"),
        "category": market.get("category"),
        "active": market.get("active"),
        "prices": prices[-200:],  # Last 200 data points for chart
        "analysis": free_analysis,
    }


@app.get("/api/chart/{market_id}")
def api_chart_data(market_id: str, interval: str = "1h", lookback: int = 168):
    """Get chart OHLC data for a market."""
    prices = get_price_history(market_id, interval=interval, lookback_hours=lookback)
    return {
        "market_id": market_id,
        "interval": interval,
        "data": prices,
        "count": len(prices),
    }


# ─── Premium Endpoints (requires subscription) ──────────────────────────

def _check_premium(request: Request) -> tuple:
    """Check premium subscription. Returns (address, status_dict) or (None, error_response)."""
    address = request.headers.get("X-Address", "")
    if not address:
        return None, JSONResponse(status_code=401, content={"error": "X-Address header required"})

    try:
        address = Web3.to_checksum_address(address)
    except Exception:
        return None, JSONResponse(status_code=400, content={"error": "Invalid address format"})

    sub = is_subscribed(address)
    if not sub["active"]:
        return None, JSONResponse(status_code=402, content={
            "error": "Premium subscription required",
            "message": f"Subscribe for ${_price_usd} USDC to unlock premium TA (30-day access)",
            "subscription": sub,
            "subscribe_endpoint": "/api/subscribe",
        })

    return address, sub


@app.get("/api/premium/status")
def api_premium_status(request: Request):
    """Check subscription status for an address."""
    address = request.headers.get("X-Address", "")
    if not address:
        return JSONResponse(status_code=400, content={"error": "X-Address header required"})

    try:
        address = Web3.to_checksum_address(address)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid address format"})

    sub = is_subscribed(address)
    return {
        "address": address,
        "subscription": sub,
        "price": f"${_price_usd} USDC for {SUBSCRIPTION_DAYS} days",
    }


@app.get("/api/premium/analysis/{market_id}")
def api_premium_analysis(market_id: str, request: Request):
    """Full technical analysis — requires subscription."""
    _, sub = _check_premium(request)
    if sub is None:
        return _

    prices = get_price_history(market_id, interval="1h", lookback_hours=336)
    price_values = [p["p"] for p in prices]

    if len(price_values) < 26:
        return JSONResponse(status_code=400, content={
            "error": f"Insufficient data. Have {len(price_values)} points, need >= 26"
        })

    analysis = full_analysis(price_values)
    analysis["subscription"] = sub
    return analysis


@app.get("/api/premium/signals")
def api_premium_signals(request: Request, limit: int = 20):
    """Scan top markets for the strongest signals — requires subscription."""
    _, sub = _check_premium(request)
    if sub is None:
        return _

    markets = get_top_markets(50)
    signals_list = []

    for m in markets:
        try:
            prices = get_price_history(m["id"], interval="1h", lookback_hours=168)
            price_values = [p["p"] for p in prices]
            if len(price_values) < 26:
                continue

            rsi = calc_rsi(price_values, 14)
            last_rsi = rsi[-1] if rsi[-1] else 50

            sma20 = calc_sma(price_values, 20)
            current = price_values[-1]

            trend = "BULLISH"
            if last_rsi > 70:
                trend = "OVERBOUGHT"
            elif last_rsi < 30:
                trend = "OVERSOLD"
            elif current < sma20[-1] if sma20[-1] else True:
                trend = "BEARISH"

            signals_list.append({
                "market_id": m["id"],
                "question": m["question"][:80],
                "price": round(price_values[-1], 4),
                "rsi": round(last_rsi, 1),
                "trend": trend,
                "volume_24h": m["volume_24h"],
                "change_pct": round((price_values[-1] / price_values[0] - 1) * 100 if price_values[0] else 0, 2),
            })
        except Exception:
            continue

    # Sort by RSI extremes (most actionable first)
    signals_list.sort(key=lambda x: abs(x["rsi"] - 50), reverse=True)

    return {
        "subscription": sub,
        "count": len(signals_list),
        "signals": signals_list[:limit],
    }


# ─── Subscription Payment ───────────────────────────────────────────────

@app.post("/api/subscribe")
def api_subscribe(request: Request):
    """
    Subscribe to ArcPredict Premium via x402 payment.
    Client signs EIP-712 TransferWithAuthorization for $5 USDC.
    On success, activates 30-day subscription.
    """
    payment_header = request.headers.get("Payment-Signature", "")
    address_header = request.headers.get("X-Address", "")

    # ── No payment → 402 ──
    if not payment_header:
        price_atomic = int(_price_usd * 1_000_000)
        nonce = "0x" + secrets.token_hex(32)
        payment_req = {
            "scheme": "GatewayWalletBatched",
            "network": "arc-testnet",
            "chainId": CHAIN_ID,
            "verifyingContract": GATEWAY_WALLET,
            "token": USDC_TOKEN,
            "to": seller.address if seller else "0x0000000000000000000000000000000000000000",
            "price": str(price_atomic),
            "priceHuman": f"${_price_usd} USDC",
            "nonce": nonce,
            "validAfter": 0,
            "validBefore": int(time.time()) + 3600,
            "label": f"ArcPredict Premium — {SUBSCRIPTION_DAYS} days",
        }
        return JSONResponse(
            status_code=402,
            content={
                "error": "Payment Required",
                "message": f"Subscribe for ${_price_usd} USDC ({SUBSCRIPTION_DAYS}-day access)",
                "payment": payment_req,
                "howto": "Sign EIP-712 TransferWithAuthorization against GatewayWallet, retry with Payment-Signature + X-Address headers",
            },
            headers={
                "PAYMENT-REQUIRED": json.dumps(payment_req),
                "X-Payment-Network": "arc-testnet",
                "X-Price": f"${_price_usd}",
                "X-Subscription-Days": str(SUBSCRIPTION_DAYS),
            },
        )

    # ── Payment present → verify ──
    try:
        payload = json.loads(payment_header)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "Invalid Payment-Signature format"})

    buyer = verify_payment_signature(payload)
    if not buyer:
        return JSONResponse(status_code=402, content={"error": "Invalid or expired payment signature"})

    # Check value
    price_atomic = int(_price_usd * 1_000_000)
    if int(payload["value"]) < price_atomic:
        return JSONResponse(status_code=402, content={
            "error": f"Insufficient payment. Required: {price_atomic}, got: {payload['value']}"
        })

    # Check non-replay
    spec_hash = compute_transfer_spec_hash({
        "from": Web3.to_checksum_address(payload["from"]),
        "to": Web3.to_checksum_address(payload["to"]),
        "value": int(payload["value"]),
        "validAfter": int(payload["validAfter"]),
        "validBefore": int(payload["validBefore"]),
        "nonce": Web3.to_bytes(hexstr=payload["nonce"]),
    })
    try:
        if gw.functions.isTransferSpecHashUsed(spec_hash).call():
            return JSONResponse(status_code=402, content={"error": "Payment already used (double-spend)"})
    except Exception:
        pass

    # Settle on-chain
    if seller:
        tx_hash = settle_payment(payload, seller.address)
    else:
        tx_hash = "verification-only"

    # Activate subscription
    sub = activate_subscription(buyer, SUBSCRIPTION_DAYS)

    return {
        "success": True,
        "message": f"Subscription activated! Premium access until {sub['expires_at']}",
        "subscription": sub,
        "buyer": buyer,
        "settlement_tx": tx_hash,
        "price_paid": f"${int(payload['value']) / 1_000_000:.2f} USDC",
    }


# ─── Main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ArcPredict Terminal")
    parser.add_argument("--port", type=int, default=None, help="Server port (default: $PORT or 8743)")
    parser.add_argument("--price", type=float, default=DEFAULT_PRICE_USD, help="Subscription price in USDC")
    parser.add_argument("--days", type=int, default=SUBSCRIPTION_DAYS, help="Subscription duration in days")
    parser.add_argument("--key", type=str, default="", help="Seller private key")
    args = parser.parse_args()

    price_usd = args.price
    sub_days = args.days

    seller_key = args.key or SELLER_KEY
    if seller_key:
        seller = Account.from_key(seller_key)

    port = args.port or int(os.environ.get("PORT", 8743))

    print(f"📊 ArcPredict Terminal v1.0.0")
    if seller:
        print(f"🔑 Seller: {seller.address}")
        try:
            gw_bal = gw.functions.availableBalance(USDC_TOKEN, seller.address).call()
            print(f"   Gateway balance: {gw_bal / 1e6} USDC")
        except Exception:
            pass
    else:
        print("⚠️  No seller key — verification-only mode")

    print(f"💎 Premium: ${price_usd} USDC / {sub_days} days")
    print(f"🌐 Server: http://0.0.0.0:{port}")
    print(f"📚 Docs: http://0.0.0.0:{port}/docs")

    # Set module-level globals used by API handlers
    import __main__
    __main__._price_usd = price_usd
    __main__.SUBSCRIPTION_DAYS = sub_days
    print()

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
