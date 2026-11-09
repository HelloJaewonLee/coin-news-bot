"""
엄선 뉴스 공통 설정과 기능

전체 흐름
  1. collect_news.py  (GitHub, 매시간)   기사 원문·대표 사진 주소를 모아 candidates.json 저장
  2. Claude 예약 작업 (2시간마다)         news_guide.md 기준으로 고르고 써서 queue/ 에 넣음
  3. post_queue.py    (GitHub, 글이 들어오면 바로)  점검 후 텔레그램 채널에 게시
"""

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# ───────────────────────── 설정 ─────────────────────────

RSS_FEEDS = {
    "토큰포스트": "https://www.tokenpost.kr/rss",
    "블록미디어": "https://www.blockmedia.co.kr/feed",
}

POST_START_HOUR = 8        # KST, 이 시각부터 게시
POST_END_HOUR = 23         # KST, 이 시각(23:59)까지 게시
MAX_POSTS_PER_DAY = 7
MIN_GAP_MINUTES = 100      # 보통 뉴스 사이 간격 (예약 작업이 2시간마다 돌아서 100분)
BREAKING_GAP_MINUTES = 45  # 아주 큰 뉴스(점수 9 이상)는 이 간격만 지나면 게시
LOOKBACK_HOURS = 6         # 이 시간 안에 나온 기사만 후보
MIN_SCORE = 7              # 이 점수 미만이면 올리지 않음
BREAKING_SCORE = 9
MAX_PER_SOURCE = 25       # 매체별로 최근 기사 몇 개까지 후보로 볼지
CANDIDATES_MAX_AGE_MIN = 180   # 후보 목록이 이보다 오래됐으면 쓰지 않음
QUEUE_MAX_AGE_MIN = 180        # 대기 중인 글이 이보다 오래됐으면 올리지 않음

# 제목에 이런 말이 들어가면 홍보·광고성으로 보고 후보에서 뺌
EXCLUDE_KEYWORDS = [
    "제휴", "파트너십", "MOU", "업무협약", "출시", "론칭", "런칭", "이벤트", "에어드랍",
    "에어드롭", "프로모션", "보도자료", "[광고]", "[AD]", "스폰서", "후원", "기념식",
    "컨퍼런스 개최", "밋업", "채용", "인사]", "[부고]", "[포토]",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "news_log.json")
OLD_HEADLINE_LOG = os.path.join(BASE_DIR, "headline_log.json")
CANDIDATES_FILE = os.path.join(BASE_DIR, "candidates.json")
QUEUE_DIR = os.path.join(BASE_DIR, "queue")
TEST_REQUEST = os.path.join(QUEUE_DIR, ".test_request")
DEFAULT_IMAGE = os.path.join(BASE_DIR, "assets", "news_default.jpg")

KST = timezone(timedelta(hours=9))
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TELEGRAM_LIMIT = 4096
CAPTION_LIMIT = 1024


def log(msg):
    print(msg, flush=True)


def now_utc():
    return datetime.now(timezone.utc)


# ───────────────────────── 기록 ─────────────────────────

def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def load_state():
    state = {"posted": []}
    state.update(load_json(STATE_FILE, {}))
    return state


def save_state(state):
    state["posted"] = state["posted"][-400:]
    save_json(STATE_FILE, state)


def normalize_link(url):
    """utm 같은 추적 파라미터를 지워서 같은 기사를 같은 주소로 본다."""
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")]
    return urlunparse((p.scheme, p.netloc, p.path, "", urlencode(q), ""))


def already_posted_links(state):
    links = {p["link"] for p in state["posted"]}
    links |= {normalize_link(x) for x in load_json(OLD_HEADLINE_LOG, [])}
    return links


# ───────────────────────── 게시 시점 판단 ─────────────────────────

def posts_today(state, now):
    today = now.astimezone(KST).date()
    return [
        p for p in state["posted"]
        if datetime.fromisoformat(p["posted_at"]).astimezone(KST).date() == today
    ]


def minutes_since_last_post(state, now):
    if not state["posted"]:
        return None
    last = datetime.fromisoformat(state["posted"][-1]["posted_at"])
    return (now - last).total_seconds() / 60


def in_post_hours(now):
    return POST_START_HOUR <= now.astimezone(KST).hour <= POST_END_HOUR


