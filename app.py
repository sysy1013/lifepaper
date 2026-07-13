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

import difflib
import hmac
import html
import io
import json
import re
import zipfile
import zlib
from collections import Counter

import google.generativeai as genai
import olefile
import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────
# 페이지 기본 설정
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="생기부 도우미",
    page_icon="📋",
    layout="wide",
)

GEMINI_MODEL = "gemini-2.5-flash"

# NEIS 항목별 입력 제한 (공백 포함 글자 수 기준)
NEIS_LIMITS = {
    "과목별 세특 (500자)": 500,
    "개인별 세특 (500자)": 500,
    "자율·자치활동 (500자)": 500,
    "동아리활동 (500자)": 500,
    "진로활동 (700자)": 700,
    "행동특성 및 종합의견 (500자)": 500,
    "제한 없음": 0,
}

# 학생 간 유사도 경고 기준 (0~1)
SIMILARITY_THRESHOLD = 0.55


# ──────────────────────────────────────────────
# 파일 텍스트 추출 (.txt / .hwp / .hwpx)
# ──────────────────────────────────────────────
def _parse_hwp_body_text(data: bytes) -> str:
    """HWP 5.0 BodyText 섹션 레코드에서 본문 텍스트(HWPTAG_PARA_TEXT=67)를 추출한다."""
    out = []
    pos = 0
    n = len(data)
    while pos + 4 <= n:
        header = int.from_bytes(data[pos : pos + 4], "little")
        tag = header & 0x3FF
        size = (header >> 20) & 0xFFF
        pos += 4
        if size == 0xFFF:  # 확장 크기
            size = int.from_bytes(data[pos : pos + 4], "little")
            pos += 4
        if tag == 67:  # HWPTAG_PARA_TEXT
            chunk = data[pos : pos + size]
            i = 0
            while i + 2 <= len(chunk):
                code = int.from_bytes(chunk[i : i + 2], "little")
                if code in (10, 13):
                    out.append("\n")
                    i += 2
                elif code < 32:
                    # 문자 컨트롤(0, 24~31)은 2바이트, 인라인/확장 컨트롤은 16바이트
                    i += 2 if code in (0, 24, 25, 26, 27, 28, 29, 30, 31) else 16
                else:
                    out.append(chr(code))
                    i += 2
            out.append("\n")
        pos += size
    return "".join(out)


def extract_hwp_text(file_bytes: bytes) -> str:
    """HWP(한글 5.0) 파일에서 본문 텍스트를 추출한다. 실패 시 미리보기(PrvText)로 대체."""
    ole = olefile.OleFileIO(io.BytesIO(file_bytes))
    try:
        file_header = ole.openstream("FileHeader").read()
        compressed = bool(file_header[36] & 1)

        sections = sorted(
            (e for e in ole.listdir() if e[0] == "BodyText"),
            key=lambda e: int(re.sub(r"\D", "", e[1]) or 0),
        )
        texts = []
        for entry in sections:
            raw = ole.openstream(entry).read()
            if compressed:
                raw = zlib.decompress(raw, -15)
            texts.append(_parse_hwp_body_text(raw))

        body = "\n".join(t for t in texts if t.strip())
        if body.strip():
            return body

        if ole.exists("PrvText"):
            return ole.openstream("PrvText").read().decode("utf-16-le", errors="ignore")
        return ""
    finally:
        ole.close()


def extract_hwpx_text(file_bytes: bytes) -> str:
    """HWPX(OWPML) 파일에서 본문 텍스트를 추출한다."""
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        section_names = sorted(
            n for n in zf.namelist() if re.match(r"Contents/section\d+\.xml", n)
        )
        texts = []
        for name in section_names:
            xml = zf.read(name).decode("utf-8", errors="ignore")
            xml = re.sub(r"</hp:p>", "\n", xml)
            texts.append(re.sub(r"<[^>]+>", "", xml))
        return html.unescape("\n".join(texts))


