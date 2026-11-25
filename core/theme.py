# -*- coding: utf-8 -*-
"""생기부 도우미 디자인 시스템 — '결재 서류' 콘셉트를 Streamlit에 주입한다.

색·폰트의 기본값은 .streamlit/config.toml([theme.light]/[theme.dark])이 정하고,
여기서는 Streamlit 테마가 다루지 못하는 부분(헤더 카드·단계 제목·판정 배지·
세그먼트형 라디오·알림 톤)만 CSS로 보강한다.

- 잉크 블루(#2E4FA3): 주요 버튼·선택 상태·단계 번호
- 인주 레드(#C23B2E): 로고 도장, '기재 불가' 판정
- 괘선 블루: 헤더 카드의 서식 괘선
- 제목은 명조(설치된 경우), 본문은 시스템 한글 글꼴 — 외부 웹폰트 요청 없음

여기서 렌더하는 HTML은 모두 코드가 정한 정적 문자열이다. 동적 값은 html.escape를 거친다.
"""

import html

import streamlit as st

_CSS = """
<style>
/* 외부 웹폰트·CDN을 불러오지 않는다(학생 정보를 다루는 화면에서 제3자 요청 차단).
   아래 글꼴 이름은 기기에 설치돼 있을 때만 쓰이는 순수 대체 목록이다. */
:root {
  --lp-ink: #2E4FA3;
  --lp-ink-soft: rgba(46,79,163,0.09);
  --lp-ink-line: rgba(46,79,163,0.22);
  --lp-seal: #C23B2E;
  --lp-rule: rgba(46,79,163,0.10);
  --lp-card: #FFFFFF;
  --lp-text: #1A2233;
  --lp-muted: #5B6577;
  --lp-hair: rgba(26,34,51,0.10);
  --lp-shadow: 0 1px 2px rgba(26,34,51,0.05), 0 4px 16px rgba(26,34,51,0.04);
  --lp-serif: "Noto Serif KR", "Nanum Myeongjo", "AppleMyungjo", "Batang", serif;
  --lp-sans: Pretendard, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR",
             system-ui, -apple-system, sans-serif;

  /* 판정 색 — 밝은/어두운 배경 모두에서 읽히는 중간 톤 + 반투명 바탕 */
  --v-ban: #C23B2E;   --v-ban-bg: rgba(194,59,46,0.12);
  --v-fix: #C2610C;   --v-fix-bg: rgba(194,97,12,0.12);
  --v-chk: #A86B00;   --v-chk-bg: rgba(217,150,20,0.15);
  --v-jdg: #96600A;   --v-jdg-bg: rgba(217,150,20,0.11);
  --v-adv: #5B6577;   --v-adv-bg: rgba(91,101,119,0.12);
  --v-ok:  #2F7D4F;   --v-ok-bg:  rgba(47,125,79,0.12);
}
@media (prefers-color-scheme: dark) {
  :root {
    --lp-ink: #8FA8EE;
    --lp-ink-soft: rgba(143,168,238,0.12);
    --lp-ink-line: rgba(143,168,238,0.30);
    --lp-seal: #E0675B;
    --lp-rule: rgba(143,168,238,0.10);
    --lp-card: #1A2029;
    --lp-text: #E6EAF0;
    --lp-muted: #9AA4B5;
    --lp-hair: rgba(230,234,240,0.10);
    --lp-shadow: 0 1px 2px rgba(0,0,0,0.3);
    --v-ban: #EF7B6F;  --v-fix: #F0A060;  --v-chk: #E8C15A;
    --v-jdg: #E2B55A;  --v-adv: #A7B0BF;  --v-ok: #6CC48F;
  }
}

/* ── 본문 폭·여백 ── */
.stMain .block-container { max-width: 960px; padding-top: 2.25rem; padding-bottom: 4rem; }
@media (max-width: 640px) {
  .stMain .block-container { padding-left: 1rem; padding-right: 1rem; padding-top: 1.25rem; }
}
.stMain h1, .stMain h2, .stMain h3 { letter-spacing: -0.01em; }

/* ── 헤더 카드: 생기부 서식처럼 괘선이 깔린 종이 + 인주 도장 ── */
.lp-hero {
  position: relative; overflow: hidden;
  display: flex; align-items: center; gap: 18px;
  padding: 22px 24px; margin: 0 0 22px;
  background-color: var(--lp-card);
  /* 괘선은 오른쪽 빈 여백에만 보이게 왼쪽(글자 영역)을 종이색으로 덮는다 */
  background-image:
    linear-gradient(to right, var(--lp-card) 0, var(--lp-card) 55%, transparent 85%),
    repeating-linear-gradient(
      to bottom, transparent 0, transparent 23px, var(--lp-rule) 23px, var(--lp-rule) 24px);
  border: 1px solid var(--lp-hair);
  border-left: 4px solid var(--lp-ink);
  border-radius: 12px;
  box-shadow: var(--lp-shadow);
}
.lp-seal {
  flex: none; width: 58px; height: 58px;
  display: flex; align-items: center; justify-content: center;
  font-family: var(--lp-serif); font-weight: 700; font-size: 30px; line-height: 1;
  color: var(--lp-seal);
  border: 2.5px solid var(--lp-seal); border-radius: 10px;
  box-shadow: inset 0 0 0 3px var(--lp-card), inset 0 0 0 4.5px var(--lp-seal);
  transform: rotate(-6deg);
  background: var(--lp-card);
}
.lp-hero-text { min-width: 0; }
.lp-hero-eyebrow {
  font-size: 12px; font-weight: 600; letter-spacing: 0.06em;
  color: var(--lp-ink); margin-bottom: 2px;
}
.lp-hero-title {
  font-family: var(--lp-serif); font-weight: 700;
  font-size: 28px; line-height: 1.25; letter-spacing: -0.02em; color: var(--lp-text);
}
.lp-hero-sub { margin-top: 6px; font-size: 14px; line-height: 1.6; color: var(--lp-muted); }
@media (max-width: 640px) {
  .lp-hero { padding: 16px; gap: 14px; align-items: flex-start; background-image: none; }
  .lp-seal { width: 46px; height: 46px; font-size: 24px; }
  .lp-hero-title { font-size: 22px; }
  .lp-hero-sub { font-size: 13px; }
}

/* ── 단계 제목 (1 → 2 → 3 …) ── */
.lp-step {
  display: flex; align-items: center; gap: 12px;
  margin: 18px 0 10px; padding-bottom: 8px;
  border-bottom: 1px solid var(--lp-hair);
}
.lp-step-n {
  flex: none; width: 28px; height: 28px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--lp-ink); color: #fff;
  font-weight: 700; font-size: 14px; font-variant-numeric: tabular-nums;
}
@media (prefers-color-scheme: dark) { .lp-step-n { color: #11151B; } }
.lp-step-t {
  font-family: var(--lp-serif); font-weight: 700; font-size: 20px;
  line-height: 1.35; letter-spacing: -0.01em; color: var(--lp-text); margin: 0;
}
@media (max-width: 640px) { .lp-step-t { font-size: 17px; } }

/* ── 사이드바 그룹 라벨 ── */
.lp-side-label {
  margin: 14px 0 4px; font-size: 11.5px; font-weight: 700;
  letter-spacing: 0.08em; color: var(--lp-ink);
}
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding-top: 1rem; }
[data-testid="stSidebar"] .stButton > button { width: 100%; }

/* ── 조용한 안내 패널 ── */
.lp-note {
  font-size: 13px; line-height: 1.6; color: var(--lp-muted);
  background: var(--lp-ink-soft); border-radius: 8px;
  padding: 10px 12px; margin: 4px 0 10px;
}
.lp-note b { color: var(--lp-text); font-weight: 600; }
.lp-note p { margin: 0; }
.lp-note p + p { margin-top: 6px; }

/* ── 판정 배지 · 요약 칩 ── */
.lp-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0 10px; }
.lp-chip {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 13px; font-weight: 600; line-height: 1;
  padding: 7px 10px; border-radius: 999px;
}
.lp-chip b { font-variant-numeric: tabular-nums; font-weight: 800; }
.lp-chip::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.lp-v-ban { color: var(--v-ban); background: var(--v-ban-bg); }
.lp-v-fix { color: var(--v-fix); background: var(--v-fix-bg); }
.lp-v-chk { color: var(--v-chk); background: var(--v-chk-bg); }
.lp-v-jdg { color: var(--v-jdg); background: var(--v-jdg-bg); }
.lp-v-adv { color: var(--v-adv); background: var(--v-adv-bg); }
.lp-v-ok  { color: var(--v-ok);  background: var(--v-ok-bg); }

/* ── 하이라이트 원문 박스 ── */
.lp-doc {
  border: 1px solid var(--lp-hair); border-radius: 10px;
  padding: 16px 18px; line-height: 1.9; font-size: 15px;
  background: var(--lp-card); color: var(--lp-text);
  overflow-wrap: anywhere; word-break: keep-all;
}
.lp-doc span[style*="yellow"] {
  background: var(--v-ban-bg) !important; color: var(--v-ban) !important;
  border-bottom: 2px solid var(--v-ban); border-radius: 3px; padding: 0 2px;
}
.lp-doc span[style*="fff3cd"] {
  background: var(--v-chk-bg) !important; color: var(--v-chk) !important;
  border-bottom: 2px solid var(--v-chk) !important; border-radius: 3px; padding: 0 2px;
}

/* ── 세그먼트형 라디오 (모드·입력 방식) ── */
[class*="st-key-lp_seg"] [role="radiogroup"] {
  display: flex; flex-wrap: wrap; gap: 4px;
  padding: 4px; border-radius: 12px;
  background: var(--lp-ink-soft); border: 1px solid var(--lp-hair);
}
[class*="st-key-lp_seg"] [role="radiogroup"] > label {
  flex: 1 1 auto; margin: 0 !important; justify-content: center;
  padding: 8px 14px; border-radius: 9px; cursor: pointer;
  transition: background-color .15s ease, box-shadow .15s ease;
}
/* 동그란 라디오 표시는 숨기고 버튼 전체를 선택 영역으로 */
[class*="st-key-lp_seg"] [data-testid="stRadioOption"] > div > div > div:not([data-testid]) { display: none !important; }
[class*="st-key-lp_seg"] [data-testid="stRadioOption"] > div,
[class*="st-key-lp_seg"] [data-testid="stRadioOption"] > div > div { width: 100%; justify-content: center; }
[class*="st-key-lp_seg"] [role="radiogroup"] > label p { font-weight: 500 !important; font-size: 14.5px; text-align: center; }
[class*="st-key-lp_seg"] [role="radiogroup"] > label:hover { background: var(--lp-ink-soft); }
[class*="st-key-lp_seg"] [role="radiogroup"] > label:has(input:checked) {
  background: var(--lp-card); box-shadow: 0 1px 3px rgba(26,34,51,0.14);
}
[class*="st-key-lp_seg"] [role="radiogroup"] > label:has(input:checked) p { font-weight: 700 !important; color: var(--lp-ink) !important; }
[class*="st-key-lp_seg"] [role="radiogroup"] > label:has(input:focus-visible) { outline: 2px solid var(--lp-ink); outline-offset: 1px; }
@media (max-width: 640px) {
  [class*="st-key-lp_seg"] [data-testid="stRadio"],
  [class*="st-key-lp_seg"] [data-testid="stRadio"] > div { width: 100% !important; }
  [class*="st-key-lp_seg"] [role="radiogroup"] { flex-direction: column; width: 100%; box-sizing: border-box; }
}

/* ── 버튼 ── */
.stButton > button, .stDownloadButton > button { font-weight: 600; }
.stButton > button[kind="primary"] { min-height: 46px; font-size: 15.5px; letter-spacing: -0.01em; }

/* ── 알림: 벽처럼 꽉 찬 박스 대신 왼쪽 색띠 + 옅은 바탕 ── */
[data-testid="stAlert"] [data-testid="stAlertContainer"],
[data-testid="stAlert"] > div {
  border-left: 3px solid currentColor;
  font-size: 14px; line-height: 1.6;
}

/* ── 지표 카드 ── */
[data-testid="stMetric"] {
  background: var(--lp-ink-soft); border-radius: 10px; padding: 12px 14px;
}
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; font-weight: 700; font-size: 1.5rem; }

/* ── 펼침 패널 ── */
[data-testid="stExpander"] details { border-radius: 10px; }
[data-testid="stExpander"] summary p { font-weight: 600; }

/* 긴 텍스트가 가로 스크롤을 만들지 않도록 */
.stMain [data-testid="stMarkdownContainer"] { overflow-wrap: anywhere; }
.stMain [data-testid="stText"] { white-space: pre-wrap; overflow-wrap: anywhere; }

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; }
}
</style>
"""


