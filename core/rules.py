# -*- coding: utf-8 -*-
"""규칙 기반 검출 / 문체 점검 / 유사도 검사 (API 불필요, 순수 로직)."""

import difflib
import re
from collections import Counter

# NEIS 항목별 입력 제한 (공백 포함 글자 수 기준)
NEIS_LIMITS = {
    "세특 (과목별·개인별, 500자)": 500,
    "자율·자치활동 (500자)": 500,
    "동아리활동 (500자)": 500,
    "진로활동 (700자)": 700,
    "행동특성 및 종합의견 (500자)": 500,
    "제한 없음": 0,
}

# 학생 간 유사도 경고 기준 (0~1)
SIMILARITY_THRESHOLD = 0.55


def neis_bytes(text: str) -> int:
    """NEIS 기준 바이트 수 (한글 등 멀티바이트 문자 3바이트, 그 외 1바이트)."""
    return sum(3 if ord(ch) > 127 else 1 for ch in text)


# ──────────────────────────────────────────────
# 규칙 기반 고속 필터링
# ──────────────────────────────────────────────
RULE_PATTERNS = [
    # 공인어학성적 / 모의고사 성적 패턴 (예: TOEIC 900점, 국어 1등급, 백분위 98%)
    (
        r"[A-Za-z가-힣]+\s*\d+\s*(?:점|급|등급|%)",
        "성적/점수 표기 의심 (공인어학성적·모의고사 성적 기재 금지)",
        "기재요령: 공인어학시험·교외 성적·석차 기재 불가",
    ),
    # 대표적인 공인어학시험 명칭
    (
        r"(?:TOEIC|TOEFL|TEPS|IELTS|HSK|JLPT|JPT|DELE|DELF|G-?TELP|OPIc|토익|토플|텝스|아이엘츠)",
        "공인어학시험 명칭 (기재 금지)",
        "기재요령: 공인어학시험 성적 기재 불가",
    ),
    # 모의고사 언급
    (
        r"(?:전국연합학력평가|모의고사|모의평가|학력평가)\s*(?:성적|점수|등급|결과)?",
        "모의고사 관련 표현 의심",
        "기재요령: 교외 성적·석차 기재 불가",
    ),
    # 학교 명칭 (재학 중인 학교명 기재 금지 — 의심 수준으로 표시)
    (
        r"[가-힣]{2,}(?:여자고등학교|고등학교|여고|고교)",
        "학교 명칭 의심 (재학 중인 학교명 기재 금지)",
        "기재요령: 재학 학교명 등 특정 가능 정보 기재 불가",
    ),
    # 주요 대학명
    (
        r"(?:서울대|연세대|고려대|성균관대|한양대|서강대|중앙대|경희대|이화여대|한국외대|"
        r"서울시립대|건국대|동국대|홍익대|카이스트|KAIST|포스텍|POSTECH|유니스트|UNIST|지스트|GIST)(?:학교)?",
        "특정 대학명 의심 (기재 금지)",
        "기재요령: 특정 대학·기관명 기재 불가",
    ),
    # 상업적 명칭/브랜드
    (
        # '줌'은 '보여줌' 같은 일반 어미와 겹치므로 한글 경계를 요구한다
        r"(?:구글|유튜브|네이버|카카오톡?|인스타그램|페이스북|틱톡|챗GPT|ChatGPT|GPT|제미나이|"
        r"(?<![가-힣])줌(?![가-힣])|Zoom|넷플릭스|아이폰|아이패드|갤럭시|파워포인트|엑셀)",
        "상업적 명칭/브랜드 의심",
        "기재요령: 상업적 명칭 기재 불가",
    ),
]


def rule_based_filter(text: str, custom_words: list[str] | None = None) -> list[dict]:
    """정규표현식 + 사용자 정의 금칙어로 명백한 금지 패턴을 우선 검출한다."""
    findings = []
    seen = set()
    for pattern, reason, basis in RULE_PATTERNS:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            word = m.group().strip()
            if word and word not in seen:
                seen.add(word)
                findings.append(
                    {
                        "word": word,
                        "reason": reason,
                        "basis": basis,
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
                    "basis": "사용자 정의 금칙어",
                    "suggestion_1": "해당 표현 삭제 또는 일반 명사로 대체",
                    "suggestion_2": "맥락에 맞는 중립적 표현으로 대체",
                }
            )
    return findings


def parse_custom_words(raw: str) -> list[str]:
    """쉼표/줄바꿈으로 구분된 사용자 정의 금칙어 문자열을 리스트로 변환한다."""
    return [w.strip() for w in re.split(r"[,\n]", raw) if w.strip()]


def filter_ignored(findings: list[dict], ignored) -> list[dict]:
    """ignored(단어 집합)에 포함된 검출어를 제외한다."""
    ignore_set = set(ignored)
    return [f for f in findings if f.get("word") not in ignore_set]


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
