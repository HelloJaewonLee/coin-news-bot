"""
데일리 마켓 브리핑 텔레그램 봇
- 바이낸스 공개 시세 API로 BTC/ETH 현재가 + 24시간 등락률을 가져와 깔끔한 브리핑 형태로 게시
- 하루 1번(09:00 KST) 실행 권장
- 실행: python signal_bot.py
  필요 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import os
import random
import html
from datetime import datetime, timezone, timedelta

import requests

KST = timezone(timedelta(hours=9))
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Binance 공개 시세 전용 도메인 (지역 제한 없이 시세 데이터만 제공)
BINANCE_URL = "https://data-api.binance.vision/api/v3/ticker/24hr"

COMMENTS = {
    "strong_up": ["강한 상승세", "뚜렷한 반등", "매수세 우위"],
    "up": ["완만한 상승", "순항 중", "안정적 흐름"],
    "flat": ["보합권 유지", "관망 우세", "박스권 흐름"],
    "down": ["완만한 조정", "차익 실현 흐름", "관망 필요"],
    "strong_down": ["변동성 확대", "급락 조정", "리스크 관리 필요"],
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


def format_change(change_pct):
    return f"{change_pct:+.2f}%"


def build_message(btc_price, btc_change, eth_price, eth_change):
    now_kst = datetime.now(KST)
    weekday = WEEKDAY_KR[now_kst.weekday()]
    date_str = now_kst.strftime(f"%y.%m.%d({weekday}) %H:%M")

    btc_line = (
        f"🟠 비트코인   {format_price(btc_price)} USD   "
        f"{format_change(btc_change)}   · {comment_for_change(btc_change)}"
    )
    eth_line = (
        f"🔷 이더리움   {format_price(eth_price)} USD   "
        f"{format_change(eth_change)}   · {comment_for_change(eth_change)}"
    )

    return (
        f"📊 <b>BTC 직장인 데일리 브리핑</b>\n"
        f"{html.escape(date_str)} KST\n\n"
        f"{html.escape(btc_line)}\n"
        f"{html.escape(eth_line)}\n\n"
        f"오늘도 무리하지 않게, 안전 제일로 갑니다 💪"
    )


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
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
