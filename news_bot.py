"""
엄선 뉴스 텔레그램 봇 (기존 '한 줄 뉴스'를 대체)

흐름 (매시간 실행, 08~23시 KST)
  1. 게시 여부 판단: 하루 최대 7개, 직전 게시 후 2시간 간격 (아주 큰 뉴스는 45분)
  2. 토큰포스트·블록미디어 RSS에서 최근 기사 수집 → 중복·홍보성 기사 1차 제거
  3. Claude가 '이 방 멤버의 오늘 매매에 필요한 뉴스인가' 기준으로 점수를 매김
     → 기준 점수 미만이면 이번 회차는 건너뜀 (억지로 채우지 않음)
  4. 고른 기사를 Claude가 사람이 쓴 말투로 다시 씀 (숫자는 원문에서 확인된 것만)
  5. 기사 대표 사진이 미리보기 카드로 글 위에 크게 붙어서 게시됨

실행: python news_bot.py
필요 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY
선택 환경변수:
  DRY_RUN=1   텔레그램에 보내지 않고 쓴 글만 로그로 출력 (기록도 저장 안 함)
  FORCE=1     시간·간격 조건을 무시하고 바로 한 건 시도
  CLAUDE_MODEL  사용할 Claude 모델 (기본 claude-sonnet-5-5)
"""

import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup

# ───────────────────────── 설정 ─────────────────────────

RSS_FEEDS = {
    "토큰포스트": "https://www.tokenpost.kr/rss",
    "블록미디어": "https://www.blockmedia.co.kr/feed",
}

POST_START_HOUR = 8        # KST, 이 시각부터 게시
POST_END_HOUR = 23         # KST, 이 시각(23:59)까지 게시
MAX_POSTS_PER_DAY = 7
MIN_GAP_MINUTES = 110      # 보통 뉴스 사이 간격
BREAKING_GAP_MINUTES = 45  # 아주 큰 뉴스(점수 9 이상)는 이 간격만 지나면 게시
LOOKBACK_HOURS = 4         # 이 시간 안에 나온 기사만 후보
MIN_SCORE = 7              # 이 점수 미만이면 올리지 않음
BREAKING_SCORE = 9
MAX_CANDIDATES = 30
REJECT_MEMORY_HOURS = 24   # 한 번 탈락한 기사는 이 시간 동안 다시 평가하지 않음

# 제목에 이런 말이 들어가면 홍보·광고성으로 보고 후보에서 뺌
EXCLUDE_KEYWORDS = [
    "제휴", "파트너십", "MOU", "업무협약", "출시", "론칭", "런칭", "이벤트", "에어드랍",
    "에어드롭", "프로모션", "보도자료", "[광고]", "[AD]", "스폰서", "후원", "기념식",
    "컨퍼런스 개최", "밋업", "채용", "인사]", "[부고]", "[포토]",
]

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
FORCE = os.environ.get("FORCE", "").lower() in ("1", "true", "yes")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "news_log.json")
OLD_HEADLINE_LOG = os.path.join(BASE_DIR, "headline_log.json")
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


# ───────────────────────── 기록 ─────────────────────────

def load_state():
    state = {"posted": [], "rejected": {}}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state.update(json.load(f))
    return state


