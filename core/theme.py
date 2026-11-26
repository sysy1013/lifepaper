# -*- coding: utf-8 -*-
"""생기부 도우미 디자인 시스템 — 담백한 뉴트럴 + 인디고 포인트 1색.

색·폰트의 기본값은 .streamlit/config.toml([theme.light]/[theme.dark])이 정하고,
여기서는 Streamlit 테마가 다루지 못하는 부분(헤더·단계 제목·판정 요약·
세그먼트형 라디오·입력 포커스·알림 톤)만 CSS로 보강한다.

- 뉴트럴: 흰 바탕 · 회색 채움 입력창 · 1px 헤어라인
- 포인트: 인디고(#3B5BDB) — 주요 버튼·선택 상태·단계 번호·포커스 링에만 쓴다
- 판정 색: 옅은 바탕 + 진한 글자(WCAG AA) — 기재 불가 빨강 · 수정 필요 주황 ·
  확인 필요/교사 판단 필요 호박 · 권고 슬레이트 · 통과 초록
- 글꼴은 기기에 설치된 한글 산세리프만 쓴다 — 외부 웹폰트 요청 없음

여기서 렌더하는 HTML은 모두 코드가 정한 정적 문자열이다. 동적 값은 html.escape를 거친다.
"""

import html

import streamlit as st

