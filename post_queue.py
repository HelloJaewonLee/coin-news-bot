"""
대기 중인 글 게시 (GitHub Actions: queue/에 글이 들어오면 바로, 그리고 매시간 한 번 더 확인)

queue/*.json 중 가장 먼저 들어온 글 하나를 다시 점검한 뒤 텔레그램 채널에 올린다.
- 기사 대표 사진이 미리보기 카드로 글 위에 크게 붙는다.
- 사진이 없는 큰 뉴스는 assets/news_default.jpg를 붙인다.
- 시험 글(dry_run)은 보내지 않고 queue/preview/에 실제로 올라갈 텍스트만 남긴다.
필요 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import glob
import json
import os
import shutil
import sys

import requests

from news_common import (
    DEFAULT_IMAGE, MAX_POSTS_PER_DAY, QUEUE_DIR, QUEUE_MAX_AGE_MIN, build_message,
    find_candidate, in_post_hours, load_state, log, minutes_old, normalize_link, now_utc,
    posts_today, save_state,
)
from check_post import problems

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def tg(method, **kwargs):
    resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", timeout=60, **kwargs)
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 {method} 실패: {result.get('description')}")
    return result


def send_with_preview(text, link):
    return tg("sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"url": link, "prefer_large_media": True, "show_above_text": True},
    })


def send_with_default_image(text):
    with open(DEFAULT_IMAGE, "rb") as f:
        return tg("sendPhoto", data={"chat_id": CHAT_ID, "caption": text, "parse_mode": "HTML"},
                  files={"photo": ("news.jpg", f, "image/jpeg")})


def move(path, sub, note=None):
    d = os.path.join(QUEUE_DIR, sub)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, os.path.basename(path))
    shutil.move(path, dest)
    if note:
        with open(dest + ".txt", "w", encoding="utf-8") as f:
            f.write(note + "\n")
    log(f"{os.path.basename(path)} → queue/{sub}/ {note or ''}")


def main():
    files = sorted(glob.glob(os.path.join(QUEUE_DIR, "*.json")))
    if not files:
        log("대기 중인 글이 없습니다.")
        return
    now = now_utc()
    state = load_state()
    posted = {p["link"] for p in state["posted"]}

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                post = json.load(f)
        except Exception as ex:
            move(path, "rejected", f"JSON 읽기 실패: {ex}")
            continue
        link = normalize_link(post.get("link", ""))
        post["link"] = link
        cand = find_candidate(link)
        message = build_message(post, link) if post.get("headline") and post.get("paragraphs") else ""

        if post.get("dry_run"):
            p = problems(post)
            move(path, "preview", ("[점검 통과]" if not p else f"[고칠 것] {p}") + "\n\n" + message)
            continue
        if link in posted:
            move(path, "rejected", "이미 올린 기사")
            continue
        written = post.get("written_at")
        if written and minutes_old(written, now) > QUEUE_MAX_AGE_MIN:
            move(path, "expired", f"{QUEUE_MAX_AGE_MIN}분 넘게 지난 글")
            continue
        if len(posts_today(state, now)) >= MAX_POSTS_PER_DAY:
            move(path, "expired", f"오늘 {MAX_POSTS_PER_DAY}개를 이미 올림")
            continue
        if not in_post_hours(now):
            log("게시 시간이 아니라 다음 회차로 미룹니다.")
            return
        p = problems(post)
        if p:
            move(path, "rejected", p)
            continue

        if not BOT_TOKEN or not CHAT_ID:
            sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 없습니다.")
        if cand.get("has_image"):
            send_with_preview(message, link)
        else:
            send_with_default_image(message)
        state["posted"].append({
            "link": link, "title": post.get("title"), "headline": post.get("headline"),
            "score": post.get("score"), "posted_at": now.isoformat(),
        })
        save_state(state)
        os.remove(path)
        log(f"게시 완료: {post.get('headline')}")
        return  # 한 번에 하나만 올린다


if __name__ == "__main__":
    main()
