#!/usr/bin/env python3
"""
x402 Nanopayment Client — deposit USDC + pay for scanner
═══════════════════════════════════════════════════════

Usage:
    # Setup (one-time)
    python3 client.py setup                    # generate keys
    python3 client.py deposit 1                # deposit 1 USDC into Gateway

    # Scan
    python3 client.py scan BTCUSDT             # pay $0.01, get multi-factor analysis
    python3 client.py scan BTCUSDT ETHUSDT     # scan multiple
    python3 client.py scan --all               # scan watchlist
"""

import json, os, sys, time, secrets, argparse
from pathlib import Path
from typing import Optional

from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_typed_data
import requests

# ─── Constants ──────────────────────────────────────────────────────────
ARC_RPC = "https://rpc.testnet.arc.network"
CHAIN_ID = 5042002
GATEWAY_WALLET = Web3.to_checksum_address("0x0077777d7EBA4688BDeF3E311b846F25870A19B9")
USDC_TOKEN = Web3.to_checksum_address("0x3600000000000000000000000000000000000000")
SCANNER_URL = "http://localhost:8742"
PRICE_USD = 0.01

# ERC-20 ABI (minimal)
ERC20_ABI = [
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

# GatewayWallet ABI (deposit)
GW_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
        ],
        "name": "deposit",
        "outputs": [],
        "stateMutability": "nonpayable",
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

# EIP-712 domain
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

# Key file
KEY_FILE = Path.home() / ".hermes" / "x402_client_key.txt"


# ─── Helpers ────────────────────────────────────────────────────────────

def get_w3():
    return Web3(Web3.HTTPProvider(ARC_RPC))


def load_key() -> Optional[Account]:
    """Load client private key from file or env."""
    key = os.environ.get("X402_CLIENT_KEY", "")
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    return Account.from_key(key) if key else None


def get_balances(acct: Account) -> dict:
    """Get wallet USDC + Gateway balances."""
    w3 = get_w3()
    usdc = w3.eth.contract(address=USDC_TOKEN, abi=ERC20_ABI)
    gw = w3.eth.contract(address=GATEWAY_WALLET, abi=GW_ABI)

    wallet_usdc = usdc.functions.balanceOf(acct.address).call()
    gw_available = gw.functions.availableBalance(USDC_TOKEN, acct.address).call()

    return {
        "wallet_usdc": wallet_usdc / 1e6,
        "gateway_available": gw_available / 1e6,
    }


def setup_key():
    """Generate a new client key."""
    acct = Account.create()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(acct.key.hex())
    KEY_FILE.chmod(0o600)
    print(f"🔑 New client key saved to {KEY_FILE}")
    print(f"   Address: {acct.address}")
    print(f"\n⚠️  FUND THIS ADDRESS with testnet USDC:")
    print(f"   https://faucet.circle.com")
    print(f"   Select: Arc Testnet + USDC")
    print(f"   Address: {acct.address}")


def deposit(acct: Account, amount: float):
    """Deposit USDC into GatewayWallet."""
    w3 = get_w3()
    usdc = w3.eth.contract(address=USDC_TOKEN, abi=ERC20_ABI)
    gw = w3.eth.contract(address=GATEWAY_WALLET, abi=GW_ABI)

    value_atomic = int(amount * 1_000_000)
    nonce = w3.eth.get_transaction_count(acct.address)

    # 1. Approve USDC spend
    print(f"1/2 Approving {amount} USDC for GatewayWallet...")
    approve_tx = usdc.functions.approve(GATEWAY_WALLET, value_atomic).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "gas": 100_000,
        "gasPrice": w3.eth.gas_price,
    })
    signed_approve = w3.eth.account.sign_transaction(approve_tx, acct.key)
    tx_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"   ✅ Approved: {tx_hash.hex()}")

    # 2. Deposit
    print(f"2/2 Depositing {amount} USDC into Gateway...")
    deposit_tx = gw.functions.deposit(USDC_TOKEN, value_atomic).build_transaction({
        "from": acct.address,
        "nonce": nonce + 1,
        "gas": 200_000,
        "gasPrice": w3.eth.gas_price,
    })
    signed_deposit = w3.eth.account.sign_transaction(deposit_tx, acct.key)
    tx_hash = w3.eth.send_raw_transaction(signed_deposit.raw_transaction)
    w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"   ✅ Deposited: {tx_hash.hex()}")

    # Verify
    time.sleep(3)
    bal = gw.functions.availableBalance(USDC_TOKEN, acct.address).call()
    print(f"\n💰 Gateway available: {bal / 1e6} USDC")


def sign_payment(acct: Account, to_addr: str, amount_usd: float, nonce_bytes: bytes) -> dict:
    """Sign EIP-712 TransferWithAuthorization."""
    value_atomic = int(amount_usd * 1_000_000)
    now = int(time.time())

    message = {
        "from": acct.address,
        "to": Web3.to_checksum_address(to_addr),
        "value": value_atomic,
        "validAfter": 0,
        "validBefore": now + 3600,
        "nonce": nonce_bytes,
    }

    encoded = encode_typed_data(
        domain_data=EIP712_DOMAIN,
        message_types=EIP712_TYPES,
        message_data=message,
    )
    signed = Account.sign_message(encoded, acct.key)

    payload = {
        "scheme": "GatewayWalletBatched",
        "chainId": CHAIN_ID,
        "verifyingContract": GATEWAY_WALLET,
        "from": acct.address,
        "to": to_addr,
        "value": str(value_atomic),
        "validAfter": str(message["validAfter"]),
        "validBefore": str(message["validBefore"]),
        "nonce": "0x" + nonce_bytes.hex(),
        "signature": {
            "r": "0x" + (signed.r.hex() if hasattr(signed.r, 'hex') else hex(signed.r)[2:].zfill(64)),
            "s": "0x" + (signed.s.hex() if hasattr(signed.s, 'hex') else hex(signed.s)[2:].zfill(64)),
            "v": signed.v,
        },
    }
    return payload


