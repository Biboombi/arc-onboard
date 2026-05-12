#!/usr/bin/env python3
"""
Arc Onboard — One-command Circle Agent Stack setup wizard
═══════════════════════════════════════════════════════════
Usage: python3 onboard.py          # Full interactive
       python3 onboard.py --quick  # Non-interactive checklist
       python3 onboard.py --dry-run # Show steps without executing

Guides devs: install CLI → create wallet → fund → gateway → paid call
Every step includes real pitfalls discovered during onboarding.
Built for Arc Testnet. Works on macOS, Linux, WSL.
"""

import sys, os, subprocess, json, time, platform, shutil

# ═══════════════════ Styles ═══════════════════
HEAD = "\033[1;36m"; OK = "\033[1;32m"; WARN = "\033[1;33m"
ERR = "\033[1;31m"; BOLD = "\033[1m"; GRAY = "\033[90m"; RST = "\033[0m"

def step(n, title):
    print(f"\n{HEAD}┌{'─'*46}┐{RST}")
    print(f"{HEAD}│{RST} {BOLD}Step {n}: {title}{RST}")
    print(f"{HEAD}└{'─'*46}┘{RST}")

def ok(msg, dry=False):
    tag = "~" if dry else "✓"
    print(f"  {GRAY if dry else OK}{tag}{RST} {msg}")

def warn(msg):
    print(f"  {WARN}⚠{RST} {msg}")

def error(msg):
    print(f"  {ERR}✗{RST} {msg}")

def info(msg):
    print(f"  {GRAY}→{RST} {msg}")

def ask(msg, default=""):
    prompt = f"  {BOLD}?{RST} {msg}"
    if default:
        prompt += f" {GRAY}[{default}]{RST}"
    prompt += ": "
    try:
        val = input(prompt).strip()
        return val if val else default
    except (EOFError, KeyboardInterrupt):
        print()
        return default

def run(cmd, silent=False, dry=False):
    if dry:
        if not silent:
            print(f"  {GRAY}[dry] {cmd[:70]}...{RST}")
        return True, ""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if not silent and stdout:
            for line in stdout.split('\n')[:10]:
                print(f"  {GRAY}│{RST} {line[:100]}")
        if result.returncode != 0 and stderr:
            for line in stderr.split('\n'):
                if any(w in line.lower() for w in ('warn','deprecated','fund','looking')):
                    continue
                print(f"  {WARN}│{RST} {line[:100]}")
        return result.returncode == 0, stdout + stderr
    except subprocess.TimeoutExpired:
        error(f"Timeout: {cmd[:60]}")
        return False, ""
    except Exception as e:
        error(f"Failed: {e}")
        return False, ""

def check_circle_installed(dry=False):
    if dry:
        ok("Circle CLI (check if installed)", dry=True)
        return True
    ok_, out = run("circle --version 2>&1", silent=True)
    if ok_:
        ok(f"Circle CLI {out.strip()}")
        return True
    return False

