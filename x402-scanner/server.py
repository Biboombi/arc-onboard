#!/usr/bin/env python3
"""
x402 Paywalled Multi-Factor Crypto Scanner — Server
═══════════════════════════════════════════════════════
Runs on Arc testnet. Accepts USDC nanopayments via Circle Gateway.
Returns multi-factor trading signals (OI, funding rate, RSI, taker flow).

Usage:
    python3 server.py                    # starts on port 8742
    python3 server.py --port 8742        # custom port
    python3 server.py --price 0.01       # $0.01 USDC per scan (default)
"""

import json, time, os, sys, hmac, hashlib, secrets
import requests
from datetime import datetime, timezone
from pathlib import Path

# Auto-load .env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # python-dotenv not installed, env vars still work
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from web3 import Web3
from eth_account.messages import encode_typed_data
from eth_account import Account

# ─── Constants ──────────────────────────────────────────────────────────
ARC_RPC = "https://rpc.testnet.arc.network"
CHAIN_ID = 5042002
GATEWAY_WALLET = Web3.to_checksum_address("0x0077777d7EBA4688BDeF3E311b846F25870A19B9")
USDC_TOKEN = Web3.to_checksum_address("0x3600000000000000000000000000000000000000")
DEFAULT_PRICE_USD = 0.01  # $0.01 per scan

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

# Minimal GatewayWallet ABI for settlement
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
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "address", "name": "depositor", "type": "address"},
        ],
        "name": "availableBalance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

# ─── Init ────────────────────────────────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(ARC_RPC))
gw = w3.eth.contract(address=GATEWAY_WALLET, abi=GW_ABI)
SELLER_KEY = os.environ.get("X402_SELLER_KEY", "")
seller = Account.from_key(SELLER_KEY) if SELLER_KEY else None

app = FastAPI(title="Hermes x402 Scanner", version="1.0.0")

# CORS — allow frontend from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
FRONTEND_DIR = Path(__file__).parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ─── Payment Helpers ────────────────────────────────────────────────────

def compute_transfer_spec_hash(message: dict) -> bytes:
    """Compute the EIP-712 typed data hash for TransferWithAuthorization."""
    from eth_account._utils.encode_typed_data.encoding_and_hashing import hash_domain, hash_eip712_message
    domain_hash = hash_domain(EIP712_DOMAIN)
    msg_hash = hash_eip712_message(EIP712_TYPES, message)
    return Web3.keccak(b"\x19\x01" + domain_hash + msg_hash)


def verify_payment_signature(payload: dict) -> Optional[str]:
    """
    Verify EIP-712 signature. Returns buyer address if valid, None otherwise.
    payload: { from, to, value, validAfter, validBefore, nonce, signature: {r, s, v} }
    """
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

        encoded = encode_typed_data(
            domain_data=EIP712_DOMAIN,
            message_types=EIP712_TYPES,
            message_data=msg,
        )
        recovered = Account.recover_message(encoded, vrs=(v, r, s))
        if recovered.lower() != msg["from"].lower():
            return None

        # Check timestamps
        now = int(time.time())
        if now < msg["validAfter"]:
            return None  # not yet valid
        if now > msg["validBefore"]:
            return None  # expired

        # Check that value >= price
        price_atomic = int(DEFAULT_PRICE_USD * 1_000_000)
        if msg["value"] < price_atomic:
            return None

        return recovered
    except Exception:
        return None


def settle_payment(payload: dict, seller_addr: str) -> Optional[str]:
    """
    Submit transferWithAuthorization to GatewayWallet.
    Returns tx hash on success, None on failure.
    """
    if not SELLER_KEY:
        # Read-only mode: skip settlement, just verify
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


# ─── Scanner Logic ──────────────────────────────────────────────────────

def fetch_scanner_data(symbol: str) -> dict:
    """Fetch OI, funding rate, and RSI from Bybit (Binance geo-blocked on Railway)."""
    sym = symbol.upper()
    data = {"symbol": sym, "timestamp": datetime.now(timezone.utc).isoformat(), "source": "bybit"}

    def bybit_get(path: str) -> dict:
        url = f"https://api.bybit.com{path}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("retCode") != 0:
            raise Exception(f"Bybit API error: {result.get('retMsg', 'unknown')}")
        return result["result"]

    try:
        # Tickers (mark price, index price, funding rate)
        tk = bybit_get(f"/v5/market/tickers?category=linear&symbol={sym}")
        ticker = tk["list"][0]
        data["mark_price"] = float(ticker["markPrice"])
        data["price"] = float(ticker.get("indexPrice", ticker["lastPrice"]))
        data["funding_rate"] = round(float(ticker.get("fundingRate", 0)) * 100, 4)
        data["current_price"] = float(ticker["lastPrice"])

        # Open Interest
        oi_result = bybit_get(f"/v5/market/open-interest?category=linear&symbol={sym}&intervalTime=1h&limit=2")
        oi_list = oi_result["list"]
        if oi_list:
            data["open_interest_usd"] = round(float(oi_list[0]["openInterest"]), 2)
        if len(oi_list) >= 2:
            oi_curr = float(oi_list[0]["openInterest"])
            oi_prev = float(oi_list[1]["openInterest"])
            data["oi_delta_pct"] = round((oi_curr - oi_prev) / oi_prev * 100, 2) if oi_prev else 0
        else:
            data["oi_delta_pct"] = 0

        # Taker ratio — Bybit doesn't have direct equivalent, approximate via ticker volume
        data["taker_ratio"] = None
        data["taker_ratio_delta"] = None

        # Klines for RSI(14)
        klines = bybit_get(f"/v5/market/kline?category=linear&symbol={sym}&interval=60&limit=16")
        closes = [float(k[4]) for k in reversed(klines["list"])]  # Bybit returns newest first

        # RSI(14) — Wilder smoothing
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[-14:]) / 14
        avg_loss = sum(losses[-14:]) / 14
        data["rsi"] = round(100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss != 0 else 100, 1)

        # Multi-factor score
        score = multi_factor_score(data)
        data["score"] = score

    except Exception as e:
        data["error"] = str(e)

    return data


