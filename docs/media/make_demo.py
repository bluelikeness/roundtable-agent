"""README 시연 GIF(모션그래픽) 만들기: docs/media/roundtable-demo.gif

2026-11-18 실제로 돌린 두 흐름을 바탕으로 다시 그린 것이다. 화면 녹화가 아니다.
  1. 웹에서 네 봇에게 동시에 자기소개를 시키고 Claude 가 정리
  2. 휴대폰의 다른 비서(피클)가 허브의 방을 골라 말을 걸고(POST /api/inbox) 답을 받아 감
개인정보는 넣지 않는다: 비서 별명·사용자 이름·호칭·계정 아이디·세션 목록은 빼고, 봇은 앱 이름으로만 부른다.
답 내용도 개인 일과가 드러나는 부분은 일반적인 말로 바꿨다.

    cd adapters && uv run --group android python ../docs/media/make_demo.py
(Pillow 와 한글 글꼴(맑은 고딕 또는 NANUM_FONT 경로)이 필요하다)
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).with_name("roundtable-demo.gif")
W, H = 1000, 620
FPS = 12
SIDE = 220  # 오른쪽 봇 상태 패널 폭
CHAT_X0, CHAT_X1 = 40, W - SIDE - 30
TOP = 56  # 머리줄 아래
INPUT_H = 74  # 아래 입력줄

# 웹 색(web/src/styles.css, lib/colors.ts)
BG, SOFT, FG, MUTED, LINE = "#ffffff", "#f6f7f9", "#1d2129", "#6b7280", "#e3e5e8"
ACCENT, ACCENT_SOFT, OK = "#3b5bdb", "#eef2ff", "#2e9d6b"
BOTS = {"vooy": "#9b59b6", "pickle": "#c0392b", "muse": "#16a2b8", "cue": "#8a6d3b"}

FONT_DIR = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = os.environ.get("NANUM_FONT") or str(FONT_DIR / ("malgunbd.ttf" if bold else "malgun.ttf"))
    return ImageFont.truetype(path, size)


F = font(15)
F_SM = font(13)
F_HEAD = font(12, True)
F_B = font(15, True)
F_TITLE = font(34, True)
F_SUB = font(18)

PROMPT = "다들 한 자리에 모였네요. 각자 두세 문장으로 간단히 자기소개 해 주세요."
NOTES = [
    "Claude · 전원 자기소개 요청이라 네 봇 모두에게 동시에 묻습니다. 먼저 상태를 확인합니다.",
    "Claude · 넷 다 연결돼 있습니다. 같은 요청을 동시에 보냅니다.",
]
# (봇, 걸린 시간, 답) — 실제 답을 바탕으로 별명·호칭·개인 일과를 뺐다
ANSWERS = [
    ("vooy", 37, "안녕하세요, 저는 Vooy예요. 맛집 예약, 배달 주문, 숙소와 항공권 검색, 길찾기, 날씨 확인처럼 "
                 "여러 생활 서비스를 한 대화에서 묶어 처리하는 걸 잘해요. 예약이나 결제처럼 되돌리기 어려운 일은 "
                 "하기 전에 먼저 보여 드려요."),
    ("cue", 58, "저는 Cue예요. 필요한 정보를 찾아 정리하고, 일정·예약·메일처럼 손이 많이 가는 일을 챙기는 데 "
                "강해요. 잔질문은 줄이고, 중요한 선택만 확인받는 게 제 특징이에요."),
    ("muse", 78, "안녕하세요, 저는 뮤즈예요. 매일 아침 소식 브리핑을 전해 드리고, 블로그 글을 쓰고 다듬는 걸 "
                 "제일 잘해요. 시장 소식 정리부터 사소한 궁금증까지 편하게 물어봐 주세요."),
    ("pickle", 120, "안녕하세요, 저는 피클이에요! 웹 자동화와 정보 조사·정리, 문서와 콘텐츠 제작까지 생활과 일에 "
                    "필요한 실무를 두루 챙겨요. 직접 확인하고 검증해서 끝까지 책임지는 꼼꼼함이 제 특징입니다."),
]
SUMMARY_HEAD = "네 봇 모두 자기소개를 마쳤습니다. 잘하는 일이 겹치지 않아 나눠 맡기기 좋습니다."
SUMMARY_ROWS = [
    ("봇", "잘하는 일", "특징"),
    ("vooy", "맛집·배달·숙소·길찾기·날씨", "되돌리기 어려운 일은 먼저 확인"),
    ("pickle", "웹 자동화·조사·문서 작성", "직접 검증하고 끝까지 책임"),
    ("muse", "아침 브리핑·블로그 글", "편하게 묻고 답하기"),
    ("cue", "조사·일정·예약·메일", "중요한 선택만 확인"),
]
SUMMARY_TAIL = "다음: 예약은 vooy, 자료 정리는 pickle 처럼 나눠 맡겨 보세요."


# --- 그리기 도구 ------------------------------------------------------------

def wrap(text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        cand = f"{cur} {word}".strip()
        if f.getlength(cand) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:  # 한 단어가 줄보다 길면 글자 단위로
            if f.getlength(cur + ch) > width:
                lines.append(cur)
                cur = ""
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def rgba(hex_: str, a: float = 1.0) -> tuple[int, int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(255 * a)


def tint(hex_: str, k: float) -> str:
    """색을 흰색 쪽으로 k 만큼(말풍선 배경)."""
    r, g, b, _ = rgba(hex_)
    return "#%02x%02x%02x" % tuple(int(c + (255 - c) * k) for c in (r, g, b))


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


class Block:
    """대화창에 쌓이는 한 덩어리. 미리 그려 둔 그림과 오른쪽 정렬 여부."""

    def __init__(self, img: Image.Image, right: bool = False, gap: int = 12):
        self.img, self.right, self.gap = img, right, gap

    @property
    def h(self) -> int:
        return self.img.height + self.gap


def bubble(lines: list[str], f, *, head: tuple[str, str, str] | None = None, border: str, fill: str = BG,
           dashed: bool = False, width: int | None = None, pad=(13, 9)) -> Image.Image:
    lh = int(f.size * 1.55)
    head_h = 20 if head else 0
    tw = max([f.getlength(s) for s in lines] + ([F_HEAD.getlength(head[0]) + F_SM.getlength(head[1]) + 8] if head else [0]))
    w = width or int(tw + pad[0] * 2)
    h = int(pad[1] * 2 + head_h + lh * len(lines))
    img = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((1, 1, w, h), 12, fill=fill, outline=None)
    if dashed:
        # 점선 테두리: 둘레를 따라 짧은 선
        _dash_round_rect(d, (1, 1, w, h), 12, border)
    else:
        d.rounded_rectangle((1, 1, w, h), 12, outline=border, width=2)
    y = pad[1] + 1
    if head:
        d.text((pad[0] + 1, y), head[0], font=F_HEAD, fill=head[2])
        d.text((pad[0] + 1 + F_HEAD.getlength(head[0]) + 4, y + 1), head[1], font=F_SM, fill=MUTED)
        y += head_h
    for s in lines:
        d.text((pad[0] + 1, y), s, font=f, fill=FG)
        y += lh
    return img


def _dash_round_rect(d: ImageDraw.ImageDraw, box, r, color, dash=6, gap=5):
    x0, y0, x1, y1 = box
    segs = [((x0 + r, y0), (x1 - r, y0)), ((x1, y0 + r), (x1, y1 - r)), ((x1 - r, y1), (x0 + r, y1)), ((x0, y1 - r), (x0, y0 + r))]
    for (ax, ay), (bx, by) in segs:
        length = math.hypot(bx - ax, by - ay)
        n = int(length // (dash + gap)) + 1
        for i in range(n):
            s = i * (dash + gap) / length
            e = min(1.0, (i * (dash + gap) + dash) / length)
            d.line((ax + (bx - ax) * s, ay + (by - ay) * s, ax + (bx - ax) * e, ay + (by - ay) * e), fill=color, width=2)
    for cx, cy, a0 in ((x0 + r, y0 + r, 180), (x1 - r, y0 + r, 270), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90)):
        d.arc((cx - r, cy - r, cx + r, cy + r), a0, a0 + 90, fill=color, width=2)


def note(text: str) -> Image.Image:
    lines = wrap(text, F_SM, CHAT_X1 - CHAT_X0 - 20)
    img = Image.new("RGBA", (CHAT_X1 - CHAT_X0, 20 * len(lines) + 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, s in enumerate(lines):
        d.text((4, 2 + 20 * i), s, font=F_SM, fill=MUTED)
    return img


def summary() -> Image.Image:
    w = CHAT_X1 - CHAT_X0
    cols = [90, 250, w - 90 - 250 - 30]
    rows_h = 30
    head_lines = wrap(SUMMARY_HEAD, F, w - 30)
    tail_lines = wrap(SUMMARY_TAIL, F_SM, w - 30)
    h = 14 + 22 + 24 * len(head_lines) + 8 + rows_h * len(SUMMARY_ROWS) + 10 + 20 * len(tail_lines) + 12
    img = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((1, 1, w, h), 12, fill=ACCENT_SOFT, outline=ACCENT, width=2)
    y = 12
    d.text((15, y), "Claude 정리", font=F_HEAD, fill=FG)
    y += 22
    for s in head_lines:
        d.text((15, y), s, font=F, fill=FG)
        y += 24
    y += 6
    x0 = 15
    for i, row in enumerate(SUMMARY_ROWS):
        d.rectangle((x0, y, x0 + sum(cols), y + rows_h), fill=BG if i else "#f1f3f5", outline=LINE)
        x = x0
        for j, (cell, cw) in enumerate(zip(row, cols)):
            if j:
                d.line((x, y, x, y + rows_h), fill=LINE)
            color = BOTS.get(cell, FG) if j == 0 and i else FG
            d.text((x + 10, y + 6), cell, font=F_B if (i == 0 or j == 0) else F_SM if False else F_SM, fill=color)
            x += cw
        y += rows_h
    y += 10
    for s in tail_lines:
        d.text((15, y), s, font=F_SM, fill=MUTED)
        y += 20
    return img


# --- 장면 -------------------------------------------------------------------

def chrome(base: Image.Image, typed: str, status: dict[str, str], sending: bool, caret: bool, done: bool = False) -> None:
    d = ImageDraw.Draw(base)
    # 머리줄
    d.rectangle((0, 0, W, TOP - 1), fill=BG)
    d.line((0, TOP - 1, W - SIDE, TOP - 1), fill=LINE)
    d.text((CHAT_X0, 18), "새 세션" if not typed and not sending else "자기소개", font=F_B, fill=FG)
    if sending:
        d.text((CHAT_X0 + F_B.getlength("자기소개") + 10, 20), "완료" if done else "실행 중", font=F_SM, fill=MUTED)
    d.rounded_rectangle((W - SIDE - 130, 14, W - SIDE - 30, 40), 6, outline=LINE, fill=BG)
    d.text((W - SIDE - 120, 18), "기본(Sonnet)", font=F_SM, fill=FG)
    # 오른쪽 봇 상태
    d.rectangle((W - SIDE, 0, W, H), fill=SOFT)
    d.line((W - SIDE, 0, W - SIDE, H), fill=LINE)
    d.text((W - SIDE + 16, 18), "봇 상태", font=F_B, fill=FG)
    y = 58
    for name, color in BOTS.items():
        st = status.get(name, "연결")
        dot = OK if st != "응답 중" else "#d4a017"
        d.ellipse((W - SIDE + 18, y + 6, W - SIDE + 26, y + 14), fill=dot)
        d.text((W - SIDE + 34, y), name, font=F_B, fill=color)
        d.text((W - SIDE + 34 + F_B.getlength(name) + 6, y + 2), st, font=F_SM, fill=FG)
        d.text((W - SIDE + 34, y + 20), "휴대폰 앱 · 같은 기기", font=F_SM, fill=MUTED)
        y += 50
    d.text((W - SIDE + 16, H - 62), "Claude Code", font=F_B, fill=FG)
    d.text((W - SIDE + 16, H - 40), "러너 연결 · 오케스트레이터", font=F_SM, fill=MUTED)
    # 입력줄
    d.rectangle((0, H - INPUT_H, W - SIDE - 1, H), fill=BG)
    box = (CHAT_X0 + 20, H - INPUT_H + 14, CHAT_X1 - 80, H - 14)
    d.rounded_rectangle(box, 8, outline=ACCENT if typed else LINE, width=2, fill=BG)
    if typed:
        shown = typed
        while F.getlength(shown) > box[2] - box[0] - 30:
            shown = shown[1:]
        d.text((box[0] + 12, box[1] + 13), shown + ("|" if caret else ""), font=F, fill=FG)
    else:
        d.text((box[0] + 12, box[1] + 14), "메시지 (Enter 전송, @봇, /명령)", font=F_SM, fill=MUTED)
    bx = (CHAT_X1 - 68, H - INPUT_H + 22, CHAT_X1 - 10, H - 22)
    d.rounded_rectangle(bx, 8, fill=ACCENT if typed else "#a5b4fc")
    d.text((bx[0] + 14, bx[1] + 6), "전송", font=F_SM, fill="#ffffff")


def title_card(t: float) -> Image.Image:
    img = Image.new("RGBA", (W, H), rgba(BG))
    d = ImageDraw.Draw(img)
    a = ease(t * 2)
    y_off = int((1 - a) * 20)
    title = "Roundtable-Agent"
    sub = "휴대폰의 AI 비서들을 한 테이블에 앉히고, Claude 가 회의를 진행합니다"
    d.text(((W - F_TITLE.getlength(title)) / 2, 220 + y_off), title, font=F_TITLE, fill=rgba(FG, a))
    d.text(((W - F_SUB.getlength(sub)) / 2, 278 + y_off), sub, font=F_SUB, fill=rgba(MUTED, a))
    # 원탁 위 네 자리
    cx, cy, r = W / 2, 400, 44
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=rgba(ACCENT, a), width=3)
    d.text((cx - F_HEAD.getlength("Claude") / 2, cy - 8), "Claude", font=F_HEAD, fill=rgba(ACCENT, a))
    for i, (name, color) in enumerate(BOTS.items()):
        k = ease(t * 2 - 0.3 - i * 0.12)
        ang = -math.pi / 2 + i * math.pi / 2 + math.pi / 4
        x, y = cx + math.cos(ang) * 120 * k, cy + math.sin(ang) * 70 * k
        d.rounded_rectangle((x - 38, y - 14, x + 38, y + 14), 14, fill=rgba(tint(color, 0.85), k), outline=rgba(color, k), width=2)
        d.text((x - F_HEAD.getlength(name) / 2, y - 8), name, font=F_HEAD, fill=rgba(color, k))
    return img


def caption_card(num: str, title: str, sub: str, t: float) -> Image.Image:
    img = Image.new("RGBA", (W, H), rgba(BG))
    d = ImageDraw.Draw(img)
    a = ease(t * 2.2) * (1 - ease((t - 0.8) * 5))
    d.text(((W - F_SUB.getlength(num)) / 2, 236), num, font=F_SUB, fill=rgba(ACCENT, a))
    d.text(((W - F_TITLE.getlength(title)) / 2, 268), title, font=F_TITLE, fill=rgba(FG, a))
    d.text(((W - F.getlength(sub)) / 2, 324), sub, font=F, fill=rgba(MUTED, a))
    return img


# --- 장면 2: 다른 비서(피클)가 허브의 방을 골라 말을 건다(POST /api/inbox) ---------------------
# 2026-11-18 실제로 /api/inbox 로 보내 202 → result_url 로 답을 받은 흐름. 방 이름은 예시로 바꿨다
S2_ASK = "원탁 '오늘 일정' 방에 '@cue 오늘 오후에 할 일 하나 추천해 줘' 라고 보내 줘"
S2_ACK = "원탁 '오늘 일정' 방에 보냈어요. 답이 오면 알려 드릴게요."
S2_IN = "(피클에서 보낸 의견) @cue 오늘 오후에 할 일 하나 추천해 줘"
S2_NOTE = "Claude · cue 를 지명한 요청이라 cue 에게 묻습니다."
S2_CUE = "오늘 오후에 20분쯤 바깥에서 걸어 보세요. 머리가 맑아져요."
S2_SUM = "cue 의 추천은 오늘 오후 20분 산책입니다."
S2_BACK = "원탁에서 답이 왔어요. cue 는 오늘 오후 20분쯤 산책을 추천했어요."
ROOMS = ["오늘 일정", "주말 여행", "자기소개"]
F_PH = font(14)
PH = (40, 70, 340, 590)  # 휴대폰
HUB = (400, 70, 970, 590)  # 허브 웹
ROOM_W = 150


def _paste(base: Image.Image, img: Image.Image, x: float, y: float, a: float) -> None:
    if a <= 0:
        return
    if a < 1:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda v, a=a: int(v * a)))
    base.alpha_composite(img, (int(x), int(y + (1 - a) * 12)))


def _packet(d: ImageDraw.ImageDraw, x0: float, x1: float, y: float, k: float, label: str, color: str) -> None:
    """두 화면 사이 화살표와, 그 위를 지나가는 요청 점."""
    d.line((x0, y, x1, y), fill=LINE, width=2)
    tip = x1 if x1 > x0 else x1
    s = 1 if x1 > x0 else -1
    d.polygon([(tip, y), (tip - 8 * s, y - 5), (tip - 8 * s, y + 5)], fill=LINE)
    if 0 < k < 1:
        x = x0 + (x1 - x0) * ease(k)
        d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color)
    if k > 0:
        a = min(1.0, k * 3)
        d.text(((x0 + x1) / 2 - F_HEAD.getlength(label) / 2, y - 22), label, font=F_HEAD, fill=rgba(color, a))


def scene2(t: float) -> Image.Image:
    base = Image.new("RGBA", (W, H), rgba(SOFT))
    d = ImageDraw.Draw(base)
    d.text((40, 24), "다른 비서가 방을 골라 말을 겁니다", font=F_B, fill=FG)
    d.text((40 + F_B.getlength("다른 비서가 방을 골라 말을 겁니다") + 12, 27), "휴대폰의 피클 → 허브의 '오늘 일정' 방", font=F_SM, fill=MUTED)

    # 휴대폰(피클 앱)
    x0, y0, x1, y1 = PH
    d.rounded_rectangle((x0, y0, x1, y1), 26, fill="#ffffff", outline="#c9ccd2", width=3)
    d.rounded_rectangle((x0 + 3, y0 + 3, x1 - 3, y0 + 56), 22, fill=tint(BOTS["pickle"], 0.88))
    d.rectangle((x0 + 3, y0 + 30, x1 - 3, y0 + 56), fill=tint(BOTS["pickle"], 0.88))
    d.text((x0 + 22, y0 + 18), "pickle", font=F_B, fill=BOTS["pickle"])
    d.text((x0 + 22 + F_B.getlength("pickle") + 8, y0 + 21), "휴대폰 앱", font=F_SM, fill=MUTED)
    pw = x1 - x0 - 40
    y = y0 + 72
    if t >= 0.2:
        img = bubble(wrap(S2_ASK, F_PH, pw - 50), F_PH, border=ACCENT, fill=ACCENT_SOFT)
        _paste(base, img, x1 - 16 - img.width, y, ease((t - 0.2) / 0.4))
        y += img.height + 10
    if t >= 1.6:
        img = bubble(wrap(S2_ACK, F_PH, pw - 50), F_PH, border=BOTS["pickle"], fill=tint(BOTS["pickle"], 0.93))
        _paste(base, img, x0 + 16, y, ease((t - 1.6) / 0.4))
        y += img.height + 10
    if t >= 9.6:
        img = bubble(wrap(S2_BACK, F_PH, pw - 50), F_PH, border=BOTS["pickle"], fill=tint(BOTS["pickle"], 0.93))
        _paste(base, img, x0 + 16, y, ease((t - 9.6) / 0.4))

    # 허브 웹
    hx0, hy0, hx1, hy1 = HUB
    d.rounded_rectangle((hx0, hy0, hx1, hy1), 12, fill=BG, outline=LINE, width=2)
    d.rectangle((hx0 + 2, hy0 + 2, hx0 + ROOM_W, hy1 - 2), fill=SOFT)
    d.line((hx0 + ROOM_W, hy0, hx0 + ROOM_W, hy1), fill=LINE)
    d.text((hx0 + 14, hy0 + 14), "Roundtable", font=F_B, fill=FG)
    pick = ease((t - 3.0) / 0.5)
    for i, r in enumerate(ROOMS):
        ry = hy0 + 52 + i * 34
        if i == 0 and pick > 0:
            d.rounded_rectangle((hx0 + 8, ry - 4, hx0 + ROOM_W - 8, ry + 24), 6, fill=rgba(ACCENT_SOFT, 1), outline=rgba(ACCENT, pick))
        d.text((hx0 + 16, ry), r, font=F_SM, fill=FG if i == 0 and pick > 0 else MUTED)
    cx0, cx1 = hx0 + ROOM_W + 16, hx1 - 16
    d.text((cx0, hy0 + 14), "오늘 일정" if pick > 0 else "", font=F_B, fill=FG)
    cw = cx1 - cx0
    y = hy0 + 50
    if t >= 3.4:
        img = bubble(wrap(S2_IN, F_PH, cw - 60), F_PH, border=ACCENT)
        _paste(base, img, cx1 - img.width, y, ease((t - 3.4) / 0.4))
        y += img.height + 8
    if t >= 4.0:
        img = note(S2_NOTE)
        _paste(base, img.crop((0, 0, min(img.width, cw), img.height)), cx0, y, ease((t - 4.0) / 0.4))
        y += 30
    if 4.5 <= t < 6.6:
        secs = int((t - 4.5) / 2.1 * 21)
        img = bubble(["응답 중" + "." * (1 + int(t * 3) % 3)], F_PH, head=("cue", f"· {secs}초", BOTS["cue"]), border=BOTS["cue"], dashed=True, width=180)
        _paste(base, img, cx0, y, ease((t - 4.5) / 0.4))
        y += img.height + 8
    elif t >= 6.6:
        img = bubble(wrap(S2_CUE, F_PH, cw - 60), F_PH, head=("cue", "· 21초", BOTS["cue"]), border=BOTS["cue"], fill=tint(BOTS["cue"], 0.93))
        _paste(base, img, cx0, y, ease((t - 6.6) / 0.4))
        y += img.height + 8
    if t >= 7.6:
        lines = wrap(S2_SUM, F_PH, cw - 40)
        img = bubble(lines, F_PH, head=("Claude 정리", "", FG), border=ACCENT, fill=ACCENT_SOFT, width=cw)
        _paste(base, img, cx0, y, ease((t - 7.6) / 0.5))

    # 두 화면 사이: 요청이 가고, 답이 돌아온다
    d = ImageDraw.Draw(base)
    _packet(d, x1 + 8, hx0 - 8, 250, (t - 2.0) / 1.0, "POST /api/inbox", BOTS["pickle"])
    _packet(d, hx0 - 8, x1 + 8, 330, (t - 8.4) / 1.0, "202 → 답 받아 감", ACCENT)
    return base


def render() -> list[tuple[Image.Image, int]]:
    frames: list[tuple[Image.Image, int]] = []
    ms = int(1000 / FPS)

    def add(img: Image.Image, n: int = 1):
        frames.append((img.convert("RGB"), ms * n))

    # 0. 제목
    for i in range(int(FPS * 2.4)):
        add(title_card(i / (FPS * 1.6)))
    for i in range(int(FPS * 1.6)):
        add(caption_card("1", "한 번에 묻기", "질문 하나를 네 비서에게 동시에, Claude 가 정리", i / (FPS * 1.6) * 1.2))

    # 미리 그릴 덩어리
    maxw = int((CHAT_X1 - CHAT_X0) * 0.82)
    user = Block(bubble(wrap(PROMPT, F, maxw - 30), F, border=ACCENT), right=True, gap=14)
    notes = [Block(note(n), gap=8) for n in NOTES]
    pend = {b: Block(bubble(["응답 중..."], F, head=(b, "", BOTS[b]), border=BOTS[b], dashed=True, width=190)) for b, _, _ in ANSWERS}
    ans = {b: Block(bubble(wrap(txt, F, maxw - 30), F, head=(b, f"· {sec}초", BOTS[b]), border=BOTS[b],
                           fill=tint(BOTS[b], 0.93), width=maxw)) for b, sec, txt in ANSWERS}
    final = Block(summary(), gap=10)

    # 시간표(초): 실제 걸린 시간을 줄여서 보여 준다
    T_TYPE0, T_TYPE1, T_SEND = 0.0, 2.6, 3.0
    T_NOTE = [3.5, 4.4]
    T_PEND = 5.0
    T_ANS = {"vooy": 6.6, "cue": 8.2, "muse": 9.8, "pickle": 11.4}
    T_FINAL = 13.0
    T_END = 18.5
    real = {b: s for b, s, _ in ANSWERS}

    scroll = 0.0
    n = int(T_END * FPS)
    for i in range(n):
        t = i / FPS
        typed = PROMPT[: int(len(PROMPT) * ease((t - T_TYPE0) / (T_TYPE1 - T_TYPE0)))] if t < T_SEND else ""
        sending = t >= T_SEND
        # 쌓을 덩어리와 나타나는 정도
        items: list[tuple[Block, float]] = []
        if t >= T_SEND:
            items.append((user, ease((t - T_SEND) / 0.35)))
        for k, tn in enumerate(T_NOTE):
            if t >= tn:
                items.append((notes[k], ease((t - tn) / 0.4)))
        status = {}
        for b, _, _ in ANSWERS:
            if t >= T_ANS[b]:
                items.append((ans[b], ease((t - T_ANS[b]) / 0.45)))
                status[b] = "연결"
            elif t >= T_PEND:
                items.append((pend[b], ease((t - T_PEND) / 0.4)))
                status[b] = "응답 중"
        if t >= T_FINAL:
            items.append((final, ease((t - T_FINAL) / 0.6)))

        content_h = sum(int(b.h * (0.35 + 0.65 * a)) for b, a in items) + 20
        view_h = H - TOP - INPUT_H
        target = max(0.0, content_h - view_h)
        scroll += (target - scroll) * 0.25

        base = Image.new("RGBA", (W, H), rgba(BG))
        y = TOP + 16 - scroll
        for b, a in items:
            img = b.img
            # 응답 중 말풍선: 흘러가는 시간 표시
            if b in pend.values():
                name = next(k for k, v in pend.items() if v is b)
                secs = int(min(real[name], (t - T_PEND) / (T_ANS[name] - T_PEND) * real[name]))
                img = bubble(["응답 중" + "." * (1 + int(t * 3) % 3)], F, head=(name, f"· {secs}초", BOTS[name]),
                             border=BOTS[name], dashed=True, width=190)
            x = CHAT_X1 - img.width if b.right else CHAT_X0
            off = int((1 - a) * 14)
            if a < 1:
                img = img.copy()
                img.putalpha(img.getchannel("A").point(lambda v, a=a: int(v * a)))
            if y + img.height > TOP - 40 and y < H:
                base.alpha_composite(img, (int(x), int(y + off)))
            y += b.h * (0.35 + 0.65 * a)
        chrome(base, typed, status, sending, caret=(i // 6) % 2 == 0, done=t >= T_FINAL + 0.6)
        add(base)
    # 장면 1 마지막 화면을 조금 더
    frames[-1] = (frames[-1][0], frames[-1][1] + 1800)

    # 장면 2: 다른 비서가 방에 말 걸기
    for i in range(int(FPS * 1.6)):
        add(caption_card("2", "다른 비서가 방에 말 걸기", "휴대폰의 비서가 허브의 방을 골라 일을 맡기고, 답을 받아 갑니다", i / (FPS * 1.6) * 1.2))
    for i in range(int(12.0 * FPS)):
        add(scene2(i / FPS))
    frames[-1] = (frames[-1][0], frames[-1][1] + 2200)
    return frames


def main() -> None:
    frames = render()
    imgs = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f, _ in frames]
    imgs[0].save(OUT, save_all=True, append_images=imgs[1:], duration=[d for _, d in frames], loop=0, optimize=True, disposal=1)
    print(OUT, f"{OUT.stat().st_size / 1e6:.1f}MB", len(imgs), "frames")


if __name__ == "__main__":
    main()