_CSS = """
<style>
/* 외부 웹폰트·CDN을 불러오지 않는다(학생 정보를 다루는 화면에서 제3자 요청 차단).
   아래 글꼴 이름은 기기에 설치돼 있을 때만 쓰이는 순수 대체 목록이다. */
:root {
  --lp-sans: Pretendard, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR",
             system-ui, -apple-system, sans-serif;

  --lp-bg: #FFFFFF;
  --lp-surface: #FFFFFF;
  --lp-subtle: #F7F8FA;
  --lp-sunken: #F2F4F6;
  --lp-text: #191F28;
  --lp-text-2: #4E5968;
  --lp-muted: #646F7C;
  --lp-line: #E5E8EB;
  --lp-line-strong: #D1D6DB;

  --lp-accent: #3B5BDB;
  --lp-accent-hover: #364FC7;
  --lp-accent-text: #3B5BDB;
  --lp-accent-soft: #EDF2FF;
  --lp-accent-line: #DBE4FF;
  --lp-on-accent: #FFFFFF;
  --lp-ring: 0 0 0 3px rgba(59,91,219,0.20);

  --lp-shadow-sm: 0 1px 2px rgba(16,24,40,0.05);
  --lp-shadow: 0 1px 2px rgba(16,24,40,0.04), 0 6px 16px rgba(16,24,40,0.05);

  /* 판정 — 글자 / 바탕 / 테두리 / 막대(채움) */
  --v-ban: #B42318; --v-ban-bg: #FEF3F2; --v-ban-line: #FECDCA; --v-ban-fill: #F04438;
  --v-fix: #B93815; --v-fix-bg: #FEF6EE; --v-fix-line: #F9DBAF; --v-fix-fill: #EF6820;
  --v-chk: #93370D; --v-chk-bg: #FFFAEB; --v-chk-line: #FEDF89; --v-chk-fill: #F79009;
  --v-jdg: #854A0E; --v-jdg-bg: #FEFBE8; --v-jdg-line: #FDE272; --v-jdg-fill: #EAAA08;
  --v-adv: #475467; --v-adv-bg: #F2F4F7; --v-adv-line: #E4E7EC; --v-adv-fill: #98A2B3;
  --v-ok:  #067647; --v-ok-bg:  #ECFDF3; --v-ok-line:  #ABEFC6; --v-ok-fill:  #17B26A;
}
@media (prefers-color-scheme: dark) {
  :root {
    --lp-bg: #0F1115;
    --lp-surface: #161A20;
    --lp-subtle: #161A20;
    --lp-sunken: #1D2129;
    --lp-text: #E8EAED;
    --lp-text-2: #C4C9D1;
    --lp-muted: #9AA3AF;
    --lp-line: #262B33;
    --lp-line-strong: #353B45;

    --lp-accent: #4263EB;
    --lp-accent-hover: #4C6EF5;
    --lp-accent-text: #91A7FF;
    --lp-accent-soft: rgba(76,110,245,0.16);
    --lp-accent-line: rgba(145,167,255,0.28);
    --lp-ring: 0 0 0 3px rgba(145,167,255,0.28);

    --lp-shadow-sm: 0 1px 2px rgba(0,0,0,0.35);
    --lp-shadow: 0 1px 2px rgba(0,0,0,0.35);

    --v-ban: #FDA29B; --v-ban-bg: rgba(240,68,56,0.14);  --v-ban-line: rgba(240,68,56,0.32);
    --v-fix: #FDBA74; --v-fix-bg: rgba(239,104,32,0.14); --v-fix-line: rgba(239,104,32,0.32);
    --v-chk: #FEC84B; --v-chk-bg: rgba(247,144,9,0.13);  --v-chk-line: rgba(247,144,9,0.30);
    --v-jdg: #FDE272; --v-jdg-bg: rgba(234,170,8,0.12);  --v-jdg-line: rgba(234,170,8,0.30);
    --v-adv: #CBD2DC; --v-adv-bg: rgba(152,162,179,0.14); --v-adv-line: rgba(152,162,179,0.28);
    --v-ok:  #75E0A7; --v-ok-bg:  rgba(23,178,106,0.14); --v-ok-line:  rgba(23,178,106,0.30);
  }
}

/* ── 본문 폭·여백 (8px 간격 체계) ── */
.stMain .block-container { max-width: 920px; padding-top: 2.5rem; padding-bottom: 5rem; }
.stApp, .stMain, [data-testid="stSidebar"] { font-family: var(--lp-sans); }
.stMain h1, .stMain h2, .stMain h3, .stMain h4 { letter-spacing: -0.02em; color: var(--lp-text); }
.stMain h3 { font-size: 18px; font-weight: 700; }
.stMain [data-testid="stMarkdownContainer"] p,
.stMain [data-testid="stMarkdownContainer"] li { line-height: 1.7; }
/* 한글은 어절 단위로 줄바꿈(긴 영문·URL은 overflow-wrap이 받아 줌) */
.stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stCaptionContainer"],
.stApp [data-testid="stWidgetLabel"], .stApp [data-testid="stAlertContainer"],
.stApp [data-testid="stMarkdownContainer"] :is(p, li, span, div, b, strong) { word-break: keep-all; }
.stMain [data-testid="stCaptionContainer"] { font-size: 13px; line-height: 1.6; color: var(--lp-muted); }
.stMain hr { margin: 24px 0 !important; border-color: var(--lp-line) !important; }
@media (max-width: 640px) {
  .stMain .block-container { padding-left: 16px; padding-right: 16px; padding-top: 1.5rem; }
}

/* ── 상단 헤더: 작은 로고 마크 + 제목 + 한 줄 설명 ── */
.lp-hero {
  display: flex; align-items: flex-start; gap: 16px;
  padding: 0 0 24px; margin: 0 0 24px;
  border-bottom: 1px solid var(--lp-line);
}
.lp-mark {
  flex: none; width: 40px; height: 40px; margin-top: 2px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 12px; background: var(--lp-accent); color: var(--lp-on-accent);
  box-shadow: 0 1px 2px rgba(59,91,219,0.30), inset 0 -2px 0 rgba(0,0,0,0.10);
}
.lp-mark svg { width: 22px; height: 22px; display: block; }
.lp-hero-text { min-width: 0; }
.lp-hero-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 10px; }
.lp-hero-title {
  font-size: 28px; font-weight: 700; line-height: 1.25;
  letter-spacing: -0.03em; color: var(--lp-text);
}
.lp-hero-tag {
  font-size: 12px; font-weight: 600; line-height: 1; white-space: nowrap;
  padding: 6px 10px; border-radius: 999px;
  color: var(--lp-accent-text); background: var(--lp-accent-soft);
}
.lp-hero-sub { margin: 8px 0 0; font-size: 14.5px; line-height: 1.6; color: var(--lp-muted); }
@media (max-width: 640px) {
  .lp-hero { gap: 12px; padding-bottom: 16px; margin-bottom: 16px; }
  .lp-mark { width: 36px; height: 36px; border-radius: 10px; }
  .lp-mark svg { width: 20px; height: 20px; }
  .lp-hero-title { font-size: 22px; }
  .lp-hero-sub { font-size: 13.5px; }
  .lp-hero-tag { display: none; }
}

/* ── 단계 제목 (1 → 2 → 3 …) ── */
.lp-step { display: flex; align-items: center; gap: 12px; margin: 32px 0 16px; }
[data-testid="stColumn"] .lp-step,
[data-testid="stElementContainer"]:has(hr) + [data-testid="stElementContainer"] .lp-step { margin-top: 0; }
.lp-step-n {
  flex: none; width: 28px; height: 28px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--lp-accent); color: var(--lp-on-accent);
  font-weight: 700; font-size: 14px; line-height: 1; font-variant-numeric: tabular-nums;
}
.lp-step-t {
  font-size: 19px !important; font-weight: 700 !important; line-height: 1.35 !important;
  letter-spacing: -0.02em; color: var(--lp-text); margin: 0 !important; padding: 0 !important;
}
@media (max-width: 640px) { .lp-step-t { font-size: 17px !important; } }

/* ── 사이드바 ── */
[data-testid="stSidebar"] { border-right: 1px solid var(--lp-line); }
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding-top: 0.5rem; }
[data-testid="stSidebar"] h2 { font-size: 18px !important; font-weight: 700 !important; letter-spacing: -0.02em; }
[data-testid="stSidebar"] .stButton > button { width: 100%; }
[data-testid="stSidebar"] hr { margin: 20px 0 !important; border-color: var(--lp-line) !important; }
.lp-side-label {
  display: flex; align-items: center; gap: 8px;
  margin: 24px 0 8px; font-size: 11.5px; font-weight: 700; line-height: 1;
  letter-spacing: 0.06em; color: var(--lp-muted);
}
.lp-side-label::after { content: ""; flex: 1; height: 1px; background: var(--lp-line); }

/* ── 조용한 안내 (왼쪽 포인트 띠) ── */
.lp-note {
  font-size: 13px; line-height: 1.65; color: var(--lp-text-2);
  background: var(--lp-surface); border: 1px solid var(--lp-line);
  box-shadow: inset 3px 0 0 var(--lp-accent);
  border-radius: 10px; padding: 12px 14px 12px 16px; margin: 4px 0 12px;
}
.lp-note b { color: var(--lp-text); font-weight: 650; }
.lp-note p { margin: 0; }
.lp-note p + p { margin-top: 8px; }

/* ── 판정 요약: 분포 막대 + 건수 칩 ── */
.lp-summary {
  border: 1px solid var(--lp-line); border-radius: 16px;
  background: var(--lp-surface); box-shadow: var(--lp-shadow-sm);
  padding: 16px; margin: 8px 0 12px;
}
.lp-summary-head {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  margin-bottom: 12px; font-size: 13px; font-weight: 600; color: var(--lp-muted);
}
.lp-summary-total b {
  font-size: 22px; font-weight: 700; color: var(--lp-text);
  font-variant-numeric: tabular-nums; letter-spacing: -0.02em; margin-right: 2px;
}
.lp-bar {
  display: flex; gap: 2px; height: 8px; margin-bottom: 14px;
  border-radius: 999px; overflow: hidden; background: var(--lp-sunken);
}
.lp-bar span { display: block; min-width: 6px; flex-basis: 0; }
.lp-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.lp-chip {
  display: inline-flex; align-items: center; gap: 8px; min-height: 32px;
  padding: 0 12px; border-radius: 999px; border: 1px solid transparent;
  font-size: 13px; font-weight: 600; line-height: 1;
}
.lp-chip b { font-variant-numeric: tabular-nums; font-weight: 700; }
.lp-chip::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--dot, currentColor); }
.lp-v-ban { color: var(--v-ban); background: var(--v-ban-bg); border-color: var(--v-ban-line); --dot: var(--v-ban-fill, var(--v-ban)); }
.lp-v-fix { color: var(--v-fix); background: var(--v-fix-bg); border-color: var(--v-fix-line); --dot: var(--v-fix-fill, var(--v-fix)); }
.lp-v-chk { color: var(--v-chk); background: var(--v-chk-bg); border-color: var(--v-chk-line); --dot: var(--v-chk-fill, var(--v-chk)); }
.lp-v-jdg { color: var(--v-jdg); background: var(--v-jdg-bg); border-color: var(--v-jdg-line); --dot: var(--v-jdg-fill, var(--v-jdg)); }
.lp-v-adv { color: var(--v-adv); background: var(--v-adv-bg); border-color: var(--v-adv-line); --dot: var(--v-adv-fill, var(--v-adv)); }
.lp-v-ok  { color: var(--v-ok);  background: var(--v-ok-bg);  border-color: var(--v-ok-line);  --dot: var(--v-ok-fill, var(--v-ok)); }
.lp-bar .lp-v-ban { background: var(--v-ban-fill, var(--v-ban)); }
.lp-bar .lp-v-fix { background: var(--v-fix-fill, var(--v-fix)); }
.lp-bar .lp-v-chk { background: var(--v-chk-fill, var(--v-chk)); }
.lp-bar .lp-v-jdg { background: var(--v-jdg-fill, var(--v-jdg)); }
.lp-bar .lp-v-adv { background: var(--v-adv-fill, var(--v-adv)); }
.lp-bar .lp-v-ok  { background: var(--v-ok-fill, var(--v-ok)); }
@media (prefers-color-scheme: dark) {
  :root { --v-ban-fill: #F97066; --v-fix-fill: #F38744; --v-chk-fill: #FDB022;
          --v-jdg-fill: #FAC515; --v-adv-fill: #7D8899; --v-ok-fill: #47CD89; }
}

/* ── 하이라이트 원문 박스 ── */
.lp-doc {
  border: 1px solid var(--lp-line); border-radius: 12px;
  padding: 16px 20px; line-height: 1.9; font-size: 15.5px;
  background: var(--lp-surface); color: var(--lp-text);
  overflow-wrap: anywhere; word-break: keep-all;
}
/* 인라인 style은 브라우저가 rgb(...)로 다시 쓰므로 두 표기 모두 잡는다 */
.lp-doc span[style*="yellow"], .lp-doc span[style*="255, 255, 0"] {
  background: var(--v-ban-bg) !important; color: var(--v-ban) !important;
  border-bottom: 0 !important; font-weight: 700;
  box-shadow: inset 0 -2px 0 var(--v-ban-fill, var(--v-ban)); border-radius: 4px; padding: 1px 3px;
}
.lp-doc span[style*="fff3cd"], .lp-doc span[style*="255, 243, 205"] {
  background: var(--v-chk-bg) !important; color: var(--v-chk) !important;
  border-bottom: 0 !important;
  box-shadow: inset 0 -2px 0 var(--v-chk-fill, var(--v-chk)); border-radius: 4px; padding: 1px 3px;
}

/* ── 세그먼트형 라디오 (모드·입력 방식) ── */
[class*="st-key-lp_seg"] [data-testid="stElementContainer"],
[class*="st-key-lp_seg"] [data-testid="stRadio"] { width: 100% !important; }
[class*="st-key-lp_seg"] [data-testid="stWidgetLabel"] p { font-size: 13px; font-weight: 600; color: var(--lp-muted); }
[class*="st-key-lp_seg"] [role="radiogroup"] {
  display: flex; flex-wrap: wrap; gap: 4px;
  padding: 4px; border-radius: 14px; background: var(--lp-sunken);
  width: 100%; box-sizing: border-box;
}
[class*="st-key-lp_seg"] [role="radiogroup"] > label {
  flex: 1 1 auto; margin: 0 !important; justify-content: center;
  min-height: 40px; padding: 8px 16px; border-radius: 10px; cursor: pointer;
  transition: background-color .15s ease, box-shadow .15s ease;
}
/* 동그란 라디오 표시는 숨기고 버튼 전체를 선택 영역으로 */
[class*="st-key-lp_seg"] [data-testid="stRadioOption"] > div > div > div:not([data-testid]) { display: none !important; }
[class*="st-key-lp_seg"] [data-testid="stRadioOption"] > div,
[class*="st-key-lp_seg"] [data-testid="stRadioOption"] > div > div { width: 100%; justify-content: center; }
[class*="st-key-lp_seg"] [role="radiogroup"] > label p {
  font-weight: 500 !important; font-size: 14.5px; text-align: center; color: var(--lp-text-2);
}
[class*="st-key-lp_seg"] [role="radiogroup"] > label:hover { background: rgba(127,137,150,0.10); }
[class*="st-key-lp_seg"] [role="radiogroup"] > label:has(input:checked) {
  background: var(--lp-surface); box-shadow: 0 1px 2px rgba(16,24,40,0.08), 0 1px 6px rgba(16,24,40,0.06);
}
@media (prefers-color-scheme: dark) {
  [class*="st-key-lp_seg"] [role="radiogroup"] > label:has(input:checked) { background: #2A303A; box-shadow: none; }
}
[class*="st-key-lp_seg"] [role="radiogroup"] > label:has(input:checked) p { font-weight: 700 !important; color: var(--lp-accent-text) !important; }
[class*="st-key-lp_seg"] [role="radiogroup"] > label[data-focus-visible] { outline: 2px solid var(--lp-accent); outline-offset: 1px; }
@media (max-width: 640px) {
  [class*="st-key-lp_seg"] [data-testid="stRadio"],
  [class*="st-key-lp_seg"] [data-testid="stRadio"] > div { width: 100% !important; }
  [class*="st-key-lp_seg"] [role="radiogroup"] { flex-direction: column; align-items: stretch; width: 100%; box-sizing: border-box; }
  [class*="st-key-lp_seg"] [role="radiogroup"] > label { min-height: 44px; width: 100%; box-sizing: border-box; }
}

/* ── 일반 라디오: 선택 안 된 동그라미 테두리를 또렷하게(비텍스트 대비 3:1) ── */
[data-testid="stRadioOption"]:not(:has(input:checked)) > div > div > div:not([data-testid]) {
  background-color: #8B95A1 !important;
}
@media (prefers-color-scheme: dark) {
  [data-testid="stRadioOption"]:not(:has(input:checked)) > div > div > div:not([data-testid]) {
    background-color: #6B7480 !important;
  }
}

/* ── 버튼: 주요 = 포인트 채움, 보조 = 고스트 ── */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
  min-height: 44px; padding: 0 16px; font-weight: 600; letter-spacing: -0.01em;
  transition: background-color .15s ease, border-color .15s ease, box-shadow .15s ease;
}
.stMain [data-testid="stBaseButton-secondary"] {
  background: transparent; border: 1px solid var(--lp-line-strong); color: var(--lp-text);
}
.stMain [data-testid="stBaseButton-secondary"]:hover:not(:disabled) {
  background: var(--lp-sunken); border-color: var(--lp-line-strong); color: var(--lp-text);
}
[data-testid="stBaseButton-primary"] {
  min-height: 48px; font-size: 15.5px; font-weight: 700;
  background: var(--lp-accent); border-color: var(--lp-accent); color: var(--lp-on-accent);
  box-shadow: 0 1px 2px rgba(59,91,219,0.25);
}
[data-testid="stBaseButton-primary"]:hover:not(:disabled) {
  background: var(--lp-accent-hover); border-color: var(--lp-accent-hover); color: var(--lp-on-accent);
}
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible {
  box-shadow: var(--lp-ring) !important; outline: none;
}
@media (max-width: 640px) {
  .stMain .stButton > button[data-testid="stBaseButton-primary"] { width: 100%; }
}

/* ── 입력창: 넉넉한 높이 · 둥근 모서리 · 포인트 포커스 링 ── */
[data-testid="stTextInputRootElement"], [data-testid="stNumberInputContainer"],
[data-baseweb="select"] > div { min-height: 44px; }
.stTextArea textarea { font-size: 15px; line-height: 1.7; padding: 12px 14px; }
.stMain .stTextArea textarea { min-height: 120px; }
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stTextAreaRootElement"]:focus-within,
[data-testid="stNumberInputContainer"]:focus-within,
[data-baseweb="select"] > div:focus-within {
  border-color: var(--lp-accent) !important; box-shadow: var(--lp-ring);
}
[data-testid="stWidgetLabel"] p { font-weight: 600; }

/* ── 파일 올리기 영역 ── */
[data-testid="stFileUploaderDropzone"] {
  border: 1.5px dashed var(--lp-line-strong); border-radius: 12px;
  background: var(--lp-subtle); padding: 20px;
}
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--lp-accent); }

/* ── 알림: 꽉 찬 박스 대신 왼쪽 띠 + 옅은 바탕 ── */
[data-testid="stAlertContainer"] {
  border-radius: 12px; box-shadow: inset 3px 0 0 currentColor;
  padding: 12px 16px 12px 18px; font-size: 14px; line-height: 1.6;
}
[data-testid="stAlertContainer"] p { font-size: 14px; line-height: 1.6; }

/* ── 지표 카드 ── */
[data-testid="stMetric"] {
  background: var(--lp-surface); border: 1px solid var(--lp-line);
  border-radius: 12px; padding: 14px 16px;
}
[data-testid="stMetricLabel"] p { font-size: 13px; color: var(--lp-muted); font-weight: 600; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; font-weight: 700; font-size: 24px; letter-spacing: -0.02em; }

/* ── 펼침 패널 · 표 ── */
[data-testid="stExpander"] details { border-radius: 12px; border-color: var(--lp-line); }
[data-testid="stExpander"] summary { min-height: 44px; }
[data-testid="stExpander"] summary p { font-weight: 600; }
[data-testid="stDataFrame"] { border-radius: 12px; }
.stMain [data-testid="stMarkdownContainer"] table { border-collapse: collapse; font-size: 14px; }
.stMain [data-testid="stMarkdownContainer"] th { background: var(--lp-subtle); font-weight: 650; }
.stMain [data-testid="stMarkdownContainer"] th,
.stMain [data-testid="stMarkdownContainer"] td { border-color: var(--lp-line) !important; padding: 8px 12px; vertical-align: top; }

/* 긴 텍스트가 가로 스크롤을 만들지 않도록 */
.stMain [data-testid="stMarkdownContainer"] { overflow-wrap: anywhere; }
.stMain [data-testid="stText"] { white-space: pre-wrap; overflow-wrap: anywhere; }

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; }
}
</style>
"""

