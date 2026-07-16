# -*- coding: utf-8 -*-
"""하이라이트 HTML 렌더링 / 검토 결과 DataFrame 변환."""

import html
import re

import pandas as pd

# ──────────────────────────────────────────────
# 하이라이트 렌더링 / 분량 표시
# ──────────────────────────────────────────────
HIGHLIGHT_STYLE = "background-color: yellow; color: red; font-weight: bold;"


def highlight_text(original: str, words: list[str]) -> str:
    """원본 텍스트에서 위반 단어들을 <span> 하이라이트 처리한 HTML을 만든다.

    원문을 단일 패스로 스캔하므로 검출어끼리 겹쳐도 span이 중첩되지 않는다.
    긴 단어를 정규식 대안(|) 앞에 두어 같은 위치에서는 긴 매치가 우선한다.
    """
    unique_words = sorted({w for w in words if w.strip()}, key=len, reverse=True)
    if not unique_words:
        return html.escape(original).replace("\n", "<br>")

    pattern = "|".join(re.escape(w) for w in unique_words)
    parts = []
    last = 0
    for m in re.finditer(pattern, original):
        parts.append(html.escape(original[last : m.start()]))
        parts.append(
            f'<span style="{HIGHLIGHT_STYLE}">{html.escape(m.group())}</span>'
        )
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
            lines.append(
                f"- 「{f.get('word', '')}」 ({sev}/{f.get('reason', '')}) "
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
    return df.rename(
        columns={
            "word": "발견된 표현",
            "reason": "위반 사유",
            "severity": "구분",
            "suggestion_1": "대체 추천 1",
            "suggestion_2": "대체 추천 2",
            "source": "검출 단계",
        }
    )[["구분", "발견된 표현", "위반 사유", "대체 추천 1", "대체 추천 2", "검출 단계"]]
