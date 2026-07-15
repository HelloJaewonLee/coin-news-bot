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
INTRO_VIDEO_PATH = os.path.join(os.path.dirname(__file__), "btc_jikjangin_real.mp4")

# 매일 하나씩 순서대로 돌아가며 사용하는 마무리 문구 (20개, 식상함 방지)
CLOSING_LINES = [
    "오늘도 무리하지 않게, 안전 제일로 갑니다 💪",
    "급할수록 돌아가기, 오늘도 여유 있게 가시죠 🙂",
    "무리한 매매보다 원칙을 지키는 하루 되세요 📈",
    "시장은 늘 열려있어요, 서두르지 마세요 ⏳",
    "오늘 하루도 리스크 관리 잊지 마세요 🛡️",
    "흔들리지 않는 투자, 오늘도 함께해요 🧭",
    "작은 이익도 소중히, 큰 손실은 조심히 💼",
    "조급함은 손실의 지름길, 천천히 가요 🐢",
    "오늘도 내 페이스대로 갑니다 🚶",
    "계획한 대로, 오늘 하루도 흔들림 없이 📊",
    "시장보다 내 원칙이 먼저입니다 ✅",
    "쉬어가는 것도 전략입니다, 오늘 하루 편안하게 ☕",
    "벌 때보다 지킬 때가 진짜 실력이죠 🔒",
    "오늘도 무너지지 않는 멘탈로 갑니다 🧠",
    "급등락에 흔들리지 않는 하루 되세요 🌊",
    "원칙 있는 투자자가 결국 웃습니다 😌",
    "오늘 하루도 안전벨트 단단히 매세요 🎢",
    "조급한 마음은 잠시 내려두세요 🍵",
    "꾸준함이 결국 답입니다, 오늘도 화이팅 🔥",
    "천천히, 그러나 꾸준하게 가는 하루 되세요 🌱",
]


def pick_closing_line(now_kst):
    idx = now_kst.timetuple().tm_yday % len(CLOSING_LINES)
    return CLOSING_LINES[idx]

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

    quote_block = (
        f"<blockquote>{html.escape(btc_line)}\n{html.escape(eth_line)}</blockquote>"
    )

    return (
        f"📊 <b>BTC 직장인 데일리 브리핑</b>\n"
        f"{html.escape(date_str)} KST\n\n"
        f"{quote_block}\n\n"
        f"{html.escape(pick_closing_line(now_kst))}"
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


def send_telegram_video_with_caption(caption):
    """영상 + 브리핑을 한 메시지로 함께 전송. 영상 파일이 없으면 False 반환."""
    if not os.path.exists(INTRO_VIDEO_PATH):
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
    with open(INTRO_VIDEO_PATH, "rb") as f:
        resp = requests.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "supports_streaming": True,
                "caption": caption,
                "parse_mode": "HTML",
            },
            files={"video": ("btc_jikjangin_real.mp4", f, "video/mp4")},
            timeout=60,
        )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        print(f"[경고] 영상+브리핑 전송 실패: {result}")
        return False
    return True


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되어 있지 않습니다."
        )

    btc_price, btc_change = fetch_price("BTCUSDT")
    eth_price, eth_change = fetch_price("ETHUSDT")

    message = build_message(btc_price, btc_change, eth_price, eth_change)
    sent_together = send_telegram_video_with_caption(message)
    if not sent_together:
        send_telegram_message(message)
    print("데일리 브리핑 게시 완료")


if __name__ == "__main__":
    main()
