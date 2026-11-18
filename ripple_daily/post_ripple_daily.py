"""
리플 데일리 게시 (GitHub Actions: ripple_daily/outbox/에 오늘 세트가 들어오면 실행)

Claude 예약 작업이 매일 아침 ripple_daily/outbox/YYYYMMDD.json + YYYYMMDD.jpg 를 올리면
오전 11시(한국시간)에 맞춰 텔레그램 업무 정보방에 아래 순서로 올린다.
  1) 리플 데일리 사진 (짧은 설명 포함)
  2) 카톡에 그대로 붙여 쓸 문구 (복사하기 쉽게 별도 메시지)
게시한 세트는 ripple_daily/sent/로 옮기고 sent_log.json에 기록한다.

필요 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_WORK_CHAT_ID (업무 정보방 ID)
"""

import glob
import json
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
BASE = os.path.dirname(os.path.abspath(__file__))
OUTBOX = os.path.join(BASE, "outbox")
SENT = os.path.join(BASE, "sent")
LOG = os.path.join(BASE, "sent_log.json")
POST_HOUR = 11          # 오전 11시에 맞춰 올림
MAX_WAIT_MIN = 40       # 일찍 들어온 세트는 최대 40분까지 기다렸다가 올림

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_WORK_CHAT_ID")


def log(msg):
    print(f"[{datetime.now(KST):%H:%M:%S}] {msg}", flush=True)


def tg(method, **kwargs):
    resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", timeout=60, **kwargs)
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 {method} 실패: {result.get('description')}")
    return result


def load_log():
    try:
        with open(LOG, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"sent": []}


def move_set(json_path, sub, note=None):
    d = os.path.join(BASE, sub) if sub != "sent" else SENT
    os.makedirs(d, exist_ok=True)
    stem = os.path.splitext(os.path.basename(json_path))[0]
    for ext in (".json", ".jpg"):
        src = os.path.join(OUTBOX, stem + ext)
        if os.path.exists(src):
            shutil.move(src, os.path.join(d, stem + ext))
    if note:
        with open(os.path.join(d, stem + ".txt"), "w", encoding="utf-8") as f:
            f.write(note + "\n")
    log(f"{stem} → ripple_daily/{sub}/ {note or ''}")


def wait_until_post_time():
    now = datetime.now(KST)
    target = now.replace(hour=POST_HOUR, minute=0, second=0, microsecond=0)
    if now < target:
        wait = (target - now).total_seconds()
        if wait > MAX_WAIT_MIN * 60:
            log(f"아직 {wait/60:.0f}분 남아 이번엔 올리지 않고 11시 확인 회차에 올립니다.")
            return False
        log(f"오전 {POST_HOUR}시까지 {wait/60:.1f}분 기다립니다.")
        time.sleep(wait)
    return True


def main():
    files = sorted(glob.glob(os.path.join(OUTBOX, "*.json")))
    if not files:
        log("올릴 리플 데일리가 없습니다.")
        return
    state = load_log()
    done_dates = {s["date"] for s in state["sent"]}
    today = datetime.now(KST).strftime("%Y-%m-%d")

    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                post = json.load(f)
        except Exception as ex:
            move_set(path, "rejected", f"JSON 읽기 실패: {ex}")
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        image = os.path.join(OUTBOX, stem + ".jpg")
        date = post.get("date", "")

        if post.get("dry_run"):
            move_set(path, "preview", "[시험 세트 — 게시 안 함]\n\n" + post.get("message", ""))
            continue
        if not os.path.exists(image):
            move_set(path, "rejected", "사진 파일(.jpg)이 없음")
            continue
        if not post.get("message", "").strip():
            move_set(path, "rejected", "문구(message)가 비어 있음")
            continue
        if date != today:
            move_set(path, "expired", f"날짜가 오늘({today})이 아님: {date}")
            continue
        if date in done_dates:
            move_set(path, "rejected", "오늘 세트는 이미 올림")
            continue
        if not BOT_TOKEN or not CHAT_ID:
            sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_WORK_CHAT_ID 가 설정되지 않았습니다. "
                     "GitHub 저장소 Settings → Secrets and variables → Actions 에서 TELEGRAM_WORK_CHAT_ID 를 추가하세요.")
        if not wait_until_post_time():
            return

        with open(image, "rb") as f:
            tg("sendPhoto", data={"chat_id": CHAT_ID, "caption": post.get("photo_caption", "📰 리플 데일리")},
               files={"photo": (stem + ".jpg", f, "image/jpeg")})
        tg("sendMessage", json={"chat_id": CHAT_ID, "text": post["message"]})

        state["sent"].append({"date": date, "headline": post.get("headline", ""),
                              "posted_at": datetime.now(KST).isoformat(timespec="seconds")})
        state["sent"] = state["sent"][-120:]
        with open(LOG, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        move_set(path, "sent")
        log(f"게시 완료: {post.get('headline', stem)}")
        return  # 하루 한 세트


if __name__ == "__main__":
    main()
