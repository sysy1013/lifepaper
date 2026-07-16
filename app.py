# -*- coding: utf-8 -*-
"""
생기부(학교생활기록부) 기재 금지 표현 심사 + 세특 초안 작성 도우미

[검토 모드]
- 규칙 기반 고속 필터링 + Gemini 문맥 심사 (LLM-as-a-Judge)
- 단일/일괄 검토 (반 전체 요약표), 위반 표현 하이라이트, 대체 표현 추천
- NEIS 항목별 글자 수 제한 검사, 문체·상투 표현 점검, 학생 간 유사도 검사
- 검토 결과를 반영한 수정본 자동 생성, 수석교사 루브릭 품질 진단

[초안 작성 모드]
- 희망 진로 + 과목명 + 수행평가 진행 내용 입력
- 학생 자기평가서 파일(.hwp/.hwpx/.txt) 단일/일괄 업로드
- Gemini가 기재요령을 준수하는 세특 초안 생성 (일괄 생성 + ZIP/CSV 다운로드)
- 피드백 반영 재생성, 품질 진단, 초안 간 유사도 검사
"""

import hmac
import io
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import streamlit as st

from core.formatting import build_student_report, findings_to_df, highlight_text
from core.gemini import (
    GEMINI_MODEL,
    adjust_length_with_gemini,
    assess_quality_with_gemini,
    generate_draft_with_gemini,
    proofread_with_gemini,
    quality_avg,
    refine_draft_with_gemini,
    review_text_masked,
    rewrite_with_gemini,
)
from core.masking import apply_mask, build_mask_map, remove_mask
from core.parsing import file_stem, read_uploaded_file, unique_names
from core.rules import (
    NEIS_LIMITS,
    find_similar_pairs,
    parse_custom_words,
    rule_based_filter,
    style_check,
)

# ──────────────────────────────────────────────
# 페이지 기본 설정
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="생기부 도우미",
    page_icon="📋",
    layout="wide",
)

# 일괄 처리 동시 호출 수 (무료 쿼터에서도 429는 재시도로 흡수)
BATCH_WORKERS = 4


def record_history(label: str, content: str) -> None:
    """세션 이력에 결과를 기록한다 (최대 20건, 오래된 것부터 삭제)."""
    hist = st.session_state.setdefault("history", [])
    hist.append(
        {
            "time": datetime.now().strftime("%H:%M:%S"),
            "label": label,
            "content": content,
        }
    )
    del hist[:-20]


def run_parallel(n: int, job, label: str) -> list:
    """job(idx)를 BATCH_WORKERS 동시 실행. 진행바 표시, 입력 순서대로 결과 반환."""
    results: list = [None] * n
    if n == 0:
        return results
    progress = st.progress(0.0, text=label)
    with ThreadPoolExecutor(max_workers=BATCH_WORKERS) as pool:
        futures = {pool.submit(job, i): i for i in range(n)}
        done = 0
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
            done += 1
            progress.progress(done / n, text=f"{label} ({done}/{n})")
    progress.empty()
    return results


# ──────────────────────────────────────────────
# 문체·상투 표현 점검 결과 표시
# ──────────────────────────────────────────────
def render_style_warnings(text: str) -> None:
    """문체 점검 결과를 표시한다."""
    warnings = style_check(text)
    if warnings:
        with st.expander(f"📝 문체·표현 점검 — 권장 사항 {len(warnings)}건", expanded=True):
            for w in warnings:
                st.warning(w)
    else:
        st.caption("📝 문체·표현 점검: 특이사항 없음 (개조식 어미·상투 표현 기준)")


# ──────────────────────────────────────────────
# 학생 간 유사도 검사 결과 표시
# ──────────────────────────────────────────────
def render_similarity_check(items: list[tuple[str, str]]) -> None:
    """학생 간 유사도 검사 결과를 표시한다."""
    if len(items) < 2:
        return
    pairs = find_similar_pairs(items)
    if pairs:
        st.error(
            f"🚨 **학생 간 유사도 경고 {len(pairs)}쌍** — 생기부에 동일·유사 문장이 "
            "여러 학생에게 반복 기재되면 감사 지적 대상입니다. 개별화가 필요합니다."
        )
        sim_df = pd.DataFrame(
            [{"학생 A": a, "학생 B": b, "유사도": f"{r * 100:.0f}%"} for a, b, r in pairs]
        )
        st.dataframe(sim_df, use_container_width=True, hide_index=True)
    else:
        st.success("✅ 학생 간 유사도 검사: 유사한 쌍이 발견되지 않았습니다.")


# ──────────────────────────────────────────────
# Gemini 세특 품질 진단 결과 표시 (수석교사 루브릭)
# ──────────────────────────────────────────────
def render_quality_result(q: dict) -> None:
    """품질 진단 결과(루브릭 점수 + 총평 + 개선 제안)를 렌더링한다."""
    scores = q.get("scores", [])
    if scores:
        cols = st.columns(len(scores))
        for col, s in zip(cols, scores):
            col.metric(str(s.get("criterion", "")), f"{s.get('score', '-')}/5")
        avg = sum(float(s.get("score", 0)) for s in scores) / len(scores)
        st.progress(min(avg / 5, 1.0), text=f"종합 {avg:.1f} / 5.0")
        with st.expander("기준별 세부 코멘트"):
            for s in scores:
                st.markdown(
                    f"- **{s.get('criterion', '')} ({s.get('score', '-')}/5)**: {s.get('comment', '')}"
                )
    if q.get("overall"):
        st.markdown(f"**🧑‍🏫 총평**: {q['overall']}")
    improvements = q.get("improvements", [])
    if improvements:
        st.markdown("**✅ 개선 제안**")
        for i, imp in enumerate(improvements, 1):
            st.markdown(f"{i}. {imp}")


