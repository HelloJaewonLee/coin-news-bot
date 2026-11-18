# 리플 데일리 (텔레그램 업무 정보방 자동 게시)

매일 오전 11시, 업무 정보방에 **리플 뉴스 사진 1장 + 카톡에 붙여 보낼 문구**가 올라옵니다.

## 흐름
1. 오전 10시 40분대 — Claude 예약 작업이 리플 뉴스·업비트 시세를 모아 사진과 문구를 만들고
   `ripple_daily/outbox/`에 넣습니다. (제작 기준: `GUIDE.md`)
2. 오전 11시 — GitHub Actions(`Post Ripple Daily to Work Room`)가 업무 정보방에 사진 → 문구 순서로 올립니다.
3. 올린 세트는 `ripple_daily/sent/`에 보관됩니다.

## 처음 한 번만 설정
1. 엄선 뉴스에 쓰는 텔레그램 봇을 **업무 정보방에 초대**합니다 (사진·메시지 보내기 권한 필요).
2. 업무 정보방 ID를 GitHub 비밀값으로 등록합니다.
   저장소 **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `TELEGRAM_WORK_CHAT_ID`
   - Secret: 업무 정보방 ID (예: `-1001234567890`)
   - 방 ID 확인: 웹 텔레그램(web.telegram.org/a/)에서 방을 열면 주소 끝 `#-100…` 숫자가 방 ID입니다.

## 파일
- `render_daily.py` — content.json → 카톡용 사진(1080×1920 비율)
- `assets/coinnews_logo.png` — 오늘의 코인뉴스 로고
- `make_logo.py` — 로고를 다시 그릴 때만 사용 (`pip install global-land-mask`)
- `post_ripple_daily.py` — 업무 정보방 게시 (Actions에서 실행)
- `example_content.json` — 내용 예시
