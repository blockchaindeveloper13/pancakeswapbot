from web3 import Web3
import os
import time
import json
import requests
from dotenv import load_dotenv
import pandas as pd
import pandas_ta as ta

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
pancake_router_address = web3.to_checksum_address("0x10ED43C718714eb63d5aA57B78B54704E256024E")
pancake_factory_address = web3.to_checksum_address("0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73")
with open("pancakeswap_router_abi.json") as f:
    pancake_router_abi = json.load(f)
with open("pancakeswap_factory_abi.json") as f:
    pancake_factory_abi = json.load(f)
with open("pair_abi.json") as f:
    pair_abi = json.load(f)
with open("bep20_abi.json") as f:
    bep20_abi = json.load(f)

pancake_router = web3.eth.contract(address=pancake_router_address, abi=pancake_router_abi)
pancake_factory = web3.eth.contract(address=pancake_factory_address, abi=pancake_factory_abi)

# WBNB adresi
wbnb_address = web3.to_checksum_address("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")

# Alınan token’ları takip et
portfolio = {}  # {token_address: {"buy_price": float, "buy_time": timestamp, "amount": float, "pair_address": str}}

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
                "priceUsd": pair.get("priceUsd"),
                "marketCap": pair.get("marketCap"),
                "fdv": pair.get("fdv"),
                "volume.h24": pair.get("volume", {}).get("h24"),
                "liquidity.usd": pair.get("liquidity", {}).get("usd")
            }, indent=2))
        return pairs
    except Exception as e:
        print(f"DexScreener hatası: {e}")
        return []

# Fiyat geçmişi çek (RSI için, simüle edilmiş)
def get_price_history(token_address, pair_address):
    url = f"https://api.dexscreener.com/latest/dex/pairs/bsc/{pair_address}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        pair_data = response.json().get("pairs", [])[0]
        prices = []
        for i in range(14):  # 14 dönem
            price = float(pair_data.get("priceUsd", 0)) * (1 - i * 0.01)
            prices.append(price)
        return prices[::-1]
    except Exception as e:
        print(f"Fiyat geçmişi hatası: {e}")
        return []

# RSI hesapla
def calculate_rsi(prices):
    if len(prices) < 14:
        return None
    df = pd.DataFrame(prices, columns=["close"])
    df["rsi"] = ta.rsi(df["close"], length=14)
    return df["rsi"].iloc[-1]

# Token verisi çek (PancakeSwap’tan likidite doğrulama)
def get_pair_data(pair_address):
    pair_address = web3.to_checksum_address(pair_address)
    pair_contract = web3.eth.contract(address=pair_address, abi=pair_abi)
    try:
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
    except Exception as e:
        print(f"PancakeSwap veri hatası: {pair_address} ({e})")
        return None

# Mevcut fiyatı çek (DexScreener)
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
        price_usd = pair.get("priceUsd", 0)
        fdv = pair.get("fdv", float("inf"))
        chain_id = pair.get("chainId")

        if chain_id != "bsc":
            print(f"BSC değil: {pair_address} ({chain_id})")
            continue

        # Likidite filtresi
        if liquidity_usd < 100000:
            print(f"Likidite düşük: {pair_address} ({liquidity_usd})")
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

        # RSI kontrolü
        price_history = get_price_history(token_address, pair_address)
        rsi = calculate_rsi(price_history)
        if rsi is None or rsi >= 30:
            print(f"RSI uygun değil: {pair_address} (RSI: {rsi})")
            continue

        # PancakeSwap’tan likidite doğrulama
        data = get_pair_data(pair_address)
        if not data or data["token_address"].lower() != token_address.lower():
            print(f"PancakeSwap verisi uyumsuz: {pair_address}")
            continue

        # Skor: Volume/Market Cap oranı
        score = 1 / volume_to_market_cap
        if score < best_score:
            best_score = score
            best_token = token_address
            print(f"Potansiyel token: {best_token} (score: {score}, priceUsd: {price_usd}, fdv: {fdv}, RSI: {rsi})")

    return best_token

# Alım işlemi
def buy_token(token_address, pair_address):
    token_address = web3.to_checksum_address(token_address)
    path = [wbnb_address, token_address]
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

        # Alınan token miktarını bul
        token_contract = web3.eth.contract(address=token_address, abi=bep20_abi)
        token_amount = token_contract.functions.balanceOf(wallet_address).call() / 10**18
        price = get_current_price(pair_address)
        take_profit_price = price * 1.10  # %10 kâr hedefi

        portfolio[token_address] = {
            "buy_price": price,
            "buy_time": time.time(),
            "amount": token_amount,
            "pair_address": pair_address
        }
        print(f"Alım işlemi: {tx_hash.hex()}, Miktar: {token_amount}, Fiyat: {price} USD, Kâr al hedefi: {take_profit_price} USD")
    except Exception as e:
        print(f"Alım hatası: {e}")

# Satış işlemi
def sell_token(token_address):
    token_data = portfolio.get(token_address)
    if not token_data:
        return

    token_contract = web3.eth.contract(address=token_address, abi=bep20_abi)
    amount_to_sell = int(token_data["amount"] * 10**18)

    # Onay ver
    approve_tx = token_contract.functions.approve(
        pancake_router_address,
        amount_to_sell
    ).build_transaction({
        "from": wallet_address,
        "gas": 100000,
        "gasPrice": web3.to_wei("5", "gwei"),
        "nonce": web3.eth.get_transaction_count(wallet_address)
    })
    signed_approve_tx = web3.eth.account.sign_transaction(approve_tx, private_key)
    approve_tx_hash = web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)
    web3.eth.wait_for_transaction_receipt(approve_tx_hash)

    # Satış yap
    path = [web3.to_checksum_address(token_address), wbnb_address]
    try:
        sell_tx = pancake_router.functions.swapExactTokensForETH(
            amount_to_sell,
            0,
            path,
            wallet_address,
            int(time.time()) + 60
        ).build_transaction({
            "from": wallet_address,
            "gas": 200000,
            "gasPrice": web3.to_wei("5", "gwei"),
            "nonce": web3.eth.get_transaction_count(wallet_address)
        })
        signed_sell_tx = web3.eth.account.sign_transaction(sell_tx, private_key)
        sell_tx_hash = web3.eth.send_raw_transaction(signed_sell_tx.rawTransaction)
        receipt = web3.eth.wait_for_transaction_receipt(sell_tx_hash)
        print(f"Satış işlemi: {sell_tx_hash.hex()}, Fiyat: {get_current_price(token_data['pair_address'])} USD")
        del portfolio[token_address]
    except Exception as e:
        print(f"Satış hatası: {e}")

# Portföyü kontrol et ve sat
def check_portfolio():
    for token_address, data in list(portfolio.items()):
        current_price = get_current_price(data["pair_address"])
        buy_price = data["buy_price"]
        price_history = get_price_history(token_address, data["pair_address"])
        rsi = calculate_rsi(price_history)

        # RSI > 70
        if rsi and rsi > 70:
            print(f"RSI aşırı alım: {token_address} (RSI: {rsi}, Alım: {buy_price}, Şu an: {current_price})")
            sell_token(token_address)

# Ana döngü
if __name__ == "__main__":
    while True:
        try:
            # Portföyü kontrol et
            check_portfolio()

            # Yeni token tara
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
