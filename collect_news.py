"""
기사 후보 수집 (GitHub Actions에서 매시간 실행)

토큰포스트·블록미디어 RSS에서 최근 기사를 모으고, 홍보성·중복·이미 올린 기사를 뺀 뒤
기사 페이지에서 대표 사진 주소와 본문을 가져와 candidates.json에 저장한다.
Claude 예약 작업은 이 파일을 읽고 기사를 고른다.
실행: python collect_news.py
"""

import re
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

import glob
import json
import os

from news_common import (
    CANDIDATES_FILE, EXCLUDE_KEYWORDS, LOOKBACK_HOURS, MAX_PER_SOURCE, QUEUE_DIR, RSS_FEEDS, UA,
    already_posted_links, load_json, load_state, log, normalize_link, now_utc, save_json,
)


def strip_html(text):
    return re.sub(r"\s+", " ", BeautifulSoup(text or "", "html.parser").get_text(" ")).strip()


def fetch_feed_items(now, skip_links):
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    seen_titles, items = set(), []
    for source, url in RSS_FEEDS.items():
        taken = 0
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as ex:
            log(f"[경고] {source} RSS 가져오기 실패: {ex}")
            continue
        for e in feed.entries:
            link, title = e.get("link"), (e.get("title") or "").strip()
            if not link or not title or not e.get("published_parsed"):
                continue
            link = normalize_link(link)
            if link in skip_links:
                continue
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            if published < cutoff:
                continue
            if any(k.lower() in title.lower() for k in EXCLUDE_KEYWORDS):
                continue
            key = re.sub(r"\W", "", title)
            if key in seen_titles:
                continue
            seen_titles.add(key)
            if taken >= MAX_PER_SOURCE:
                continue
            taken += 1
            content = e.content[0].get("value", "") if e.get("content") else ""
            items.append({
                "source": source, "title": title, "link": link,
                "published": published.isoformat(),
                "summary": strip_html(content or e.get("summary", "")),
            })
    items.sort(key=lambda x: x["published"], reverse=True)
    return items


def fetch_article(link):
    """(대표 사진 주소, 본문, 페이지를 열었는지)"""
    try:
        resp = requests.get(link, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
    except Exception as ex:
        log(f"[경고] 기사 페이지 열기 실패 {link}: {ex}")
        return None, "", False
    soup = BeautifulSoup(resp.text, "html.parser")
    img = None
    for attr, name in (("property", "og:image"), ("name", "twitter:image")):
        tag = soup.find("meta", attrs={attr: name})
        if tag and tag.get("content"):
            img = tag["content"].strip()
            break
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        t.decompose()
    container = (soup.find("article")
                 or soup.find(class_=re.compile(r"(article|entry|news)[-_]?(body|content|view)", re.I))
                 or soup)
    paras = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    body = "\n".join(p for p in paras if len(p) > 25)
    return img, body[:3500], True


def pending_links():
    links = set()
    for path in glob.glob(os.path.join(QUEUE_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                links.add(normalize_link(json.load(f).get("link", "")))
        except Exception:
            pass
    return links


def main():
    now = now_utc()
    state = load_state()
    items = fetch_feed_items(now, already_posted_links(state))
    out = []
    for it in items:
        img, body, opened = fetch_article(it["link"])
        text = body if len(body) > len(it["summary"]) else it["summary"]
        if len(text) < 120:
            continue
        out.append({
            "id": len(out),
            "source": it["source"],
            "title": it["title"],
            "link": it["link"],
            "published": it["published"],
            # 페이지를 못 열었으면 사진 여부를 모름 → 텔레그램 미리보기에 맡김
            "has_image": bool(img) or not opened,
            "image": img,
            "summary": text[:280],
            "body": text[:3500],
        })
    # 아직 게시 대기 중인 글의 기사는 목록에서 빠지지 않게 이전 목록에서 그대로 가져온다
    have = {c["link"] for c in out}
    for c in load_json(CANDIDATES_FILE, {"items": []}).get("items", []):
        if c["link"] in pending_links() and c["link"] not in have:
            c = dict(c, id=len(out))
            out.append(c)
    save_json(CANDIDATES_FILE, {"generated_at": now.isoformat(), "items": out})
    log(f"후보 {len(out)}건 저장")


if __name__ == "__main__":
    main()