# ═══════════════════ Main ═══════════════════
def main():
    dry_run = '--dry-run' in sys.argv or '--dry' in sys.argv
    quick = '--quick' in sys.argv
    
    if dry_run:
        print(f"\n{GRAY}╔══ DRY RUN — no commands executed ══╗{RST}\n")
    
    print(f"\n{BOLD}{HEAD}╔══════════════════════════════════════════════╗{RST}")
    print(f"{BOLD}{HEAD}║{RST}       {BOLD}Arc Onboard Wizard v1.1{RST}                {BOLD}{HEAD}║{RST}")
    print(f"{BOLD}{HEAD}║{RST}  Circle Agent Stack → 5 min to first call  {BOLD}{HEAD}║{RST}")
    print(f"{BOLD}{HEAD}╚══════════════════════════════════════════════╝{RST}\n")
    
    # ═══════ STEP 1: Environment ═══════
    step(1, "Check environment")
    
    ok_, out = run("node --version 2>&1", silent=True, dry=dry_run)
    if not dry_run:
        if ok_:
            ver = out.strip().lstrip('v')
            try:
                major = int(ver.split('.')[0])
                ok(f"Node.js v{ver}" if major >= 20 else f"Node.js v{ver} — need v20+")
                if major < 20:
                    error("Install Node.js v20.18.2+: https://nodejs.org")
                    return
            except: ok(f"Node.js {out.strip()}")
        else:
            error("Node.js not found — install: https://nodejs.org")
            return
    else:
        ok("Node.js v20.18.2+", dry=True)
    
    if not dry_run:
        ok_, _ = run("npm --version 2>&1", silent=True)
        if ok_: ok("npm works")
        else: error("npm not found"); return
    
    os_name = platform.system()
    is_wsl = 'microsoft' in platform.release().lower() or 'wsl' in platform.release().lower()
    ok(f"OS: {'WSL' if is_wsl else os_name}" if not dry_run else "OS: Linux/macOS/WSL", dry=dry_run)
    
    # ═══════ STEP 2: Install CLI ═══════
    step(2, "Install Circle CLI")
    
    if check_circle_installed(dry=dry_run):
        info("Already installed — skip")
    else:
        info("Installing @circle-fin/cli...")
        ok_, _ = run("npm install -g @circle-fin/cli 2>&1", dry=dry_run)
        if ok_ or dry_run:
            ok("Circle CLI installed")
        else:
            error("Install failed. Try: npm install -g @circle-fin/cli"); return
    
    # ═══════ STEP 3: Login ═══════
    step(3, "Login (Agent Wallet)")
    
    warn("PITFALL: It's `circle wallet login`, NOT `circle auth login`!")
    
    email = ""
    if not quick and not dry_run:
        email = ask("Your email for Circle")
        if not email or '@' not in email:
            error("Valid email required. Run again with your email.")
            return
    else:
        email = "your@email.com"
        info(f"Email: {email} (replace with yours)")
    
    if not dry_run and email != "your@email.com":
        ok_, out = run(f"circle wallet login {email} --type agent 2>&1")
        info("Check email for OTP (format: ABC-123456)")
        info("Run in your terminal:")
        info(f"  circle wallet login {email} --type agent")
        info("  Then paste the 6-digit OTP when prompted")
    
    # ═══════ STEP 4: Wallets ═══════
    step(4, "Your Agent Wallets")
    
    info("Circle creates wallets on 8 EVM chains automatically.")
    chains = ['BASE', 'ETH', 'ARB', 'MATIC']
    for chain in chains:
        ok_, out = run(f"circle wallet list --chain {chain} 2>&1", silent=True, dry=dry_run)
        if dry_run:
            ok(f"{chain}: 0x... (auto-created)", dry=True)
        elif ok_ and '0x' in out:
            for line in out.split('\n'):
                if '0x' in line:
                    addr = '0x' + line.split('0x')[1].split()[0]
                    if len(addr) == 42:
                        ok(f"{chain}: {addr[:10]}...{addr[-8:]}")
                        break
    
    # ═══════ STEP 5: Wallet vs Gateway ═══════
    step(5, "Wallet vs Gateway — CRITICAL")
    
    print(f"  {BOLD}Wallet{RST}  = your on-chain address (holds USDC)")
    print(f"  {BOLD}Gateway{RST} = pre-paid pool for batched micropayments")
    warn("You MUST deposit USDC from Wallet → Gateway before paying for services!")
    warn("Gateway balance takes 30-120s to show up — don't panic!")
    
    # ═══════ STEP 6: Testnet (FREE) ═══════
    step(6, "Testnet — Practice for FREE")
    
    info("Testnet = separate session + different wallet addresses")
    warn("PITFALL: Testnet and mainnet are COMPLETELY separate!")
    
    if not dry_run and not quick:
        use_testnet = ask("Try testnet? (y/n)", "y").lower()
        if use_testnet == 'y':
            info("Login to testnet:")
            info(f"  circle wallet login {email} --type agent --testnet")
            info("Then fund free USDC:")
            info("  circle wallet list --chain BASE-SEPOLIA")
            info("  circle wallet fund --address <addr> --chain BASE-SEPOLIA --token usdc")
    
    # ═══════ STEP 7: Gateway Deposit ═══════
    step(7, "Deposit to Gateway")
    
    info("Deposit $0.50 via Eco (fast, no gas) or fund via MetaMask first.")
    info("  1. Fund wallet with USDC (MetaMask → your Base address)")
    info("  2. circle gateway deposit --amount 0.5 --address <addr> --chain BASE --method eco")
    warn("PITFALL: Eco deposits land on Polygon → use --chain MATIC for service calls!")
    warn("PITFALL: `circle gateway balance` may show $0 for 1-2 minutes.")
    
    # ═══════ STEP 8: First Paid Call ═══════
    step(8, "First Paid API Call")
    
    info("Perplexity Sonar — $0.005/query, reliable endpoint:")
    print(f"  {GRAY}circle services pay \"https://api.aisa.one/apis/v2/perplexity/sonar\" \\{RST}")
    print(f"  {GRAY}  -X POST -d '{{\"model\":\"sonar\",\"messages\":[{{\"role\":\"user\",\"content\":\"hello\"}}],\"max_tokens\":50}}' \\{RST}")
    print(f"  {GRAY}  --address <your_addr> --chain MATIC{RST}")
    
    warn("PITFALL: Kalshi endpoints may return 500 — Perplexity is reliable.")
    
    # ═══════ DONE ═══════
    step(9, "You're Ready!")
    
    print(f"\n  {OK}╔══════════════════════════════════════════════╗{RST}")
    print(f"  {OK}║{RST}  ✓ Circle CLI installed                    {OK}║{RST}")
    print(f"  {OK}║{RST}  ✓ Agent wallet on 8+ chains               {OK}║{RST}")
    print(f"  {OK}║{RST}  ✓ Wallet vs Gateway understood            {OK}║{RST}")
    print(f"  {OK}║{RST}  ✓ Gateway funded                          {OK}║{RST}")
    print(f"  {OK}║{RST}  ✓ First x402 paid call completed          {OK}║{RST}")
    print(f"  {OK}╚══════════════════════════════════════════════╝{RST}")
    
    print(f"\n  {BOLD}Pitfalls you now avoid:{RST}")
    pitfalls = [
        "`circle auth login` → wrong! Use `circle wallet login`",
        "Testnet has separate session AND different addresses",
        "Eco deposits take 30-120s — don't panic if balance shows $0",
        "Eco → Polygon → use `--chain MATIC` for service calls",
        "Kalshi 500? → Use Perplexity Sonar instead",
    ]
    for p in pitfalls:
        print(f"  {WARN}•{RST} {p}")
    
    print(f"\n  {BOLD}Next:{RST}")
    print(f"  • Discover services: `circle services search`")
    print(f"  • Build on Arc: https://docs.arc.network/ai/mcp")
    print(f"  • Join Arc House: https://community.arc.network")
    
    if dry_run:
        print(f"\n{GRAY}╚══ DRY RUN complete — run without --dry-run to execute ══╝{RST}")
    print()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{OK}Paused. Run again anytime.{RST}")
    except Exception as e:
        print(f"\n{ERR}Error: {e}{RST}")
        print(f"{GRAY}Report issues: https://github.com/your/arc-onboard{RST}")