# -*- coding: utf-8 -*-
"""하이라이트 HTML 렌더링 / 검토 결과 DataFrame 변환."""

import html
import io
import re

import pandas as pd

# ──────────────────────────────────────────────
# 하이라이트 렌더링 / 분량 표시
# ──────────────────────────────────────────────
HIGHLIGHT_STYLE = "background-color: yellow; color: red; font-weight: bold;"
HIGHLIGHT_STYLE_CAUTION = (
    "background-color: #fff3cd; color: #856404; font-weight: bold; "
    "border-bottom: 2px solid #ffc107;"
)


def highlight_text(
    original: str, words: list[str], severities: dict[str, str] | None = None
) -> str:
    """원본 텍스트에서 위반 단어들을 <span> 하이라이트 처리한 HTML을 만든다.

    원문을 단일 패스로 스캔하므로 검출어끼리 겹쳐도 span이 중첩되지 않는다.
    긴 단어를 정규식 대안(|) 앞에 두어 같은 위치에서는 긴 매치가 우선한다.

    severities: {검출어: "위반"/"주의"} — "주의"는 노란색 스타일, 그 외/미지정은 위반 스타일.
    """
    unique_words = sorted({w for w in words if w.strip()}, key=len, reverse=True)
    if not unique_words:
        return html.escape(original).replace("\n", "<br>")

    severities = severities or {}
    pattern = "|".join(re.escape(w) for w in unique_words)
    parts = []
    last = 0
    for m in re.finditer(pattern, original):
        matched = m.group()
        parts.append(html.escape(original[last : m.start()]))
        style = (
            HIGHLIGHT_STYLE_CAUTION
            if severities.get(matched) == "주의"
            else HIGHLIGHT_STYLE
        )
        parts.append(f'<span style="{style}">{html.escape(matched)}</span>')
        last = m.end()
    parts.append(html.escape(original[last:]))

    # 줄바꿈 유지
    return "".join(parts).replace("\n", "<br>")


def build_student_report(b: dict, neis_limit: int = 0) -> str:
    """일괄 검토 결과 dict 하나를 학생별 종합 리포트(plain text)로 조립한다.

    b 키: name, text, findings(list), 선택적 revised / quality / proofread.
    dict에 없는 섹션은 리포트에서 통째로 생략한다.
    """
    div = "─" * 40
    name = b.get("name", "")
    text = b.get("text", "")
    lines: list[str] = []

    # 헤더
    lines.append(f"생기부 검토 리포트 — {name}")
    lines.append(f"글자 수 (공백 포함): {len(text):,}자")
    if neis_limit > 0:
        if len(text) > neis_limit:
            lines.append(
                f"NEIS 제한 초과: {len(text):,}자 / {neis_limit:,}자 "
                f"({len(text) - neis_limit:,}자 초과)"
            )
        else:
            lines.append(f"NEIS 제한 이내: {len(text):,}자 / {neis_limit:,}자")

    # 검출된 기재 금지 표현
    lines.append(div)
    lines.append("[검출된 기재 금지 표현]")
    findings = b.get("findings", [])
    if findings:
        for f in findings:
            sev = f.get("severity", "위반")
            basis = f.get("basis", "")
            meta = f"{sev}/{f.get('reason', '')}"
            if basis:
                meta += f" · {basis}"
            lines.append(
                f"- 「{f.get('word', '')}」 ({meta}) "
                f"→ 추천: {f.get('suggestion_1', '')} / {f.get('suggestion_2', '')}"
            )
    else:
        lines.append("검출 없음")

    # 수정본
    if b.get("revised"):
        lines.append(div)
        lines.append("[수정본]")
        lines.append(b["revised"])

    # 품질 진단
    q = b.get("quality")
    if q:
        lines.append(div)
        lines.append("[품질 진단]")
        scores = q.get("scores", [])
        for s in scores:
            lines.append(
                f"{s.get('criterion', '')} {s.get('score', '-')}/5 — {s.get('comment', '')}"
            )
        if scores:
            avg = sum(float(s.get("score", 0)) for s in scores) / len(scores)
            lines.append(f"종합 평균: {avg:.1f} / 5.0")
        if q.get("overall"):
            lines.append(f"총평: {q['overall']}")
        improvements = q.get("improvements", [])
        if improvements:
            lines.append("개선 제안:")
            for i, imp in enumerate(improvements, 1):
                lines.append(f"  {i}. {imp}")

    # 오탈자
    proofread = b.get("proofread")
    if proofread is not None:
        lines.append(div)
        lines.append("[오탈자]")
        if proofread:
            for it in proofread:
                lines.append(
                    f"- {it.get('wrong', '')} → {it.get('correct', '')} "
                    f"({it.get('reason', '')})"
                )
        else:
            lines.append("발견된 오탈자 없음")

    return "\n".join(lines)