def required_score(state, now):
    """(필요 점수, 이유). 지금 올릴 수 없으면 필요 점수가 None."""
    hour = now.astimezone(KST).hour
    if not in_post_hours(now):
        return None, f"게시 시간이 아님 (KST {hour}시, {POST_START_HOUR}~{POST_END_HOUR}시만 게시)"
    if len(posts_today(state, now)) >= MAX_POSTS_PER_DAY:
        return None, f"오늘 {MAX_POSTS_PER_DAY}개를 이미 올림"
    gap = minutes_since_last_post(state, now)
    if gap is None or gap >= MIN_GAP_MINUTES:
        return MIN_SCORE, "보통 기준"
    if gap >= BREAKING_GAP_MINUTES:
        return BREAKING_SCORE, f"직전 게시 후 {gap:.0f}분이라 아주 큰 뉴스만"
    return None, f"직전 게시 후 {gap:.0f}분밖에 안 지남"


def minutes_old(iso, now):
    return (now - datetime.fromisoformat(iso)).total_seconds() / 60


# ───────────────────────── 글 점검과 메시지 만들기 ─────────────────────────

def squash(s):
    return re.sub(r"[\s,·'\"“”‘’]", "", s or "")


def check_post(post, source_text):
    """문제가 있으면 문제 설명을, 없으면 None을 돌려준다."""
    headline = (post.get("headline") or "").strip()
    paras = [p.strip() for p in post.get("paragraphs") or [] if p and p.strip()]
    if not headline or len(headline) > 60:
        return "제목이 없거나 너무 깁니다 (60자 이하)."
    if not (2 <= len(paras) <= 5):
        return "문단은 3~4개로 써야 합니다."
    full = headline + "\n".join(paras)
    cl = post.get("checklist")
    if cl:
        if not isinstance(cl, dict) or not cl.get("items"):
            return "checklist는 null이거나 title과 items가 있어야 합니다."
        full += " ".join(cl.get("items") or [])
    if re.search(r"https?://|www\.", full):
        return "본문에 URL을 넣으면 안 됩니다."
    if "#" in full:
        return "해시태그를 쓰면 안 됩니다."
    if "—" in full:
        return "줄표(—)를 쓰지 마세요."
    joined = "\n".join(paras)
    if joined.count("**") % 2:
        return "굵은 글씨 표시(**)가 짝이 맞지 않습니다."
    bolds = re.findall(r"\*\*(.+?)\*\*", joined)
    if not (2 <= len(bolds) <= 6):
        return f"굵은 글씨는 2~5군데만 쓰세요 (지금 {len(bolds)}군데)."
    if len(full) > 1100:
        return f"글이 너무 깁니다 ({len(full)}자). 900자 안쪽으로 줄여주세요."
    src = squash(source_text)
    facts = post.get("facts") or []
    for f in facts:
        quote = squash(f.get("source_quote"))
        if not quote:
            return f"'{f.get('number')}'의 source_quote가 비어 있습니다."
        if quote not in src:
            return (f"'{f.get('number')}'의 근거 source_quote가 기사 원문(candidates.json의 body)에 "
                    f"글자 그대로 없습니다. 원문에서 그대로 복사하세요.")
    body_numbers = re.findall(r"\d[\d,.]*", re.sub(r"\*\*", "", headline + joined))
    if body_numbers and not facts:
        return "제목·본문에 숫자를 썼다면 facts에 원문 근거를 적어야 합니다."
    return None


def to_html(text):
    """**굵게** 표시를 텔레그램 HTML로 바꾼다."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def build_message(post, link):
    parts = [f"<b>{html.escape(post['headline'].replace('**', ''), quote=False)}</b>"]
    paras = [p.strip() for p in post["paragraphs"] if p and p.strip()]
    cl = post.get("checklist")
    after = None
    if cl and cl.get("items"):
        after = int(cl.get("after_paragraph") or len(paras) - 1)
        after = max(1, min(after, len(paras)))
    for i, p in enumerate(paras, start=1):
        parts.append(to_html(p))
        if after == i:
            items = "\n".join(f"• {to_html(x)}" for x in cl["items"][:4])
            parts.append(f"<blockquote><b>{to_html(cl.get('title') or '📌 체크할 것')}</b>\n{items}</blockquote>")
    parts.append(f'<a href="{html.escape(link, quote=True)}">🔗 원문 보기</a>')
    return "\n\n".join(parts)


def find_candidate(link):
    data = load_json(CANDIDATES_FILE, {"items": []})
    link = normalize_link(link)
    for c in data.get("items", []):
        if c["link"] == link:
            return c
    return None