def save_state(state, now):
    cutoff = now - timedelta(hours=REJECT_MEMORY_HOURS)
    state["rejected"] = {
        k: v for k, v in state["rejected"].items()
        if datetime.fromisoformat(v) >= cutoff
    }
    state["posted"] = state["posted"][-400:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def old_headline_links():
    if not os.path.exists(OLD_HEADLINE_LOG):
        return set()
    with open(OLD_HEADLINE_LOG, "r", encoding="utf-8") as f:
        return {normalize_link(x) for x in json.load(f)}


def normalize_link(url):
    """utm 같은 추적 파라미터를 지워서 같은 기사를 같은 주소로 본다."""
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")]
    return urlunparse((p.scheme, p.netloc, p.path, "", urlencode(q), ""))


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


def required_score(state, now):
    """지금 올릴 수 있으면 필요한 최소 점수를, 올릴 수 없으면 None을 돌려준다."""
    if FORCE:
        return MIN_SCORE
    hour = now.astimezone(KST).hour
    if not (POST_START_HOUR <= hour <= POST_END_HOUR):
        log(f"게시 시간이 아님 (KST {hour}시)")
        return None
    if len(posts_today(state, now)) >= MAX_POSTS_PER_DAY:
        log(f"오늘 {MAX_POSTS_PER_DAY}개를 이미 올림")
        return None
    gap = minutes_since_last_post(state, now)
    if gap is None or gap >= MIN_GAP_MINUTES:
        return MIN_SCORE
    if gap >= BREAKING_GAP_MINUTES:
        return BREAKING_SCORE
    log(f"직전 게시 후 {gap:.0f}분밖에 안 지남")
    return None


# ───────────────────────── 기사 수집 ─────────────────────────

def strip_html(text):
    return re.sub(r"\s+", " ", BeautifulSoup(text or "", "html.parser").get_text(" ")).strip()


def fetch_candidates(state, now):
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    posted_links = {p["link"] for p in state["posted"]} | old_headline_links()
    rejected = set(state["rejected"])
    seen_titles = set()
    items = []
    for source, url in RSS_FEEDS.items():
        try:
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
        except Exception as ex:
            log(f"[경고] {source} RSS 가져오기 실패: {ex}")
            continue
        for e in feed.entries:
            link, title = e.get("link"), (e.get("title") or "").strip()
            if not link or not title:
                continue
            link = normalize_link(link)
            if link in posted_links or link in rejected:
                continue
            if e.get("published_parsed"):
                published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
            else:
                continue
            if published < cutoff:
                continue
            if any(k.lower() in title.lower() for k in EXCLUDE_KEYWORDS):
                continue
            key = re.sub(r"\W", "", title)
            if key in seen_titles:
                continue
            seen_titles.add(key)
            content = ""
            if e.get("content"):
                content = e.content[0].get("value", "")
            summary = strip_html(content or e.get("summary", ""))
            items.append({
                "source": source, "title": title, "link": link,
                "published": published, "summary": summary,
            })
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:MAX_CANDIDATES]


