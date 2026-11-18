# 리플 데일리 제작 가이드 (Claude 예약 작업용)

매일 오전 11시, **사진 1장 + 카톡에 붙일 문구** 한 세트를 만든다.
이 세트는 그대로 카톡으로 고객(주로 50대 이상)에게 보내진다.
Claude가 하는 일은 **자료 수집 → content.json + 문구 작성 → 사진 렌더링·확인 → 전달**이다.
전달 방법은 예약 작업 지시를 따른다 (현재: Henry에게 직접 + 구글 드라이브 보관).
7번(outbox)은 텔레그램 업무 정보방 자동 게시를 켤 때만 쓴다. Claude는 텔레그램에 직접 보내지 않는다.

---

## 1. 시세 (숫자는 전부 오늘 받은 값만)

- 업비트: `https://api.upbit.com/v1/ticker?markets=KRW-XRP,KRW-USDT` 를 WebFetch
  - `price.krw` = KRW-XRP `trade_price`
  - `price.change_pct` = KRW-XRP `signed_change_rate` × 100 (소수 1자리)
  - `price.high_krw` / `price.low_krw` = KRW-XRP `high_price` / `low_price`
  - 원화 환산 기준 = KRW-USDT `trade_price` (예: 1,355원 = 1달러)
- 달러 가격: Crypto.com `get_ticker` (`XRP_USDT`)의 `last`
- 7일 범위가 필요할 때: `https://api.upbit.com/v1/candles/days?market=KRW-XRP&count=7`
- `asof` = 업비트 시세를 받은 한국시간 (예: "11/19 오전 10시 42분 기준")

## 2. 뉴스 고르기

- 최근 24~48시간 리플(XRP)·리플사 뉴스. WebSearch(standard)를 한국어·영어로 여러 번:
  "XRP news today", "Ripple XRP", "리플 XRP 뉴스", "XRP ETF flows" 등. 부족하면 extended.
- 숫자가 들어가는 기사는 반드시 WebFetch로 본문을 열어 숫자를 확인한다. 확인 못 한 숫자는 쓰지 않는다.
- 믿을 만한 출처 우선: CoinDesk, The Block, Decrypt, Cointelegraph, CoinMarketCap, Bloomberg, Reuters,
  블록미디어, 코인데스크코리아, 토큰포스트, 디지털애셋 등. AI 가격 예측, 출처 없는 X 게시물, 광고성 글은 제외.
- 최근 3일 세트(구글 드라이브 '리플 데일리' 폴더의 문구, 또는 `ripple_daily/sent/`)를 읽어서, 같은 사건을 또 큰 소식으로 쓰지 않는다
  (진전이 있으면 "후속"으로 다룰 수 있음).
- 고르는 기준: 50대 이상 투자자가 "그래서 내 리플은?" 하고 궁금해할 소식 — 가격 큰 움직임, ETF·기관 자금,
  규제·소송, 리플사 큰 발표, 큰손 움직임, 다가오는 일정.

## 3. 쉬운 말 규칙 (가장 중요)

- 독자는 50대 이상. 휴대폰으로 한 번 훑어봐도 이해돼야 한다.
- 용어 바꾸기: ETF → "주식처럼 사고파는 리플 투자상품(ETF)" 또는 "리플 투자상품", 고래 → "큰손",
  에스크로 → "리플사가 묶어둔 물량", 테스트넷 → "시험용 네트워크", 지지선 → "버팀목 가격".
- 쓰지 않는 것: RSI·MACD·펀딩비·AMM 같은 기술 용어, 영어 약어 남발.
- 사라/팔라는 말, "오를 것/내릴 것" 단정 금지. 분석가 수치는 "분석가들이 꼽은"처럼 출처를 밝힌다.
- 강조(`**굵게**`)는 필드마다 1~2개, 숫자 위주.

## 4. content.json 작성

`ripple_daily/example_content.json`과 같은 구조로 쓴다. 길이 기준(넘치면 렌더러가 알려줌):