def scan(acct: Account, symbol: str, server: str):
    """Pay and scan a symbol."""
    # Step 1: Call without payment to get 402 + payment details
    url = f"{server}/scan?symbol={symbol}"
    print(f"📡 {symbol} — requesting scan...")

    resp = requests.get(url, timeout=15)
    if resp.status_code == 402:
        payment_info = resp.json().get("payment", {})
        to_addr = payment_info.get("to", "")
        print(f"   💰 Price: {payment_info.get('priceHuman', f'${PRICE_USD}')}")
        print(f"   📍 Paying to: {to_addr[:10]}...")
    else:
        # Maybe already paid or error
        print(f"   Status: {resp.status_code}")
        print(json.dumps(resp.json(), indent=2))
        return

    # Step 2: Sign payment
    nonce = secrets.token_bytes(32)
    payment = sign_payment(acct, to_addr, PRICE_USD, nonce)
    print(f"   ✍️  Signed payment authorization")

    # Step 3: Retry with Payment-Signature header
    payment_json = json.dumps(payment)
    resp2 = requests.get(
        url,
        headers={"Payment-Signature": payment_json},
        timeout=30,
    )

    if resp2.status_code == 200:
        data = resp2.json()
        scan = data.get("scan", {})
        score = scan.get("score", {})

        print(f"\n{'═'*50}")
        print(f"📊 {symbol} — Multi-Factor Analysis")
        print(f"{'═'*50}")
        price_val = scan.get('current_price', 0)
        price_str = f"${price_val:,.2f}" if isinstance(price_val, (int, float)) else str(price_val)
        print(f"  Price:      {price_str}")
        print(f"  RSI(14):    {scan.get('rsi', 'N/A')}")
        print(f"  OI Δ 1h:    {scan.get('oi_delta_pct', 0):+.2f}%")
        print(f"  OI Total:   ${scan.get('open_interest_usd', 0):,.0f}")
        print(f"  Funding:    {scan.get('funding_rate', 0):+.4f}%")
        print(f"  Taker:      {scan.get('taker_ratio', 'N/A')}")
        print(f"\n  🎯 Score:   {score.get('total', 'N/A')}/100 ({score.get('grade', '?')})")
        print(f"  📌 Signal:  {score.get('recommendation', 'N/A')}")
        if "factors" in score:
            print(f"\n  Factors:")
            for name, f in score["factors"].items():
                print(f"    {name:10s} → {f['signal']:20s} ({f['score']}/??)")
        print(f"\n  💳 Paid: {data.get('price_paid', 'N/A')}")
        print(f"  🔗 TX:   {data.get('settlement_tx', 'N/A')[:20]}...")
    else:
        print(f"   ❌ Payment failed: {resp2.status_code}")
        print(f"   {resp2.text[:500]}")


# ─── Main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="x402 Nanopayment Client")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("setup", help="Generate client key")

    dep = sub.add_parser("deposit", help="Deposit USDC into Gateway")
    dep.add_argument("amount", type=float, help="USDC amount")

    sc = sub.add_parser("scan", help="Scan symbols (pay per scan)")
    sc.add_argument("symbols", nargs="*", default=["BTCUSDT"], help="Symbols to scan")
    sc.add_argument("--all", action="store_true", help="Scan watchlist")
    sc.add_argument("--server", default=SCANNER_URL, help="Scanner URL")

    bal = sub.add_parser("balance", help="Check balances")

    args = parser.parse_args()

    if args.cmd == "setup":
        setup_key()

    elif args.cmd == "deposit":
        acct = load_key()
        if not acct:
            print("❌ No key found. Run 'python3 client.py setup' first.")
            sys.exit(1)
        deposit(acct, args.amount)

    elif args.cmd == "scan":
        acct = load_key()
        if not acct:
            print("❌ No key found. Run 'python3 client.py setup' first.")
            sys.exit(1)

        bal = get_balances(acct)
        print(f"💰 Wallet: {bal['wallet_usdc']:.2f} USDC  |  Gateway: {bal['gateway_available']:.2f} USDC\n")

        if bal["gateway_available"] < PRICE_USD:
            print(f"❌ Gateway balance too low ({bal['gateway_available']:.4f} < ${PRICE_USD})")
            print(f"   Run: python3 client.py deposit 1")
            sys.exit(1)

        symbols = args.symbols
        if args.all:
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT",
                       "XRPUSDT", "ADAUSDT", "SUIUSDT", "AVAXUSDT", "LINKUSDT"]

        for sym in symbols:
            scan(acct, sym.upper(), args.server)
            if len(symbols) > 1:
                print()  # spacer between scans

    elif args.cmd == "balance":
        acct = load_key()
        if not acct:
            print("❌ No key found. Run 'python3 client.py setup' first.")
            sys.exit(1)
        bal = get_balances(acct)
        print(f"👛 Address:    {acct.address}")
        print(f"💰 Wallet USDC: {bal['wallet_usdc']:.4f}")
        print(f"🏦 Gateway:     {bal['gateway_available']:.4f} USDC")

    else:
        parser.print_help()
