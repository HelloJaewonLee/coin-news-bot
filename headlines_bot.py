"""
아침 한 줄 뉴스 텔레그램 봇
- 토큰포스트/블록미디어(한국 정식 암호화폐 매체) RSS에서 최근 뉴스를 모아
  "한 줄 뉴스" 리스트(제목만, 링크 연결) 형태로 게시
- 번역 필요 없음 (원래 한국어 매체)
- 하루 2번(08:00, 20:00 KST) 실행 권장
- 실행: python headlines_bot.py
  필요 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import os
import json
import html
from datetime import datetime, timezone, timedelta

import feedparser
import requests

RSS_FEEDS = [
    "https://www.tokenpost.kr/rss",
    "https://www.blockmedia.co.kr/feed",
]

HEADLINE_COUNT = 6  # 매 회차에 보여줄 헤드라인 개수
LOOKBACK_HOURS = 14  # 최근 몇 시간 이내 기사만 대상으로 할지 (하루 2회 실행에 맞춤)
STATE_FILE = os.path.join(os.path.dirname(__file__), "headline_log.json")
MAX_LOG_SIZE = 500
INTRO_VIDEO_PATH = os.path.join(
    os.path.dirname(__file__), "btc_jikjangin_real.mp4"
)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

KST = timezone(timedelta(hours=9))
WEEKDAY_KR = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def load_log():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_log(log):
    log = log[-MAX_LOG_SIZE:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def fetch_recent_headlines():
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=LOOKBACK_HOURS)
    items = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                link = e.get("link")
                title = e.get("title", "").strip()
                if not link or not title:
                    continue
                if e.get("published_parsed"):
                    published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                else:
                    published = now_utc
                if published < cutoff:
                    continue
                items.append({"title": title, "link": link, "published": published})
        except Exception as ex:
            print(f"[경고] {url} 가져오기 실패: {ex}")
    items.sort(key=lambda x: x["published"], reverse=True)
    return items


def select_new_headlines(items, posted_links, n):
    seen_titles = set()
    result = []
    for item in items:
        if item["link"] in posted_links:
            continue
        if item["title"] in seen_titles:
            continue
        seen_titles.add(item["title"])
        result.append(item)
        if len(result) >= n:
            break
    return result


CAPTION_LIMIT = 1024  # 텔레그램 영상/사진 캡션 최대 글자 수


def build_message(headlines):
    now_kst = datetime.now(KST)
    weekday = WEEKDAY_KR[now_kst.weekday()]
    date_str = now_kst.strftime(f"%y년 %m월 %d일 {weekday}")

    lines = [f"💌 <b>{html.escape(date_str)} 한 줄 뉴스</b>", ""]
    for item in headlines:
        title = html.escape(item["title"])
        link = html.escape(item["link"], quote=True)
        lines.append(f"▪ <a href=\"{link}\">{title}</a>")
    return "\n".join(lines)


def build_caption(headlines):
    """영상 캡션 글자수 제한(1024자)에 맞을 때까지 헤드라인을 줄여서 메시지를 만든다."""
    items = list(headlines)
    while True:
        msg = build_message(items)
        if len(msg) <= CAPTION_LIMIT or not items:
            return msg
        items = items[:-1]


def send_telegram_video_with_caption(caption):
    """영상 + 한 줄 뉴스를 한 메시지로 함께 전송. 영상 파일이 없으면 False 반환."""
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
        print(f"[경고] 영상+뉴스 전송 실패: {result}")
        return False
    return True


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
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

    log = load_log()
    posted_links = set(log)

    items = fetch_recent_headlines()
    headlines = select_new_headlines(items, posted_links, HEADLINE_COUNT)

    if not headlines:
        print("최근 새 기사가 없어 이번 회차는 건너뜁니다.")
        return

    caption = build_caption(headlines)
    sent_together = send_telegram_video_with_caption(caption)
    if not sent_together:
        # 영상이 없거나 실패한 경우, 텍스트만이라도 전송
        send_telegram_message(build_message(headlines))

    for h in headlines:
        log.append(h["link"])
    save_log(log)
    print(f"한 줄 뉴스 게시 완료 ({len(headlines)}건)")


if __name__ == "__main__":
    main()