def fetch_article(link):
    """기사 페이지에서 대표 사진 주소와 본문을 가져온다. 실패하면 (None, None, False)."""
    try:
        resp = requests.get(link, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
    except Exception as ex:
        log(f"[경고] 기사 페이지 열기 실패: {ex}")
        return None, None, False
    soup = BeautifulSoup(resp.text, "html.parser")
    img = None
    for attr, name in (("property", "og:image"), ("name", "twitter:image")):
        tag = soup.find("meta", attrs={attr: name})
        if tag and tag.get("content"):
            img = tag["content"].strip()
            break
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        t.decompose()
    container = soup.find("article") or soup.find(class_=re.compile(r"(article|entry|news)[-_]?(body|content|view)", re.I)) or soup
    paras = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    body = "\n".join(p for p in paras if len(p) > 25)
    return img, body[:6000], True


# ───────────────────────── Claude ─────────────────────────

def call_claude(system, user, max_tokens=2000):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Claude API 오류 {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"JSON을 찾지 못함: {text[:300]}")
    return json.loads(m.group(0))


SELECT_SYSTEM = """너는 텔레그램 채널 '직장인 트레이더 코인방'의 뉴스 편집장이다.
멤버는 비트코인·이더리움·리플을 주로 매매하는 직장인이고, 선물 매매를 하는 사람도 많다.
기준은 하나다: "이 방 멤버가 오늘 코인을 사고팔 때 알아야 하는 뉴스인가."

점수 기준 (0~10)
- 9~10: 시장 전체를 움직이는 큰 뉴스. 연준 금리 결정·CPI·고용 같은 핵심 지표, 대형 해킹·거래소 사고,
  ETF 승인·대규모 자금 유출입, 비트코인 급등락의 직접 원인, 전쟁·관세 같은 큰 위험 이벤트.
- 7~8: 매매에 바로 참고할 뉴스. 연준 인사 발언, 현물 ETF·스테이블코인·청산 등 자금 흐름,
  미국·한국 규제와 정책, 업비트·바이낸스 상장·상폐, BTC·ETH·XRP의 지지선·저항선 분석,
  엔비디아 등 코인과 같이 움직이는 AI주·빅테크의 큰 변동.
- 5~6: 흥미롭지만 오늘 매매에 영향은 적은 뉴스.
- 0~4: 올리지 않음. 기업 홍보(제휴·출시·행사), 가격 예측만 있는 기사, 작은 회사의 코인 보유량 공시,
  자체 집계 홍보, 코인과 무관한 기술·실적·해외 지역 소식, 근거 없는 루머.

같은 사건을 다룬 기사가 여러 개면 가장 자세해 보이는 것 하나만 점수를 주고 나머지는 3점 이하로 준다.
오늘 이미 올린 뉴스와 같은 사건이면 same_as_posted를 true로 한다.
반드시 JSON만 출력한다."""


def select_article(candidates, todays_titles):
    lines = []
    now = datetime.now(timezone.utc)
    for i, c in enumerate(candidates):
        mins = int((now - c["published"]).total_seconds() / 60)
        lines.append(f"[{i}] ({c['source']}, {mins}분 전) {c['title']}\n    {c['summary'][:260]}")
    posted = "\n".join(f"- {t}" for t in todays_titles) or "- (없음)"
    user = (
        f"오늘 이미 올린 뉴스:\n{posted}\n\n후보 기사:\n" + "\n".join(lines) +
        '\n\n각 후보를 평가해서 이 형식의 JSON만 출력해:\n'
        '{"scores":[{"id":0,"score":7,"same_as_posted":false,"reason":"한 줄 이유"}]}'
    )
    data = parse_json(call_claude(SELECT_SYSTEM, user, max_tokens=3000))
    return data.get("scores", [])


WRITE_SYSTEM = """너는 텔레그램 채널 '직장인 트레이더 코인방' 운영자다. 직장 다니면서 코인 매매를 하는 사람이고,
멤버들에게 오늘 알아야 할 뉴스를 직접 정리해서 올린다. 기자나 AI가 아니라 사람이 쓴 글처럼 읽혀야 한다.

말투와 구성
- 존댓말(~습니다/~요)로 쓰되 말하듯이 쓴다. "밖에서 맞은 겁니다", "더 어이없는 건", "~했는데요" 같은 자연스러운 표현을 섞는다.
- 매번 같은 틀로 시작하지 않는다. 사실부터 바로 말하기, "○○ 보도입니다"로 시작하기, 질문으로 시작하기 등을 기사에 맞게 고른다.
- 제목 한 줄 + 문단 3~4개. 문단은 1~3문장으로 짧게.
- 마지막 문단은 운영자 한마디: 멤버가 오늘 같이 볼 것, 조심할 것을 한두 문장으로. 매수·매도 지시나 가격 예측은 하지 않는다.
- 일정이나 체크할 항목이 뚜렷할 때만 checklist를 쓴다(📌로 시작하는 제목 + 항목 2~3개). 없으면 null.

제목
- 25~40자 한 줄. "주어, 사건…덧붙임" 형태를 자주 쓰되 늘 같을 필요는 없다.
- 이모지는 많아야 1개, 제목 맨 앞에만. 해외 정치·경제 뉴스면 국기(🇺🇸 등), 사고·속보면 🚨, 아니면 없어도 된다.

굵은 글씨
- **이렇게** 표시한다. 핵심 사실(숫자, 무슨 일이 있었는지)에만 쓰고 글 전체에서 2~5군데. 문장 통째로 굵게 하지 않는다.

정확성
- 기사에 있는 사실과 숫자만 쓴다. 기사에 없는 숫자, 환율 계산, 날짜를 만들어 내지 않는다.
- 확인되지 않은 내용은 확인되지 않았다고 쓴다.
- 기사 문장을 그대로 옮기지 말고 자기 말로 다시 쓴다.
- 본문에 숫자를 쓸 때마다 facts에 그 숫자와, 기사 원문에서 그 숫자가 나온 부분을 글자 그대로 10~40자 옮겨 적는다.

쓰지 않는 것
- 해시태그, URL, "결론적으로", "핵심은", "주목됩니다"의 반복, 줄표(—), "투자 권유" 같은 표현,
  기사에 없는 과장(폭등, 대폭락 등).

예시 (이 느낌으로)

예시 1
제목: 비트코인 7만5천 달러 깨졌습니다…이번엔 엔비디아발 악재
비트코인이 **5주 연속 하락**하면서 결국 **7만5천 달러 선까지 내줬습니다.**
이번엔 코인 쪽 악재가 아니라 밖에서 맞은 겁니다. 젠슨 황 CEO가 **중국 스파이 연루 의혹**에 휘말렸다는 보도가 나오면서 AI주가 한꺼번에 빠졌고, 비트코인도 같이 끌려 내려갔습니다.
엔비디아는 이사회가 직접 **자체 조사에 들어갔고, 경영 공백은 없다**는 입장입니다.
요즘 비트코인이 AI주랑 거의 같이 움직이고 있어서, 오늘 밤은 엔비디아 주가부터 같이 보시는 게 좋겠습니다 👀

예시 2
제목: 🚨 바이낸스 털어간 돈, 코인 플랫폼이 세탁해주고 수수료까지 챙겼다
월스트리트저널(WSJ) 보도입니다. 바이낸스에서 **60억 달러 넘게 훔쳐 간 북한 해커들**이 **1만 개 넘는 지갑**으로 돈을 쪼개서 세탁한 것으로 알려졌습니다.
더 어이없는 건, 일부 디파이 플랫폼이 이 거래를 처리해주면서 **수수료까지 받아 챙겼다**는 부분입니다.
이 정도 규모면 디파이 쪽 규제 얘기가 다시 나올 수밖에 없어 보입니다. 쓰시는 플랫폼이 이번 건에 이름이 오르내리는지 한 번씩 확인해 두세요.

예시 3
제목: 🇺🇸 물가 발표 주간, 연준에서 나온 한마디
시카고 연준 굴스비 총재가 **"가장 큰 문제는 물가"**라고 했습니다.
AI 붐에 대해서도 **데이터센터 투자에 과열 조짐이 보인다**고 짚었는데요. 다만 개인 의견일 뿐 정책 결정은 아니라고 선은 그었습니다.
(checklist) 📌 이번 주 체크할 것 / 미국 소비자물가(CPI) 발표 / AI 기업 실적
물가가 높게 나오면 금리 인하 기대가 줄어서 코인엔 부담입니다. 발표 날은 변동성이 크게 나올 수 있으니 무리한 포지션은 피하시는 게 좋겠습니다.

반드시 JSON만 출력한다."""

WRITE_FORMAT = """이 형식의 JSON만 출력해:
{
  "headline": "제목 한 줄",
  "paragraphs": ["문단1", "문단2", "문단3"],
  "checklist": null 또는 {"title": "📌 ...", "items": ["항목1", "항목2"], "after_paragraph": 2},
  "facts": [{"number": "60억 달러", "source_quote": "기사 원문에서 그대로 옮긴 부분"}]
}
after_paragraph는 checklist를 몇 번째 문단 뒤에 넣을지(1부터 셈)."""


def write_post(article, source_text, feedback=None):
    user = (
        f"매체: {article['source']}\n기사 제목: {article['title']}\n\n기사 원문:\n{source_text}\n\n"
        + WRITE_FORMAT
    )
    if feedback:
        user += f"\n\n앞에서 쓴 글의 문제: {feedback}\n이 문제를 고쳐서 다시 써줘."
    return parse_json(call_claude(WRITE_SYSTEM, user, max_tokens=2500))


# ───────────────────────── 검증과 메시지 만들기 ─────────────────────────

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
        full += " ".join(cl.get("items") or [])
    if re.search(r"https?://|www\.", full):
        return "본문에 URL을 넣으면 안 됩니다."
    if "#" in full:
        return "해시태그를 쓰면 안 됩니다."
    if "—" in full:
        return "줄표(—)를 쓰지 마세요."
    bolds = re.findall(r"\*\*(.+?)\*\*", "\n".join(paras))
    if not (1 <= len(bolds) <= 6):
        return "굵은 글씨는 2~5군데만 쓰세요."
    if full.count("**") % 2:
        return "굵은 글씨 표시(**)가 짝이 맞지 않습니다."
    if len(full) > 1100:
        return "글이 너무 깁니다. 더 짧게 써주세요."
    src = squash(source_text)
    for f in post.get("facts") or []:
        quote = squash(f.get("source_quote"))
        if quote and quote not in src:
            return f"'{f.get('number')}'의 근거로 적은 문장이 기사 원문에 없습니다. 원문에 있는 숫자만 쓰세요."
    body_numbers = re.findall(r"\d[\d,.]*", re.sub(r"\*\*", "", "\n".join(paras)))
    if body_numbers and not post.get("facts"):
        return "숫자를 썼다면 facts에 원문 근거를 적어야 합니다."
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


# ───────────────────────── 텔레그램 ─────────────────────────

def tg(method, **kwargs):
    resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", timeout=60, **kwargs)
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 {method} 실패: {result}")
    return result


def send_with_preview(text, link):
    """기사 대표 사진을 미리보기 카드로 글 위에 크게 붙여서 보낸다."""
    return tg("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {
            "url": link,
            "prefer_large_media": True,
            "show_above_text": True,
        },
    })