def multi_factor_score(d: dict) -> dict:
    """6-factor scoring: RSI + OI + Taker + Funding + Trend + BTC."""
    factors = {}
    total = 0

    # RSI (25%)
    rsi = d.get("rsi", 50)
    if rsi < 35:
        factors["rsi"] = {"signal": "oversold_bounce", "score": 25}
    elif rsi > 65:
        factors["rsi"] = {"signal": "overbought_reversal", "score": 10}
    elif rsi < 50:
        factors["rsi"] = {"signal": "neutral_bearish", "score": 10}
    else:
        factors["rsi"] = {"signal": "neutral_bullish", "score": 10}
    total += factors["rsi"]["score"]

    # OI delta (20%)
    oi_d = d.get("oi_delta_pct", 0)
    if oi_d > 1:
        factors["oi"] = {"signal": "inflow_strong", "score": 20}
    elif oi_d > 0.3:
        factors["oi"] = {"signal": "inflow_moderate", "score": 12}
    elif oi_d < -1:
        factors["oi"] = {"signal": "outflow_strong", "score": 2}
    elif oi_d < -0.3:
        factors["oi"] = {"signal": "outflow_moderate", "score": 5}
    else:
        factors["oi"] = {"signal": "flat", "score": 8}
    total += factors["oi"]["score"]

    # Taker ratio (15%)
    tr = d.get("taker_ratio", 1.0) or 1.0
    if tr > 1.10:
        factors["taker"] = {"signal": "buyers_dominate", "score": 15}
    elif tr > 1.03:
        factors["taker"] = {"signal": "buyers_slight", "score": 10}
    elif tr < 0.90:
        factors["taker"] = {"signal": "sellers_dominate", "score": 2}
    elif tr < 0.97:
        factors["taker"] = {"signal": "sellers_slight", "score": 5}
    else:
        factors["taker"] = {"signal": "balanced", "score": 7}
    total += factors["taker"]["score"]

    # Funding rate (15%) — v1.1: 猎人轧空分级
    fr = d.get("funding_rate", 0) or 0
    if fr < -0.50:
        factors["funding"] = {"signal": "extreme_short_pays_squeeze", "score": 20}
    elif fr < -0.20:
        factors["funding"] = {"signal": "strong_short_pays_long", "score": 18}
    elif fr < -0.10:
        factors["funding"] = {"signal": "short_pays_long", "score": 15}
    elif fr < -0.02:
        factors["funding"] = {"signal": "short_pays_long_mild", "score": 13}
    elif fr < 0:
        factors["funding"] = {"signal": "slightly_negative", "score": 10}
    elif fr > 0.20:
        factors["funding"] = {"signal": "extreme_long_pays_warning", "score": 0}
    elif fr > 0.05:
        factors["funding"] = {"signal": "extreme_long", "score": 2}
    elif fr > 0.01:
        factors["funding"] = {"signal": "long_pays_short", "score": 5}
    else:
        factors["funding"] = {"signal": "neutral", "score": 8}
    total += factors["funding"]["score"]

    # Trend / RSI direction (15%)
    factors["trend"] = {"signal": "data_insufficient", "score": 8}
    total += 8

    # BTC correlation (10%)
    factors["btc_corr"] = {"signal": "not_calculated", "score": 5}
    total += 5

    grade = "A" if total >= 70 else ("B" if total >= 45 else "C")
    return {
        "total": total,
        "grade": grade,
        "factors": factors,
        "recommendation": (
            "LONG" if grade == "A" and rsi < 35
            else "SHORT" if grade == "A" and rsi > 65
            else "WAIT"
        ),
    }


# ─── Routes ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return index_path.read_text()
    return {
        "service": "Hermes x402 Multi-Factor Scanner",
        "version": "1.1.0-bybit",
        "network": "Arc Testnet",
        "price": f"${DEFAULT_PRICE_USD} USDC per scan",
        "endpoints": ["GET /scan?symbol=BTCUSDT", "GET /debug-binance", "GET /version"],
        "docs": "/docs",
    }


