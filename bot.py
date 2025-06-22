from web3 import Web3
import os
import time
import json
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Ortam değişkenlerini yükle
load_dotenv()

# BSC ağına bağlan
bsc = os.getenv("BSC_RPC_URL")
web3 = Web3(Web3.HTTPProvider(bsc))

# Cüzdan bilgileri
private_key = os.getenv("PRIVATE_KEY")
account = web3.eth.account.from_key(private_key)
wallet_address = account.address

# PancakeSwap Router ve Factory
pancake_router_address = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
pancake_factory_address = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
with open("pancakeswap_router_abi.json") as f:
    pancake_router_abi = json.load(f)
with open("pancakeswap_factory_abi.json") as f:
    pancake_factory_abi = json.load(f)
with open("pair_abi.json") as f:
    pair_abi = json.load(f)

pancake_router = web3.eth.contract(address=pancake_router_address, abi=pancake_router_abi)
pancake_factory = web3.eth.contract(address=pancake_factory_address, abi=pancake_factory_abi)

# WBNB adresi
wbnb_address = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

# Unicrypt kilit sözleşmesi
unicrypt_locker_address = "0x663A5C229c09b049E36dCc11a9B0d4a8Eb9db214"
unicrypt_abi = [
    {
        "constant": True,
        "inputs": [{"name": "lpToken", "type": "address"}],
        "name": "getLockedTokens",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    }
]
unicrypt_contract = web3.eth.contract(address=unicrypt_locker_address, abi=unicrypt_abi)

# DexScreener’dan token profilleri çek
def get_dexscreener_tokens():
    url = "https://api.dexscreener.com/token-profiles/latest/v1"
    headers = {}
    if os.getenv("DEXSCREENER_API_KEY"):
        headers["Authorization"] = f"Bearer {os.getenv('DEXSCREENER_API_KEY')}"
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        profiles = response.json()
        # BSC profillerini logla
        bsc_profiles = [p for p in profiles if p.get("chainId") == "bsc"]
        if bsc_profiles:
            print("DexScreener BSC Token Profilleri:")
            for profile in bsc_profiles[:10]:  # İlk 10 BSC profili
                print(json.dumps(profile, indent=2))
        else:
            print("BSC profili bulunamadı.")
        return profiles
    except Exception as e:
        print(f"DexScreener hatası: {e}")
        return []

# Likidite kilidi kontrolü (Unicrypt)
def is_liquidity_locked(pair_address):
    try:
        locked_amount = unicrypt_contract.functions.getLockedTokens(pair_address).call()
        return locked_amount > 0
    except:
        return False

# Token verisi çek
def get_pair_data(pair_address):
    pair_contract = web3.eth.contract(address=pair_address, abi=pair_abi)
    reserves = pair_contract.functions.getReserves().call()
    token0 = pair_contract.functions.token0().call()
    token1 = pair_contract.functions.token1().call()

    is_wbnb_token0 = token0.lower() == wbnb_address.lower()
    reserve_wbnb = reserves[0] if is_wbnb_token0 else reserves[1]
    reserve_token = reserves[1] if is_wbnb_token0 else reserves[0]
    
    if reserve_wbnb == 0:
        return None
    price = reserve_token / reserve_wbnb
    
    liquidity_usd = (reserve_wbnb / 10**18) * 300
    
    token_address = token1 if is_wbnb_token0 else token0
    
    return {
        "token_address": token_address,
        "price": price,
        "liquidity": liquidity_usd
    }

# Token tarama
def scan_tokens():
    profiles = get_dexscreener_tokens()
    if not profiles:
        return None
    
    best_token = None
    best_score = float("inf")
    
    # Sadece BSC profillerini tara
    for profile in profiles[:50]:
        token_address = profile.get("tokenAddress")
        chain_id = profile.get("chainId")
        if chain_id != "bsc":
            continue
        
        # Pair adresini bul
        pair_address = pancake_factory.functions.getPair(
            web3.to_checksum_address(token_address),
            web3.to_checksum_address(wbnb_address)
        ).call()
        if pair_address == "0x0000000000000000000000000000000000000000":
            continue

        # DexScreener’dan pair verisi çek
        pair_url = f"https://api.dexscreener.com/latest/dex/pairs/bsc/{pair_address}"
        headers = {}
        if os.getenv("DEXSCREENER_API_KEY"):
            headers["Authorization"] = f"Bearer {os.getenv('DEXSCREENER_API_KEY')}"
        try:
            pair_response = requests.get(pair_url, headers=headers)
            pair_response.raise_for_status()
            pair_data = pair_response.json().get("pairs", [])[0]
        except:
            continue

        created_at = pair_data.get("pairCreatedAt")
        market_cap = pair_data.get("marketCap", float("inf"))
        volume_24h = pair_data.get("volume", {}).get("h24", 0)
        liquidity_usd = pair_data.get("liquidity", {}).get("usd", 0)

        # Yeni listelenmiş mi? (son 48 saat)
        if not created_at:
            continue
        created_time = datetime.fromtimestamp(created_at / 1000)
        if datetime.now() - created_time > timedelta(hours=48):
            continue

        # Likidite kilidi kontrolü
        if not is_liquidity_locked(pair_address):
            continue

        # Moonshot potansiyeli
        if market_cap > 500000 or volume_24h < 100000:
            continue
        volume_to_market_cap = volume_24h / market_cap if market_cap > 0 else 0
        if volume_to_market_cap < 0.2:
            continue

        # PancakeSwap’tan veri çek
        data = get_pair_data(pair_address)
        if not data or data["token_address"].lower() != token_address.lower():
            continue

        # Filtreleme: Minimum likidite
        if data["liquidity"] < float(os.getenv("MIN_LIQUIDITY", 5000)):
            continue

        # Skor: Volume/Market Cap oranı
        score = 1 / volume_to_market_cap
        if score < best_score:
            best_score = score
            best_token = token_address

    return best_token

# Alım işlemi
def buy_token(token_address):
    path = [web3.to_checksum_address(wbnb_address), web3.to_checksum_address(token_address)]
    amount_to_spend = web3.to_wei(float(os.getenv("AMOUNT_TO_SPEND", 0.1)), "ether")

    tx = pancake_router.functions.swapExactETHForTokens(
        0,
        path,
        wallet_address,
        int(time.time()) + 60
    ).build_transaction({
        "from": wallet_address,
        "value": amount_to_spend,
        "gas": 200000,
        "gasPrice": web3.to_wei("5", "gwei"),
        "nonce": web3.eth.get_transaction_count(wallet_address)
    })
    signed_tx = web3.eth.account.sign_transaction(tx, private_key)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    print(f"Alım işlemi: {tx_hash.hex()}")

# Ana döngü
if __name__ == "__main__":
    while True:
        try:
            token_to_buy = scan_tokens()
            if token_to_buy:
                print(f"En uygun token: {token_to_buy}")
                buy_token(token_to_buy)
            else:
                print("Uygun token bulunamadı.")
            time.sleep(int(os.getenv("CHECK_INTERVAL", 120)))
        except Exception as e:
            print(f"Hata: {e}")
            time.sleep(120)
