"""
Claude 예약 작업용 상태 확인 도구.

  python3 news_status.py          지금 글을 써도 되는지, 필요 점수, 오늘 올린 뉴스 (JSON)
  python3 news_status.py --list   후보 기사 목록 (번호, 매체, 몇 분 전, 사진 여부, 제목, 요약)
  python3 news_status.py --show N N번 기사의 원문 전체 (글을 쓸 때만 읽기)
"""

import glob
import json
import os
import sys

from news_common import (
    CANDIDATES_FILE, CANDIDATES_MAX_AGE_MIN, MIN_SCORE, QUEUE_DIR, TEST_REQUEST, load_json,
    load_state, minutes_old, now_utc, posts_today, required_score,
)


def status():
    now = now_utc()
    state = load_state()
    test = os.path.exists(TEST_REQUEST)
    need, reason = required_score(state, now)
    if test:
        need, reason = MIN_SCORE, "시험 실행 요청(queue/.test_request)이 있어서 시간·간격 조건 무시"

    cand = load_json(CANDIDATES_FILE, {})
    age = minutes_old(cand["generated_at"], now) if cand.get("generated_at") else None
    pending = [os.path.basename(p) for p in glob.glob(os.path.join(QUEUE_DIR, "*.json"))]

    can_write = need is not None
    if can_write and pending:
        can_write, reason = False, f"아직 게시 안 된 글이 대기 중: {pending}"
    if can_write and (age is None or age > CANDIDATES_MAX_AGE_MIN):
        can_write, reason = False, f"후보 목록이 없거나 오래됨 ({age and round(age)}분 전)"

    return {
        "can_write": can_write,
        "reason": reason,
        "test_run": test,
        "required_score": need,
        "candidates_count": len(cand.get("items", [])),
        "candidates_age_minutes": age and round(age),
        "posted_today": [
            {"headline": p.get("headline"), "title": p["title"], "posted_at": p["posted_at"]}
            for p in posts_today(state, now)
        ],
    }


def list_candidates():
    now = now_utc()
    for c in load_json(CANDIDATES_FILE, {"items": []}).get("items", []):
        mins = round(minutes_old(c["published"], now))
        photo = "사진O" if c.get("has_image") else "사진X"
        print(f"[{c['id']}] {c['source']} · {mins}분 전 · {photo}\n    {c['title']}\n    {c.get('summary', c['body'][:280])}\n")


def show(i):
    for c in load_json(CANDIDATES_FILE, {"items": []}).get("items", []):
        if c["id"] == i:
            print(json.dumps({k: c[k] for k in ("id", "source", "title", "link", "has_image")},
                             ensure_ascii=False, indent=2))
            print("\n[body]\n" + c["body"])
            return
    sys.exit(f"{i}번 후보가 없습니다.")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--list":
        list_candidates()
    elif len(sys.argv) >= 3 and sys.argv[1] == "--show":
        show(int(sys.argv[2]))
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