def send_with_default_image(text):
    with open(DEFAULT_IMAGE, "rb") as f:
        return tg("sendPhoto", data={
            "chat_id": CHAT_ID, "caption": text, "parse_mode": "HTML",
        }, files={"photo": ("news.jpg", f, "image/jpeg")})


# ───────────────────────── 실행 ─────────────────────────

def main():
    needed = {"ANTHROPIC_API_KEY": ANTHROPIC_API_KEY}
    if not DRY_RUN:
        needed.update({"TELEGRAM_BOT_TOKEN": BOT_TOKEN, "TELEGRAM_CHAT_ID": CHAT_ID})
    missing = [n for n, v in needed.items() if not v]
    if missing:
        msg = f"환경변수가 없습니다: {', '.join(missing)} (GitHub Settings > Secrets에 넣어주세요)"
        if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
            log(msg + " / 예약 실행이라 조용히 건너뜁니다.")
            return
        raise SystemExit(msg)

    now = datetime.now(timezone.utc)
    state = load_state()
    need = required_score(state, now)
    if need is None:
        return

    candidates = fetch_candidates(state, now)
    if not candidates:
        log("새 후보 기사가 없습니다.")
        return
    todays_titles = [p["title"] for p in posts_today(state, now)]
    scores = select_article(candidates, todays_titles)

    ranked = []
    for s in scores:
        try:
            i = int(s.get("id"))
        except (TypeError, ValueError):
            continue
        if not (0 <= i < len(candidates)):
            continue
        c = candidates[i]
        score = float(s.get("score") or 0)
        log(f"  {score:>4.1f}  {c['title']}  ({s.get('reason', '')})")
        if s.get("same_as_posted"):
            state["rejected"][c["link"]] = now.isoformat()
            continue
        if score < MIN_SCORE:
            state["rejected"][c["link"]] = now.isoformat()
            continue
        ranked.append((score, c))
    ranked.sort(key=lambda x: x[0], reverse=True)
    ranked = [(s, c) for s, c in ranked if s >= need]

    if not ranked:
        log(f"기준({need}점)을 넘는 기사가 없어 이번 회차는 건너뜁니다.")
        if not DRY_RUN:
            save_state(state, now)
        return

    for score, article in ranked[:3]:
        img, body, fetched = fetch_article(article["link"])
        has_image = bool(img) or not fetched  # 페이지를 못 열었으면 텔레그램 미리보기에 맡긴다
        if not has_image and score < BREAKING_SCORE:
            log(f"대표 사진이 없어 건너뜀: {article['title']}")
            state["rejected"][article["link"]] = now.isoformat()
            continue
        source_text = body if body and len(body) > len(article["summary"]) else article["summary"]
        if len(source_text) < 120:
            log(f"기사 내용이 너무 짧아 건너뜀: {article['title']}")
            continue

        post, problem = None, None
        for _ in range(2):
            post = write_post(article, source_text, feedback=problem)
            problem = check_post(post, source_text)
            if not problem:
                break
            log(f"[다시 쓰기] {problem}")
        if problem:
            log(f"두 번 써도 기준을 못 맞춰 건너뜀: {article['title']} / {problem}")
            state["rejected"][article["link"]] = now.isoformat()
            continue

        text = build_message(post, article["link"])
        use_default = not has_image
        if use_default and len(text) > CAPTION_LIMIT:
            log("사진 캡션 길이 초과로 건너뜀")
            continue
        if len(text) > TELEGRAM_LIMIT:
            log("메시지 길이 초과로 건너뜀")
            continue

        log("\n──────── 게시할 글 ────────\n" + text + "\n───────────────────────────")
        if DRY_RUN:
            log(f"[DRY_RUN] 보내지 않음 (점수 {score}, 사진 {'기본 이미지' if use_default else '기사 미리보기'})")
            return

        if use_default:
            send_with_default_image(text)
        else:
            send_with_preview(text, article["link"])
        state["posted"].append({
            "link": article["link"], "title": article["title"],
            "headline": post["headline"], "score": score,
            "posted_at": now.isoformat(),
        })
        save_state(state, now)
        log(f"게시 완료: {post['headline']} (점수 {score})")
        return

    log("후보는 있었지만 올릴 수 있는 기사가 없었습니다.")
    if not DRY_RUN:
        save_state(state, now)


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        log(f"[오류] {ex}")
        sys.exit(1)