| 필드 | 기준 |
|---|---|
| `date_label` / `weekday` | "11월 19일" / "목요일" |
| `top.headline_lines` | 2줄, 줄마다 한글 15자 안팎 |
| `top.body` | 2문장, 90자 안팎 |
| `items[3].title` | 22자 안팎 (한 줄) |
| `items[3].sub` | 30자 안팎 (한 줄) |
| `schedule[2]` | date "12월 1일", title 16자 안팎, sub 16자 안팎 |
| `levels.cells[3]` | 아래 참고 |
| `sources` | 실제로 숫자를 가져온 곳 이름만 (업비트 포함) |

**levels (알아두면 좋은 가격)** — 원화 크게, 달러 작게
- 최근 7일 안의 기사에서 분석가가 꼽은 가격(버팀목·넘어야 할 선)이 있으면: 달러 × 업비트 USDT 가격 → 10원 단위 반올림.
  `main: true`는 지금 가격 바로 위의 중요한 선(예: 1달러) 하나에만.
  `note_lines`: ["버팀목 = 분석가들이 꼽은, 가격이 버텨줄 만한 자리", "원화는 업비트 달러(테더) 시세 N원으로 바꾼 값입니다"]
- 그런 수치가 없으면 지어내지 말고 `title`을 "최근 7일 가격 범위"로 바꿔
  cells = [7일 최고, 지금(main), 7일 최저] (업비트 일봉), usd는 비워두고 note_lines = ["업비트 일봉 기준"].

**schedule (꼭 기억하세요)** — 앞으로 3주 안의 날짜가 확실한 일정 2개
- 매달 1일 리플 에스크로 해제 가능일(10억 XRP)은 늘 쓸 수 있는 정기 일정: "리플 10억 개가 풀릴 수 있는 날 / 매달 1일 정기 일정".
- 그 밖에: 미국 금리 결정(FOMC)·물가 발표(CPI) 날짜(공식 일정 확인), ETF 심사 기한, 리플 관련 큰 행사·업그레이드 등.
- 지난 일정은 쓰지 않는다. 가까운 날짜를 왼쪽에.

## 5. 렌더링과 확인

```
python3 ripple_daily/render_daily.py content.json ripple_daily/assets/coinnews_logo.png /tmp/rd/YYYYMMDD.jpg
```
- 끝에 `OK`가 나와야 한다. `OVERFLOW: …`(종료코드 3)이면 그 부분 글을 줄여서 다시 실행.
- `/tmp/rd/YYYYMMDD.jpg`를 Read로 직접 열어 본다: 글자 겹침·잘림, 어색한 줄바꿈, 숫자 오타, 사진과 기사 내용 일치.

## 6. 카톡 문구 (message)

사진과 함께 카톡으로 그대로 붙여 보내는 글. 운영자(BTC워크맨)가 직접 쓴 것처럼.
- 첫 줄: "어머니 아버지, 좋은 아침입니다 😊" 처럼 호칭 + 인사 (매일 같은 문장 반복 금지 — 최근 3일 문구와 다르게).
- 둘째 줄: 가벼운 일상 한마디(요일·계절 이야기). 날씨는 확인했을 때만 구체적으로.
- 본문 2~3줄: 오늘 사진의 핵심을 원화 숫자 중심으로 쉽게. 사진에 없는 새 숫자는 넣지 않는다.
- 주의 한 줄: 급하게 사고팔기·빌린 돈 투자 조심 등. 사라/팔라 금지.
- 마무리 인사 한 줄.
- 6~10줄, 350자 안팎, 이모지 2~3개. HTML·마크다운 쓰지 않음(그대로 텍스트로 나감).

## 7. outbox에 넣기 (업무 정보방 자동 게시를 켰을 때만)

- `ripple_daily/outbox/YYYYMMDD.jpg` (렌더링한 사진)
- `ripple_daily/outbox/YYYYMMDD.json`:
```json
{
  "date": "2026-11-19",
  "headline": "큰 소식 제목 한 줄",
  "photo_caption": "📰 리플 데일리 · 11월 19일(목)\n카톡 발송용 사진이에요. 아래 문구와 함께 보내주세요.",
  "message": "카톡 문구 전체",
  "sources": ["기사 URL", "..."],
  "written_at": "2026-11-19T10:48:00+09:00",
  "dry_run": false
}
```
- 커밋 메시지: `ripple daily: YYYY-MM-DD`. push하면 Actions가 11시에 업무 정보방에 올린다.
