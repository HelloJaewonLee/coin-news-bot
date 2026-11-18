"""리플 데일리 — content.json -> 카톡용 한 장(1080x1920, 출력 1620x2880 JPG).

Usage:
  python3 render_daily.py <content.json> <logo_png> <out_jpg>

Exit codes: 0 = OK, 3 = 내용이 넘침(OVERFLOW) -> 글을 줄여서 다시 실행, 2 = 입력 오류.

Text fields accept **굵게** markup: in top.body it becomes red bold, in items[].title it becomes red.
Everything else is HTML-escaped.
"""
import base64, html, io, json, re, sys
from PIL import Image
from playwright.sync_api import sync_playwright

CSS = r"""
  * { box-sizing:border-box; margin:0; padding:0 }
  body { width:1080px; font-family:"Noto Sans CJK KR","Noto Sans KR",sans-serif; color:#111; word-break:keep-all; overflow-wrap:break-word; }
  .serif { font-family:"Noto Serif CJK KR","Noto Serif KR",serif; }
  .paper { width:1080px; height:1920px; padding:40px 50px 34px; display:flex; flex-direction:column;
    background: radial-gradient(ellipse at 20% 6%, rgba(255,255,255,.65), transparent 55%), #f5efe1; }
  .mast { display:flex; align-items:center; gap:20px; padding-bottom:14px; border-bottom:7px double #111; }
  .mast img { width:150px; filter:drop-shadow(0 3px 5px rgba(0,0,0,.25)); }
  .mast h1 { font-size:84px; line-height:1; font-weight:700; letter-spacing:-2px; }
  .mast h1 b { color:#b3121b; }
  .mast .d { margin-left:auto; text-align:right; font-size:27px; font-weight:900; line-height:1.35; color:#3a342a; }
  .price { margin-top:20px; background:#0f1a33; color:#fff; border-radius:12px; padding:20px 30px 20px; display:flex; align-items:center; gap:26px; }
  .price .lab { font-size:26px; font-weight:700; color:#c9d4ea; }
  .price .won { font-size:112px; font-weight:900; letter-spacing:-2px; line-height:1.05; }
  .price .won small { font-size:50px; margin-left:4px; }
  .price .right { margin-left:auto; text-align:right; }
  .price .chg { font-size:46px; font-weight:900; line-height:1.1; }
  .price .chg.down { color:#4da3ff; } .price .chg.up { color:#ff5a5f; } .price .chg.flat { color:#dbe3f2; }
  .price .sub { font-size:25px; color:#dbe3f2; line-height:1.45; margin-top:6px; }
  .price .sub b { color:#fff; }
  .top { margin-top:22px; }
  .tag { display:inline-block; font-size:24px; font-weight:900; color:#fff; background:#b3121b; padding:4px 14px; }
  .top h2 { font-size:62px; line-height:1.24; font-weight:700; letter-spacing:-1.5px; margin:12px 0 12px; }
  .top p { font-size:35px; line-height:1.6; }
  .top p strong { color:#b3121b; font-weight:900; }
  .sec { margin-top:24px; border-top:3px solid #111; padding-top:12px; }
  .sec h4 { font-size:34px; font-weight:900; margin-bottom:4px; }
  .n { display:flex; gap:18px; padding:16px 0; border-bottom:1px dashed #9d947f; }
  .n:last-child { border-bottom:none; }
  .n .ic { flex:none; width:54px; height:54px; border-radius:50%; background:#111; color:#fff; font-size:28px; font-weight:900; display:flex; align-items:center; justify-content:center; margin-top:2px; }
  .n .t { font-size:38px; font-weight:900; line-height:1.35; letter-spacing:-.5px; }
  .n .t em { font-style:normal; color:#b3121b; }
  .n .e { font-size:31px; line-height:1.45; color:#4a4337; margin-top:2px; }
  .sched { margin-top:20px; display:flex; gap:16px; }
  .sched div { flex:1; background:#fff6d6; border-left:10px solid #e2a400; padding:14px 18px; }
  .sched b { display:block; font-size:36px; font-weight:900; color:#b3121b; }
  .sched span { font-size:30px; line-height:1.4; font-weight:700; }
  .sched small { display:block; font-size:26px; font-weight:400; color:#4a4337; margin-top:2px; }
  .lvl { margin-top:24px; border:3px solid #111; background:rgba(255,255,255,.5); padding:16px 20px 14px; }
  .lvh { font-size:32px; font-weight:900; margin-bottom:12px; }
  .cells { display:flex; gap:12px; }
  .cell { flex:1; text-align:center; background:#fff; border:2px solid #d8cfbb; border-radius:8px; padding:12px 6px; }
  .cell span { display:block; font-size:25px; font-weight:700; color:#4a4337; }
  .cell b { display:block; font-size:42px; font-weight:900; margin-top:4px; letter-spacing:-1px; }
  .cell.main { background:#0f1a33; border-color:#0f1a33; }
  .cell.main span { color:#c9d4ea; } .cell.main b { color:#fff; }
  .cell i { display:block; font-style:normal; font-size:22px; color:#6b6252; margin-top:2px; }
  .cell.main i { color:#9fb0cf; }
  .note { font-size:23px; line-height:1.45; color:#4a4337; margin-top:10px; }
  .foot { margin-top:auto; padding-top:10px; border-top:2px solid #111; font-size:21px; line-height:1.5; color:#4a4337; }
"""

