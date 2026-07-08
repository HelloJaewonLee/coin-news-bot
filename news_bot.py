"""
코인 뉴스 텔레그램 봇
- RSS로 암호화폐 뉴스를 모아서 아직 안 올린 것 중 최신 3개를 텔레그램 채널에 게시
- 각 뉴스마다: 대표 이미지(스크린샷 대용) + 기사 전문 한국어 번역 + 원본 링크
- 이미 올린 기사는 posted_log.json 에 기록해서 중복 게시 방지
- 실행: python news_bot.py  (환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요)

※ 주의: 기사 전문을 번역해서 그대로 올리면 원문 매체의 저작권을 침해할 소지가 있습니다.
   개인/소규모 채널 운영이라도 문제가 될 수 있으니, 가능하면 요약 위주로 사용하는 것을 권장합니다.
"""

import os
import json
import html
import time
from datetime import datetime, timezone, timedelta

import feedparser
import requests
import trafilatura
from deep_translator import GoogleTranslator

# ---- 설정 ----
RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

POSTS_PER_RUN = 3
STATE_FILE = os.path.join(os.path.dirname(__file__), "posted_log.json")
MAX_LOG_SIZE = 1000  # 로그 파일이 너무 커지지 않도록 최근 N개만 유지
TRANSLATE_CHUNK_SIZE = 4000  # 구글 번역 1회 호출 최대 글자 수 여유치
TELEGRAM_TEXT_LIMIT = 4000  # 텔레그램 메시지 1개 최대 글자 수 여유치
TELEGRAM_CAPTION_LIMIT = 1000  # 텔레그램 사진 캡션 최대 글자 수 여유치

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


def scrape_article(url):
    """기사 본문 전체와 대표 이미지를 가져온다. 실패하면 (None, None)."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None, None
        text = trafilatura.extract(
            downloaded, include_comments=False, include_tables=False
        )
        image = None
        try:
            metadata = trafilatura.extract_metadata(downloaded)
            if metadata:
                image = metadata.image
        except Exception:
            image = None
        return text, image
    except Exception as ex:
        print(f"[경고] 기사 본문 수집 실패 ({url}): {ex}")
        return None, None


def chunk_text(text, max_len):
    """긴 텍스트를 문단 단위로 max_len 이하 조각으로 나눈다."""
    if not text:
        return []
    paragraphs = text.split("\n")
    chunks = []
    current = ""
    for p in paragraphs:
        candidate = f"{current}\n{p}" if current else p
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = p
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def translate_text(text):
    try:
        return GoogleTranslator(source="en", target="ko").translate(text)
    except Exception as ex:
        print(f"[경고] 번역 실패, 원문 사용: {ex}")
        return text


def translate_long_text(text):
    if not text:
        return ""
    chunks = chunk_text(text, TRANSLATE_CHUNK_SIZE)
    translated = []
    for c in chunks:
        translated.append(translate_text(c))
        time.sleep(1)
    return "\n".join(translated)


def send_telegram_photo(photo_url, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    resp = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "photo": photo_url,
            "caption": caption[:TELEGRAM_CAPTION_LIMIT],
            "parse_mode": "HTML",
        },
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 사진 전송 실패: {result}")
    return result


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


def post_entry(entry):
    title_ko = translate_text(entry["title"])
    article_text, image_url = scrape_article(entry["link"])

    if not article_text:
        article_text = entry.get("summary", "")

    body_ko = translate_long_text(article_text) if article_text else ""

    kst_time = entry["published"].astimezone(KST).strftime("%m/%d %H:%M")
    header = f"🪙 <b>{html.escape(title_ko)}</b>\n📰 {html.escape(entry['source'])} · {kst_time} (KST)"
    footer = f"🔗 원문: {entry['link']}"

    photo_sent = False
    if image_url:
        try:
            send_telegram_photo(image_url, header)
            photo_sent = True
            time.sleep(1)
        except Exception as ex:
            print(f"[경고] 이미지 전송 실패, 텍스트로만 진행: {ex}")

    body_full = body_ko if body_ko else "(본문을 가져오지 못했습니다.)"
    first_chunk_prefix = "" if photo_sent else header + "\n\n"
    combined = f"{first_chunk_prefix}{body_full}\n\n{footer}"

    for chunk in chunk_text(combined, TELEGRAM_TEXT_LIMIT):
        send_telegram_message(chunk)
        time.sleep(1)


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
        try:
            post_entry(entry)
            posted_log.append(entry["link"])
            print(f"게시 완료: {entry['title']}")
        except Exception as ex:
            print(f"[오류] 게시 실패 ({entry['link']}): {ex}")
        time.sleep(2)

    save_posted_log(posted_log)


if __name__ == "__main__":
    main()
