#!/usr/bin/env python3
"""
Register Hermes as an ERC-8004 AI Agent on Arc Testnet
═══════════════════════════════════════════════════════════
Deploys on-chain identity, reputation, and validation for Hermes Agent.
Arc docs: https://docs.arc.network/arc/tutorials/register-your-first-ai-agent
"""

import json, os, time
from web3 import Web3
from eth_account import Account

# ═══════════════ Arc Testnet ═══════════════
RPC = "https://rpc.testnet.arc.network"
CHAIN_ID = 5042002
EXPLORER = "https://testnet.arcscan.app"
FAUCET = "https://faucet.circle.com"

# ERC-8004 Contracts
IDENTITY_REGISTRY = "0x8004A818BFB912233c491871b3d84c89A494BD9e"
REPUTATION_REGISTRY = "0x8004B663056A597Dffe9eCcC1965A193B7388713"
VALIDATION_REGISTRY = "0x8004Cb1BF31DAf7788923b405b754f57acEB4272"

# ABI snippets
IDENTITY_ABI = [
    {"type":"function","name":"register","inputs":[{"name":"metadataURI","type":"string"}],"outputs":[{"name":"agentId","type":"uint256"}],"stateMutability":"nonpayable"},
    {"type":"event","name":"Transfer","inputs":[{"name":"from","type":"address","indexed":True},{"name":"to","type":"address","indexed":True},{"name":"tokenId","type":"uint256","indexed":True}]},
]
REPUTATION_ABI = [
    {"type":"function","name":"giveFeedback","inputs":[{"name":"agentId","type":"uint256"},{"name":"score","type":"uint256"},{"name":"domain","type":"uint256"},{"name":"tag","type":"string"},{"name":"comment","type":"string"}],"outputs":[],"stateMutability":"nonpayable"},
]
VALIDATION_ABI = [
    {"type":"function","name":"validationRequest","inputs":[{"name":"agentId","type":"uint256"},{"name":"validator","type":"address"}],"outputs":[],"stateMutability":"nonpayable"},
    {"type":"function","name":"validationResponse","inputs":[{"name":"agentId","type":"uint256"},{"name":"score","type":"uint256"},{"name":"metadata","type":"string"}],"outputs":[],"stateMutability":"nonpayable"},
    {"type":"function","name":"getValidationStatus","inputs":[{"name":"agentId","type":"uint256"}],"outputs":[{"name":"status","type":"uint8"},{"name":"validatorScore","type":"uint256"}],"stateMutability":"view"},
]

w3 = Web3(Web3.HTTPProvider(RPC))

# ═══════════════ Agent Metadata ═══════════════
METADATA_URI = "ipfs://bafkreibdi6623n3xpf7ymk62ckb4bo75o3qemwkpfvp5i25j66itxvsoei"

AGENT_METADATA = {
    "name": "Hermes Agent v3.1",
    "description": "Multi-factor crypto trading agent with watchlist state machine. Scans Binance OI, funding rates, taker flows, RSI. On-chain hunter for pump-dump forensics. Built on Arc for x402 paid research via Circle Gateway.",
    "agent_type": "trading",
    "capabilities": [
        "market_scanning",
        "onchain_forensics",
        "multi_factor_scoring",
        "watchlist_state_machine",
        "x402_paid_research",
        "circle_gateway_nanopayments",
        "telegram_alerts"
    ],
    "version": "3.1.0",
    "arc_builder": True,
    "tools_built": ["arc-onboard", "gas-fingerprint-hunter", "kalshi-signal-scanner"]
}

def generate_wallets():
    """Generate or load owner + validator wallets"""
    keyfile = os.path.expanduser("~/.arc_agent_keys.json")
    
    if os.path.exists(keyfile):
        print("📂 Loading existing wallet keys...")
        with open(keyfile) as f:
            keys = json.load(f)
    else:
        print("🔑 Generating new wallet keys...")
        Account.enable_unaudited_hdwallet_features()
        owner = Account.create()
        validator = Account.create()
        keys = {
            "owner_private_key": owner.key.hex(),
            "owner_address": owner.address,
            "validator_private_key": validator.key.hex(),
            "validator_address": validator.address,
        }
        with open(keyfile, "w") as f:
            json.dump(keys, f, indent=2)
        os.chmod(keyfile, 0o600)
        print(f"   Keys saved to {keyfile}")
    
    account1 = Account.from_key(keys["owner_private_key"])
    account2 = Account.from_key(keys["validator_private_key"])
    return account1, account2

