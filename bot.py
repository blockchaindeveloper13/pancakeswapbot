from web3 import Web3
import os
import time
import json
import pandas_ta as ta
from ta.trend import RSIIndicator
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

# Fiyat ve likidite hesaplama
def get_pair_data(pair_address):
    pair_contract = web3.eth.contract(address=pair_address, abi=pair_abi)
    reserves = pair_contract.functions.getReserves().call()
    token0 = pair_contract.functions.token0().call()
    token1 = pair_contract.functions.token1().call()

    # Token sırasını kontrol et (WBNB/TOKEN veya TOKEN/WBNB)
    is_wbnb_token0 = token0.lower() == wbnb_address.lower()
    reserve_wbnb = reserves[0] if is_wbnb_token0 else reserves[1]
    reserve_token = reserves[1] if is_wbnb_token0 else reserves[0]
    
    # Fiyat: WBNB cinsinden
    if reserve_wbnb == 0:
        return None
    price = reserve_token / reserve_wbnb
    
    # Likidite: Yaklaşık USD cinsinden (WBNB fiyatını sabit 300 USD varsayalım)
    liquidity_usd = (reserve_wbnb / 10**18) * 300
    
    # Fiyat geçmişi (basitlik için son 14 bloğu alalım)
    price_history = []
    for i in range(14):
        try:
            past_block = web3.eth.get_block_number() - (i * 100)
            past_reserves = pair_contract.functions.getReserves().call(block_identifier=past_block)
            past_price = (past_reserves[1] / past_reserves[0]) if is_wbnb_token0 else (past_reserves[0] / past_reserves[1])
            price_history.append(past_price)
        except:
            price_history.append(price)
    
    token_address = token1 if is_wbnb_token0 else token0
    
    return {
        "token_address": token_address,
        "price": price,
        "liquidity": liquidity_usd,
        "price_history": price_history
    }

# RSI hesaplama
def calculate_rsi(prices):
    if len(prices) < 14:
        return None
    series = pd.Series(prices)
    rsi = ta.rsi(series, length=14)
    return rsi.iloc[-1]
    
# Token tarama
def scan_tokens():
    pair_count = pancake_factory.functions.allPairsLength().call()
    print(f"Toplam {pair_count} pair bulundu.")
    
    best_token = None
    best_score = float("inf")
    
    # İlk 100 pair'i tara (performans için)
    for i in range(min(100, pair_count)):
        pair_address = pancake_factory.functions.allPairs(i).call()
        data = get_pair_data(pair_address)
        if not data:
            continue

        # Filtreleme: Minimum likidite
        if data["liquidity"] < float(os.getenv("MIN_LIQUIDITY", 10000)):
            continue

        # RSI hesaplama
        rsi = calculate_rsi(data["price_history"])
        if rsi is None or rsi > float(os.getenv("RSI_THRESHOLD", 30)):
            continue

        # Skor: RSI bazlı
        score = rsi
        if score < best_score:
            best_score = score
            best_token = data["token_address"]

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
            time.sleep(int(os.getenv("CHECK_INTERVAL", 60)))
        except Exception as e:
            print(f"Hata: {e}")
            time.sleep(60)
