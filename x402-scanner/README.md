# x402 Multi-Factor Crypto Scanner

**Paywalled crypto scanner powered by Hermes Agent — $0.01 USDC per scan on Arc.**

Server delivers real-time Binance Futures data behind an x402 nanopayment wall. Clients pay with EIP-712 signed `TransferWithAuthorization` via Circle GatewayWallet. Settlement is on-chain on Arc Testnet.

---

## Quick Start (5 Minutes)

### Prerequisites

- Python 3.8+
- Arc Testnet RPC access (public: `https://rpc.testnet.arc.network`)
- A seller wallet with Arc testnet ETH (for gas) + USDC (to receive payments)

### 1. Install

```bash
git clone https://github.com/Biboombi/arc-onboard.git
cd arc-onboard/x402-scanner

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your seller private key
```

### 3. Run Server

```bash
# Terminal 1: Start the paywalled scanner
X402_SELLER_KEY=0xyour_seller_private_key python3 server.py --port 8742
```

### 4. Use Client

```bash
# Terminal 2: First-time setup
python3 client.py setup           # generate client key
python3 client.py deposit 1       # deposit 1 USDC into Gateway

# Scan anything
python3 client.py scan BTCUSDT    # $0.01 per scan
python3 client.py scan ETHUSDT SOLUSDT
python3 client.py scan --all      # scan top 10 coins
```

---

## How It Works

### Payment Flow (x402)

```
Client                          Server
  │                                │
  ├── GET /scan?symbol=BTCUSDT ──→│
  │                                ├─ No payment header
  │←── 402 Payment Required ──────┤   (returns GatewayWallet params)
  │                                │
  ├─ Sign EIP-712 TransferWithAuth
  ├── GET + Payment-Signature ──→│
  │                                ├─ Verify signature
  │                                ├─ Settle on-chain (GatewayWallet)
  │                                ├─ Fetch Binance data
  │←── 200 + multi-factor report ─┤
```

### What You Get Per Scan

| Field | Source | Description |
|-------|--------|-------------|
| `price` | Binance | Current index price |
| `rsi` | Calculated | 14-period Wilder RSI (1h candles) |
| `oi_delta_pct` | Binance | Open Interest change 1h |
| `open_interest_usd` | Binance | Total OI in USD |
| `funding_rate` | Binance | Current funding rate |
| `taker_ratio` | Binance | Taker buy/sell ratio |
| `score` | Calculated | Multi-factor grade (see below) |

### Scoring System (6 Factors, 100 Points)

| Factor | Weight | Signals |
|--------|--------|---------|
| **RSI** | 25% | Oversold bounce (<35) / Overbought reversal (>65) |
| **OI Delta** | 20% | Inflow strong (>1%) / Outflow strong (<-1%) |
| **Taker** | 15% | Buyers dominate (>1.10) / Sellers dominate (<0.90) |
| **Funding** | 15% | Shorts pay longs (<-0.02%) / Extreme longs (>0.05%) |
| **Trend** | 15% | RSI direction context |
| **BTC Corr** | 10% | BTC correlation anchor |

**Grades:** A ≥ 70 | B ≥ 45 | C < 45  
**Signals:** LONG (A + oversold) | SHORT (A + overbought) | WAIT (otherwise)

---

## Architecture

```
x402-scanner/
├── server.py          # FastAPI server with x402 paywall
│   ├── EIP-712 signature verification
│   ├── GatewayWallet on-chain settlement
│   └── Binance Futures data fetching + scoring
├── client.py          # Nanopayment client
│   ├── Key generation + management
│   ├── Gateway USDC deposit
│   └── Pay + scan workflow
├── requirements.txt   # Python dependencies
└── .env.example       # Environment template
```

---

## API Reference

### `GET /`

Service info and pricing.

```json
{
  "service": "Hermes x402 Multi-Factor Scanner",
  "version": "1.0.0",
  "network": "Arc Testnet",
  "price": "$0.01 USDC per scan"
}
```

### `GET /scan?symbol=BTCUSDT`

**Without payment** → `402 Payment Required` with GatewayWallet params:

```json
{
  "error": "Payment Required",
  "message": "This endpoint costs $0.01 USDC per scan",
  "payment": {
    "scheme": "GatewayWalletBatched",
    "network": "arc-testnet",
    "chainId": 5042002,
    "verifyingContract": "0x0077...19B9",
    "token": "0x3600...0000",
    "to": "0x4ca6...",
    "price": "10000",
    "priceHuman": "$0.01 USDC"
  }
}
```

**With valid EIP-712 payment** → `200 OK` with full analysis:

```json
{
  "paid": true,
  "buyer": "0x...",
  "settlement_tx": "0x...",
  "price_paid": "$0.0100 USDC",
  "scan": {
    "symbol": "BTCUSDT",
    "price": 87234.50,
    "rsi": 28.3,
    "oi_delta_pct": 2.41,
    "funding_rate": -0.0150,
    "taker_ratio": 1.12,
    "score": {
      "total": 78,
      "grade": "A",
      "recommendation": "LONG"
    }
  }
}
```

---

## Server Options

```bash
python3 server.py --port 8742          # custom port (default: 8742)
python3 server.py --price 0.05         # custom price (default: $0.01)
python3 server.py --key 0x...          # seller key (or use X402_SELLER_KEY env)
```

## Client Commands

```bash
python3 client.py setup                # generate new client key
python3 client.py deposit 1            # deposit 1 USDC into Gateway
python3 client.py scan BTCUSDT         # pay & scan one symbol
python3 client.py scan --all           # scan top 10 coins
python3 client.py scan --server http://myserver:8742 BTCUSDT  # custom server
python3 client.py balance              # check wallet + Gateway balances
```

---

## Network Details

| Parameter | Value |
|-----------|-------|
| **Chain** | Arc Testnet |
| **Chain ID** | 5042002 |
| **RPC** | `https://rpc.testnet.arc.network` |
| **GatewayWallet** | `0x0077777d7EBA4688BDeF3E311b846F25870A19B9` |
| **USDC Token** | `0x3600000000000000000000000000000000000000` |
| **Payment Scheme** | EIP-712 `TransferWithAuthorization` |

---

## Pricing

- Default: **$0.01 USDC** per scan
- Configurable via `--price` flag
- All payments settled on-chain through GatewayWallet
- No API keys, no subscriptions — just nanopayments

---

## Hermes Agent Attribution

This scanner is built by **Hermes Agent (ID 5414)** — a verifiable on-chain AI agent on Arc Testnet.

| Field | Value |
|-------|-------|
| Agent ID | 5414 |
| NFT | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| Owner | `0x8075dE962BcEf1dF183b82dAD30Ac260F61798fF` |
| x402 Seller | `0x4ca6...` |
| Explorer | [Arcscan](https://testnet.arcscan.app/token/0x8004A818BFB912233c491871b3d84c89A494BD9e?a=5414) |

---

## License

MIT — built for the Arc community.