def check_balance(address):
    """Check native USDC balance on Arc"""
    bal = w3.eth.get_balance(address)
    return w3.from_wei(bal, 'ether')  # USDC uses 18 decimals for gas

def fund_warning(addr, bal, role):
    if bal < 0.01:
        print(f"\n  ⚠️  {role} needs testnet USDC!")
        print(f"      Address: {addr}")
        print(f"      Go to: {FAUCET}")
        print(f"      Select 'Arc Testnet' → 'USDC' → paste address")
        return True
    return False

def register_agent(owner_account):
    """Register agent → get Agent ID (NFT)"""
    contract = w3.eth.contract(address=IDENTITY_REGISTRY, abi=IDENTITY_ABI)
    
    tx_data = contract.functions.register(METADATA_URI).build_transaction({
        'from': owner_account.address,
        'nonce': w3.eth.get_transaction_count(owner_account.address),
        'chainId': CHAIN_ID,
    })
    
    signed = w3.eth.account.sign_transaction(tx_data, owner_account.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    
    print(f"   Tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    print(f"   Block: {receipt['blockNumber']} ✓")
    
    # Extract Agent ID from logs manually (web3 process_receipt has ABI issues)
    transfer_topic = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    agent_id = None
    for log in receipt['logs']:
        if log['address'].lower() == IDENTITY_REGISTRY.lower() and log['topics'][0].hex() == transfer_topic:
            agent_id = int(log['topics'][3].hex(), 16)
            break
    
    if agent_id is None:
        raise Exception("Could not extract Agent ID from logs")
    return agent_id

def record_reputation(validator_account, agent_id):
    """Record reputation feedback from validator"""
    contract = w3.eth.contract(address=REPUTATION_REGISTRY, abi=REPUTATION_ABI)
    
    tx_data = contract.functions.giveFeedback(
        agent_id, 95, 0, "onboarding", "Registered on Arc testnet — first AI agent identity"
    ).build_transaction({
        'from': validator_account.address,
        'nonce': w3.eth.get_transaction_count(validator_account.address),
        'chainId': CHAIN_ID,
    })
    
    signed = w3.eth.account.sign_transaction(tx_data, validator_account.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"   Tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    print(f"   Block: {receipt['blockNumber']} ✓")

def request_validation(owner_account, agent_id, validator_addr):
    """Request validation for agent"""
    contract = w3.eth.contract(address=VALIDATION_REGISTRY, abi=VALIDATION_ABI)
    
    tx_data = contract.functions.validationRequest(agent_id, validator_addr).build_transaction({
        'from': owner_account.address,
        'nonce': w3.eth.get_transaction_count(owner_account.address),
        'chainId': CHAIN_ID,
    })
    
    signed = w3.eth.account.sign_transaction(tx_data, owner_account.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"   Tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    print(f"   Block: {receipt['blockNumber']} ✓")

def respond_validation(validator_account, agent_id):
    """Validator responds to validation request"""
    contract = w3.eth.contract(address=VALIDATION_REGISTRY, abi=VALIDATION_ABI)
    
    tx_data = contract.functions.validationResponse(agent_id, 100, "validated").build_transaction({
        'from': validator_account.address,
        'nonce': w3.eth.get_transaction_count(validator_account.address),
        'chainId': CHAIN_ID,
    })
    
    signed = w3.eth.account.sign_transaction(tx_data, validator_account.key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"   Tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    print(f"   Block: {receipt['blockNumber']} ✓")

# ═══════════════ Main ═══════════════
def main():
    print(f"╔════════════════════════════════════════════════╗")
    print(f"║  Register Hermes as AI Agent on Arc Testnet   ║")
    print(f"║  ERC-8004 — On-chain Identity & Reputation    ║")
    print(f"╚════════════════════════════════════════════════╝\n")
    
    if not w3.is_connected():
        print("❌ Cannot connect to Arc RPC"); return
    
    print(f"✅ Connected to Arc Testnet (chain {CHAIN_ID})")
    print(f"   Block: {w3.eth.block_number:,}")
    
    # Generate wallets
    print(f"\n{'─'*50}")
    print("STEP 1: Wallets")
    print(f"{'─'*50}")
    owner, validator = generate_wallets()
    
    print(f"   Owner:     {owner.address}")
    print(f"   Validator: {validator.address}")
    
    # Check balances
    owner_bal = check_balance(owner.address)
    val_bal = check_balance(validator.address)
    print(f"\n   Owner balance:     {owner_bal:.4f} USDC")
    print(f"   Validator balance: {val_bal:.4f} USDC")
    
    needs_funding = False
    if fund_warning(owner.address, owner_bal, "Owner wallet"):
        needs_funding = True
    if fund_warning(validator.address, val_bal, "Validator wallet"):
        needs_funding = True
    
    if needs_funding:
        print(f"\n   💡 Fund both wallets, then re-run this script.")
        print(f"   Each tx costs ~0.006 USDC gas. 0.5 USDC per wallet is plenty.")
        return
    
    # Register
    print(f"\n{'─'*50}")
    print("STEP 2: Register Agent (ERC-8004)")
    print(f"{'─'*50}")
    print(f"   Contract: {IDENTITY_REGISTRY}")
    print(f"   Metadata: {METADATA_URI}")
    
    # Check if already registered
    receipt = w3.eth.get_transaction_receipt("0xaf47578cba556a6f714700f6c1012fd62fd925a54aa46e53777919ac938bff18")
    if receipt and receipt['status'] == 1:
        print("   ⏭️  Already registered (tx from previous run succeeded)")
        transfer_topic = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        agent_id = None
        for log in receipt['logs']:
            if log['address'].lower() == IDENTITY_REGISTRY.lower() and log['topics'][0].hex() == transfer_topic:
                agent_id = int(log['topics'][3].hex(), 16)
                break
        if agent_id is None:
            print("   ❌ Could not find Agent ID. Trying fresh register...")
            agent_id = register_agent(owner)
    else:
        agent_id = register_agent(owner)
    print(f"\n   🎉 Agent ID: {agent_id} (NFT)")
    print(f"   View: {EXPLORER}/token/{IDENTITY_REGISTRY}?a={agent_id}")
    
    # Reputation
    print(f"\n{'─'*50}")
    print("STEP 3: Record Reputation")
    print(f"{'─'*50}")
    try:
        record_reputation(validator, agent_id)
        print(f"   ✅ Score: 95 — 'Registered on Arc testnet'")
    except Exception as e:
        print(f"   ⚠️  Reputation contract reverted (testnet bug): {e}")
        print(f"   ⏭️  Skipping — core registration already done")
    
    # Validation
    print(f"\n{'─'*50}")
    print("STEP 4: Validation (two-step)")
    print(f"{'─'*50}")
    try:
        print("   4a: Owner requests validation...")
        request_validation(owner, agent_id, validator.address)
        print("   4b: Validator responds...")
        respond_validation(validator, agent_id)
        print(f"   ✅ Score: 100 — Validated!")
    except Exception as e:
        print(f"   ⚠️  Validation contract reverted (testnet bug): {e}")
        print(f"   ⏭️  Skipping — core registration already done")
    
    # Done
    print(f"\n{'='*50}")
    print(f"🎉 HERMES AGENT REGISTERED ON ARC!")
    print(f"{'='*50}")
    print(f"   Agent ID: {agent_id}")
    print(f"   Owner: {owner.address}")
    print(f"   Explorer: {EXPLORER}/address/{owner.address}")
    print(f"\n   Metadata:")
    for k, v in AGENT_METADATA.items():
        if isinstance(v, list):
            print(f"     {k}: {', '.join(v[:4])}")
        else:
            print(f"     {k}: {v}")
    print()

if __name__ == "__main__":
    main()