def findings_to_df(findings: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(findings)
    if "severity" not in df.columns:
        df["severity"] = "위반"
    if "source" not in df.columns:
        df["source"] = "-"
    has_basis = any(str(f.get("basis", "")).strip() for f in findings)
    if has_basis and "basis" not in df.columns:
        df["basis"] = ""
    df = df.rename(
        columns={
            "word": "발견된 표현",
            "reason": "위반 사유",
            "basis": "근거",
            "severity": "구분",
            "suggestion_1": "대체 추천 1",
            "suggestion_2": "대체 추천 2",
            "source": "검출 단계",
        }
    )
    cols = ["구분", "발견된 표현", "위반 사유"]
    if has_basis:
        cols.append("근거")
    cols += ["대체 추천 1", "대체 추천 2", "검출 단계"]
    return df[cols]


# ──────────────────────────────────────────────
# 일괄 검토 종합 결과 엑셀(.xlsx) 생성
# ──────────────────────────────────────────────
def build_batch_workbook(batch: list[dict], neis_limit: int = 0) -> bytes:
    """일괄 검토 결과(list of dict)를 여러 시트의 엑셀 통합문서(bytes)로 조립한다.

    시트 구성:
    - "요약": 학생/글자 수/위반/주의/(품질 평균)/(오탈자)
    - "검출 상세": 검출 항목별 행 (학생/검출어/심각도/사유/근거/추천1/추천2)
    - "수정본": 학생/수정본 (revised가 있는 항목만, 없으면 시트 생략)
    - "품질 진단": 학생/기준별 점수/종합/총평 (품질 결과가 있으면)

    batch 각 항목 키: name, text, findings(list), 선택적 revised/quality/proofread.
    빈 batch는 요약 시트만 있는 통합문서를 반환한다.
    """
    batch = batch or []
    any_quality = any(b.get("quality") for b in batch)
    any_proofread = any(b.get("proofread") is not None for b in batch)

    # 요약 시트
    summary_rows = []
    for b in batch:
        text = b.get("text", "")
        findings = b.get("findings", [])
        n_violation = sum(1 for f in findings if f.get("severity", "위반") == "위반")
        n_caution = len(findings) - n_violation
        row = {
            "학생": b.get("name", ""),
            "글자 수": len(text),
            "위반": n_violation,
            "주의": n_caution,
        }
        if neis_limit > 0:
            row["분량"] = (
                f"{len(text) - neis_limit}자 초과"
                if len(text) > neis_limit
                else "이내"
            )
        if any_quality:
            q = b.get("quality")
            scores = q.get("scores", []) if q else []
            row["품질(평균)"] = (
                round(sum(float(s.get("score", 0)) for s in scores) / len(scores), 1)
                if scores
                else "-"
            )
        if any_proofread:
            pr = b.get("proofread")
            row["오탈자"] = len(pr) if pr is not None else "-"
        summary_rows.append(row)
    summary_df = pd.DataFrame(summary_rows) if summary_rows else pd.DataFrame(
        columns=["학생", "글자 수", "위반", "주의"]
    )

    # 검출 상세 시트
    detail_rows = []
    for b in batch:
        for f in b.get("findings", []):
            detail_rows.append(
                {
                    "학생": b.get("name", ""),
                    "검출어": f.get("word", ""),
                    "심각도": f.get("severity", "위반"),
                    "사유": f.get("reason", ""),
                    "근거": f.get("basis", ""),
                    "추천1": f.get("suggestion_1", ""),
                    "추천2": f.get("suggestion_2", ""),
                }
            )
    detail_df = pd.DataFrame(detail_rows) if detail_rows else pd.DataFrame(
        columns=["학생", "검출어", "심각도", "사유", "근거", "추천1", "추천2"]
    )

    # 수정본 시트 (revised가 있는 항목만)
    revised_rows = [
        {"학생": b.get("name", ""), "수정본": b["revised"]}
        for b in batch
        if b.get("revised")
    ]

    # 품질 진단 시트
    quality_rows = []
    for b in batch:
        q = b.get("quality")
        if not q:
            continue
        row = {"학생": b.get("name", "")}
        scores = q.get("scores", [])
        for s in scores:
            row[str(s.get("criterion", ""))] = s.get("score", "-")
        if scores:
            row["종합"] = round(
                sum(float(s.get("score", 0)) for s in scores) / len(scores), 1
            )
        row["총평"] = q.get("overall", "")
        quality_rows.append(row)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="요약", index=False)
        if batch:
            detail_df.to_excel(writer, sheet_name="검출 상세", index=False)
        if revised_rows:
            pd.DataFrame(revised_rows).to_excel(
                writer, sheet_name="수정본", index=False
            )
        if quality_rows:
            pd.DataFrame(quality_rows).to_excel(
                writer, sheet_name="품질 진단", index=False
            )
    return buf.getvalue()