# 로고 마크: 문서 + 체크 (인라인 SVG, 외부 요청 없음)
_MARK_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
    '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>'
    '<path d="M14 3v5h5"/><path d="m9 14.5 2 2 4-4.5"/></svg>'
)


def apply_theme() -> None:
    """디자인 시스템 CSS를 주입한다. set_page_config 직후 1회 호출한다."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_brand_header(subtitle: str = "") -> None:
    """작은 로고 마크 + 제목 + 한 줄 설명의 상단 헤더를 렌더한다."""
    sub = f'<p class="lp-hero-sub">{html.escape(subtitle)}</p>' if subtitle else ""
    st.markdown(
        '<div class="lp-hero">'
        f'<span class="lp-mark">{_MARK_SVG}</span>'
        '<div class="lp-hero-text">'
        '<div class="lp-hero-row">'
        '<span class="lp-hero-title">생기부 도우미</span>'
        '<span class="lp-hero-tag">학교생활기록부 검토·작성</span>'
        "</div>"
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

# st.dataframe Styler용 (판정 칸 글자색·바탕). 표는 캔버스로 그려져 CSS 변수를 못 쓰므로
# 불투명한 옅은 바탕 + 진한 글자로 밝은/어두운 테마 모두에서 대비(AA)를 지킨다.
VERDICT_CELL_STYLE = {
    "기재 불가": "color: #B42318; background-color: #FEF3F2; font-weight: 700;",
    "수정 필요": "color: #B93815; background-color: #FEF6EE; font-weight: 700;",
    "교사 판단 필요": "color: #854A0E; background-color: #FEFBE8; font-weight: 700;",
    "확인 필요": "color: #93370D; background-color: #FFFAEB; font-weight: 700;",
    "권고": "color: #475467; background-color: #F2F4F7;",
    "통과": "color: #067647; background-color: #ECFDF3; font-weight: 700;",
}


# 어두운 테마용 — 어두운 바탕에 옅은 색 글자 + 반투명 바탕
VERDICT_CELL_STYLE_DARK = {
    "기재 불가": "color: #FDA29B; background-color: rgba(240,68,56,0.16); font-weight: 700;",
    "수정 필요": "color: #FDBA74; background-color: rgba(239,104,32,0.16); font-weight: 700;",
    "교사 판단 필요": "color: #FDE272; background-color: rgba(234,170,8,0.14); font-weight: 700;",
    "확인 필요": "color: #FEC84B; background-color: rgba(247,144,9,0.15); font-weight: 700;",
    "권고": "color: #CBD2DC; background-color: rgba(152,162,179,0.14);",
    "통과": "color: #75E0A7; background-color: rgba(23,178,106,0.16); font-weight: 700;",
}

_VERDICT_ORDER = ("기재 불가", "수정 필요", "교사 판단 필요", "확인 필요", "권고", "통과")


def verdict_cell_style(value, styles: dict | None = None) -> str:
    """판정 칸 값에 맞는 CSS. '확인 필요 · 교사 판단 필요'는 교사 판단 색을 쓴다."""
    table = styles if styles is not None else VERDICT_CELL_STYLE
    s = str(value)
    for key in _VERDICT_ORDER:
        if key in s:
            return table[key]
    return ""


def verdict_cell_style_themed(value) -> str:
    """현재 브라우저 테마(밝음/어두움)에 맞춘 판정 칸 CSS (st.dataframe Styler용)."""
    try:
        dark = getattr(st.context.theme, "type", None) == "dark"
    except Exception:
        dark = False
    return verdict_cell_style(value, VERDICT_CELL_STYLE_DARK if dark else None)


def render_verdict_chips(items: list[tuple[str, int]]) -> None:
    """(판정 라벨, 건수) 목록을 분포 막대 + 색 배지 줄로 렌더한다. 0건은 생략."""
    shown = [(label, int(n)) for label, n in items if n]
    if not shown:
        return
    total = sum(n for _, n in shown)
    bar = "".join(
        f'<span class="{VERDICT_CLASS.get(label, "lp-v-adv")}" '
        f'style="flex-grow:{n}"></span>'
        for label, n in shown
    )
    chips = "".join(
        f'<span class="lp-chip {VERDICT_CLASS.get(label, "lp-v-adv")}">'
        f"{html.escape(label)} <b>{n}</b></span>"
        for label, n in shown
    )
    st.markdown(
        '<div class="lp-summary">'
        '<div class="lp-summary-head"><span>판정별 건수</span>'
        f'<span class="lp-summary-total"><b>{total}</b>건</span></div>'
        f'<div class="lp-bar" aria-hidden="true">{bar}</div>'
        f'<div class="lp-chips">{chips}</div>'
        "</div>",
        unsafe_allow_html=True,
    )