def render_quality_compact(q: dict) -> None:
    """expander 내부용 품질 진단 결과 (중첩 expander 없이 한 덩어리로)."""
    scores = q.get("scores", [])
    if scores:
        avg = sum(float(s.get("score", 0)) for s in scores) / len(scores)
        st.markdown(f"**🏅 품질 진단 — 종합 {avg:.1f} / 5.0**")
        st.markdown(
            " · ".join(
                f"{s.get('criterion', '')} **{s.get('score', '-')}/5**" for s in scores
            )
        )
    if q.get("overall"):
        st.markdown(f"🧑‍🏫 {q['overall']}")
    improvements = q.get("improvements", [])
    if improvements:
        st.markdown("개선 제안: " + " / ".join(str(i) for i in improvements))


def render_quality_block(text: str, major: str, api_key: str, state_key: str) -> None:
    """품질 진단 실행 버튼 + 결과 표시 블록."""
    st.subheader("🏅 세특 품질 진단 (수석교사 루브릭)")
    st.caption("구체성 · 개별성 · 탐구 과정 · 성장·변화 · 진로 연계 5개 기준으로 평가합니다.")
    if st.button("품질 진단 실행", key=f"btn_{state_key}", use_container_width=True):
        if not api_key.strip():
            st.warning("⚠️ 서버에 Gemini API Key가 설정되지 않아 실행할 수 없습니다.")
        else:
            with st.spinner(f"수석교사 루브릭 평가 중… ({GEMINI_MODEL})"):
                try:
                    st.session_state[state_key] = assess_quality_with_gemini(
                        text, major, api_key
                    )
                except Exception as e:
                    st.error(f"❌ 품질 진단 실패: {e}")
    q = st.session_state.get(state_key)
    if q:
        render_quality_result(q)


# ──────────────────────────────────────────────
# Gemini 오탈자·맞춤법 검사 결과 표시
# ──────────────────────────────────────────────
def render_proofread_block(
    text: str, api_key: str, mask_map: list[tuple[str, str]], state_key: str
) -> None:
    """오탈자 검사 실행 버튼 + 결과(표·하이라이트·교정 반영본) 표시 블록."""
    st.subheader("🔤 오탈자·맞춤법 검사")
    st.caption("오탈자·맞춤법·띄어쓰기·조사 오용을 검사합니다. 개조식 어미는 오류로 보지 않습니다.")
    if st.button("오탈자 검사 실행", key=f"btn_{state_key}", use_container_width=True):
        if not api_key.strip():
            st.warning("⚠️ 서버에 Gemini API Key가 설정되지 않아 실행할 수 없습니다.")
        else:
            with st.spinner(f"오탈자 검사 중… ({GEMINI_MODEL})"):
                try:
                    items = proofread_with_gemini(apply_mask(text, mask_map), api_key)
                    for it in items:
                        it["wrong"] = remove_mask(it["wrong"], mask_map)
                        it["correct"] = remove_mask(it["correct"], mask_map)
                    st.session_state[state_key] = items
                except Exception as e:
                    st.error(f"❌ 오탈자 검사 실패: {e}")

    items = st.session_state.get(state_key)
    if items is None:
        return
    if not items:
        st.success("✅ 발견된 오탈자가 없습니다.")
        return

    st.warning(f"오탈자·표기 오류 **{len(items)}건** 발견")
    df = pd.DataFrame(items).rename(
        columns={"wrong": "잘못된 표기", "correct": "교정안", "reason": "사유"}
    )
    st.dataframe(
        df[["잘못된 표기", "교정안", "사유"]], use_container_width=True, hide_index=True
    )

    # 원문에서 실제로 위치를 찾은 항목만 하이라이트·자동 교정에 사용
    found = [it for it in items if it["wrong"] and it["wrong"] in text]
    if found:
        render_highlight_box(text, [it["wrong"] for it in found])
        corrected = text
        for it in found:
            corrected = corrected.replace(it["wrong"], it["correct"])
        st.text_area(
            "교정 반영본 (복사해서 사용하세요)",
            value=corrected,
            height=200,
            key=f"ta_{state_key}",
        )
    missing = len(items) - len(found)
    if missing:
        st.caption(f"ℹ️ {missing}건은 원문에서 정확한 위치를 찾지 못해 표로만 표시했습니다.")


# ──────────────────────────────────────────────
# Gemini 분량 조절 실행 UI (줄이기/늘리기)
# ──────────────────────────────────────────────
def render_length_adjuster(
    state_key: str,
    api_key: str,
    mask_map: list[tuple[str, str]],
    default_len: int,
    widget_key: str,
    clear_keys: tuple[str, ...] = (),
) -> None:
    """세션 상태 state_key에 저장된 본문의 분량을 글자 수 또는 바이트 기준으로 조절한다."""
    text = st.session_state.get(state_key, "")
    if not text:
        return
    cur_chars = len(text)
    cur_bytes = len(text.encode("utf-8"))

    st.markdown("**📏 분량 조절 (줄이기/늘리기)**")
    unit = st.radio(
        "조절 기준",
        ["글자 수 (공백 포함)", "바이트 (UTF-8 · 한글 3byte)"],
        horizontal=True,
        key=f"unit_{widget_key}",
    )
    by_bytes = unit.startswith("바이트")

    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    if by_bytes:
        target = c1.number_input(
            "목표 바이트",
            min_value=300,
            max_value=9000,
            value=min(max((default_len or cur_chars) * 3, 300), 9000),
            step=30,
            key=f"len_b_{widget_key}",
        )
    else:
        target = c1.number_input(
            "목표 글자 수 (공백 포함)",
            min_value=100,
            max_value=3000,
            value=min(max(default_len or cur_chars, 100), 3000),
            step=10,
            key=f"len_{widget_key}",
        )

    if c2.button("✂️ 분량 맞추기", key=f"adj_{widget_key}", use_container_width=True):
        if not api_key.strip():
            st.warning("⚠️ 서버에 Gemini API Key가 설정되지 않아 실행할 수 없습니다.")
        else:
            if by_bytes:
                # Gemini는 바이트를 직접 세지 못하므로 현재 본문의 글자당 바이트 비율로 환산
                char_target = max(
                    int(round(int(target) * cur_chars / max(cur_bytes, 1))), 50
                )
            else:
                char_target = int(target)
            with st.spinner(f"분량 조절 중… ({GEMINI_MODEL})"):
                try:
                    adjusted = adjust_length_with_gemini(
                        apply_mask(text, mask_map), char_target, api_key
                    )
                    st.session_state[state_key] = remove_mask(adjusted, mask_map)
                    for k in clear_keys:
                        st.session_state.pop(k, None)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 분량 조절 실패: {e}")

    if by_bytes:
        st.caption(
            f"현재 **{cur_bytes:,} byte** ({cur_chars:,}자) → 목표 {int(target):,} byte. "
            "조절하면 본문이 새 버전으로 교체됩니다 (사실 추가 없이 줄이기/풀어쓰기)."
        )
    else:
        st.caption(
            f"현재 **{cur_chars:,}자** ({cur_bytes:,} byte) → 목표 {int(target):,}자. "
            "조절하면 본문이 새 버전으로 교체됩니다 (사실 추가 없이 줄이기/풀어쓰기)."
        )