def read_uploaded_file(uploaded_file) -> str:
    """업로드 파일의 확장자에 따라 텍스트를 추출한다."""
    name = uploaded_file.name.lower()
    # read()는 재실행 시 포인터가 끝에 있어 빈 값이 될 수 있으므로 getvalue() 사용
    data = uploaded_file.getvalue()
    if name.endswith(".hwp"):
        return extract_hwp_text(data)
    if name.endswith(".hwpx"):
        return extract_hwpx_text(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp949")


def file_stem(name: str) -> str:
    return re.sub(r"\.(hwp|hwpx|txt)$", "", name, flags=re.IGNORECASE)


def unique_names(names: list[str]) -> list[str]:
    """중복된 파일명(stem)에 순번을 붙여 위젯 key·ZIP 내 파일명 충돌을 방지한다."""
    seen: Counter = Counter()
    out = []
    for n in names:
        seen[n] += 1
        out.append(n if seen[n] == 1 else f"{n} ({seen[n]})")
    return out


# ──────────────────────────────────────────────
# 규칙 기반 고속 필터링
# ──────────────────────────────────────────────
RULE_PATTERNS = [
    # 공인어학성적 / 모의고사 성적 패턴 (예: TOEIC 900점, 국어 1등급, 백분위 98%)
    (
        r"[A-Za-z가-힣]+\s*\d+\s*(?:점|급|등급|%)",
        "성적/점수 표기 의심 (공인어학성적·모의고사 성적 기재 금지)",
    ),
    # 대표적인 공인어학시험 명칭
    (
        r"(?:TOEIC|TOEFL|TEPS|IELTS|HSK|JLPT|JPT|DELE|DELF|G-?TELP|OPIc|토익|토플|텝스|아이엘츠)",
        "공인어학시험 명칭 (기재 금지)",
    ),
    # 모의고사 언급
    (
        r"(?:전국연합학력평가|모의고사|모의평가|학력평가)\s*(?:성적|점수|등급|결과)?",
        "모의고사 관련 표현 의심",
    ),
    # 학교 명칭 (재학 중인 학교명 기재 금지 — 의심 수준으로 표시)
    (
        r"[가-힣]{2,}(?:여자고등학교|고등학교|여고|고교)",
        "학교 명칭 의심 (재학 중인 학교명 기재 금지)",
    ),
    # 주요 대학명
    (
        r"(?:서울대|연세대|고려대|성균관대|한양대|서강대|중앙대|경희대|이화여대|한국외대|"
        r"서울시립대|건국대|동국대|홍익대|카이스트|KAIST|포스텍|POSTECH|유니스트|UNIST|지스트|GIST)(?:학교)?",
        "특정 대학명 의심 (기재 금지)",
    ),
    # 상업적 명칭/브랜드
    (
        r"(?:구글|유튜브|네이버|카카오톡?|인스타그램|페이스북|틱톡|챗GPT|ChatGPT|GPT|제미나이|"
        r"줌|Zoom|넷플릭스|아이폰|아이패드|갤럭시|파워포인트|엑셀)",
        "상업적 명칭/브랜드 의심",
    ),
]


def rule_based_filter(text: str, custom_words: list[str] | None = None) -> list[dict]:
    """정규표현식 + 사용자 정의 금칙어로 명백한 금지 패턴을 우선 검출한다."""
    findings = []
    seen = set()
    for pattern, reason in RULE_PATTERNS:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            word = m.group().strip()
            if word and word not in seen:
                seen.add(word)
                findings.append(
                    {
                        "word": word,
                        "reason": reason,
                        "suggestion_1": "해당 표현 삭제 후 학습 과정 중심으로 서술",
                        "suggestion_2": "구체적 명칭·수치 대신 성장 과정과 노력을 서술",
                    }
                )
    for word in custom_words or []:
        word = word.strip()
        if word and word not in seen and word in text:
            seen.add(word)
            findings.append(
                {
                    "word": word,
                    "reason": "사용자 정의 금칙어",
                    "suggestion_1": "해당 표현 삭제 또는 일반 명사로 대체",
                    "suggestion_2": "맥락에 맞는 중립적 표현으로 대체",
                }
            )
    return findings


def parse_custom_words(raw: str) -> list[str]:
    """쉼표/줄바꿈으로 구분된 사용자 정의 금칙어 문자열을 리스트로 변환한다."""
    return [w.strip() for w in re.split(r"[,\n]", raw) if w.strip()]


# ──────────────────────────────────────────────
# 개인정보 마스킹 (Gemini API 전송 전 치환, 결과에서 복원)
# ──────────────────────────────────────────────
def build_mask_map(mask_words: list[str]) -> list[tuple[str, str]]:
    """마스킹 단어 → 치환 토큰 쌍 목록을 만든다. 긴 단어부터 치환되도록 정렬."""
    uniq = sorted({w.strip() for w in mask_words if w.strip()}, key=len, reverse=True)
    return [(w, f"《비공개{i + 1}》") for i, w in enumerate(uniq)]


def apply_mask(text: str, mask_map: list[tuple[str, str]]) -> str:
    """API 전송 전 개인정보 단어를 토큰으로 치환한다."""
    for word, token in mask_map:
        text = text.replace(word, token)
    return text


def remove_mask(text: str, mask_map: list[tuple[str, str]]) -> str:
    """API 응답에 남은 토큰을 원래 단어로 복원한다."""
    for word, token in mask_map:
        text = text.replace(token, word)
    return text


# ──────────────────────────────────────────────
# 문체·상투 표현 점검 (규칙 기반, API 불필요)
# ──────────────────────────────────────────────
CLICHE_EXPRESSIONS = [
    "적극적으로 참여함",
    "적극적으로 참여하",
    "성실하게 임",
    "성실한 태도",
    "열심히 노력",
    "최선을 다함",
    "최선을 다하는",
    "눈에 띄",
    "매우 우수함",
    "탁월한 능력",
    "뛰어난 역량을 보임",
    "훌륭한 자세",
    "많은 것을 배움",
    "큰 도움이 됨",
    "모범이 됨",
]


def style_check(text: str) -> list[str]:
    """개조식 어미 위반, 어미 반복, 상투적 표현을 점검하여 경고 목록을 반환한다."""
    warnings = []

    # 1) 개조식이 아닌 종결어미 (~했다, ~합니다 등)
    bad_endings = re.findall(
        r"[가-힣]+(?:했다|하였다|한다|이다|입니다|합니다|했습니다|하였습니다|있다|있었다|해요|했어요)(?=[.\s]|$)",
        text,
    )
    if bad_endings:
        uniq = list(dict.fromkeys(bad_endings))
        sample = ", ".join(f"「{w}」" for w in uniq[:5])
        warnings.append(
            f"개조식이 아닌 종결어미 {len(bad_endings)}회 발견 — 명사형 어미('~함', '~임')로 통일 권장: {sample}"
        )

    # 2) 같은 종결 어미의 과도한 반복
    sentence_ends = re.findall(r"([가-힣])(?=\.)", text)
    if len(sentence_ends) >= 5:
        counter = Counter(sentence_ends)
        top_char, top_count = counter.most_common(1)[0]
        if top_count / len(sentence_ends) > 0.6:
            warnings.append(
                f"종결 어미 '~{top_char}.'이 {top_count}/{len(sentence_ends)}문장에서 반복됨 — "
                "'~보임', '~드러남', '~기름' 등으로 다양화 권장"
            )

    # 3) 상투적(클리셰) 표현
    found = [c for c in CLICHE_EXPRESSIONS if c in text]
    if found:
        warnings.append(
            "상투적 표현 발견 — 구체적 행동·산출물 서술로 대체 권장: "
            + ", ".join(f"「{c}」" for c in found)
        )

    return warnings


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
# 학생 간 유사도 검사 (동일·유사 문장 복붙 방지)
# ──────────────────────────────────────────────
def _normalize_for_similarity(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def find_similar_pairs(
    items: list[tuple[str, str]], threshold: float = SIMILARITY_THRESHOLD
) -> list[tuple[str, str, float]]:
    """(이름, 텍스트) 목록에서 유사도가 임계값을 넘는 쌍을 찾는다."""
    pairs = []
    normalized = [(name, _normalize_for_similarity(t)) for name, t in items]
    for i in range(len(normalized)):
        for j in range(i + 1, len(normalized)):
            ratio = difflib.SequenceMatcher(
                None, normalized[i][1], normalized[j][1]
            ).ratio()
            if ratio >= threshold:
                pairs.append((normalized[i][0], normalized[j][0], ratio))
    return sorted(pairs, key=lambda p: -p[2])


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
# Gemini 문맥 심사 (LLM-as-a-Judge)
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """너는 교육부 '학교생활기록부 기재요령'을 완벽하게 숙지한 심사관이다.
주어진 생기부 텍스트에서 아래 8가지 기준에 해당하는 '기재 금지 표현'을 모두 찾아라.

[심사 기준]
1. 상업적 명칭/특정 브랜드 (예: 구글, 줌, 유튜브, 네이버 등)
2. 교외상/교외 대회 수상 실적
3. 특정 대학/기관/강사명
4. 부모의 직업/사회적 지위를 암시하는 표현
5. 해외 활동 (어학연수, 해외 봉사 등)
6. 논문/출판/특허 실적
7. 장학생/장학금 관련 내용
8. 재학 중인 학교 명칭

[출력 규칙 - 반드시 준수]
- 결과는 반드시 JSON 배열(List of Dicts)로만 반환한다. 다른 설명 문장을 절대 붙이지 않는다.
- 각 항목은 다음 5개의 키를 반드시 포함한다:
  "word": 원문에서 발견된 표현 그대로 (원문 텍스트와 완전히 동일한 문자열)
  "reason": 위반 사유 (위 8가지 기준 중 해당 항목)
  "severity": "위반"(명백한 기재 금지) 또는 "주의"(맥락상 문제 소지)
  "suggestion_1": 학생의 희망 진로와 연결한 대체 표현 1
  "suggestion_2": 학생의 희망 진로와 연결한 대체 표현 2
- 위반 사항이 없으면 빈 배열 []을 반환한다.

[출력 예시]
[{"word": "의사인 아버지", "reason": "부모의 직업 암시", "severity": "위반", "suggestion_1": "가족의 헌신을 보며", "suggestion_2": "생명 존중의 가치를 배우며"}]
"""


def analyze_with_gemini(text: str, major: str, api_key: str) -> list[dict]:
    """전체 텍스트와 희망 진로를 Gemini에 전달하여 위반 표현 목록(JSON)을 받아온다."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        generation_config={
            "temperature": 0.2,
            "response_mime_type": "application/json",
        },
    )

    user_prompt = (
        f"[학생의 희망 진로/학과]\n{major if major.strip() else '미입력'}\n\n"
        f"[심사 대상 생기부 텍스트]\n{text}\n\n"
        "위 텍스트를 심사하여 JSON 배열로만 답하라."
    )

    response = model.generate_content(user_prompt)
    raw = response.text.strip()

    # 혹시 모를 마크다운 코드펜스 제거 (```json ... ```)
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()

    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("Gemini 응답이 JSON 배열 형식이 아닙니다.")

    results = []
    for item in parsed:
        if isinstance(item, dict) and item.get("word"):
            results.append(
                {
                    "word": str(item.get("word", "")).strip(),
                    "reason": str(item.get("reason", "")).strip(),
                    "severity": str(item.get("severity", "위반")).strip() or "위반",
                    "suggestion_1": str(item.get("suggestion_1", "")).strip(),
                    "suggestion_2": str(item.get("suggestion_2", "")).strip(),
                }
            )
    return results


def review_text(text: str, major: str, api_key: str, custom_words: list[str]) -> list[dict]:
    """규칙 기반 + Gemini 심사를 실행하고 병합된 위반 목록을 반환한다."""
    rule_findings = rule_based_filter(text, custom_words)
    gemini_findings = analyze_with_gemini(text, major, api_key)

    merged: dict[str, dict] = {}
    for item in rule_findings:
        merged[item["word"]] = {**item, "severity": "주의", "source": "규칙 기반"}
    for item in gemini_findings:
        merged[item["word"]] = {**item, "source": "Gemini 심사"}
    return list(merged.values())


def review_text_masked(
    text: str,
    major: str,
    api_key: str,
    custom_words: list[str],
    mask_map: list[tuple[str, str]],
) -> list[dict]:
    """개인정보를 마스킹한 상태로 검토하고, 결과의 토큰을 원래 단어로 복원한다."""
    findings = review_text(apply_mask(text, mask_map), major, api_key, custom_words)
    for f in findings:
        for k in ("word", "reason", "suggestion_1", "suggestion_2"):
            if f.get(k):
                f[k] = remove_mask(f[k], mask_map)
    return findings


# ──────────────────────────────────────────────
# Gemini 수정본 자동 생성 (검토 결과 반영)
# ──────────────────────────────────────────────
REWRITE_SYSTEM_PROMPT = """너는 교육부 '학교생활기록부 기재요령'을 완벽히 숙지한 대한민국 고등학교 교사다.
검토에서 발견된 기재 금지 표현 목록과 대체 표현 추천을 반영하여 생기부 텍스트의 '수정본'을 작성한다.

[수정 원칙]
1. 위반 표현만 자연스럽게 대체하거나 삭제하고, 나머지 문장·문체·구성은 원문 그대로 유지한다.
2. 대체 표현 추천을 참고하되, 문맥에 맞게 자연스럽게 다듬는다.
3. 원문에 없는 새로운 사실을 추가하지 않는다.
4. 수정 후에도 기재 금지 사항(상업적 명칭, 교외상, 특정 대학/기관, 부모 직업,
   해외 활동, 논문/특허, 장학금, 학교 명칭, 각종 성적·점수)이 남지 않도록 한다.

[출력 규칙]
- 수정된 전체 본문만 출력한다. 설명, 제목, 마크다운 서식을 붙이지 않는다.
"""


def rewrite_with_gemini(
    text: str, findings: list[dict], major: str, api_key: str
) -> str:
    """검토 결과(위반 목록)를 반영한 수정본 전문을 생성한다."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=REWRITE_SYSTEM_PROMPT,
        generation_config={"temperature": 0.3},
    )
    findings_lines = "\n".join(
        f"- 「{f['word']}」 (사유: {f['reason']}) → 추천: {f['suggestion_1']} / {f['suggestion_2']}"
        for f in findings
    )
    prompt = (
        f"[학생의 희망 진로/학과]\n{major.strip() or '미입력'}\n\n"
        f"[원문]\n{text}\n\n"
        f"[검토에서 발견된 위반 표현과 대체 추천]\n{findings_lines}\n\n"
        "위 위반 표현을 모두 반영하여 수정된 전체 본문만 출력하라."
    )
    response = model.generate_content(prompt)
    return response.text.strip()


# ──────────────────────────────────────────────
# Gemini 세특 품질 진단 (수석교사 루브릭)
# ──────────────────────────────────────────────
QUALITY_SYSTEM_PROMPT = """너는 학교생활기록부 기재를 컨설팅하는 대한민국 고등학교 수석교사다.
주어진 세특(세부능력 및 특기사항) 텍스트를 아래 5가지 루브릭 기준으로 각각 1~5점으로 평가하라.

[평가 루브릭]
1. 구체성: 활동이 추상적 칭찬이 아닌 구체적 사실·산출물·과정으로 서술되었는가
2. 개별성: 다른 학생에게 그대로 옮겨도 어색하지 않은 범용 문장이 아니라, 이 학생만의 고유한 모습이 드러나는가
3. 탐구 과정: 동기 → 과정 → 결과의 흐름이 논리적으로 이어지는가
4. 성장·변화: 배우고 느낀 점, 태도나 역량의 변화가 서술되었는가
5. 진로 연계 적절성: 진로와 자연스럽게 연결되면서도 과도하게 억지로 엮지 않았는가

[출력 규칙 - 반드시 준수]
반드시 아래 형식의 JSON 객체로만 반환한다. 다른 설명을 붙이지 않는다.
{
  "scores": [
    {"criterion": "구체성", "score": 4, "comment": "한 줄 평가"},
    {"criterion": "개별성", "score": 3, "comment": "한 줄 평가"},
    {"criterion": "탐구 과정", "score": 4, "comment": "한 줄 평가"},
    {"criterion": "성장·변화", "score": 2, "comment": "한 줄 평가"},
    {"criterion": "진로 연계", "score": 5, "comment": "한 줄 평가"}
  ],
  "overall": "수석교사로서의 총평 2~3문장",
  "improvements": ["가장 시급한 개선 제안 1", "개선 제안 2", "개선 제안 3"]
}
"""


def assess_quality_with_gemini(text: str, major: str, api_key: str) -> dict:
    """세특 텍스트를 수석교사 루브릭으로 평가한 결과(JSON)를 받아온다."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=QUALITY_SYSTEM_PROMPT,
        generation_config={
            "temperature": 0.3,
            "response_mime_type": "application/json",
        },
    )
    prompt = (
        f"[학생의 희망 진로/학과]\n{major.strip() or '미입력'}\n\n"
        f"[평가 대상 세특 텍스트]\n{text}\n\n"
        "위 텍스트를 루브릭으로 평가하여 JSON으로만 답하라."
    )
    response = model.generate_content(prompt)
    raw = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", response.text.strip(), flags=re.MULTILINE
    ).strip()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or "scores" not in parsed:
        raise ValueError("Gemini 응답이 기대한 JSON 형식이 아닙니다.")
    return parsed


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
# Gemini 세특 초안 생성 / 재생성
# ──────────────────────────────────────────────
DRAFT_SYSTEM_PROMPT = """너는 교육부 '학교생활기록부 기재요령'을 완벽히 숙지한 대한민국 고등학교 교사다.
'과목별 세부능력 및 특기사항(세특)' 초안을 작성한다.

[작성 원칙]
1. 교사가 학생을 관찰하여 기록하는 시점으로 서술한다. 학생 이름이나 인칭 대명사는 쓰지 않는다.
2. 문장은 개조식 명사형 어미('~함', '~임', '~보임', '~드러남' 등)로 끝맺되, 같은 어미를 반복하지 않는다.
3. 활동 동기 → 탐구 과정 → 배우고 느낀 점 → 후속 활동/성장 의 흐름으로 구성한다.
4. 학생의 희망 진로와 자연스럽게 연결하되, 특정 대학·기관명은 쓰지 않는다.
   과도하게 진로와 엮지 말고 해당 과목에 대한 역량이 드러나도록 쓴다.
5. '적극적으로 참여함', '성실한 태도', '최선을 다함' 같은 상투적 표현 대신
   구체적 행동과 산출물이 드러나는 서술을 사용한다.
6. 다음 기재 금지 사항을 절대 포함하지 않는다:
   상업적 명칭/특정 브랜드, 교외상/교외 대회, 특정 대학/기관/강사명, 부모의 직업/지위 암시,
   해외 활동, 논문/출판/특허, 장학생/장학금, 재학 중인 학교 명칭,
   공인어학성적·모의고사 성적 등 각종 점수·등급.
7. 제공된 자료(수행평가 내용, 학생 자기평가서)에 있는 사실만 활용한다.
   자료에 없는 구체적 사실(수치, 수상 실적, 자료명 등)을 지어내지 않는다.
   교육적 의미 부여, 성장 과정 서술 등 일반적 표현으로 다듬는 것은 허용된다.
8. 학생 자기평가서가 제공된 경우 그 내용을 우선 활용하고, 수행평가 내용으로 보완한다.
9. 요청된 목표 분량(공백 포함 글자 수)에 최대한 가깝게 작성한다.

[출력 규칙]
- 완성된 세특 초안 본문만 출력한다. 제목, 인사말, 부가 설명, 마크다운 서식을 붙이지 않는다.
"""


def generate_draft_with_gemini(
    subject: str,
    major: str,
    performance: str,
    self_eval: str,
    target_len: int,
    api_key: str,
) -> str:
    """입력 자료를 바탕으로 세특 초안을 생성한다."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=DRAFT_SYSTEM_PROMPT,
        generation_config={"temperature": 0.7},
    )

    parts = [
        f"[과목명]\n{subject.strip() or '미입력'}",
        f"[학생의 희망 진로/학과]\n{major.strip() or '미입력'}",
        f"[목표 분량]\n공백 포함 약 {target_len}자",
    ]
    if performance.strip():
        parts.append(f"[수행평가 활동 내용 (교사 입력)]\n{performance.strip()}")
    if self_eval.strip():
        parts.append(f"[학생 자기평가서 원문]\n{self_eval.strip()}")
        parts.append("학생 자기평가서 내용을 우선 활용하고, 수행평가 내용으로 보완하여 작성하라.")
    else:
        parts.append("학생 자기평가서가 없으므로 수행평가 활동 내용을 기반으로 작성하라.")

    parts.append("위 자료를 바탕으로 세특 초안 본문만 출력하라.")

    response = model.generate_content("\n\n".join(parts))
    return response.text.strip()


