# -*- coding: utf-8 -*-
"""생기부 도우미 디자인 시스템 — macOS 네이티브 스타일을 Streamlit에 주입한다.

Figma 'macOS 26' 커뮤니티 파일 기반 디자인 시스템(SF Pro/Pretendard, 시스템 블루
rgb(13,111,255), 불투명도 기반 라벨, 헤어라인 스트로크, 소프트 엘리베이션, 라운드 반경)을
Streamlit 네이티브 위젯에 CSS로 이식한다. React 컴포넌트는 사용하지 않는다.
"""

import streamlit as st

# 원시 토큰 (fig-tokens.css Light 모드에서 발췌)
_CSS = """
<style>
@import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.css");

:root {
  --font-system: -apple-system, BlinkMacSystemFont, "Pretendard Variable", Pretendard, "Helvetica Neue", system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", Menlo, monospace;

  --accent: rgb(13,111,255);
  --accent-hover: rgb(10,95,225);
  --accent-soft: rgba(13,111,255,0.10);
  --accent-soft-hover: rgba(13,111,255,0.16);
  --indigo: rgb(97,85,245);
  --destructive: rgb(255,56,60);
  --success: rgb(52,199,89);

  --label-1: rgba(0,0,0,0.85);
  --label-2: rgba(0,0,0,0.50);
  --label-3: rgba(0,0,0,0.25);

  --surface-window: rgb(246,246,246);
  --surface-content: rgb(255,255,255);
  --section-fill: rgb(245,245,245);
  --separator: rgba(60,60,67,0.20);
  --hairline: rgba(0,0,0,0.10);

  --shadow-control: 0 0.5px 1px rgba(0,0,0,0.12), 0 0 0 0.5px rgba(0,0,0,0.05);
  --shadow-card: 0 1px 3px rgba(0,0,0,0.06), 0 0 0 0.5px rgba(0,0,0,0.06);
  --focus-ring: 0 0 0 3.5px rgba(13,111,255,0.45);

  --radius: 6px;
  --radius-lg: 8px;
  --radius-pill: 1000px;
  --ease: cubic-bezier(.32,.72,.35,1);
}

/* ── 전역 폰트·배경 ── */
html, body, .stApp, [class*="css"] { font-family: var(--font-system); }
.stApp { background: var(--surface-window); }
.stApp, .stMarkdown, p, span, label, li { color: var(--label-1); }

/* 본문 컨테이너를 카드처럼 */
.stMain .block-container {
  background: var(--surface-content);
  border-radius: 12px;
  border: 0.5px solid var(--hairline);
  box-shadow: var(--shadow-card);
  padding: 28px 32px 40px;
  margin-top: 20px;
  max-width: 900px;
}

/* ── 타이포그래피 ── */
h1, h2, h3 { font-family: var(--font-system); letter-spacing: -0.01em; color: var(--label-1); }
.stMain h1 { font-size: 26px; font-weight: 700; }
.stMain h2 { font-size: 20px; font-weight: 600; }
.stMain h3 { font-size: 17px; font-weight: 600; }
.stCaption, [data-testid="stCaptionContainer"], small { color: var(--label-2) !important; }

/* ── 앱 헤더 브랜드 타일 ── */
.lp-brand { display: flex; align-items: center; gap: 12px; margin: 0 0 4px; }
.lp-logo {
  width: 40px; height: 40px; border-radius: 10px; flex: none;
  background: var(--accent); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 22px; box-shadow: var(--shadow-control);
}
.lp-brand .lp-title { font-size: 24px; font-weight: 700; letter-spacing: -0.02em; color: var(--label-1); }

/* ── 버튼 ── */
.stButton > button, .stDownloadButton > button {
  font-family: var(--font-system); font-weight: 500; font-size: 14px;
  border-radius: var(--radius); border: 0.5px solid var(--hairline);
  box-shadow: var(--shadow-control); transition: all var(--dur, 150ms) var(--ease);
  background: linear-gradient(180deg, #fff, #f6f6f6); color: var(--label-1);
}
.stButton > button:hover, .stDownloadButton > button:hover {
  background: linear-gradient(180deg, #fff, #f0f0f0); border-color: var(--separator);
}
.stButton > button:active, .stDownloadButton > button:active { background: #ececec; }
/* Primary (type=primary) */
.stButton > button[kind="primary"], .stButton > button[data-testid="baseButton-primary"] {
  background: var(--accent); color: #fff; border: 0.5px solid rgba(0,0,0,0.10);
}
.stButton > button[kind="primary"]:hover { background: var(--accent-hover); color: #fff; }
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible {
  box-shadow: var(--focus-ring); outline: none;
}

/* ── 입력 필드 ── */
.stTextInput input, .stTextArea textarea, .stNumberInput input {
  border-radius: 5px !important; border: 0.5px solid var(--separator) !important;
  box-shadow: inset 0 1px 1px rgba(0,0,0,0.04) !important; font-family: var(--font-system);
  background: #fff !important;
}
.stTextInput input:focus, .stTextArea textarea:focus, .stNumberInput input:focus {
  border-color: var(--accent) !important; box-shadow: var(--focus-ring) !important;
}

/* Selectbox / multiselect */
[data-baseweb="select"] > div {
  border-radius: 5px !important; border: 0.5px solid var(--separator) !important;
  box-shadow: var(--shadow-control);
}
[data-baseweb="tag"] { background: var(--accent-soft) !important; color: var(--accent) !important; border-radius: 5px !important; }

/* ── 라디오 · 세그먼트 느낌 ── */
[data-testid="stRadio"] label { color: var(--label-1); }
[data-testid="stRadio"] [role="radiogroup"] { gap: 4px; }

/* ── 슬라이더 · 토글 ── */
[data-testid="stSlider"] [role="slider"] { box-shadow: var(--shadow-knob, var(--shadow-control)); }
.stSlider [data-baseweb="slider"] div[style*="background"] { background: var(--accent) !important; }

/* ── 사이드바 (반투명 머티리얼) ── */
[data-testid="stSidebar"] {
  background: rgba(246,246,246,0.80);
  backdrop-filter: blur(30px) saturate(180%);
  -webkit-backdrop-filter: blur(30px) saturate(180%);
  border-right: 0.5px solid var(--separator);
}
[data-testid="stSidebar"] .stButton > button { width: 100%; }

/* ── 확장 패널 = 카드 ── */
[data-testid="stExpander"] {
  border: 0.5px solid var(--hairline) !important; border-radius: var(--radius-lg) !important;
  box-shadow: var(--shadow-card); background: var(--surface-content); overflow: hidden;
}
[data-testid="stExpander"] summary { font-weight: 500; font-size: 14px; }
[data-testid="stExpander"] summary:hover { color: var(--accent); }

/* ── 메트릭 = 스탯 카드 ── */
[data-testid="stMetric"] {
  background: var(--section-fill); border-radius: var(--radius-lg);
  border: 0.5px solid var(--hairline); padding: 12px 16px;
}
[data-testid="stMetricValue"] { font-weight: 600; letter-spacing: -0.02em; }
[data-testid="stMetricLabel"] { color: var(--label-2); }

/* ── 알림(info/success/warning/error) — 소프트 톤 ── */
[data-testid="stAlert"] { border-radius: var(--radius-lg); border: 0.5px solid var(--hairline); }

/* ── 데이터프레임 ── */
[data-testid="stDataFrame"] { border-radius: var(--radius-lg); overflow: hidden; border: 0.5px solid var(--hairline); }

/* ── 탭 ── */
.stTabs [data-baseweb="tab-list"] { gap: 2px; border-bottom: 0.5px solid var(--separator); }
.stTabs [data-baseweb="tab"] { font-weight: 500; }
.stTabs [aria-selected="true"] { color: var(--accent) !important; }

/* ── 진행바 ── */
.stProgress > div > div > div { background: var(--accent) !important; }

/* ── 구분선 ── */
hr, [data-testid="stDivider"] { border-color: var(--separator) !important; }

/* ── 다운로드 버튼 액센트 힌트 ── */
.stDownloadButton > button { color: var(--accent); font-weight: 500; }

/* 코드/모노 (글자수 등) */
code, .stCode { font-family: var(--font-mono); }

/* 상단 여백 정리 */
[data-testid="stToolbar"] { display: none; }
</style>
"""


def apply_theme() -> None:
    """디자인 시스템 CSS를 주입한다. set_page_config 직후 1회 호출한다."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_brand_header() -> None:
    """'생' 라운드 로고 타일 + 워드마크 헤더를 렌더한다 (디자인 시스템 브랜드 규칙)."""
    st.markdown(
        '<div class="lp-brand">'
        '<span class="lp-logo">생</span>'
        '<span class="lp-title">생기부 도우미</span>'
        "</div>",
        unsafe_allow_html=True,
    )
