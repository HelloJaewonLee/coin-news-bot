"""
코인 뉴스 텔레그램 봇
- RSS로 암호화폐 뉴스를 모아서 그 시점 기준 가장 최신(이슈가 되는) 기사 1개를 골라 게시
- 각 뉴스마다: 대표 이미지 + Gemini가 정리한 한국어 요약(헤드라인/핵심 포인트/배경) + 원본 링크
- 이미 올린 기사는 posted_log.json 에 기록해서 중복 게시 방지
- 실행: python news_bot.py
  필요 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY(선택, 없으면 단순 번역으로 대체)
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

CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency",
    "blockchain", "defi", "nft", "altcoin", "token", "coin", "stablecoin",
    "binance", "coinbase", "solana", " sol ", "xrp", "ripple", "dogecoin",
    "doge", "web3", "wallet", "mining", "satoshi", "memecoin", "staking",
    "airdrop", "usdt", "usdc", "layer 2", "l2",
]

POSTS_PER_RUN = 1  # 실행 1회당 1개 기사만 게시 (하루 4번 실행)
STATE_FILE = os.path.join(os.path.dirname(__file__), "posted_log.json")
MAX_LOG_SIZE = 1000  # 로그 파일이 너무 커지지 않도록 최근 N개만 유지
ARTICLE_TEXT_LIMIT = 8000  # Gemini에 넘길 기사 본문 최대 글자 수
TELEGRAM_TEXT_LIMIT = 4000  # 텔레그램 메시지 1개 최대 글자 수 여유치
TELEGRAM_CAPTION_LIMIT = 1000  # 텔레그램 사진 캡션 최대 글자 수 여유치

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

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
                        "summary": e.get("summary", ""),
                    }
                )
        except Exception as ex:
            print(f"[경고] {url} 가져오기 실패: {ex}")
    return entries


def is_crypto_relevant(entry):
    text = f"{entry['title']} {entry.get('summary', '')}".lower()
    return any(kw in text for kw in CRYPTO_KEYWORDS)


def select_new_entries(entries, posted_links, n):
    # 가장 최신 기사부터 = 그 시점 기준 가장 화제가 되는 뉴스로 간주
    entries.sort(key=lambda x: x["published"], reverse=True)
    new_entries = [
        e
        for e in entries
        if e["link"] not in posted_links and is_crypto_relevant(e)
    ]
    return new_entries[:n]


def scrape_article(url):
    """기사 본문 전체와 대표 이미지를 가져온다. 실패하면 (None, None)."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None, None
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
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


def dedupe_paragraphs(text):
    """스크래핑 과정에서 같은 문단이 중복 수집되는 것을 제거한다."""
    if not text:
        return text
    seen = set()
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        lines.append(line)
    return "\n".join(lines)


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


SUMMARY_PROMPT = """당신은 암호화폐 뉴스 채널의 에디터입니다. 아래 영어 기사를 한국 구독자를 위해 보기 좋게 정리해주세요.

형식(반드시 지켜주세요, 각 블록 사이는 반드시 빈 줄로 구분):
1번째 줄: 기사 핵심을 압축한 한국어 헤드라인 (이모지 1개 정도, 15~30자)
(빈 줄)
"- "로 시작하는 핵심 포인트 3~5개, 한 줄에 한 문장씩 (숫자·수치는 정확히 살리기)
(빈 줄)
배경/맥락 설명 딱 1문단, 2~3문장 이내로 아주 간결하게

주의사항:
- 마크다운 기호(*, #, ** 등)는 쓰지 말고 순수 텍스트로만 작성
- 배경 설명은 여러 문단으로 나누지 말고 반드시 1문단으로만 작성
- 기사에 같은 내용이 반복돼 있으면 한 번만 언급
- 광고, 관련기사 목록, 탐색 메뉴 같은 내용은 무시
- 전체 분량은 700자를 넘기지 않기

기사 제목: {title}

기사 본문:
{article_text}
"""


def normalize_summary(text):
    """Gemini/번역 결과의 줄바꿈을 정리해 헤드라인-불릿-문단 사이에 빈 줄을 보장한다."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return text
    output = [lines[0]]
    prev_is_bullet = None
    for line in lines[1:]:
        is_bullet = line.startswith("-") or line.startswith("•")
        if prev_is_bullet is None:
            output.append("")
        elif not (is_bullet and prev_is_bullet):
            output.append("")
        output.append(line)
        prev_is_bullet = is_bullet
    return "\n".join(output)


def summarize_with_gemini(title, article_text):
    if not GEMINI_API_KEY or not article_text:
        return None
    prompt = SUMMARY_PROMPT.format(
        title=title, article_text=article_text[:ARTICLE_TEXT_LIMIT]
    )
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    try:
        resp = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as ex:
        print(f"[경고] Gemini 요약 실패, 번역으로 대체: {ex}")
        return None


def build_summary(entry, article_text):
    """Gemini 요약을 우선 시도하고, 실패하면 제목+본문 일부 번역으로 대체한다."""
    summary = summarize_with_gemini(entry["title"], article_text)
    if summary:
        return normalize_summary(summary)

    title_ko = translate_text(entry["title"])
    fallback_source = article_text or entry.get("summary", "")
    body_ko = translate_text(fallback_source[:1000]) if fallback_source else ""
    parts = [f"🪙 {title_ko}"]
    if body_ko:
        parts.append(body_ko)
    return normalize_summary("\n\n".join(parts))


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
    article_text, image_url = scrape_article(entry["link"])
    article_text = dedupe_paragraphs(article_text) if article_text else article_text

    summary = build_summary(entry, article_text)

    # 첫 줄은 헤드라인으로 사진 캡션에, 나머지는 본문 메시지에 사용
    lines = summary.split("\n", 1)
    headline = lines[0].strip()
    rest = lines[1].strip() if len(lines) > 1 else ""

    kst_time = entry["published"].astimezone(KST).strftime("%m/%d %H:%M")
    meta_line = f"📰 {entry['source']} · {kst_time} (KST)"
    footer = f"🔗 원문: {entry['link']}"

    headline_html = f"<b>{html.escape(headline)}</b>"
    meta_html = html.escape(meta_line)

    photo_sent = False
    if image_url:
        try:
            send_telegram_photo(image_url, f"{headline_html}\n{meta_html}")
            photo_sent = True
            time.sleep(1)
        except Exception as ex:
            print(f"[경고] 이미지 전송 실패, 텍스트로만 진행: {ex}")

    body_html = html.escape(rest) if rest else ""
    if photo_sent:
        combined = f"{body_html}\n\n{footer}" if body_html else footer
    else:
        combined = f"{headline_html}\n{meta_html}\n\n{body_html}\n\n{footer}"

    for chunk in chunk_text(combined, TELEGRAM_TEXT_LIMIT):
        send_telegram_message(chunk)
        time.sleep(1)


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되어 있지 않습니다."
        )
    if not GEMINI_API_KEY:
        print("[안내] GEMINI_API_KEY가 없어 AI 요약 대신 단순 번역으로 대체됩니다.")

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