def apply_theme() -> None:
    """디자인 시스템 CSS를 주입한다. set_page_config 직후 1회 호출한다."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_brand_header(subtitle: str = "") -> None:
    """괘선 서식 카드 + '생' 도장 로고 헤더를 렌더한다."""
    sub = f'<div class="lp-hero-sub">{html.escape(subtitle)}</div>' if subtitle else ""
    st.markdown(
        '<div class="lp-hero">'
        '<span class="lp-seal" aria-hidden="true">생</span>'
        '<div class="lp-hero-text">'
        '<div class="lp-hero-eyebrow">학교생활기록부 검토·작성</div>'
        '<div class="lp-hero-title">생기부 도우미</div>'
        f"{sub}"
        "</div></div>",
        unsafe_allow_html=True,
    )


def render_step(n: int | str, title: str, container=None) -> None:
    """번호가 붙은 단계 제목. n은 순서 번호(또는 짧은 기호)."""
    target = container if container is not None else st
    target.markdown(
        '<div class="lp-step">'
        f'<span class="lp-step-n">{html.escape(str(n))}</span>'
        f'<h3 class="lp-step-t">{html.escape(title)}</h3>'
        "</div>",
        unsafe_allow_html=True,
    )


def render_side_label(text: str) -> None:
    """사이드바 그룹 라벨."""
    st.markdown(
        f'<div class="lp-side-label">{html.escape(text)}</div>', unsafe_allow_html=True
    )


def render_note(paragraphs: list[tuple[str, str, str]]) -> None:
    """조용한 안내 패널. 각 문단은 (앞, 굵게, 뒤) 세 조각 — 모두 escape된다."""
    body = "".join(
        f"<p>{html.escape(a)}<b>{html.escape(b)}</b>{html.escape(c)}</p>"
        for a, b, c in paragraphs
    )
    st.markdown(f'<div class="lp-note">{body}</div>', unsafe_allow_html=True)


# 판정 문자열 → 배지 클래스 (core/report.py VERDICT_* 와 일치)
VERDICT_CLASS = {
    "기재 불가": "lp-v-ban",
    "수정 필요": "lp-v-fix",
    "확인 필요": "lp-v-chk",
    "교사 판단 필요": "lp-v-jdg",
    "권고": "lp-v-adv",
    "통과": "lp-v-ok",
}

# st.dataframe Styler용 (판정 칸 글자색·바탕) — 밝은/어두운 테마 모두 읽히는 톤
VERDICT_CELL_STYLE = {
    "기재 불가": "color: #C23B2E; background-color: rgba(194,59,46,0.12); font-weight: 700;",
    "수정 필요": "color: #C2610C; background-color: rgba(194,97,12,0.12); font-weight: 700;",
    "교사 판단 필요": "color: #96600A; background-color: rgba(217,150,20,0.11); font-weight: 700;",
    "확인 필요": "color: #A86B00; background-color: rgba(217,150,20,0.15); font-weight: 700;",
    "권고": "color: #6B7280; background-color: rgba(91,101,119,0.10);",
    "통과": "color: #2F7D4F; background-color: rgba(47,125,79,0.12); font-weight: 700;",
}


def verdict_cell_style(value) -> str:
    """판정 칸 값에 맞는 CSS. '확인 필요 · 교사 판단 필요'는 교사 판단 색을 쓴다."""
    s = str(value)
    for key in ("기재 불가", "수정 필요", "교사 판단 필요", "확인 필요", "권고", "통과"):
        if key in s:
            return VERDICT_CELL_STYLE[key]
    return ""


def render_verdict_chips(items: list[tuple[str, int]]) -> None:
    """(판정 라벨, 건수) 목록을 색 배지 줄로 렌더한다. 0건은 생략."""
    chips = "".join(
        f'<span class="lp-chip {VERDICT_CLASS.get(label, "lp-v-adv")}">'
        f"{html.escape(label)} <b>{int(n)}</b></span>"
        for label, n in items
        if n
    )
    if chips:
        st.markdown(f'<div class="lp-chips">{chips}</div>', unsafe_allow_html=True)