# ──────────────────────────────────────────────
# 하이라이트 렌더링 / 분량 표시
# ──────────────────────────────────────────────
def render_highlight_box(text: str, words: list[str]) -> None:
    st.markdown(
        f'<div style="border: 1px solid #ddd; border-radius: 8px; '
        f'padding: 16px; line-height: 1.8; background-color: #fafafa; color: #222;">'
        f"{highlight_text(text, words)}</div>",
        unsafe_allow_html=True,
    )


def render_length_metrics(text: str, limit: int) -> None:
    """글자 수·바이트 수 지표와 NEIS 제한 초과 여부를 표시한다."""
    char_with_space = len(text)
    char_no_space = len(re.sub(r"\s", "", text))
    nbytes = len(text.encode("utf-8"))

    c1, c2, c3 = st.columns(3)
    c1.metric("글자 수 (공백 포함)", f"{char_with_space:,}자")
    c2.metric("글자 수 (공백 제외)", f"{char_no_space:,}자")
    c3.metric("바이트 (NEIS 기준·한글 3byte)", f"{nbytes:,} byte")

    if limit > 0:
        if char_with_space > limit:
            st.error(
                f"🚨 NEIS 제한 초과: {char_with_space:,}자 / {limit:,}자 "
                f"(**{char_with_space - limit:,}자 초과**)"
            )
        else:
            st.success(f"✅ NEIS 제한 이내: {char_with_space:,}자 / {limit:,}자")


def show_review_output(text: str, findings: list[dict], csv_name: str = "생기부_심사결과.csv") -> None:
    """하이라이트 + 결과 테이블 + CSV 다운로드를 렌더링한다."""
    if not findings:
        st.success("🎉 검출된 기재 금지 표현이 없습니다!")
        return

    n_violation = sum(1 for f in findings if f.get("severity", "위반") == "위반")
    n_caution = len(findings) - n_violation
    st.info(
        f"총 **{len(findings)}건** 검출 — 🚨 위반 **{n_violation}건**, "
        f"⚠️ 주의 **{n_caution}건**"
    )

    render_highlight_box(text, [f["word"] for f in findings])

    st.subheader("3️⃣ 위반 사유 및 대체 표현 추천")
    df = findings_to_df(findings)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.download_button(
        "📥 심사 결과 CSV 다운로드",
        data=df.to_csv(index=False).encode("utf-8-sig"),
        file_name=csv_name,
        mime="text/csv",
    )


# ──────────────────────────────────────────────
# 접근 암호 게이트 (배포용)
# ──────────────────────────────────────────────
def check_password() -> bool:
    """secrets.toml의 APP_PASSWORD를 아는 사용자만 통과시킨다.

    암호가 설정되지 않은 경우(로컬 개발)는 게이트 없이 통과한다.
    """
    try:
        correct = str(st.secrets.get("APP_PASSWORD", ""))
    except Exception:
        correct = ""
    if not correct:
        return True
    if st.session_state.get("auth_ok"):
        return True

    st.title("📋 생기부 도우미")
    st.caption("본 도구는 승인된 사용자 전용입니다. 접근 암호를 입력해 주세요.")
    pw = st.text_input("접근 암호", type="password", key="app_password_input")
    if pw:
        # 타이밍 공격 방지를 위해 상수 시간 비교 사용
        if hmac.compare_digest(pw, correct):
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("❌ 암호가 올바르지 않습니다.")
    return False


if not check_password():
    st.stop()


# ──────────────────────────────────────────────
# UI 구성
# ──────────────────────────────────────────────
st.title("📋 생기부 도우미")
st.caption(
    "교육부 「학교생활기록부 기재요령」 기준으로 기재 금지 표현을 검출·수정하고, "
    "수행평가 자료와 학생 자기평가서를 활용해 세특 초안을 작성·진단합니다."
)

# secrets.toml(.gitignore 처리됨)에 저장된 키가 있으면 자동 사용
try:
    DEFAULT_API_KEY = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    DEFAULT_API_KEY = ""

