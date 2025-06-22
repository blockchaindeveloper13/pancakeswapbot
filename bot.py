from web3 import Web3
import os
import time
import json
import requests
from dotenv import load_dotenv

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

# Kilit sözleşmeleri
unicrypt_locker_address = "0x663A5C229c09b049E36dCc11a9B0d4a8Eb9db214"
team_finance_address = "0xE2fE530C047f2d85298b07D9333C05737f1435fB"
pinklock_address = "0x7ee058420e5937496F5a2096f0Fb5F40b050C0A6"
dxsale_address = "0x6b4d6F732B49dD77B6C7aD784E0D0C067C4B5B43"
locker_abi = [
    {
        "constant": True,
        "inputs": [{"name": "lpToken", "type": "address"}],
        "name": "getLockedTokens",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    }
]
unicrypt_contract = web3.eth.contract(address=unicrypt_locker_address, abi=locker_abi)
team_finance_contract = web3.eth.contract(address=team_finance_address, abi=locker_abi)
pinklock_contract = web3.eth.contract(address=pinklock_address, abi=locker_abi)
dxsale_contract = web3.eth.contract(address=dxsale_address, abi=locker_abi)

# DexScreener’dan PancakeSwap pair’lerini çek
def get_dexscreener_tokens():
    url = "https://api.dexscreener.com/latest/dex/search?q=pancakeswap"
    headers = {}
    if os.getenv("DEXSCREENER_API_KEY"):
        headers["Authorization"] = f"Bearer {os.getenv('DEXSCREENER_API_KEY')}"
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        pairs = response.json().get("pairs", [])
        print("DexScreener PancakeSwap Pair’leri:")
        for pair in pairs[:10]:
            print(json.dumps({
                "chainId": pair.get("chainId"),
                "pairAddress": pair.get("pairAddress"),
                "baseToken": pair.get("baseToken", {}).get("address"),
                "marketCap": pair.get("marketCap"),
                "volume.h24": pair.get("volume", {}).get("h24"),
                "liquidity.usd": pair.get("liquidity", {}).get("usd")
            }, indent=2))
        return pairs
    except Exception as e:
        print(f"DexScreener hatası: {e}")
        return []

# Likidite kilidi kontrolü
def is_liquidity_locked(pair_address):
    try:
        locked_amount = unicrypt_contract.functions.getLockedTokens(pair_address).call()
        if locked_amount > 0:
            print(f"Unicrypt’te kilitli: {pair_address}")
            return True
        locked_amount = team_finance_contract.functions.getLockedTokens(pair_address).call()
        if locked_amount > 0:
            print(f"Team.Finance’te kilitli: {pair_address}")
            return True
        locked_amount = pinklock_contract.functions.getLockedTokens(pair_address).call()
        if locked_amount > 0:
            print(f"PinkLock’ta kilitli: {pair_address}")
            return True
        locked_amount = dxsale_contract.functions.getLockedTokens(pair_address).call()
        if locked_amount > 0:
            print(f"DxSale’de kilitli: {pair_address}")
            return True
        print(f"Likidite kilitli değil: {pair_address}")
        return False
    except:
        print(f"Kilit kontrolü hatası: {pair_address}")
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

# Mevcut fiyatı çek
def get_current_price(pair_address):
    url = f"https://api.dexscreener.com/latest/dex/pairs/bsc/{pair_address}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        pair_data = response.json().get("pairs", [])[0]
        return float(pair_data.get("priceUsd", 0))
    except Exception as e:
        print(f"Fiyat çekme hatası: {e}")
        return 0

# Token tarama
def scan_tokens():
    pairs = get_dexscreener_tokens()
    if not pairs:
        return None
    
    best_token = None
    best_score = float("inf")
    
    for pair in pairs[:40]:  # Ankr limiti için 40 pair
        token_address = pair.get("baseToken", {}).get("address")
        pair_address = pair.get("pairAddress")
        market_cap = pair.get("marketCap", float("inf"))
        volume_24h = pair.get("volume", {}).get("h24", 0)
        liquidity_usd = pair.get("liquidity", {}).get("usd", 0)
        chain_id = pair.get("chainId")

        if chain_id != "bsc":
            print(f"BSC değil: {pair_address} ({chain_id})")
            continue

        # Likidite kilidi kontrolü (zorunlu)
        if not is_liquidity_locked(pair_address):
            continue

        # Moonshot potansiyeli
        if market_cap > 2000000:
            print(f"Market cap yüksek: {pair_address} ({market_cap})")
            continue
        if volume_24h < 10000:
            print(f"Hacim düşük: {pair_address} ({volume_24h})")
            continue
        volume_to_market_cap = volume_24h / market_cap if market_cap > 0 else 0
        if volume_to_market_cap < 0.1:
            print(f"Volume/market cap düşük: {pair_address} ({volume_to_market_cap})")
            continue

        # PancakeSwap’tan veri çek
        data = get_pair_data(pair_address)
        if not data or data["token_address"].lower() != token_address.lower():
            print(f"PancakeSwap verisi uyumsuz: {pair_address}")
            continue

        # Filtreleme: Minimum likidite
        if data["liquidity"] < float(os.getenv("MIN_LIQUIDITY", 1000)):
            print(f"Likidite düşük: {pair_address} ({data['liquidity']})")
            continue

        # Skor: Volume/Market Cap oranı
        score = 1 / volume_to_market_cap
        if score < best_score:
            best_score = score
            best_token = token_address
            print(f"Potansiyel token: {best_token} (score: {score})")

    return best_token

# Alım işlemi
def buy_token(token_address, pair_address):
    path = [web3.to_checksum_address(wbnb_address), web3.to_checksum_address(token_address)]
    amount_to_spend = web3.to_wei(float(os.getenv("AMOUNT_TO_SPEND", 0.0070)), "ether")

    try:
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
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

        price = get_current_price(pair_address)
        take_profit_price = price * 1.05
        print(f"Alım işlemi: {tx_hash.hex()}, Fiyat: {price} USD, Kâr al hedefi: {take_profit_price} USD")
    except Exception as e:
        print(f"Alım hatası: {e}")

# Ana döngü
if __name__ == "__main__":
    while True:
        try:
            token_to_buy = scan_tokens()
            if token_to_buy:
                pair_address = next(
                    (pair["pairAddress"] for pair in get_dexscreener_tokens() if pair["baseToken"]["address"].lower() == token_to_buy.lower()),
                    None
                )
                if pair_address:
                    print(f"En uygun token: {token_to_buy}")
                    buy_token(token_to_buy, pair_address)
                else:
                    print(f"Pair adresi bulunamadı: {token_to_buy}")
            else:
                print("Uygun token bulunamadı.")
            time.sleep(int(os.getenv("CHECK_INTERVAL", 120)))
        except Exception as e:
            print(f"Hata: {e}")
            time.sleep(120)
