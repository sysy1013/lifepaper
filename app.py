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

from core.formatting import (
    build_batch_workbook,
    build_neis_workbook,
    build_student_report,
    findings_to_df,
    highlight_text,
)
from core.gemini import (
    GEMINI_MODEL,
    adjust_length_with_gemini,
    assess_quality_with_gemini,
    category_for_neis_item,
    generate_draft_with_gemini,
    proofread_with_gemini,
    quality_avg,
    refine_draft_with_gemini,
    review_text_masked,
    rewrite_with_gemini,
)
from core.masking import (
    apply_mask,
    build_mask_map,
    remove_mask,
    suggest_mask_candidates,
)
from core.parsing import (
    build_eval_template,
    file_stem,
    guess_roster_columns,
    parse_eval_table,
    parse_roster_table,
    read_uploaded_file,
    unique_names,
)
from core.project import (
    PROJECT_KEYS,
    deserialize_project,
    replace_in_entries,
    serialize_project,
)
from core.rules import (
    NEIS_LIMITS,
    filter_ignored,
    find_similar_pairs,
    neis_bytes,
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


def export_project() -> str:
    """현재 세션의 작업 상태를 프로젝트 JSON 문자열로 직렬화한다."""
    data = {k: st.session_state.get(k) for k in PROJECT_KEYS if k in st.session_state}
    return serialize_project(data)


def import_project(raw: bytes) -> str:
    """프로젝트 JSON을 검증해 세션에 복원한다. 성공 시 '' 또는 오류 메시지 반환.

    on_click 콜백에서 호출되므로(콜백은 스크립트 재실행 전에 실행된다) 위젯 key인
    'mask_words_raw'도 위젯 생성 전에 안전하게 되돌릴 수 있다.
    """
    data, err = deserialize_project(raw)
    if err:
        return err
    for k, v in data.items():
        st.session_state[k] = v
    return ""


def _restore_project() -> None:
    """복원 실행 버튼 콜백 — 업로드된 파일을 읽어 세션에 복원한다."""
    up = st.session_state.get("proj_file")
    if up is None:
        st.session_state["proj_msg"] = "❌ 불러올 프로젝트 파일을 먼저 선택해 주세요."
        return
    err = import_project(up.getvalue())
    st.session_state["proj_msg"] = err if err else "✅ 프로젝트를 복원했습니다."


def render_replace_control(state_key: str, field: str, widget_key: str) -> None:
    """일괄 텍스트 치환 UI. state_key의 항목 목록 중 field 문자열을 치환한다."""
    entries = st.session_state.get(state_key)
    if not entries:
        return
    with st.expander("🔁 텍스트 일괄 치환"):
        find = st.text_input("찾을 문구", key=f"find_{widget_key}")
        repl = st.text_input("바꿀 문구", key=f"repl_{widget_key}")
        if find:
            _, n_match = replace_in_entries(entries, field, find, repl)
            st.caption(f"총 매치 **{n_match}건** (치환 적용 시 교체됩니다)")
        if st.button("치환 적용", key=f"apply_{widget_key}", use_container_width=True):
            if not find:
                st.warning("⚠️ 찾을 문구를 먼저 입력해 주세요.")
            else:
                new_entries, n = replace_in_entries(entries, field, find, repl)
                st.session_state[state_key] = new_entries
                record_history(f"일괄 치환: '{find}'→'{repl}' {n}건", "")
                st.rerun()


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


def run_batch_review_pipeline(
    inputs: list[tuple[str, str, str]], major, api_key, custom_words, mask_map
) -> list:
    """(stem, text, err) 입력 목록으로 병렬 일괄 검토를 실행한다.

    여러 파일 업로드·명렬표 업로드가 동일한 파이프라인을 공유한다.
    """
    def _review_job(idx: int) -> dict:
        stem, text, err = inputs[idx]
        if err:
            return {"name": stem, "text": "", "findings": [], "error": err}
        try:
            findings = review_text_masked(text, major, api_key, custom_words, mask_map)
            return {"name": stem, "text": text, "findings": findings, "error": ""}
        except Exception as e:
            return {"name": stem, "text": "", "findings": [], "error": str(e)}

    return run_parallel(len(inputs), _review_job, "일괄 검토 중…")


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
    text: str,
    api_key: str,
    mask_map: list[tuple[str, str]],
    state_key: str,
    apply_to_key: str | None = None,
    clear_keys: tuple[str, ...] = (),
) -> None:
    """오탈자 검사 실행 버튼 + 결과(표·하이라이트·교정 반영본) 표시 블록.

    apply_to_key가 주어지면 교정 반영본을 해당 세션 키(예: draft_text)에
    원클릭으로 덮어쓰는 버튼을 함께 표시한다.
    """
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
        if apply_to_key:
            if st.button(
                "✅ 교정 반영본을 본문에 적용",
                key=f"apply_{state_key}",
                use_container_width=True,
            ):
                st.session_state[apply_to_key] = corrected
                record_history("오탈자 교정 반영", corrected)
                st.session_state.pop(state_key, None)
                for k in clear_keys:
                    st.session_state.pop(k, None)
                st.rerun()
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
    cur_bytes = neis_bytes(text)

    st.markdown("**📏 분량 조절 (줄이기/늘리기)**")
    unit = st.radio(
        "조절 기준",
        ["글자 수 (공백 포함)", "바이트 (NEIS · 한글 3B)"],
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
def render_highlight_box(
    text: str, words: list[str], severities: dict[str, str] | None = None
) -> None:
    st.markdown(
        f'<div style="border: 1px solid #ddd; border-radius: 8px; '
        f'padding: 16px; line-height: 1.8; background-color: #fafafa; color: #222;">'
        f"{highlight_text(text, words, severities)}</div>",
        unsafe_allow_html=True,
    )


def render_length_metrics(text: str, limit: int) -> None:
    """글자 수·NEIS 바이트 지표와 제한 초과 여부를 표시한다.

    초과 판정은 사이드바에서 선택한 분량 기준(len_unit)을 따른다.
    바이트 기준일 때 유효 제한은 글자 수 제한 × 3이다.
    """
    char_with_space = len(text)
    char_no_space = len(re.sub(r"\s", "", text))
    nbytes = neis_bytes(text)

    c1, c2, c3 = st.columns(3)
    c1.metric("글자 수 (공백 포함)", f"{char_with_space:,}자")
    c2.metric("글자 수 (공백 제외)", f"{char_no_space:,}자")
    c3.metric("NEIS 바이트 (한글 3B)", f"{nbytes:,} byte")

    if limit > 0:
        by_bytes = str(st.session_state.get("len_unit", "")).startswith("NEIS 바이트")
        if by_bytes:
            value, eff_limit, unit = nbytes, limit * 3, "byte"
        else:
            value, eff_limit, unit = char_with_space, limit, "자"
        if value > eff_limit:
            st.error(
                f"🚨 NEIS 제한 초과: {value:,}{unit} / {eff_limit:,}{unit} "
                f"(**{value - eff_limit:,}{unit} 초과**)"
            )
        else:
            st.success(f"✅ NEIS 제한 이내: {value:,}{unit} / {eff_limit:,}{unit}")


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

    render_highlight_box(
        text,
        [f["word"] for f in findings],
        {f["word"]: f.get("severity", "위반") for f in findings},
    )

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
# 마스킹 자동 제안 (개인정보로 보이는 표현 탐지)
# ──────────────────────────────────────────────
def _apply_mask_suggestions(suggestions: list[str]) -> None:
    # on_click 콜백은 위젯 생성 전에 실행되므로 위젯 key 수정이 허용된다.
    cur = st.session_state.get("mask_words_raw", "")
    new = ", ".join(suggestions)
    st.session_state["mask_words_raw"] = f"{cur}, {new}" if cur.strip() else new


def render_mask_suggestions(texts: list[str], mask_words_raw: str, key: str) -> None:
    """텍스트에서 개인정보로 보이는 표현을 탐지해 마스킹 목록 추가를 제안한다."""
    suggestions = suggest_mask_candidates(texts, parse_custom_words(mask_words_raw))
    if not suggestions:
        return
    st.info("🕵️ 개인정보로 보이는 표현 발견: " + ", ".join(suggestions))
    st.button(
        "🔒 마스킹 목록에 추가",
        key=f"masksug_{key}",
        on_click=_apply_mask_suggestions,
        args=(suggestions,),
    )


# ──────────────────────────────────────────────
# 오탈(오탐) 무시 목록 — 선택한 검출어를 이후 결과에서 제외
# ──────────────────────────────────────────────
def render_ignore_control(findings: list[dict], key: str) -> list[dict]:
    """검출어 중 오탐으로 표시할 항목을 고르는 UI. 필터링된 findings를 반환한다."""
    options = sorted({f.get("word", "") for f in findings if f.get("word")})
    ignored = st.session_state["ignored_words"]
    default = [w for w in options if w in ignored]
    selected = st.multiselect(
        "🚫 오탐으로 표시 (선택한 검출어는 이후 결과에서 제외)",
        options=options,
        default=default,
        key=key,
    )
    if set(selected) != set(default):
        # 이 검출어들에 한해 선택 목록으로 교체 (다른 무시어는 유지)
        st.session_state["ignored_words"] = list(
            (set(ignored) - set(options)) | set(selected)
        )
    return filter_ignored(findings, st.session_state["ignored_words"])


# ──────────────────────────────────────────────
# 사용 안내 (인앱 사용 설명서)
# ──────────────────────────────────────────────
def render_guide() -> None:
    """앱 전체 사용법을 단계별로 안내하는 도움말 화면."""
    st.subheader("📖 사용 안내")
    st.caption(
        "이 도구는 ① 생기부 **기재 금지 표현 검토**와 ② 세특 **초안 작성**, "
        "두 가지 작업을 도와줍니다. 위쪽 모드 선택에서 원하는 작업을 고른 뒤 아래 흐름을 따라가세요."
    )

    # ── (a) 전체 워크플로 ──
    st.subheader("① 전체 워크플로")
    col_r, col_w = st.columns(2)
    with col_r:
        with st.container(border=True):
            st.markdown("### 🔍 검토 워크플로")
            st.markdown(
                "1️⃣ 사이드바 설정 — 희망 진로 · NEIS 항목/분량 기준 · 개인정보 마스킹\n\n"
                "2️⃣ 파일 업로드 — txt · hwp · hwpx · docx · pdf 여러 개, "
                "또는 반 전체 명렬표 xlsx · csv 1개 (또는 직접 붙여넣기)\n\n"
                "3️⃣ **🔍 검토 실행**\n\n"
                "4️⃣ 결과 확인 — 위반 표현 하이라이트 · 수정본 자동 생성 · 품질 진단 · 오탈자 검사\n\n"
                "5️⃣ 다운로드 — 심사결과 CSV · 종합 엑셀 · 나이스 입력용 엑셀 · 개인별 리포트 ZIP"
            )
    with col_w:
        with st.container(border=True):
            st.markdown("### ✍️ 초안 워크플로")
            st.markdown(
                "1️⃣ 과목명 · 수행평가 활동 내용 입력\n\n"
                "2️⃣ 자기평가서 업로드 — 파일 여러 개, 또는 엑셀 양식 1개 (없어도 진행 가능)\n\n"
                "3️⃣ (선택) 예시 세특 문체 참고 · **⚖️ 2버전 생성 후 비교** 토글\n\n"
                "4️⃣ **✍️ 초안 생성**\n\n"
                "5️⃣ 다듬기 — 피드백 반영 재생성 · 분량 조절 · 품질 진단 · 오탈자 검사"
            )

    # ── (b) 파일 형식 안내 ──
    st.subheader("② 파일 형식 안내")
    st.markdown(
        "| 형식 | 어디에 쓰나요 | 비고 |\n"
        "| --- | --- | --- |\n"
        "| **txt** | 생기부·자기평가서·예시 세특 | 순수 텍스트 |\n"
        "| **hwp / hwpx** | 생기부·자기평가서·예시 세특 | 한글 문서 |\n"
        "| **docx** | 생기부·자기평가서·예시 세특 | Word 문서 |\n"
        "| **pdf** | 생기부·자기평가서 | 텍스트형 PDF만 가능 · 스캔본(이미지)은 불가 |\n"
        "| **xlsx / csv** | 반 전체 명렬표 · 엑셀 자기평가서 | **단독으로 1개만** 업로드 · 1열은 이름, 나머지 열은 내용 |"
    )
    st.caption(
        "여러 파일을 올리면 반 전체 일괄 처리가 됩니다. "
        "명렬표·엑셀 자기평가서는 다른 파일과 섞지 말고 한 개만 단독으로 올려주세요."
    )

    # ── (c) 사이드바 기능 표 ──
    st.subheader("③ 사이드바 기능")
    st.markdown(
        "| 기능 | 설명 | 언제 쓰나요 |\n"
        "| --- | --- | --- |\n"
        "| **개인정보 마스킹** | 등록한 단어를 전송 전 《비공개n》으로 치환하고 결과에서 복원 | 학생 이름·학번 등 개인정보 보호 |\n"
        "| **마스킹 자동 제안** | 개인정보로 보이는 표현을 찾아 목록 추가 제안 | 마스킹 누락 방지 |\n"
        "| **희망 진로/학과** | 대체 표현 추천·진로 연계 서술에 활용 | 검토·초안 모두 |\n"
        "| **NEIS 항목 · 분량 기준** | 항목별 글자 수 제한 기준으로 초과 검사 (글자 수 또는 바이트) | 분량 초과 점검 |\n"
        "| **사용자 정의 금칙어** | 학교명 등 반드시 걸러야 할 단어를 규칙 검출에 추가 | 학교·학원명 차단 |\n"
        "| **작업 이력** | 이번 세션 결과를 시각과 함께 기록 · TXT 다운로드 | 이전 결과 되찾기 |\n"
        "| **오탐 무시 목록** | 오탐으로 표시한 검출어를 이후 결과에서 제외 | 반복되는 오탐 정리 |\n"
        "| **프로젝트 저장/복원** | 세션 작업 상태를 JSON으로 저장·복원 | 작업 중단 후 이어하기 |"
    )

    # ── (d) 자주 묻는 질문 ──
    st.subheader("④ 자주 묻는 질문")
    with st.expander("결과가 사라졌어요"):
        st.markdown(
            "새로고침·재실행 시 화면 결과는 초기화될 수 있습니다. "
            "사이드바 **💾 프로젝트 저장(JSON)** 으로 작업 상태를 파일로 보관했다가 "
            "**📂 복원 실행**으로 되돌릴 수 있고, **🕘 작업 이력**에서 이전 결과 TXT를 다시 받을 수 있습니다."
        )
    with st.expander("학생 이름이 외부로 전송되나요?"):
        st.markdown(
            "입력 텍스트는 Google Gemini API(외부 서버)로 전송됩니다. "
            "다만 사이드바 **개인정보 마스킹 단어**에 등록한 단어는 전송 직전 《비공개n》 토큰으로 치환되고, "
            "돌아온 결과에서 원래 단어로 자동 복원됩니다. "
            "이름·학번 등은 마스킹 목록에 등록해 주세요(자동 제안도 활용)."
        )
    with st.expander("일괄 검토에서 실패한 학생이 있어요"):
        st.markdown(
            "일시적 오류(예: 호출 한도)는 자동으로 재시도됩니다. "
            "한 학생이 최종 실패해도 나머지 학생 처리는 그대로 진행되며(개별 실패 격리), "
            "실패한 학생 이름은 결과 상단에 따로 표시됩니다. 해당 학생만 다시 실행하면 됩니다."
        )
    with st.expander("명렬표/자기평가서 엑셀은 어떤 모양이어야 하나요?"):
        st.markdown(
            "**1열은 이름**, 나머지 열은 내용(생기부 문장 또는 자기평가 문항)입니다. 열 이름은 자유입니다. "
            "이 파일 **1개만** 단독으로 올리면 표 안 학생 전체가 일괄 처리됩니다. "
            "초안 모드의 **📄 자기평가서 엑셀 양식 다운로드** 버튼에서 표준 양식을 받을 수 있습니다."
        )
    with st.expander("분량 기준의 '글자 수'와 '바이트'는 뭐가 다른가요?"):
        st.markdown(
            "**글자 수(공백 포함)** 는 문자 개수 기준이고, "
            "**NEIS 바이트** 는 나이스 저장 기준으로 한글 1자를 3바이트로 계산합니다. "
            "바이트 기준을 고르면 항목별 글자 수 제한을 ×3으로 환산해 초과 여부를 판정합니다."
        )
    with st.expander("2버전 비교는 비용이 더 드나요?"):
        st.markdown(
            "**⚖️ 2버전 생성 후 비교**를 켜면 초안을 두 번 생성하므로 API 호출이 **2배**가 됩니다. "
            "서로 다른 두 버전을 나란히 보고 마음에 드는 쪽을 고르고 싶을 때 사용하세요."
        )

    # ── (e) 안전 고지 ──
    st.warning(
        "⚠️ 본 도구의 결과는 **참고용 초안/심사**입니다. 최종 기재 내용은 반드시 "
        "당해 연도 교육부 「학교생활기록부 기재요령」 원문을 확인한 뒤 **교사가 직접 작성·확정**하시기 바랍니다."
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

# 오탐(false-positive)으로 표시된 검출어 목록 — 이후 결과에서 제외한다.
st.session_state.setdefault("ignored_words", [])


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
        key="mask_words_raw",
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

    use_bytes = st.radio(
        "분량 기준",
        ["글자 수 (공백 포함)", "NEIS 바이트 (한글 3B)"],
        horizontal=True,
        key="len_unit",
        help="NEIS 바이트 기준은 글자 수 제한을 자→바이트(×3)로 환산해 초과 여부를 판정합니다.",
    ).startswith("NEIS 바이트")

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

    # 오탐(false-positive) 무시 목록
    ignored_words = st.session_state.get("ignored_words", [])
    if ignored_words:
        st.caption("🚫 오탐 무시 목록: " + ", ".join(ignored_words))
        if st.button("무시 목록 비우기", key="clear_ignored"):
            st.session_state["ignored_words"] = []
            st.rerun()

    # 프로젝트 저장/복원 — 세션 작업 상태를 JSON 파일로 내보내고 되돌린다.
    st.divider()
    st.markdown("**💾 프로젝트**")
    st.caption(
        "⚠️ 저장 파일에는 **학생 정보가 포함**됩니다. 외부에 올리지 말고 "
        "교사 PC에만 보관하세요."
    )
    st.download_button(
        "📥 프로젝트 저장 (JSON)",
        data=export_project(),
        file_name="생기부_프로젝트.json",
        mime="application/json",
        key="proj_dl",
        use_container_width=True,
    )
    st.file_uploader("프로젝트 불러오기", type=["json"], key="proj_file")
    st.button(
        "📂 복원 실행",
        key="proj_restore",
        use_container_width=True,
        on_click=_restore_project,
    )
    proj_msg = st.session_state.get("proj_msg")
    if proj_msg:
        if proj_msg.startswith("✅"):
            st.success(proj_msg)
        else:
            st.error(proj_msg)
        st.session_state.pop("proj_msg", None)

# ── 모드 선택 ──
mode = st.radio(
    "모드를 선택하세요.",
    ["🔍 기재 금지 표현 검토", "✍️ 세특 초안 작성", "📖 사용 안내"],
    horizontal=True,
)

# 첫 방문 안내 배너 (세션당 1회, 안내 모드에서는 표시하지 않음)
if not st.session_state.get("seen_guide_hint") and mode != "📖 사용 안내":
    st.info(
        "💡 처음이신가요? 위에서 **📖 사용 안내**를 선택하면 전체 사용법을 단계별로 볼 수 있습니다."
    )
    st.session_state["seen_guide_hint"] = True

st.divider()

# ══════════════════════════════════════════════
# 모드 1: 기재 금지 표현 검토
# ══════════════════════════════════════════════
if mode == "🔍 기재 금지 표현 검토":
    st.subheader("1️⃣ 생기부 텍스트 입력")

    input_method = st.radio(
        "입력 방식을 선택하세요.",
        ["파일 업로드 (.txt / .hwp / .hwpx / .docx / .pdf / 명렬표 .csv·.xlsx — 여러 개 가능)", "직접 붙여넣기"],
        horizontal=True,
    )

    input_text = ""
    review_files = []
    # 명렬표(반 전체) 모드 상태
    is_roster = False
    roster_mixed = False
    roster_df = None
    roster_name_col = None
    roster_text_col = None

    if input_method.startswith("파일 업로드"):
        review_files = st.file_uploader(
            "생기부 파일 또는 반 전체 명렬표(.csv/.xlsx)를 업로드하세요. "
            "여러 개 올리면 반 전체 일괄 검토가 됩니다. 명렬표는 한 개만 단독으로 올려주세요.",
            type=["txt", "hwp", "hwpx", "docx", "pdf", "csv", "xlsx"],
            accept_multiple_files=True,
            key="review_files",
        )
        spreadsheet_files = [
            f for f in review_files if f.name.lower().endswith((".csv", ".xlsx"))
        ]
        if spreadsheet_files and len(review_files) > 1:
            # 명렬표와 다른 파일이 섞여 있으면 실행하지 않는다.
            roster_mixed = True
            st.error("❌ 명렬표는 단독으로 업로드해 주세요.")
        elif len(spreadsheet_files) == 1:
            # ── 명렬표(반 전체) 모드 ──
            is_roster = True
            f = spreadsheet_files[0]
            try:
                if f.name.lower().endswith(".csv"):
                    try:
                        roster_df = pd.read_csv(io.BytesIO(f.getvalue()), encoding="utf-8")
                    except UnicodeDecodeError:
                        roster_df = pd.read_csv(io.BytesIO(f.getvalue()), encoding="cp949")
                else:
                    roster_df = pd.read_excel(io.BytesIO(f.getvalue()))
            except Exception as e:
                st.error(f"❌ 명렬표를 읽지 못했습니다: {e}")
                roster_df = None
            if roster_df is not None and not roster_df.empty:
                st.success(f"✅ 명렬표 로드 완료 ({len(roster_df):,}행)")
                st.dataframe(roster_df.head(5), use_container_width=True)
                guess_name, guess_text = guess_roster_columns(roster_df)
                cols = list(roster_df.columns)
                c1, c2 = st.columns(2)
                roster_name_col = c1.selectbox(
                    "이름 열", cols, index=cols.index(guess_name), key="roster_name_col"
                )
                roster_text_col = c2.selectbox(
                    "내용 열", cols, index=cols.index(guess_text), key="roster_text_col"
                )
        elif len(review_files) == 1:
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

    # 개인정보 마스킹 자동 제안 (단일 파일 미리보기 / 직접 붙여넣기)
    if input_text.strip():
        render_mask_suggestions([input_text], mask_words_raw, "mode1_input")

    run = st.button("🔍 검토 실행", type="primary", use_container_width=True)

    if run:
        if not api_key.strip():
            st.warning("⚠️ 서버에 Gemini API Key가 설정되지 않아 실행할 수 없습니다.")
            st.stop()

        if roster_mixed:
            st.error("❌ 명렬표는 단독으로 업로드해 주세요. 명렬표만 남기고 다시 실행해 주세요.")
            st.stop()

        if is_roster:
            # ── 명렬표(반 전체) 일괄 검토 ──
            if roster_df is None or roster_df.empty:
                st.warning("⚠️ 명렬표에서 읽을 데이터가 없습니다.")
                st.stop()
            entries = parse_roster_table(roster_df, roster_name_col, roster_text_col)
            if not entries:
                st.warning("⚠️ 이름·내용이 모두 채워진 행이 없습니다. 열 선택을 확인해 주세요.")
                st.stop()

            st.session_state.pop("review_result", None)
            st.session_state.pop("revised_text", None)
            st.session_state.pop("quality_review", None)
            st.session_state.pop("proofread_review", None)

            inputs = [(name, text, "") for name, text in entries]
            st.session_state["batch_review"] = run_batch_review_pipeline(
                inputs, major, api_key, custom_words, mask_map
            )
            record_history(f"명렬표 일괄 검토 {len(inputs)}건", "")
        elif len(review_files) > 1:
            # ── 일괄 검토 (여러 파일) ──
            st.session_state.pop("review_result", None)
            st.session_state.pop("revised_text", None)
            st.session_state.pop("quality_review", None)
            st.session_state.pop("proofread_review", None)

            stems = unique_names([file_stem(f.name) for f in review_files])

            # 파일 읽기는 메인 스레드에서 (UploadedFile은 스레드 안전하지 않음)
            inputs = []
            for f, stem in zip(review_files, stems):
                try:
                    inputs.append((stem, read_uploaded_file(f), ""))
                except Exception as e:
                    inputs.append((stem, "", str(e)))

            st.session_state["batch_review"] = run_batch_review_pipeline(
                inputs, major, api_key, custom_words, mask_map
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
        findings = render_ignore_control(review["findings"], "ign_single")
        show_review_output(review["text"], findings)

        if findings:
            st.subheader("4️⃣ 수정본 자동 생성")
            if st.button("✏️ 검토 결과를 반영한 수정본 생성", use_container_width=True):
                with st.spinner(f"수정본 생성 중… ({GEMINI_MODEL})"):
                    try:
                        masked_findings = [
                            {**f, "word": apply_mask(f["word"], mask_map)}
                            for f in findings
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

                col_dl, col_re = st.columns(2)
                col_dl.download_button(
                    "📥 수정본 TXT 다운로드",
                    data=revised.encode("utf-8"),
                    file_name="생기부_수정본.txt",
                    mime="text/plain",
                    use_container_width=True,
                )
                # 수정본을 새 원문으로 삼아 검토 루프를 다시 돈다
                if col_re.button(
                    "🔁 이 수정본 다시 검토", use_container_width=True
                ):
                    with st.spinner(f"수정본 재검토 중… ({GEMINI_MODEL})"):
                        try:
                            new_findings = review_text_masked(
                                revised, major, api_key, custom_words, mask_map
                            )
                            st.session_state["review_result"] = {
                                "text": revised,
                                "findings": new_findings,
                            }
                            for k in (
                                "revised_text",
                                "quality_review",
                                "proofread_review",
                            ):
                                st.session_state.pop(k, None)
                            record_history(
                                f"수정본 재검토: {len(new_findings)}건 검출", revised
                            )
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ 재검토 실패: {e}")

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

        # 개인정보 마스킹 자동 제안 (반 전체 텍스트 기반)
        render_mask_suggestions(
            [b["text"] for b in ok], mask_words_raw, "mode1_batch"
        )

        # 반 전체 오탐(false-positive) 무시 컨트롤 (모든 검출어 대상)
        all_findings = [f for b in ok for f in b["findings"]]
        render_ignore_control(all_findings, "ign_batch")
        ignored_now = set(st.session_state["ignored_words"])

        # 반 전체 요약표 (사이드바에서 선택한 분량 기준으로 계산)
        count_label = "NEIS 바이트" if use_bytes else "글자 수"
        eff_limit = neis_limit * 3 if use_bytes else neis_limit
        unit = "byte" if use_bytes else "자"
        summary_rows = []
        for b in ok:
            b_findings = filter_ignored(b["findings"], ignored_now)
            n_violation = sum(
                1 for f in b_findings if f.get("severity", "위반") == "위반"
            )
            n_caution = len(b_findings) - n_violation
            top_words = ", ".join(f["word"] for f in b_findings[:3])
            count = neis_bytes(b["text"]) if use_bytes else len(b["text"])
            row = {
                "학생(파일명)": b["name"],
                count_label: count,
                "분량": (
                    f"{count - eff_limit}{unit} 초과"
                    if eff_limit and count > eff_limit
                    else "이내"
                )
                if eff_limit
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

            def _highlight_over(col: pd.Series) -> list[str]:
                return [
                    "background-color: #ffcccc"
                    if isinstance(v, str) and "초과" in v
                    else ""
                    for v in col
                ]

            st.dataframe(
                summary_df.style.apply(_highlight_over, subset=["분량"]),
                use_container_width=True,
                hide_index=True,
            )
            # 오탐 무시 목록을 반영한 사본으로 엑셀 생성 (요약표와 일관성 유지)
            ok_filtered = [
                {**b, "findings": filter_ignored(b["findings"], ignored_now)}
                for b in ok
            ]
            dl_csv, dl_xlsx, dl_neis = st.columns(3)
            dl_csv.download_button(
                "📥 반 전체 검토 요약 CSV 다운로드",
                data=summary_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="생기부_일괄검토_요약.csv",
                mime="text/csv",
                use_container_width=True,
            )
            dl_xlsx.download_button(
                "📊 종합 결과 엑셀 다운로드",
                data=build_batch_workbook(ok_filtered, neis_limit),
                file_name="생기부_일괄검토_종합.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            dl_neis.download_button(
                "🗂️ 나이스 입력용 엑셀",
                data=build_neis_workbook(ok_filtered, neis_limit),
                file_name="생기부_나이스입력.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

        # ── 일괄 후처리: 수정본 / 품질 진단 / 오탈자 ──
        st.subheader("3️⃣ 일괄 후처리")
        st.caption(
            "반 전체에 대해 수정본 생성·품질 진단·오탈자 검사를 병렬로 실행합니다. "
            "결과는 요약표와 학생별 상세에 반영됩니다."
        )
        targets = [b for b in ok if filter_ignored(b["findings"], ignored_now)]
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
                        for f in filter_ignored(b["findings"], ignored_now)
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

        # 수정본 일괄 치환 (수정본이 있는 학생에게만 적용됩니다)
        st.caption("아래 치환은 **수정본이 있는 학생에게만** 적용됩니다.")
        render_replace_control("batch_review", "revised", "batch_review_replace")

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
            b_findings = filter_ignored(b["findings"], ignored_now)
            n_found = len(b_findings)
            label = f"📄 {b['name']} — {len(b['text']):,}자, 검출 {n_found}건"
            with st.expander(label):
                if b_findings:
                    render_highlight_box(
                        b["text"],
                        [f["word"] for f in b_findings],
                        {f["word"]: f.get("severity", "위반") for f in b_findings},
                    )
                    st.dataframe(
                        findings_to_df(b_findings),
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
# 사용 안내
# ══════════════════════════════════════════════
elif mode == "📖 사용 안내":
    render_guide()

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

    draft_category = category_for_neis_item(neis_item)
    st.caption(
        f"✏️ 작성 항목: **{draft_category}** — 사이드바 'NEIS 항목' 선택을 따릅니다."
    )

    if draft_category == "세특":
        subject = st.text_input("과목명", placeholder="예: 정보, 수학Ⅰ, 여행지리 …")
    else:
        subject = st.text_input(
            "활동명 (선택)", placeholder="예) 학급 자치회, 진로 박람회 …"
        )

    performance_text = st.text_area(
        "수행평가 활동 내용 — 무엇을, 어떻게 진행했는지 적어주세요. (일괄 생성 시 모든 학생에게 공통 적용)",
        height=180,
        placeholder=(
            "예) 파이썬으로 학교 급식 잔반량 데이터를 분석하는 수행평가를 진행함.\n"
            "- 설문으로 데이터를 수집하고 표로 정리함\n"
            "- 분석 결과를 바탕으로 잔반 줄이기 캠페인 아이디어를 발표함"
        ),
    )

    observations_text = st.text_area(
        "교사 관찰 메모 (선택) — 학기 중 기록한 짧은 메모를 한 줄에 하나씩",
        height=120,
        placeholder=(
            "예)\n"
            "5월 수행평가에서 조원 갈등을 중재하고 역할을 재분배함\n"
            "몬티홀 문제를 스스로 코드로 검증해 와서 질문함\n"
            "발표 자료에 출처를 빠짐없이 표기함"
        ),
        help="자기평가서가 없어도 관찰 메모만으로 초안을 만들 수 있습니다. "
        "메모의 사실들이 하나의 서사로 통합됩니다. (단일 초안에만 적용)",
        key="observations_text",
    )

    eval_files = st.file_uploader(
        "학생 자기평가서 업로드 (선택, .hwp / .hwpx / .txt / .docx / .pdf / 엑셀 일괄 .xlsx·.csv — 여러 개 선택 가능)",
        type=["hwp", "hwpx", "txt", "docx", "pdf", "xlsx", "csv"],
        key="eval_files",
        accept_multiple_files=True,
        help="1개 업로드 시 단일 초안, 2개 이상 업로드 시 학생별 일괄 생성됩니다. "
        "엑셀·CSV 자기평가서를 1개 올리면 표의 학생 전체가 일괄 생성됩니다. "
        "파일명에 학번·이름을 넣어두면 결과 구분이 쉽습니다.",
    )
    st.download_button(
        "📄 자기평가서 엑셀 양식 다운로드",
        data=build_eval_template(),
        file_name="자기평가서_양식.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="eval_template_dl",
    )
    st.caption(
        "엑셀 양식 구조: **1열은 이름**, 나머지 열은 자기평가 문항입니다 "
        "(열 이름은 자유롭게 정할 수 있습니다). 이 파일 1개만 올리면 표 안의 "
        "학생 전체 초안이 한 번에 생성됩니다."
    )

    style_files = st.file_uploader(
        "예시 세특 업로드 (선택, 문체 참고용 — 잘 쓴 세특 1~2개)",
        type=["txt", "docx", "hwp", "hwpx"],
        accept_multiple_files=True,
        key="style_files",
        help="업로드한 예시는 문체·어미·구성 방식만 참고하며 내용·사실·활동은 사용되지 않습니다. "
        "개인정보가 없는 자료를 쓰거나 이름·학교 등을 마스킹하는 것을 권장합니다.",
    )

    # 문체 참고 예시 읽기 (앞 2개, 각 1500자 이내)
    style_examples: list[str] = []
    for f in (style_files or [])[:2]:
        try:
            txt = read_uploaded_file(f)
        except Exception as e:
            st.warning(f"⚠️ 예시 세특 '{f.name}'에서 텍스트를 추출하지 못해 건너뜁니다: {e}")
            continue
        if txt.strip():
            style_examples.append(txt.strip()[:1500])
    if style_examples:
        st.caption(f"🖋️ 문체 참고 예시 {len(style_examples)}개 적용 — 내용은 사용되지 않습니다.")

    # 엑셀 자기평가서(일괄) 모드 상태
    is_eval_table = False
    eval_mixed = False
    eval_df = None
    eval_name_col = None
    eval_content_cols = None
    eval_spreadsheets = [
        f for f in eval_files if f.name.lower().endswith((".csv", ".xlsx"))
    ]

    # 단일 파일 미리보기
    single_eval_text = ""
    if eval_spreadsheets and len(eval_files) > 1:
        # 엑셀 자기평가서와 다른 파일이 섞여 있으면 실행하지 않는다.
        eval_mixed = True
        st.error("❌ 엑셀 자기평가서는 단독으로 업로드해 주세요.")
    elif len(eval_spreadsheets) == 1:
        # ── 엑셀 자기평가서(일괄) 모드 ──
        is_eval_table = True
        f = eval_spreadsheets[0]
        try:
            if f.name.lower().endswith(".csv"):
                try:
                    eval_df = pd.read_csv(io.BytesIO(f.getvalue()), encoding="utf-8")
                except UnicodeDecodeError:
                    eval_df = pd.read_csv(io.BytesIO(f.getvalue()), encoding="cp949")
            else:
                eval_df = pd.read_excel(io.BytesIO(f.getvalue()))
        except Exception as e:
            st.error(f"❌ 엑셀 자기평가서를 읽지 못했습니다: {e}")
            eval_df = None
        if eval_df is not None and not eval_df.empty:
            st.success(f"✅ 엑셀 자기평가서 로드 완료 ({len(eval_df):,}행)")
            st.dataframe(eval_df.head(5), use_container_width=True)
            guess_name, _ = guess_roster_columns(eval_df)
            cols = list(eval_df.columns)
            eval_name_col = st.selectbox(
                "이름 열", cols, index=cols.index(guess_name), key="eval_name_col"
            )
            eval_content_cols = st.multiselect(
                "자기평가 문항 열 (여러 개 선택 가능)",
                [c for c in cols if c != eval_name_col],
                default=[c for c in cols if c != eval_name_col],
                key="eval_content_cols",
            )
    elif len(eval_files) == 1:
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

    # 개인정보 마스킹 자동 제안 (단일 자기평가서 기반)
    if single_eval_text.strip():
        render_mask_suggestions([single_eval_text], mask_words_raw, "mode2_eval")

    default_len = neis_limit if neis_limit else 500
    target_len = st.slider(
        "목표 분량 (공백 포함 글자 수)",
        min_value=200,
        max_value=1500,
        value=min(max(default_len, 200), 1500),
        step=50,
        help="사이드바에서 선택한 NEIS 항목의 제한이 기본값으로 반영됩니다.",
    )

    dual_draft = st.toggle(
        "⚖️ 2버전 생성 후 비교 선택 (API 호출 2배)",
        key="dual_draft",
        value=False,
    )

    gen = st.button("✍️ 초안 생성", type="primary", use_container_width=True)

    if gen:
        if not api_key.strip():
            st.warning("⚠️ 서버에 Gemini API Key가 설정되지 않아 실행할 수 없습니다.")
            st.stop()
        if eval_mixed:
            st.error("❌ 엑셀 자기평가서는 단독으로 업로드해 주세요. 엑셀만 남기고 다시 실행해 주세요.")
            st.stop()
        if not major.strip() and len(eval_files) <= 1 and not is_eval_table:
            st.warning("⚠️ 사이드바에 학생의 희망 진로/학과를 입력해 주세요. (자기평가서에 장래희망이 있다면 그대로 진행됩니다)")
        if (
            not performance_text.strip()
            and not eval_files
            and not observations_text.strip()
        ):
            st.warning(
                "⚠️ 수행평가 활동 내용·교사 관찰 메모를 입력하거나 자기평가서 파일을 업로드해 주세요."
            )
            st.stop()

        st.session_state.pop("draft_text", None)
        st.session_state.pop("batch_drafts", None)
        st.session_state.pop("draft_variants", None)
        st.session_state.pop("quality_draft", None)
        st.session_state.pop("proofread_draft", None)

        if len(eval_files) > 1 or is_eval_table:
            # ── 일괄 생성 ──
            if is_eval_table:
                # 엑셀 자기평가서: 표에서 (이름, 자기평가 텍스트) 목록을 만든다.
                if eval_df is None or eval_df.empty:
                    st.warning("⚠️ 엑셀 자기평가서에서 읽을 데이터가 없습니다.")
                    st.stop()
                if not eval_content_cols:
                    st.warning("⚠️ 자기평가 문항 열을 하나 이상 선택해 주세요.")
                    st.stop()
                entries = parse_eval_table(eval_df, eval_name_col, eval_content_cols)
                if not entries:
                    st.warning("⚠️ 이름·자기평가 내용이 채워진 행이 없습니다. 열 선택을 확인해 주세요.")
                    st.stop()
                inputs: list[tuple[str, str, str]] = [
                    (name, text, "") for name, text in entries
                ]
            else:
                stems = unique_names([file_stem(f.name) for f in eval_files])

                # 파일 읽기는 메인 스레드에서 (UploadedFile은 스레드 안전하지 않음)
                inputs = []
                for f, stem in zip(eval_files, stems):
                    try:
                        inputs.append((stem, read_uploaded_file(f), ""))
                    except Exception as e:
                        inputs.append((stem, "", str(e)))

            masked_performance = apply_mask(performance_text, mask_map)
            masked_style_examples = [apply_mask(x, mask_map) for x in style_examples]

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
                        style_examples=masked_style_examples,
                        category=draft_category,
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
            masked_performance = apply_mask(performance_text, mask_map)
            masked_self_eval = apply_mask(single_eval_text, mask_map)
            masked_style_examples = [apply_mask(x, mask_map) for x in style_examples]
            masked_observations = apply_mask(observations_text, mask_map)
            # 재생성 시 원자료 맥락 전달용 (마스킹된 상태로 보관)
            draft_context = {
                "subject": subject.strip(),
                "performance": masked_performance,
                "self_eval": masked_self_eval,
                "style_examples": masked_style_examples,
                "category": draft_category,
                "observations": masked_observations,
            }

            def _single_draft_job(_idx: int = 0) -> tuple[str, str]:
                try:
                    return (
                        generate_draft_with_gemini(
                            subject,
                            major,
                            masked_performance,
                            masked_self_eval,
                            target_len,
                            api_key,
                            style_examples=masked_style_examples,
                            category=draft_category,
                            observations=masked_observations,
                        ),
                        "",
                    )
                except Exception as e:
                    return ("", str(e))

            if dual_draft:
                # ── 2버전 동시 생성 ──
                outs = run_parallel(2, _single_draft_job, "2버전 생성 중…")
                variants = [remove_mask(d, mask_map) for d, _ in outs]
                errors = [err for _, err in outs if err]
                if any(not v.strip() for v in variants):
                    st.error(
                        "❌ 2버전 생성 실패: " + "; ".join(errors)
                        if errors
                        else "❌ 2버전 생성에 실패했습니다."
                    )
                else:
                    st.session_state["draft_variants"] = variants
                    st.session_state["draft_context"] = draft_context
            else:
                with st.spinner(f"세특 초안 생성 중… ({GEMINI_MODEL})"):
                    draft, err = _single_draft_job()
                    if err:
                        st.error(f"❌ Gemini API 호출 실패: {err}")
                    else:
                        st.session_state["draft_text"] = remove_mask(draft, mask_map)
                        record_history(
                            f"초안: {subject or '무제'}", st.session_state["draft_text"]
                        )
                        st.session_state["draft_context"] = draft_context

    # ── 2버전 비교 선택 ──
    variants = st.session_state.get("draft_variants")
    if variants:
        st.divider()
        st.subheader("2️⃣ 버전 비교")
        col_a, col_b = st.columns(2)
        for col, label, v, sel_key in (
            (col_a, "버전 A", variants[0], "select_a"),
            (col_b, "버전 B", variants[1], "select_b"),
        ):
            col.text_area(label, value=v, height=280, key=f"variant_{sel_key}")
            col.caption(f"{len(v):,}자")
            if col.button("✅ 이 버전 선택", key=sel_key, use_container_width=True):
                st.session_state["draft_text"] = v
                record_history("초안(버전 선택)", v)
                st.session_state.pop("draft_variants", None)
                st.rerun()

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
        render_proofread_block(
            draft,
            api_key,
            mask_map,
            "proofread_draft",
            apply_to_key="draft_text",
            clear_keys=("quality_draft",),
        )

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

        # 초안 일괄 치환
        render_replace_control("batch_drafts", "draft", "batch_drafts_replace")

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
