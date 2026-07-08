"""
코인 뉴스 텔레그램 봇
- RSS로 암호화폐 뉴스를 모아서 아직 안 올린 것 중 최신 3개를 텔레그램 채널에 게시
- 이미 올린 기사는 posted_log.json 에 기록해서 중복 게시 방지
- 실행: python news_bot.py  (환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요)
"""

import os
import json
import html
import time
from datetime import datetime, timezone, timedelta

import feedparser
import requests

# ---- 설정 ----
RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

POSTS_PER_RUN = 3
STATE_FILE = os.path.join(os.path.dirname(__file__), "posted_log.json")
MAX_LOG_SIZE = 1000  # 로그 파일이 너무 커지지 않도록 최근 N개만 유지

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

KST = timezone(timedelta(hours=9))


def load_posted_log():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_posted_log(log):
    log = log[-MAX_LOG_SIZE:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def fetch_all_entries():
    entries = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            source = feed.feed.get("title", url)
            for e in feed.entries:
                link = e.get("link")
                title = e.get("title", "").strip()
                if not link or not title:
                    continue
                # published_parsed이 없으면 지금 시간으로 대체
                if e.get("published_parsed"):
                    published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                else:
                    published = datetime.now(timezone.utc)
                entries.append(
                    {
                        "title": title,
                        "link": link,
                        "source": source,
                        "published": published,
                    }
                )
        except Exception as ex:
            print(f"[경고] {url} 가져오기 실패: {ex}")
    return entries


def select_new_entries(entries, posted_links, n):
    entries.sort(key=lambda x: x["published"], reverse=True)
    new_entries = [e for e in entries if e["link"] not in posted_links]
    return new_entries[:n]


def format_message(entry):
    title = html.escape(entry["title"])
    source = html.escape(entry["source"])
    kst_time = entry["published"].astimezone(KST).strftime("%m/%d %H:%M")
    return (
        f"🪙 <b>{title}</b>\n"
        f"📰 {source} · {kst_time} (KST)\n"
        f"{entry['link']}"
    )


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
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

    posted_log = load_posted_log()
    posted_links = set(posted_log)

    entries = fetch_all_entries()
    new_entries = select_new_entries(entries, posted_links, POSTS_PER_RUN)

    if not new_entries:
        print("새로운 뉴스가 없습니다. 이번 회차는 건너뜁니다.")
        return

    for entry in new_entries:
        message = format_message(entry)
        send_telegram_message(message)
        posted_log.append(entry["link"])
        print(f"게시 완료: {entry['title']}")
        time.sleep(1.5)  # 텔레그램 rate limit 여유

    save_posted_log(posted_log)


if __name__ == "__main__":
    main()
