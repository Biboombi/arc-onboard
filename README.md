# Arc Onboard

**One command to go from zero to your first x402 paid API call on Arc.**

> Built by [@biboombii](https://github.com/Biboombi) — after personally hitting every pitfall during Circle Agent Stack onboarding.

## Tools

| Tool | What | How |
|------|------|-----|
| `onboard.py` | CLI→Wallet→Gateway→x402 wizard | `python3 onboard.py` |
| `register_agent.py` | ERC-8004 Agent NFT | `python3 register_agent.py` |
| `x402-scanner/` | **Paid multi-factor scanner** | See below ↓ |

## Quick Start

```bash
# Run the wizard
python3 onboard.py

# Or just see what it does
python3 onboard.py --dry-run
```

## What It Does

| Step | Action | Pitfalls Caught |
|------|--------|----------------|
| 1 | Check Node.js + npm | Warns if version too old |
| 2 | Install `@circle-fin/cli` | Skips if already installed |
| 3 | Login with email OTP | ❗ `circle auth login` → WRONG! Use `circle wallet login` |
| 4 | View wallets on 8 chains | Shows actual addresses |
| 5 | Wallet vs Gateway explained | ❗ Must deposit before paying! |
| 6 | Testnet (free USDC) | ❗ Testnet = separate session + different addresses |
| 7 | Gateway deposit | ❗ Eco → Polygon → use `--chain MATIC` |
| 8 | First paid Perplexity call | ❗ Kalshi returns 500, use Perplexity Sonar |
| 9 | Summary + next steps | Build on Arc, join community |

## Why

The Circle Agent Stack docs are good but onboarding has silent traps:
- `circle auth login` doesn't exist (it's `circle wallet login`)
- Testnet and mainnet are completely independent (different login, different addresses)
- Gateway eco deposits take 1-2 minutes to show — no spinner, no warning
- Kalshi endpoints return HTTP 500 despite payment going through

This tool catches all of them so you don't waste 30 minutes debugging.

## Requirements

- **Python 3.8+** (runs the wizard)
- **Node.js v20.18.2+** (for Circle CLI)
- **npm** (for `npm install -g @circle-fin/cli`)

## Real Walkthrough (5 min)

```
$ python3 onboard.py

╔══════════════════════════════════════════════╗
║       Arc Onboard Wizard v1.1                ║
║  Circle Agent Stack → 5 min to first call  ║
╚══════════════════════════════════════════════╝

┌──────────────────────────────────────────────┐
│ Step 1: Check environment
└──────────────────────────────────────────────┘
  ✓ Node.js 24.14.0
  ✓ npm works
  ✓ OS: WSL (Windows)

┌──────────────────────────────────────────────┐
│ Step 2: Install Circle CLI
└──────────────────────────────────────────────┘
  ✓ Circle CLI 0.0.1 is installed

┌──────────────────────────────────────────────┐
│ Step 3: Login (Agent Wallet)
└──────────────────────────────────────────────┘
  ⚠ PITFALL: It's `circle wallet login`, NOT `circle auth login`!
  ? Your email for Circle: ilikegreengreen@gmail.com
  → Check email for OTP (format: ABC-123456)
  → Paste OTP → ✓ Login successful

┌──────────────────────────────────────────────┐
│ Step 4: Your Agent Wallets
└──────────────────────────────────────────────┘
  ✓ BASE: 0xfda03d5a...c4fe01a0
  ✓ ETH: 0x...
  ... (8 chains total)

┌──────────────────────────────────────────────┐
│ Step 5: Wallet vs Gateway — CRITICAL
└──────────────────────────────────────────────┘
  ⚠ You MUST deposit USDC from Wallet → Gateway!
  ⚠ Gateway balance takes 30-120s to show up!

... (continues to first paid call)
```

## Register as an Arc AI Agent (ERC-8004)

Once onboarded, register your agent on-chain:

```bash
python3 register_agent.py
```

This mints an ERC-8004 Agent NFT, giving your AI agent:
- **On-chain identity** — verifiable Agent ID
- **Reputation** — immutable record on Arc
- **Discoverability** — others can find your agent

### Hermes Agent (Live Demo)

| Field | Value |
|-------|-------|
| **Agent ID** | **5414** |
| **NFT Contract** | `0x8004A818BFB912233c491871b3d84c89A494BD9e` |
| **Owner** | `0x8075dE962BcEf1dF183b82dAD30Ac260F61798fF` |
| **Chain** | Arc Testnet (5042002) |
| **TX** | `0xaf475776ba6177c3a2d7e1d5f4cf15a803de4350db12d91f9b5b2f7c938bff18` |
| **Block** | 41,856,444 |
| **Explorer** | [View on Arcscan](https://testnet.arcscan.app/token/0x8004A818BFB912233c491871b3d84c89A494BD9e?a=5414) |

## After Onboarding

- **Register your agent:** `python3 register_agent.py`
- **Discover services:** `circle services search`
- **Build on Arc:** https://docs.arc.network/ai/mcp
- **Join Arc House:** https://community.arc.network

## x402 Paid Scanner (NEW)

Deploy your own paywalled crypto scanner on Arc:

```bash
cd x402-scanner
pip install -r requirements.txt

# Terminal 1: Server
X402_SELLER_KEY=0x... python3 server.py --port 8742

# Terminal 2: Client
python3 client.py setup           # generate keys
python3 client.py deposit 1       # fund Gateway
python3 client.py scan BTCUSDT    # $0.01 per scan
```

**Flow:** 402 Payment Required → EIP-712 TransferWithAuthorization → on-chain settlement → multi-factor analysis

**Scoring:** RSI(25%) + OI Δ(20%) + Taker(15%) + Funding(15%) + Trend(15%) + BTC Corr(10%)

**Live:** [arc-onboard.onrender.com](https://arc-onboard.onrender.com)


## ArcPredict Terminal (NEW) 🔮

Polymarket prediction market charting with full TA and x402 subscription:

```bash
cd polymarket-terminal
pip install -r requirements.txt
X402_SELLER_KEY=0x... python3 server.py --port 8743
```

**Features:** Polymarket Gamma+CLOB API · RSI/MACD/Bollinger/Fib/Volume Profile · TradingView charts · x402 $5/30-day subscription

**Scoring:** 6-factor TA with sentiment labels (BULLISH/BEARISH/NEUTRAL)

**Live:** [arcpredict.onrender.com](https://arcpredict.onrender.com)

## License

MIT — use it, fork it, build on it. Built for the Arc community.