def refine_draft_with_gemini(
    draft: str, feedback: str, target_len: int, api_key: str
) -> str:
    """기존 초안에 교사 피드백을 반영하여 재작성한다."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=DRAFT_SYSTEM_PROMPT,
        generation_config={"temperature": 0.7},
    )
    prompt = (
        f"[기존 세특 초안]\n{draft}\n\n"
        f"[교사 피드백]\n{feedback.strip()}\n\n"
        f"[목표 분량]\n공백 포함 약 {target_len}자\n\n"
        "기존 초안을 교사 피드백에 맞게 수정하여 세특 초안 본문만 출력하라. "
        "피드백과 무관한 부분은 최대한 유지한다."
    )
    response = model.generate_content(prompt)
    return response.text.strip()


# ──────────────────────────────────────────────
# Gemini 오탈자·맞춤법 검사
# ──────────────────────────────────────────────
PROOFREAD_SYSTEM_PROMPT = """너는 한국어 맞춤법·표기 교정 전문가이자 고등학교 생기부 감수자다.
주어진 텍스트에서 오탈자, 맞춤법 오류, 띄어쓰기 오류, 조사 오용, 명백한 단어 중복을 찾아라.

[주의]
- 생기부 특유의 개조식 명사형 어미('~함', '~임', '~보임' 등)는 오류가 아니다.
- 문장 스타일이나 내용에 대한 제안은 하지 않는다. 표기 오류만 찾는다.