# ── Sidebar ──
with st.sidebar:
    st.header("⚙️ 설정")
    # API 키는 서버 secrets에서만 읽는다. 화면에 입력란·키 관련 표시를 하지 않는다.
    api_key = DEFAULT_API_KEY
    if not api_key:
        st.error(
            "⚠️ 서버에 Gemini API Key가 설정되지 않았습니다. "
            ".streamlit/secrets.toml(또는 배포 플랫폼 Secrets)에 GEMINI_API_KEY를 등록하세요."
        )

    st.caption(
        "🔒 입력한 생기부·자기평가서 텍스트는 **Google Gemini API(외부 서버)로 전송**됩니다. "
        "학생 이름·학번 등 개인정보는 아래 마스킹 목록에 등록하세요."
    )
    mask_words_raw = st.text_area(
        "개인정보 마스킹 단어 (선택)",
        height=68,
        placeholder="쉼표 또는 줄바꿈으로 구분\n예) 홍길동, 20301",
        help="등록한 단어는 Gemini API 전송 전 《비공개n》 토큰으로 치환되고, "
        "결과에서 원래 단어로 자동 복원됩니다.",
    )
    mask_map = build_mask_map(parse_custom_words(mask_words_raw))

    major = st.text_input(
        "학생의 희망 진로/학과",
        placeholder="예: 의예과, 컴퓨터공학과, 교육학과 …",
        help="검토 시 대체 표현 추천, 초안 작성 시 진로 연계 서술에 활용됩니다.",
    )

    neis_item = st.selectbox(
        "생기부 항목 (NEIS 글자 수 제한)",
        list(NEIS_LIMITS.keys()),
        help="선택한 항목의 글자 수 제한을 기준으로 분량 초과 여부를 검사합니다.",
    )
    neis_limit = NEIS_LIMITS[neis_item]

    custom_words_raw = st.text_area(
        "사용자 정의 금칙어 (선택)",
        height=90,
        placeholder="쉼표 또는 줄바꿈으로 구분\n예) 홍성여고, ○○학원, △△대학교",
        help="학교명 등 반드시 걸러야 할 단어를 등록하면 규칙 기반 검출에 추가됩니다.",
    )
    custom_words = parse_custom_words(custom_words_raw)

    st.divider()
    st.markdown(
        "**심사 기준 (8가지)**\n"
        "1. 상업적 명칭/브랜드\n"
        "2. 교외상/대회\n"
        "3. 특정 대학/기관/강사명\n"
        "4. 부모 직업/지위 암시\n"
        "5. 해외 활동\n"
        "6. 논문/출판/특허\n"
        "7. 장학생 관련 내용\n"
        "8. 재학 중인 학교 명칭\n\n"
        "➕ 규칙 기반 검출: 성적·어학시험, 모의고사, 학교명, 주요 대학명, "
        "브랜드명, 사용자 정의 금칙어\n\n"
        "➕ 문체 점검: 개조식 어미, 어미 반복, 상투적 표현\n\n"
        "➕ 유사도 검사: 학생 간 동일·유사 문장"
    )

    # 세션 작업 이력
    # 참고: 사이드바는 모드 본문보다 먼저 렌더링되므로, 이번 실행에서 기록된
    # 이력은 다음 rerun 때 이 목록에 나타난다 (의도된 동작).
    st.divider()
    with st.expander("🕘 이번 세션 작업 이력", expanded=False):
        history = st.session_state.get("history", [])
        if not history:
            st.caption("아직 기록이 없습니다.")
        else:
            for i, h in enumerate(reversed(history)):
                st.caption(f"{h['time']} — {h['label']}")
                if h["content"]:
                    safe_name = re.sub(r'[\\/:*?"<>|]', "_", h["label"])
                    st.download_button(
                        "TXT",
                        data=h["content"].encode("utf-8"),
                        file_name=f"{safe_name}.txt",
                        mime="text/plain",
                        key=f"hist_{i}",
                    )

# ── 모드 선택 ──
mode = st.radio(
    "모드를 선택하세요.",
    ["🔍 기재 금지 표현 검토", "✍️ 세특 초안 작성"],
    horizontal=True,
)

st.divider()