@app.get("/version")
def version():
    import subprocess
    try:
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        sha = "unknown"
    return {"version": "1.1.0-bybit", "commit": sha, "source": "bybit", "binance_status": "geo-blocked-451"}


@app.get("/debug-binance")
def debug_binance():
    """Test exchange connectivity from this server's location."""
    import requests as req
    results = {}
    for name, url in [
        ("binance", "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=2"),
        ("bybit", "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&limit=2"),
        ("okx", "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP"),
        ("coingecko", "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"),
        ("binance_spot", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"),
    ]:
        try:
            r = req.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            results[name] = f"HTTP {r.status_code} ({len(r.text)} bytes)"
        except Exception as e:
            results[name] = f"ERROR: {e}"
    return results


@app.get("/scan")
def scan(symbol: str = Query(..., description="Symbol, e.g. BTCUSDT"), request: Request = None):
    """
    Multi-factor crypto scan behind x402 paywall.
    Free without payment: returns 402 Payment Required.
    Paid: returns full multi-factor analysis.
    """
    payment_header = request.headers.get("Payment-Signature", "")

    # ── No payment → 402 ──
    if not payment_header:
        price_atomic = int(DEFAULT_PRICE_USD * 1_000_000)
        nonce = "0x" + secrets.token_hex(32)
        payment_req = {
            "scheme": "GatewayWalletBatched",
            "network": "arc-testnet",
            "chainId": CHAIN_ID,
            "verifyingContract": GATEWAY_WALLET,
            "token": USDC_TOKEN,
            "to": seller.address if seller else "0x0000000000000000000000000000000000000000",
            "price": str(price_atomic),  # atomic units
            "priceHuman": f"${DEFAULT_PRICE_USD} USDC",
            "nonce": nonce,
            "validAfter": 0,
            "validBefore": int(time.time()) + 3600,
        }
        return JSONResponse(
            status_code=402,
            content={
                "error": "Payment Required",
                "message": f"This endpoint costs ${DEFAULT_PRICE_USD} USDC per scan",
                "payment": payment_req,
                "howto": "Sign EIP-712 TransferWithAuthorization against GatewayWallet, retry with Payment-Signature header",
            },
            headers={
                "PAYMENT-REQUIRED": json.dumps(payment_req),
                "X-Payment-Network": "arc-testnet",
                "X-Price": f"${DEFAULT_PRICE_USD}",
            },
        )

    # ── Payment present → verify ──
    try:
        payload = json.loads(payment_header)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "Invalid Payment-Signature format"})

    # Verify the EIP-712 signature
    buyer = verify_payment_signature(payload)
    if not buyer:
        return JSONResponse(status_code=402, content={"error": "Invalid or expired payment signature"})

    # Check non-replay (transfer spec hash)
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
        pass  # If contract call fails, proceed anyway

    # Settle payment on-chain
    if seller:
        tx_hash = settle_payment(payload, seller.address)
    else:
        tx_hash = "verification-only"

    # ── Run scanner ──
    result = fetch_scanner_data(symbol)

    return {
        "paid": True,
        "buyer": buyer,
        "settlement_tx": tx_hash,
        "price_paid": f"${int(payload['value']) / 1_000_000:.4f} USDC",
        "scan": result,
    }


# ─── Main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hermes x402 Multi-Factor Scanner")
    parser.add_argument("--port", type=int, default=None, help="Server port (default: $PORT or 8742)")
    parser.add_argument("--price", type=float, default=DEFAULT_PRICE_USD, help="Price in USDC per scan")
    parser.add_argument("--key", type=str, default="", help="Seller private key (or set X402_SELLER_KEY env)")
    args = parser.parse_args()

    if args.price != DEFAULT_PRICE_USD:
        DEFAULT_PRICE_USD = args.price

    seller_key = args.key or SELLER_KEY
    if seller_key:
        seller = Account.from_key(seller_key)

    # Port: CLI arg > $PORT env > default 8742
    port = args.port or int(os.environ.get("PORT", 8742))

    if seller:
        print(f"🔑 Seller: {seller.address}")
        bal = w3.eth.get_balance(seller.address)
        print(f"   ETH balance: {w3.from_wei(bal, 'ether')} ETH")
        try:
            gw_bal = gw.functions.availableBalance(USDC_TOKEN, seller.address).call()
            print(f"   Gateway balance: {gw_bal / 1e6} USDC")
        except Exception:
            pass
    else:
        print("⚠️  No seller key — running in verification-only mode (no settlement)")

    print(f"\n💰 Price: ${DEFAULT_PRICE_USD} USDC per scan")
    print(f"🌐 Server: http://0.0.0.0:{port}")
    print(f"📡 Endpoint: GET /scan?symbol=BTCUSDT")
    print(f"📚 Docs: http://0.0.0.0:{port}/docs\n")

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