[출력 규칙 - 반드시 준수]
- JSON 배열로만 반환한다. 다른 설명을 붙이지 않는다.
- 각 항목: {"wrong": "원문 그대로의 오류 부분", "correct": "교정안", "reason": "오류 유형 한 줄"}
- "wrong"은 원문 텍스트와 완전히 동일한 문자열이어야 한다.
- 오류가 없으면 빈 배열 []을 반환한다.
"""


def proofread_with_gemini(text: str, api_key: str) -> list[dict]:
    """텍스트의 오탈자·맞춤법 오류 목록(JSON)을 받아온다."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=PROOFREAD_SYSTEM_PROMPT,
        generation_config={
            "temperature": 0.1,
            "response_mime_type": "application/json",
        },
    )
    response = model.generate_content(
        f"[검사 대상 텍스트]\n{text}\n\n오탈자·표기 오류를 JSON 배열로만 답하라."
    )
    raw = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", response.text.strip(), flags=re.MULTILINE
    ).strip()
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("Gemini 응답이 JSON 배열 형식이 아닙니다.")
    return [
        {
            "wrong": str(i.get("wrong", "")).strip(),
            "correct": str(i.get("correct", "")).strip(),
            "reason": str(i.get("reason", "")).strip(),
        }
        for i in parsed
        if isinstance(i, dict) and i.get("wrong")
    ]


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
# Gemini 분량 조절 (줄이기/늘리기)
# ──────────────────────────────────────────────
ADJUST_SYSTEM_PROMPT = """너는 대한민국 고등학교 교사이며 생기부 문장 분량 조절 전문가다.
원문의 사실·내용·개조식 문체를 그대로 유지하면서 목표 글자 수(공백 포함)에 맞게 본문을 줄이거나 늘린다.

[조절 원칙]
1. 줄일 때: 중복 표현과 군더더기 수식어를 먼저 정리하고, 핵심 사실·활동·성장 서술은 유지한다.
2. 늘릴 때: 원문에 없는 새로운 사실(수치, 자료명, 활동, 수상 등)을 지어내지 않는다.
   이미 있는 내용의 과정·의미·배운 점을 자연스럽게 풀어 쓴다.
3. 개조식 명사형 어미와 문장 순서를 최대한 유지한다.
4. 목표 글자 수 ±5% 이내로 맞춘다.

[출력 규칙]
- 조절된 본문만 출력한다. 설명·제목·서식을 붙이지 않는다.
"""