# ══════════════════════════════════════════════
# 모드 1: 기재 금지 표현 검토
# ══════════════════════════════════════════════
if mode == "🔍 기재 금지 표현 검토":
    st.subheader("1️⃣ 생기부 텍스트 입력")

    input_method = st.radio(
        "입력 방식을 선택하세요.",
        ["파일 업로드 (.txt / .hwp / .hwpx — 여러 개 가능)", "직접 붙여넣기"],
        horizontal=True,
    )

    input_text = ""
    review_files = []

    if input_method.startswith("파일 업로드"):
        review_files = st.file_uploader(
            "생기부 파일을 업로드하세요. 여러 개 올리면 반 전체 일괄 검토가 됩니다.",
            type=["txt", "hwp", "hwpx"],
            accept_multiple_files=True,
            key="review_files",
        )
        if len(review_files) == 1:
            try:
                input_text = read_uploaded_file(review_files[0])
                st.success(f"✅ 파일 로드 완료 ({len(input_text):,}자)")
                with st.expander("업로드된 원문 미리보기"):
                    st.text(input_text[:2000] + ("…" if len(input_text) > 2000 else ""))
            except Exception as e:
                st.error(f"❌ 파일에서 텍스트를 추출하지 못했습니다: {e}")
        elif len(review_files) > 1:
            st.info(f"📚 파일 **{len(review_files)}개** 업로드됨 — 반 전체 일괄 검토 모드")
    else:
        input_text = st.text_area(
            "생기부 내용을 붙여넣으세요.",
            height=250,
            placeholder="예) 의사인 아버지의 영향을 받아 TOEIC 900점을 취득하였으며 …",
        )

    run = st.button("🔍 검토 실행", type="primary", use_container_width=True)

    if run:
        if not api_key.strip():
            st.warning("⚠️ 서버에 Gemini API Key가 설정되지 않아 실행할 수 없습니다.")
            st.stop()

        if len(review_files) > 1:
            # ── 일괄 검토 ──
            st.session_state.pop("review_result", None)
            st.session_state.pop("revised_text", None)
            st.session_state.pop("quality_review", None)
            st.session_state.pop("proofread_review", None)

            stems = unique_names([file_stem(f.name) for f in review_files])

            # 파일 읽기는 메인 스레드에서 (UploadedFile은 스레드 안전하지 않음)
            inputs: list[tuple[str, str, str]] = []
            for f, stem in zip(review_files, stems):
                try:
                    inputs.append((stem, read_uploaded_file(f), ""))
                except Exception as e:
                    inputs.append((stem, "", str(e)))

            def _review_job(idx: int) -> dict:
                stem, text, err = inputs[idx]
                if err:
                    return {"name": stem, "text": "", "findings": [], "error": err}
                try:
                    findings = review_text_masked(
                        text, major, api_key, custom_words, mask_map
                    )
                    return {"name": stem, "text": text, "findings": findings, "error": ""}
                except Exception as e:
                    return {"name": stem, "text": "", "findings": [], "error": str(e)}

            st.session_state["batch_review"] = run_parallel(
                len(inputs), _review_job, "일괄 검토 중…"
            )
            record_history(f"일괄 검토 {len(inputs)}건", "")
        else:
            # ── 단일 검토 ──
            if not input_text.strip():
                st.warning("⚠️ 심사할 텍스트를 먼저 입력해 주세요.")
                st.stop()

            st.session_state.pop("batch_review", None)
            st.session_state.pop("quality_review", None)
            st.session_state.pop("revised_text", None)
            st.session_state.pop("proofread_review", None)

            with st.spinner(f"규칙 기반 필터링 + Gemini 문맥 심사 중… ({GEMINI_MODEL})"):
                try:
                    findings = review_text_masked(
                        input_text, major, api_key, custom_words, mask_map
                    )
                    st.session_state["review_result"] = {
                        "text": input_text,
                        "findings": findings,
                    }
                    record_history(f"검토: {len(findings)}건 검출", input_text)
                except json.JSONDecodeError:
                    st.error("❌ Gemini 응답을 JSON으로 파싱하지 못했습니다. 다시 시도해 주세요.")
                except Exception as e:
                    st.error(f"❌ Gemini API 호출 실패: {e}")

    # ── 검토 실행 전에도 입력 텍스트만으로 오탈자 검사 가능 ──
    if input_text.strip() and not st.session_state.get("review_result"):
        st.divider()
        render_proofread_block(input_text, api_key, mask_map, "proofread_review")

    # ── 단일 검토 결과 렌더링 ──
    review = st.session_state.get("review_result")
    if review:
        st.divider()
        st.subheader("2️⃣ 위반 표현 하이라이트 및 분량 검사")

        render_length_metrics(review["text"], neis_limit)
        render_style_warnings(review["text"])
        show_review_output(review["text"], review["findings"])

        if review["findings"]:
            st.subheader("4️⃣ 수정본 자동 생성")
            if st.button("✏️ 검토 결과를 반영한 수정본 생성", use_container_width=True):
                with st.spinner(f"수정본 생성 중… ({GEMINI_MODEL})"):
                    try:
                        masked_findings = [
                            {**f, "word": apply_mask(f["word"], mask_map)}
                            for f in review["findings"]
                        ]
                        revised = rewrite_with_gemini(
                            apply_mask(review["text"], mask_map),
                            masked_findings,
                            major,
                            api_key,
                        )
                        st.session_state["revised_text"] = remove_mask(
                            revised, mask_map
                        )
                        record_history("수정본", st.session_state["revised_text"])
                    except Exception as e:
                        st.error(f"❌ Gemini API 호출 실패: {e}")

            revised = st.session_state.get("revised_text", "")
            if revised:
                st.text_area("수정본 (복사해서 사용하세요)", value=revised, height=250)
                render_length_metrics(revised, neis_limit)
                render_length_adjuster(
                    "revised_text", api_key, mask_map, neis_limit, "revised"
                )

                recheck = rule_based_filter(revised, custom_words)
                if recheck:
                    st.warning(
                        "⚠️ 수정본에서 규칙 기반 금지 패턴이 여전히 검출됩니다: "
                        + ", ".join(f"「{f['word']}」" for f in recheck)
                    )
                else:
                    st.success("✅ 수정본에서 규칙 기반 금지 패턴이 검출되지 않았습니다.")

                st.download_button(
                    "📥 수정본 TXT 다운로드",
                    data=revised.encode("utf-8"),
                    file_name="생기부_수정본.txt",
                    mime="text/plain",
                )

        st.divider()
        render_quality_block(
            apply_mask(review["text"], mask_map), major, api_key, "quality_review"
        )

        st.divider()
        render_proofread_block(review["text"], api_key, mask_map, "proofread_review")

    # ── 일괄 검토 결과 렌더링 ──
    batch_review = st.session_state.get("batch_review")
    if batch_review:
        st.divider()
        st.subheader(f"2️⃣ 일괄 검토 결과 ({len(batch_review)}건)")

        failed = [b for b in batch_review if b["error"]]
        ok = [b for b in batch_review if not b["error"]]
        if failed:
            st.error("❌ 검토 실패: " + ", ".join(b["name"] for b in failed))

        # 반 전체 요약표
        summary_rows = []
        for b in ok:
            n_violation = sum(
                1 for f in b["findings"] if f.get("severity", "위반") == "위반"
            )
            n_caution = len(b["findings"]) - n_violation
            top_words = ", ".join(f["word"] for f in b["findings"][:3])
            chars = len(b["text"])
            row = {
                "학생(파일명)": b["name"],
                "글자 수": chars,
                "분량": (
                    f"{chars - neis_limit}자 초과"
                    if neis_limit and chars > neis_limit
                    else "이내"
                )
                if neis_limit
                else "-",
                "위반": n_violation,
                "주의": n_caution,
                "주요 검출 표현": top_words,
            }
            if b.get("quality"):
                avg = quality_avg(b["quality"])
                row["품질(5점)"] = f"{avg:.1f}" if avg is not None else "-"
            if b.get("proofread") is not None:
                row["오탈자"] = len(b["proofread"])
            summary_rows.append(row)
        if summary_rows:
            summary_df = pd.DataFrame(summary_rows)
            st.dataframe(summary_df, use_container_width=True, hide_index=True)
            st.download_button(
                "📥 반 전체 검토 요약 CSV 다운로드",
                data=summary_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="생기부_일괄검토_요약.csv",
                mime="text/csv",
            )

        # ── 일괄 후처리: 수정본 / 품질 진단 / 오탈자 ──
        st.subheader("3️⃣ 일괄 후처리")
        st.caption(
            "반 전체에 대해 수정본 생성·품질 진단·오탈자 검사를 병렬로 실행합니다. "
            "결과는 요약표와 학생별 상세에 반영됩니다."
        )
        targets = [b for b in ok if b["findings"]]
        col_rev, col_q, col_p = st.columns(3)

        if col_rev.button(
            f"✏️ 전체 수정본 생성 ({len(targets)}명)",
            use_container_width=True,
            disabled=not targets,
        ):
            def _revise_job(idx: int) -> tuple[str, str]:
                b = targets[idx]
                try:
                    masked_findings = [
                        {**f, "word": apply_mask(f["word"], mask_map)}
                        for f in b["findings"]
                    ]
                    revised = rewrite_with_gemini(
                        apply_mask(b["text"], mask_map), masked_findings, major, api_key
                    )
                    return remove_mask(revised, mask_map), ""
                except Exception as e:
                    return "", str(e)

            outs = run_parallel(len(targets), _revise_job, "수정본 생성 중…")
            for b, (revised, err) in zip(targets, outs):
                b["revised"], b["revised_error"] = revised, err
            st.rerun()

        if col_q.button(f"🏅 전체 품질 진단 ({len(ok)}명)", use_container_width=True):
            def _quality_job(idx: int) -> tuple[dict | None, str]:
                b = ok[idx]
                try:
                    return (
                        assess_quality_with_gemini(
                            apply_mask(b["text"], mask_map), major, api_key
                        ),
                        "",
                    )
                except Exception as e:
                    return None, str(e)

            outs = run_parallel(len(ok), _quality_job, "품질 진단 중…")
            for b, (q, err) in zip(ok, outs):
                b["quality"], b["quality_error"] = q, err
            st.rerun()

        if col_p.button(f"🔤 전체 오탈자 검사 ({len(ok)}명)", use_container_width=True):
            def _proofread_job(idx: int) -> tuple[list | None, str]:
                b = ok[idx]
                try:
                    items = proofread_with_gemini(
                        apply_mask(b["text"], mask_map), api_key
                    )
                    for it in items:
                        it["wrong"] = remove_mask(it["wrong"], mask_map)
                        it["correct"] = remove_mask(it["correct"], mask_map)
                    return items, ""
                except Exception as e:
                    return None, str(e)

            outs = run_parallel(len(ok), _proofread_job, "오탈자 검사 중…")
            for b, (items, err) in zip(ok, outs):
                b["proofread"], b["proofread_error"] = items, err
            st.rerun()

        post_errors = [
            f"{b['name']}({kind}): {b.get(key)}"
            for b in ok
            for kind, key in (
                ("수정본", "revised_error"),
                ("품질", "quality_error"),
                ("오탈자", "proofread_error"),
            )
            if b.get(key)
        ]
        if post_errors:
            st.error("❌ 일부 후처리 실패 — " + " / ".join(post_errors))

        revised_ok = [b for b in ok if b.get("revised")]
        if revised_ok:
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for b in revised_ok:
                    zf.writestr(f"{b['name']}_수정본.txt", b["revised"])
            st.download_button(
                "📥 전체 수정본 ZIP 다운로드",
                data=zip_buf.getvalue(),
                file_name="생기부_수정본_일괄.zip",
                mime="application/zip",
            )

        # 개인별 종합 리포트 ZIP (검출/수정본/품질/오탈자 등 실행된 결과 모두 포함)
        if ok:
            report_buf = io.BytesIO()
            with zipfile.ZipFile(report_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for b in ok:
                    zf.writestr(
                        f"{b['name']}_리포트.txt",
                        build_student_report(b, neis_limit),
                    )
            st.download_button(
                "📥 개인별 종합 리포트 ZIP 다운로드",
                data=report_buf.getvalue(),
                file_name="생기부_개인별_리포트.zip",
                mime="application/zip",
            )

        # 학생 간 유사도 검사
        st.subheader("👥 학생 간 유사도 검사")
        render_similarity_check([(b["name"], b["text"]) for b in ok])

        # 학생별 상세
        st.subheader("📄 학생별 상세 결과")
        for b in ok:
            n_found = len(b["findings"])
            label = f"📄 {b['name']} — {len(b['text']):,}자, 검출 {n_found}건"
            with st.expander(label):
                if b["findings"]:
                    render_highlight_box(b["text"], [f["word"] for f in b["findings"]])
                    st.dataframe(
                        findings_to_df(b["findings"]),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.success("검출된 기재 금지 표현이 없습니다.")
                for w in style_check(b["text"]):
                    st.warning(w)

                if b.get("revised"):
                    st.text_area(
                        "✏️ 수정본",
                        value=b["revised"],
                        height=180,
                        key=f"batch_revised_{b['name']}",
                    )
                    recheck = rule_based_filter(b["revised"], custom_words)
                    if recheck:
                        st.warning(
                            "⚠️ 수정본에서 규칙 기반 금지 패턴이 여전히 검출됩니다: "
                            + ", ".join(f"「{f['word']}」" for f in recheck)
                        )

                if b.get("quality"):
                    st.divider()
                    render_quality_compact(b["quality"])

                if b.get("proofread") is not None:
                    st.divider()
                    if not b["proofread"]:
                        st.success("🔤 발견된 오탈자가 없습니다.")
                    else:
                        st.markdown(f"**🔤 오탈자 {len(b['proofread'])}건**")
                        st.dataframe(
                            pd.DataFrame(b["proofread"]).rename(
                                columns={
                                    "wrong": "잘못된 표기",
                                    "correct": "교정안",
                                    "reason": "사유",
                                }
                            ),
                            use_container_width=True,
                            hide_index=True,
                        )

# ══════════════════════════════════════════════
# 모드 2: 세특 초안 작성
# ══════════════════════════════════════════════
else:
    st.subheader("1️⃣ 초안 작성 정보 입력")
    st.caption(
        "희망 진로/학과는 사이드바에서 입력합니다. "
        "자기평가서 파일을 **여러 개 올리면 학생별로 일괄 생성**되고, "
        "없으면 수행평가 내용을 기반으로 1건을 작성합니다."
    )

    subject = st.text_input("과목명", placeholder="예: 정보, 수학Ⅰ, 여행지리 …")

    performance_text = st.text_area(
        "수행평가 활동 내용 — 무엇을, 어떻게 진행했는지 적어주세요. (일괄 생성 시 모든 학생에게 공통 적용)",
        height=180,
        placeholder=(
            "예) 파이썬으로 학교 급식 잔반량 데이터를 분석하는 수행평가를 진행함.\n"
            "- 설문으로 데이터를 수집하고 표로 정리함\n"
            "- 분석 결과를 바탕으로 잔반 줄이기 캠페인 아이디어를 발표함"
        ),
    )

    eval_files = st.file_uploader(
        "학생 자기평가서 업로드 (선택, .hwp / .hwpx / .txt — 여러 개 선택 가능)",
        type=["hwp", "hwpx", "txt"],
        key="eval_files",
        accept_multiple_files=True,
        help="1개 업로드 시 단일 초안, 2개 이상 업로드 시 학생별 일괄 생성됩니다. "
        "파일명에 학번·이름을 넣어두면 결과 구분이 쉽습니다.",
    )

    # 단일 파일 미리보기
    single_eval_text = ""
    if len(eval_files) == 1:
        try:
            single_eval_text = read_uploaded_file(eval_files[0])
            if single_eval_text.strip():
                st.success(f"✅ 자기평가서 로드 완료 ({len(single_eval_text):,}자)")
                with st.expander("자기평가서 내용 미리보기"):
                    st.text(
                        single_eval_text[:2000]
                        + ("…" if len(single_eval_text) > 2000 else "")
                    )
            else:
                st.warning("⚠️ 파일에서 텍스트를 추출하지 못했습니다. 내용을 수행평가 입력란에 직접 붙여넣어 주세요.")
        except Exception as e:
            st.error(f"❌ 파일에서 텍스트를 추출하지 못했습니다: {e}")
    elif len(eval_files) > 1:
        st.info(f"📚 자기평가서 **{len(eval_files)}개** 업로드됨 — 학생별 일괄 생성 모드")

    default_len = neis_limit if neis_limit else 500
    target_len = st.slider(
        "목표 분량 (공백 포함 글자 수)",
        min_value=200,
        max_value=1500,
        value=min(max(default_len, 200), 1500),
        step=50,
        help="사이드바에서 선택한 NEIS 항목의 제한이 기본값으로 반영됩니다.",
    )

    gen = st.button("✍️ 초안 생성", type="primary", use_container_width=True)

    if gen:
        if not api_key.strip():
            st.warning("⚠️ 서버에 Gemini API Key가 설정되지 않아 실행할 수 없습니다.")
            st.stop()
        if not major.strip() and len(eval_files) <= 1:
            st.warning("⚠️ 사이드바에 학생의 희망 진로/학과를 입력해 주세요. (자기평가서에 장래희망이 있다면 그대로 진행됩니다)")
        if not performance_text.strip() and not eval_files:
            st.warning("⚠️ 수행평가 활동 내용을 입력하거나 자기평가서 파일을 업로드해 주세요.")
            st.stop()

        st.session_state.pop("draft_text", None)
        st.session_state.pop("batch_drafts", None)
        st.session_state.pop("quality_draft", None)
        st.session_state.pop("proofread_draft", None)

        if len(eval_files) > 1:
            # ── 일괄 생성 ──
            stems = unique_names([file_stem(f.name) for f in eval_files])

            # 파일 읽기는 메인 스레드에서 (UploadedFile은 스레드 안전하지 않음)
            inputs: list[tuple[str, str, str]] = []
            for f, stem in zip(eval_files, stems):
                try:
                    inputs.append((stem, read_uploaded_file(f), ""))
                except Exception as e:
                    inputs.append((stem, "", str(e)))

            masked_performance = apply_mask(performance_text, mask_map)

            def _draft_job(idx: int) -> dict:
                stem, eval_text, err = inputs[idx]
                if err:
                    return {"name": stem, "draft": "", "error": err}
                try:
                    draft = generate_draft_with_gemini(
                        subject,
                        major,
                        masked_performance,
                        apply_mask(eval_text, mask_map),
                        target_len,
                        api_key,
                    )
                    return {"name": stem, "draft": remove_mask(draft, mask_map), "error": ""}
                except Exception as e:
                    return {"name": stem, "draft": "", "error": str(e)}

            st.session_state["batch_drafts"] = run_parallel(
                len(inputs), _draft_job, "일괄 초안 생성 중…"
            )
            record_history(f"일괄 초안 {len(inputs)}건", "")
        else:
            # ── 단일 생성 ──
            with st.spinner(f"세특 초안 생성 중… ({GEMINI_MODEL})"):
                try:
                    draft = generate_draft_with_gemini(
                        subject,
                        major,
                        apply_mask(performance_text, mask_map),
                        apply_mask(single_eval_text, mask_map),
                        target_len,
                        api_key,
                    )
                    st.session_state["draft_text"] = remove_mask(draft, mask_map)
                    record_history(
                        f"초안: {subject or '무제'}", st.session_state["draft_text"]
                    )
                    # 재생성 시 원자료 맥락 전달용 (마스킹된 상태로 보관)
                    st.session_state["draft_context"] = {
                        "subject": subject.strip(),
                        "performance": apply_mask(performance_text, mask_map),
                        "self_eval": apply_mask(single_eval_text, mask_map),
                    }
                except Exception as e:
                    st.error(f"❌ Gemini API 호출 실패: {e}")

    # ── 단일 초안 결과 ──
    draft = st.session_state.get("draft_text", "")
    if draft:
        st.divider()
        st.subheader("2️⃣ 생성된 세특 초안")

        st.text_area("초안 (복사해서 사용하세요)", value=draft, height=280)
        render_length_metrics(draft, neis_limit)
        render_length_adjuster(
            "draft_text",
            api_key,
            mask_map,
            target_len,
            "draft",
            clear_keys=("quality_draft", "proofread_draft"),
        )
        render_style_warnings(draft)

        draft_findings = rule_based_filter(draft, custom_words)
        if draft_findings:
            st.warning(
                "⚠️ 초안에서 규칙 기반 금지 패턴이 검출되었습니다: "
                + ", ".join(f"「{f['word']}」" for f in draft_findings)
            )

        st.download_button(
            "📥 초안 TXT 다운로드",
            data=draft.encode("utf-8"),
            file_name="세특_초안.txt",
            mime="text/plain",
        )

        # ── 피드백 반영 재생성 ──
        st.subheader("3️⃣ 피드백 반영 재생성")
        feedback = st.text_area(
            "초안에서 고치고 싶은 점을 적어주세요.",
            height=100,
            placeholder=(
                "예) 탐구 과정을 더 구체적으로 써줘 / 진로 연계를 줄이고 과목 역량 중심으로 / "
                "마지막 문장을 후속 탐구 계획으로 마무리해줘"
            ),
            key="draft_feedback",
        )
        if st.button("🔁 피드백 반영하여 다시 쓰기", use_container_width=True):
            if not feedback.strip():
                st.warning("⚠️ 반영할 피드백을 먼저 입력해 주세요.")
            else:
                with st.spinner(f"피드백 반영 재생성 중… ({GEMINI_MODEL})"):
                    try:
                        refined = refine_draft_with_gemini(
                            apply_mask(draft, mask_map),
                            apply_mask(feedback, mask_map),
                            target_len,
                            api_key,
                            context=st.session_state.get("draft_context"),
                        )
                        st.session_state["draft_text"] = remove_mask(refined, mask_map)
                        record_history(
                            "초안(피드백 반영)", st.session_state["draft_text"]
                        )
                        st.session_state.pop("quality_draft", None)
                        st.session_state.pop("proofread_draft", None)
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Gemini API 호출 실패: {e}")

        st.divider()
        render_quality_block(
            apply_mask(draft, mask_map), major, api_key, "quality_draft"
        )

        st.divider()
        render_proofread_block(draft, api_key, mask_map, "proofread_draft")

        st.info(
            "💡 완성된 초안은 **🔍 기재 금지 표현 검토** 모드에 붙여넣으면 "
            "Gemini 문맥 심사까지 한 번 더 확인할 수 있습니다."
        )

    # ── 일괄 초안 결과 ──
    batch = st.session_state.get("batch_drafts")
    if batch:
        st.divider()
        st.subheader(f"2️⃣ 일괄 생성 결과 ({len(batch)}건)")

        ok = [b for b in batch if not b["error"]]
        failed = [b for b in batch if b["error"]]
        if failed:
            st.error(
                f"❌ {len(failed)}건 생성 실패: "
                + ", ".join(b["name"] for b in failed)
            )

        # ── 일괄 후처리: 품질 진단 / 오탈자 ──
        col_q, col_p = st.columns(2)
        if col_q.button(f"🏅 전체 품질 진단 ({len(ok)}명)", use_container_width=True):
            def _dq_job(idx: int) -> tuple[dict | None, str]:
                b = ok[idx]
                try:
                    return (
                        assess_quality_with_gemini(
                            apply_mask(b["draft"], mask_map), major, api_key
                        ),
                        "",
                    )
                except Exception as e:
                    return None, str(e)

            outs = run_parallel(len(ok), _dq_job, "품질 진단 중…")
            for b, (q, err) in zip(ok, outs):
                b["quality"], b["quality_error"] = q, err
            st.rerun()

        if col_p.button(f"🔤 전체 오탈자 검사 ({len(ok)}명)", use_container_width=True):
            def _dp_job(idx: int) -> tuple[list | None, str]:
                b = ok[idx]
                try:
                    items = proofread_with_gemini(
                        apply_mask(b["draft"], mask_map), api_key
                    )
                    for it in items:
                        it["wrong"] = remove_mask(it["wrong"], mask_map)
                        it["correct"] = remove_mask(it["correct"], mask_map)
                    return items, ""
                except Exception as e:
                    return None, str(e)

            outs = run_parallel(len(ok), _dp_job, "오탈자 검사 중…")
            for b, (items, err) in zip(ok, outs):
                b["proofread"], b["proofread_error"] = items, err
            st.rerun()

        draft_post_errors = [
            f"{b['name']}({kind}): {b.get(key)}"
            for b in ok
            for kind, key in (("품질", "quality_error"), ("오탈자", "proofread_error"))
            if b.get(key)
        ]
        if draft_post_errors:
            st.error("❌ 일부 후처리 실패 — " + " / ".join(draft_post_errors))

        # 초안 간 유사도 검사 (같은 수행평가 기반이라 문장이 겹치기 쉬움)
        st.subheader("👥 초안 간 유사도 검사")
        render_similarity_check([(b["name"], b["draft"]) for b in ok])

        st.subheader("📄 학생별 초안")
        for b in ok:
            over = len(b["draft"]) > neis_limit if neis_limit else False
            label = f"📄 {b['name']} — {len(b['draft']):,}자" + (" 🚨 분량 초과" if over else "")
            with st.expander(label):
                st.text_area(
                    "초안", value=b["draft"], height=200, key=f"batch_{b['name']}"
                )
                findings = rule_based_filter(b["draft"], custom_words)
                if findings:
                    st.warning(
                        "⚠️ 규칙 기반 금지 패턴 검출: "
                        + ", ".join(f"「{f['word']}」" for f in findings)
                    )
                for w in style_check(b["draft"]):
                    st.warning(w)

                if b.get("quality"):
                    st.divider()
                    render_quality_compact(b["quality"])

                if b.get("proofread") is not None:
                    st.divider()
                    if not b["proofread"]:
                        st.success("🔤 발견된 오탈자가 없습니다.")
                    else:
                        st.markdown(f"**🔤 오탈자 {len(b['proofread'])}건**")
                        st.dataframe(
                            pd.DataFrame(b["proofread"]).rename(
                                columns={
                                    "wrong": "잘못된 표기",
                                    "correct": "교정안",
                                    "reason": "사유",
                                }
                            ),
                            use_container_width=True,
                            hide_index=True,
                        )

        if ok:
            # ZIP 다운로드
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for b in ok:
                    zf.writestr(f"{b['name']}_세특초안.txt", b["draft"])
            st.download_button(
                "📥 전체 초안 ZIP 다운로드",
                data=zip_buf.getvalue(),
                file_name="세특_초안_일괄.zip",
                mime="application/zip",
            )

            # CSV(엑셀용) 다운로드
            batch_df = pd.DataFrame(
                [{"학생(파일명)": b["name"], "세특 초안": b["draft"], "글자 수": len(b["draft"])} for b in ok]
            )
            st.download_button(
                "📥 전체 초안 CSV 다운로드 (엑셀용)",
                data=batch_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="세특_초안_일괄.csv",
                mime="text/csv",
            )

st.divider()
st.caption(
    "⚠️ 본 도구의 결과는 참고용 초안/심사입니다. 최종 기재 내용은 반드시 "
    "당해 연도 교육부 「학교생활기록부 기재요령」 원문을 확인 후 교사가 직접 작성·확정하시기 바랍니다."
)
