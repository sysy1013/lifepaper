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