def esc(s): return html.escape(str(s), quote=False)
def mark(s, tag):  # **x** -> <tag>x</tag>, rest escaped
    parts = re.split(r"\*\*(.+?)\*\*", str(s))
    return "".join(esc(p) if i % 2 == 0 else f"<{tag}>{esc(p)}</{tag}>" for i, p in enumerate(parts))
def won(n): return f"{int(round(float(n))):,}"

def build(c, logo_src):
    p = c["price"]
    chg = float(p["change_pct"])
    cls, arrow = ("up", "▲") if chg > 0.05 else (("down", "▼") if chg < -0.05 else ("flat", "–"))
    top = c["top"]
    h2 = "<br>".join(esc(x) for x in top["headline_lines"])
    items = "".join(
        f'<div class="n"><div class="ic">{i}</div><div><div class="t">{mark(it["title"], "em")}</div>'
        f'<div class="e">{esc(it["sub"])}</div></div></div>' for i, it in enumerate(c["items"], 1))
    sched = "".join(f'<div><b>{esc(s["date"])}</b><span>{esc(s["title"])}</span><small>{esc(s["sub"])}</small></div>'
                    for s in c["schedule"])
    lv = c["levels"]
    cells = "".join(
        f'<div class="cell{" main" if x.get("main") else ""}"><span>{esc(x["label"])}</span>'
        f'<b>{won(x["krw"])}원</b>' + (f'<i>{esc(x["usd"])}</i>' if x.get("usd") else "") + '</div>'
        for x in lv["cells"])
    note = "<br>".join(esc(x) for x in lv.get("note_lines", []))
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="paper">
  <div class="mast"><img src="{logo_src}" alt=""><h1 class="serif">리플 <b>데일리</b></h1>
    <div class="d">{esc(c["date_label"])}<br>{esc(c["weekday"])}</div></div>
  <div class="price"><div><div class="lab">리플 지금 가격 · 업비트</div><div class="won">{won(p["krw"])}<small>원</small></div></div>
    <div class="right"><div class="chg {cls}">{arrow} {abs(chg):.1f}%</div>
      <div class="sub">어제보다<br>최고 <b>{won(p["high_krw"])}원</b> · 최저 <b>{won(p["low_krw"])}원</b></div></div></div>
  <div class="top"><span class="tag">{esc(top.get("tag", "오늘의 큰 소식"))}</span>
    <h2 class="serif">{h2}</h2><p>{mark(top["body"], "strong")}</p></div>
  <div class="sec"><h4>📰 한눈에 보는 소식</h4>{items}</div>
  <div class="sched">{sched}</div>
  <div class="lvl"><div class="lvh">📌 {esc(lv.get("title", "알아두면 좋은 가격"))}</div><div class="cells">{cells}</div>
    {f'<div class="note">{note}</div>' if note else ''}</div>
  <div class="foot">{esc(c["asof"])} · 출처: {esc(", ".join(c["sources"]))} · 투자 권유가 아닙니다</div>
