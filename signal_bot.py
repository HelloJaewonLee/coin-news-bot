"""
아침 시그널(출근) 텔레그램 봇
- 바이낸스 공개 시세 API로 BTC/ETH 현재가 + 24시간 등락률을 가져와 정해진 템플릿으로 게시
- 하루 1번(09:00 KST) 실행 권장
- 실행: python signal_bot.py
  필요 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import os
import random

import requests

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Binance 공개 시세 전용 도메인 (지역 제한 없이 시세 데이터만 제공)
BINANCE_URL = "https://data-api.binance.vision/api/v3/ticker/24hr"

COMMENTS = {
    "strong_up": ["🔥 오늘 컨디션 최고", "🔥 강한 상승세", "🔥 분위기 좋음"],
    "up": ["기분 좋게 출발", "완만한 상승", "순항 중"],
    "flat": ["눈치보는 중", "보합권 유지", "숨고르기"],
    "down": ["잠시 조정 중", "완만한 하락", "관망 필요"],
    "strong_down": ["⚠️ 조심스러운 하루", "⚠️ 변동성 주의", "⚠️ 급락 조정 중"],
}


def comment_for_change(change_pct):
    if change_pct >= 3:
        bucket = "strong_up"
    elif change_pct >= 0.5:
        bucket = "up"
    elif change_pct > -0.5:
        bucket = "flat"
    elif change_pct > -3:
        bucket = "down"
    else:
        bucket = "strong_down"
    return random.choice(COMMENTS[bucket])


def fetch_price(symbol):
    """바이낸스에서 가격을 가져오고 실패하면 CoinGecko로 대체."""
    try:
        resp = requests.get(BINANCE_URL, params={"symbol": symbol}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return float(data["lastPrice"]), float(data["priceChangePercent"])
    except Exception as ex:
        print(f"[경고] 바이낸스 시세 조회 실패({symbol}), CoinGecko로 대체: {ex}")
        return fetch_price_coingecko(symbol)


COINGECKO_IDS = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum"}


def fetch_price_coingecko(symbol):
    coin_id = COINGECKO_IDS[symbol]
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()[coin_id]
    return float(data["usd"]), float(data["usd_24h_change"])


def format_price(price):
    return f"{price:,.0f}"


def build_message(btc_price, btc_change, eth_price, eth_change):
    return (
        "🌅🌅🌅🌅🌅🌅🌅🌅🌅🌅\n"
        "👷 BTCWORKMAN 출근했습니다\n"
        "#Gate X #OrangeX X #Tapbit\n"
        "🌅🌅🌅🌅🌅🌅🌅🌅🌅🌅\n"
        "📊 오늘 시장 상황\n\n"
        f"🟠 비트코인 : {format_price(btc_price)} ({comment_for_change(btc_change)})\n"
        f"🔷 이더리움 : {format_price(eth_price)} ({comment_for_change(eth_change)})\n\n"
        "📱 시그널 알림 켜두세요!\n"
        "오늘도 안전제일로 갑니다 💪"
    )


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {result}")
    return result


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되어 있지 않습니다."
        )

    btc_price, btc_change = fetch_price("BTCUSDT")
    eth_price, eth_change = fetch_price("ETHUSDT")

    message = build_message(btc_price, btc_change, eth_price, eth_change)
    send_telegram_message(message)
    print("출근 시그널 게시 완료")


if __name__ == "__main__":
    main()
