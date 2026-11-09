"""
Claude 예약 작업이 queue/에 넣기 전에 글을 점검하는 도구.
실행: python3 check_post.py queue/파일이름.json
통과하면 'OK'와 실제로 채널에 올라갈 텍스트를 보여주고, 문제가 있으면 무엇을 고칠지 알려준다.
"""

import json
import sys

from news_common import (
    BREAKING_SCORE, CAPTION_LIMIT, TELEGRAM_LIMIT, build_message, check_post, find_candidate,
)

REQUIRED = ["link", "title", "source", "score", "headline", "paragraphs", "facts"]


def problems(post):
    missing = [k for k in REQUIRED if k not in post]
    if missing:
        return f"빠진 항목: {missing}"
    cand = find_candidate(post["link"])
    if not cand:
        return "link가 candidates.json에 없는 기사입니다. 후보 목록에 있는 기사만 쓰세요."
    if not cand.get("has_image") and float(post["score"]) < BREAKING_SCORE:
        return f"대표 사진이 없는 기사는 {BREAKING_SCORE}점 이상일 때만 올립니다. 다른 기사를 고르세요."
    p = check_post(post, cand["body"])
    if p:
        return p
    text = build_message(post, cand["link"])
    limit = TELEGRAM_LIMIT if cand.get("has_image") else CAPTION_LIMIT
    if len(text) > limit:
        return f"메시지가 너무 깁니다 ({len(text)}자 > {limit}자)."
    return None


def main(path):
    with open(path, "r", encoding="utf-8") as f:
        post = json.load(f)
    p = problems(post)
    if p:
        print("고칠 것: " + p)
        sys.exit(1)
    print("OK\n\n" + build_message(post, find_candidate(post["link"])["link"]))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("사용법: python3 check_post.py queue/파일이름.json")
    main(sys.argv[1])