</div></body></html>"""

def main():
    if len(sys.argv) != 4:
        print(__doc__); sys.exit(2)
    c = json.load(open(sys.argv[1], encoding="utf-8"))
    for k in ["date_label", "weekday", "price", "top", "items", "schedule", "levels", "asof", "sources"]:
        if k not in c: print(f"입력 오류: '{k}' 없음"); sys.exit(2)
    if len(c["items"]) != 3 or len(c["schedule"]) != 2 or len(c["levels"]["cells"]) != 3 or len(c["top"]["headline_lines"]) > 2:
        print("입력 오류: items 3개, schedule 2개, levels.cells 3개, headline_lines 최대 2줄이어야 함"); sys.exit(2)
    logo = Image.open(sys.argv[2]); logo.thumbnail((400, 400), Image.LANCZOS)
    buf = io.BytesIO(); logo.save(buf, "PNG", optimize=True)
    src = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    page = build(c, src)
    out = sys.argv[3]
    tmp_html = out.rsplit(".", 1)[0] + ".html"
    open(tmp_html, "w", encoding="utf-8").write(page)
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1.5)
        pg.goto("file://" + __import__("os").path.abspath(tmp_html)); pg.wait_for_timeout(700)
        m = pg.evaluate("""(()=>{const p=document.querySelector('.paper');const f=document.querySelector('.foot');
          const lv=document.querySelector('.lvl');const h2=document.querySelector('.top h2');
          const lh=parseFloat(getComputedStyle(h2).lineHeight);
          const ts=[...document.querySelectorAll('.n .t')].map(e=>Math.round(e.getBoundingClientRect().height/parseFloat(getComputedStyle(e).lineHeight)));
          const sch=[...document.querySelectorAll('.sched span')].map(e=>Math.round(e.getBoundingClientRect().height/parseFloat(getComputedStyle(e).lineHeight)));
          return {gap: f.getBoundingClientRect().top - lv.getBoundingClientRect().bottom,
                  h2lines: Math.round(h2.getBoundingClientRect().height/lh), itemLines: ts, schedLines: sch,
                  overflow: p.scrollHeight > p.clientHeight, over: p.scrollHeight - p.clientHeight}})()""")
        pg.locator(".paper").screenshot(path=out + ".png")
        b.close()
    Image.open(out + ".png").convert("RGB").save(out, quality=92, optimize=True)
    problems = []
    if m["overflow"] or m["gap"] < 0: problems.append(f"전체 높이 초과 약 {max(m['over'], -m['gap'], 1):.0f}px — 본문·소식 설명을 줄이세요")
    if m["h2lines"] > 2: problems.append("큰 제목이 2줄을 넘음 — headline_lines를 더 짧게")
    for i, n in enumerate(m["itemLines"], 1):
        if n > 2: problems.append(f"소식 {i} 제목이 {n}줄 — 더 짧게")
    for i, n in enumerate(m["schedLines"], 1):
        if n > 2: problems.append(f"일정 {i} 설명이 {n}줄 — 더 짧게")
    print(json.dumps({"metrics": m, "out": out}, ensure_ascii=False))
    if problems:
        print("OVERFLOW: " + " / ".join(problems)); sys.exit(3)
    print("OK — 여백 %dpx" % m["gap"])

if __name__ == "__main__":
    main()