def adjust_length_with_gemini(text: str, target_len: int, api_key: str) -> str:
    """본문을 목표 글자 수(공백 포함)에 맞게 조절한다. 크게 벗어나면 1회 재조정."""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=ADJUST_SYSTEM_PROMPT,
        generation_config={"temperature": 0.3},
    )
    prompt = (
        f"[원문 — 공백 포함 {len(text)}자]\n{text}\n\n"
        f"[목표 분량]\n공백 포함 {target_len}자 (±5% 이내)\n\n"
        "조절된 본문만 출력하라."
    )
    result = model.generate_content(prompt).text.strip()

    if target_len and abs(len(result) - target_len) / target_len > 0.10:
        direction = "더 줄여라" if len(result) > target_len else "더 늘려라"
        retry_prompt = (
            f"[원문 — 공백 포함 {len(result)}자]\n{result}\n\n"
            f"[목표 분량]\n공백 포함 {target_len}자 (±5% 이내). "
            f"현재 {len(result)}자이므로 {direction}.\n\n"
            "조절된 본문만 출력하라."
        )
        result = model.generate_content(retry_prompt).text.strip()
    return result


def render_length_adjuster(
    state_key: str,
    api_key: str,
    mask_map: list[tuple[str, str]],
    default_len: int,
    widget_key: str,
    clear_keys: tuple[str, ...] = (),
) -> None:
    """세션 상태 state_key에 저장된 본문의 분량을 목표 글자 수로 조절해 제자리 교체한다."""
    text = st.session_state.get(state_key, "")
    if not text:
        return
    st.markdown("**📏 분량 조절 (줄이기/늘리기)**")
    c1, c2 = st.columns([2, 1], vertical_alignment="bottom")
    target = c1.number_input(
        "목표 글자 수 (공백 포함)",
        min_value=100,
        max_value=3000,
        value=min(max(default_len or len(text), 100), 3000),
        step=10,
        key=f"len_{widget_key}",
    )
    if c2.button("✂️ 분량 맞추기", key=f"adj_{widget_key}", use_container_width=True):
        if not api_key.strip():
            st.warning("⚠️ 서버에 Gemini API Key가 설정되지 않아 실행할 수 없습니다.")
        else:
            with st.spinner(f"분량 조절 중… ({GEMINI_MODEL})"):
                try:
                    adjusted = adjust_length_with_gemini(
                        apply_mask(text, mask_map), int(target), api_key
                    )
                    st.session_state[state_key] = remove_mask(adjusted, mask_map)
                    for k in clear_keys:
                        st.session_state.pop(k, None)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 분량 조절 실패: {e}")
    st.caption(
        f"현재 **{len(text):,}자** → 목표 {int(target):,}자. "
        "조절하면 본문이 새 버전으로 교체됩니다 (사실 추가 없이 줄이기/풀어쓰기)."
    )


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

            batch_reviews = []
            progress = st.progress(0.0, text="일괄 검토 중…")
            stems = unique_names([file_stem(f.name) for f in review_files])
            for idx, (f, stem) in enumerate(zip(review_files, stems)):
                try:
                    text = read_uploaded_file(f)
                    findings = review_text_masked(
                        text, major, api_key, custom_words, mask_map
                    )
                    batch_reviews.append(
                        {"name": stem, "text": text, "findings": findings, "error": ""}
                    )
                except Exception as e:
                    batch_reviews.append(
                        {"name": stem, "text": "", "findings": [], "error": str(e)}
                    )
                progress.progress(
                    (idx + 1) / len(review_files),
                    text=f"일괄 검토 중… ({idx + 1}/{len(review_files)}) {stem}",
                )
            progress.empty()
            st.session_state["batch_review"] = batch_reviews
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
                except json.JSONDecodeError:
                    st.error("❌ Gemini 응답을 JSON으로 파싱하지 못했습니다. 다시 시도해 주세요.")
                except Exception as e:
                    st.error(f"❌ Gemini API 호출 실패: {e}")

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
            summary_rows.append(
                {
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
            )
        if summary_rows:
            summary_df = pd.DataFrame(summary_rows)
            st.dataframe(summary_df, use_container_width=True, hide_index=True)
            st.download_button(
                "📥 반 전체 검토 요약 CSV 다운로드",
                data=summary_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="생기부_일괄검토_요약.csv",
                mime="text/csv",
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
            batch_results = []
            progress = st.progress(0.0, text="일괄 초안 생성 중…")
            stems = unique_names([file_stem(f.name) for f in eval_files])
            for idx, (f, stem) in enumerate(zip(eval_files, stems)):
                try:
                    eval_text = read_uploaded_file(f)
                    draft = generate_draft_with_gemini(
                        subject,
                        major,
                        apply_mask(performance_text, mask_map),
                        apply_mask(eval_text, mask_map),
                        target_len,
                        api_key,
                    )
                    batch_results.append(
                        {"name": stem, "draft": remove_mask(draft, mask_map), "error": ""}
                    )
                except Exception as e:
                    batch_results.append({"name": stem, "draft": "", "error": str(e)})
                progress.progress(
                    (idx + 1) / len(eval_files),
                    text=f"일괄 초안 생성 중… ({idx + 1}/{len(eval_files)}) {stem}",
                )
            progress.empty()
            st.session_state["batch_drafts"] = batch_results
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
                        )
                        st.session_state["draft_text"] = remove_mask(refined, mask_map)
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
