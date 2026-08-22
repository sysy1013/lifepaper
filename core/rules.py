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
        # '줌'(Zoom 한글 표기)은 '보여 줌' 같은 개조식 종결과 겹쳐 오탐이 잦아 규칙에서 제외.
        # Gemini LLM 단계(core/gemini.py SYSTEM_PROMPT)가 이미 '줌'을 브랜드 예시로 명시해 판단함.
        r"(?:구글|유튜브|네이버|카카오톡?|인스타그램|페이스북|틱톡|챗GPT|ChatGPT|GPT|제미나이|"
        r"Zoom|넷플릭스|아이폰|아이패드|갤럭시|파워포인트|엑셀|"
        r"오픈AI|OpenAI|마이크로소프트|Microsoft|패들렛|Padlet|삼성(?!전자고|디지털))",
        "상업적 명칭/브랜드 의심",
        "기재요령: 상업적 명칭 기재 불가",
    ),
    # 국제/공공기관명 (교육관련기관 외 기관명 기재 금지)
    # 라틴 약어는 (?-i:...)로 대소문자 구분을 되살려 'who', 'un' 같은 영단어 오탐을 막는다.
    (
        r"(?:유네스코|통계청|세계보건기구|세계경제포럼|"
        r"(?-i:(?<![A-Za-z])(?:UNESCO|OECD|UN|IMF|WHO)(?![A-Za-z])))",
        "국제/공공기관명 의심 (교육관련기관 외 기관명 기재 금지)",
        "기재요령: 교육관련기관 외 기관명 기재 불가",
    ),
    # 자격증 명칭 (자격증 취득상황 이외 항목에는 기재 금지)
    (
        r"(?:컴퓨터활용능력|정보처리기사|워드프로세서|한국사능력검정시험|한국어능력시험|"
        r"(?-i:(?<![A-Za-z])(?:ITQ|TOPCIT|GTQ)(?![A-Za-z])))",
        "자격증 명칭 의심 (자격증 취득상황 이외 항목 기재 금지)",
        "기재요령: 자격증 명칭·취득 사실은 해당 항목 외 기재 불가",
    ),
    # 대회 참여·수상 실적
    (
        r"[가-힣A-Za-z]*(?:경시대회|올림피아드|콘테스트|공모전)(?:에서)?\s*"
        r"(?:수상|입상|우승|최우수상|대상|금상|은상|동상)?",
        "대회 참여·수상 표현 의심 (교외상 등 수상실적 기재 금지)",
        "기재요령: 교내외 대회 참여·성적·수상실적 기재 불가",
    ),
    # 논문 투고·등재 / 도서 출간
    (
        r"(?:논문(?:을)?\s*(?:투고|등재|게재|발표)|학회지에|도서\s*출간|출판사에서\s*출간)",
        "논문 등재·도서출간 의심",
        "기재요령: 논문 투고/등재, 도서출간 사실 기재 불가",
    ),
    # 지식재산권 출원·등록
    (
        r"(?:특허(?:출원|등록)|실용신안|상표권|디자인권|지식재산권)",
        "지식재산권 출원/등록 의심",
        "기재요령: 지식재산권 출원 또는 등록 사실 기재 불가",
    ),
    # 해외 활동 실적
    (
        r"(?:해외\s*(?:봉사|어학연수|캠프|탐방)|어학연수|교환학생(?:으로)?\s*(?:파견|선발|참여))",
        "해외 활동 실적 의심",
        "기재요령: 어학연수·해외봉사 등 해외 활동실적 기재 불가",
    ),
    # 장학생·장학금
    (
        r"(?:장학생(?:으로)?\s*(?:선발|선정)|장학금(?:을)?\s*(?:수혜|받))",
        "장학생/장학금 관련 표현 의심",
        "기재요령: 장학생·장학금 관련 내용 기재 불가",
    ),
    # 온라인 강좌(MOOC)·방과후학교 — 세특 입력 불가 항목
    (
        r"(?:(?-i:(?<![A-Za-z])(?:K-MOOC|KOCW|MOOC)(?![A-Za-z]))|방과후\s*학교)",
        "MOOC/방과후학교 활동 의심 (세특 입력 불가 항목)",
        "기재요령: K-MOOC·MOOC·KOCW, 방과후학교 활동은 세특에 입력 불가",
    ),
    # 소논문(자율탐구활동 연구보고서)
    (
        r"소논문",
        "소논문 관련 표현 (세특 입력 불가, 지정 6개 과목 예외 존재)",
        "기재요령: 자율탐구활동 연구보고서(소논문) 실적은 기재 불가",
    ),
    # 대학명 일반 패턴 (고정 목록에 없는 'OO대학교/OO대학원'까지 포착)
    # 앞에 공백 없이 한글이 2~8자 붙은 경우만 잡아 '진학할 대학교' 같은 일반어를 피한다.
    (
        r"[가-힣]{2,8}(?:대학교|대학원)",
        "특정 대학명 의심 (일반 패턴)",
        "기재요령: 구체적인 특정 대학명 기재 불가",
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


# 한글 표기 원칙(기재요령 p.28)의 예외로 널리 쓰이는 영문 약어·단위
ALLOWED_ENGLISH_TERMS = {
    "CEO", "PD", "UCC", "IT", "POP", "CF", "TV", "PAPS", "SNS", "PPT", "AI",
    "OECD", "CD", "DVD", "GPS", "ID", "URL", "PDF", "USB", "VR", "AR", "IoT",
    "km", "cm", "mm", "kg", "cc", "ml",
}
_ALLOWED_ENGLISH_UPPER = {t.upper() for t in ALLOWED_ENGLISH_TERMS}

# 미매칭 괄호를 최대 몇 개까지 보고할지 (병리적 입력에서 경고 폭주 방지)
_MAX_BRACKET_WARNINGS = 3

_QUOTE_LABELS = {"'": "작은따옴표", '"': "큰따옴표"}

# 지양 대상 특수문자: 원문자·불릿류
_SPECIAL_CHAR_RE = re.compile(r"[①-⑳★☆●○◆◇▶▷■□※~]")
# 줄 시작의 문단 구분 번호 (1., 2), 一. 등)
_PARA_NUMBER_RE = re.compile(
    r"^\s*(?:[0-9]{1,2}[.)]|[一二三四五六七八九十]+[.)])\s", re.MULTILINE
)
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")


def _context(text: str, i: int) -> str:
    """위치 i 주변 컨텍스트(앞뒤 15자)를 한 줄로 만들어 돌려준다."""
    return re.sub(r"\s+", " ", text[max(0, i - 15) : i + 16]).strip()


def _check_quote_bracket_balance(text: str) -> list[str]:
    """따옴표 개수(홀짝)와 괄호 짝(스택 기반)을 검사한다."""
    warnings: list[str] = []

    # 1) 따옴표: 개수가 홀수면 마지막 등장 위치를 짝이 어긋난 지점으로 지목한다.
    for ch, label in _QUOTE_LABELS.items():
        positions = [i for i, c in enumerate(text) if c == ch]
        if len(positions) % 2 == 1:
            warnings.append(
                f"따옴표 짝 불일치 의심 — {label}({ch})이(가) 홀수 개"
                f"({len(positions)}개) 발견됨. 마지막 위치 부근: "
                f"「{_context(text, positions[-1])}」"
            )

    # 2) 괄호: 스택으로 미매칭 위치를 정확히 찾는다 (따옴표보다 신뢰도가 높다).
    stack: list[int] = []
    unmatched_close: list[int] = []
    for i, c in enumerate(text):
        if c == "(":
            stack.append(i)
        elif c == ")":
            if stack:
                stack.pop()
            else:
                unmatched_close.append(i)

    shown = 0
    for i in unmatched_close:
        if shown >= _MAX_BRACKET_WARNINGS:
            break
        warnings.append(
            "괄호 짝 불일치 — 닫는 괄호 ')'가 여는 괄호 없이 나타남: "
            f"「{_context(text, i)}」"
        )
        shown += 1
    for i in stack:
        if shown >= _MAX_BRACKET_WARNINGS:
            break
        warnings.append(
            "괄호 짝 불일치 — 여는 괄호 '('가 닫히지 않음: "
            f"「{_context(text, i)}」"
        )
        shown += 1

    return warnings


def _check_special_characters(text: str) -> list[str]:
    """특수문자·문단구분기호(번호) 사용을 점검한다 (기재요령 p.40 지양 사항)."""
    found = _SPECIAL_CHAR_RE.findall(text) + [
        m.group().strip() for m in _PARA_NUMBER_RE.finditer(text)
    ]
    if not found:
        return []
    uniq = list(dict.fromkeys(found))
    sample = ", ".join(f"「{s}」" for s in uniq[:5])
    return [
        f"특수문자·문단구분기호 {len(found)}회 발견 — 기재요령상 입력을 지양합니다: {sample}"
    ]


def _check_english_usage(text: str) -> list[str]:
    """한글 표기 원칙에 어긋나는 영문 표기를 점검한다 (화이트리스트 예외)."""
    flagged: dict[str, str] = {}
    for token in _LATIN_TOKEN_RE.findall(text):
        key = token.upper()
        if key in _ALLOWED_ENGLISH_UPPER or key in flagged:
            continue
        flagged[key] = token
    if not flagged:
        return []
    sample = ", ".join(f"「{t}」" for t in list(flagged.values())[:5])
    return [
        "영문 표기 검토 필요 — 한글 표기가 원칙이며 부득이한 경우"
        "(외국인 성명/도서명·저자명/일반화된 명사 등)만 영문이 허용됨: " + sample
    ]


def style_check(text: str) -> list[str]:
    """문체·표기 점검 경고 목록을 반환한다.

    개조식 어미 위반, 어미 반복, 상투적 표현에 더해 따옴표/괄호 짝,
    특수문자·문단구분기호, 한글 표기 원칙(영문 오남용)을 함께 점검한다.
    """
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

    # 4) 따옴표/괄호 짝 검사
    warnings.extend(_check_quote_bracket_balance(text))

    # 5) 특수문자·문단구분기호 사용 지양
    warnings.extend(_check_special_characters(text))

    # 6) 한글 표기 원칙(영문 오남용) 검사
    warnings.extend(_check_english_usage(text))

    return warnings


# ──────────────────────────────────────────────
# 학생 간 유사도 검사 (동일·유사 문장 복붙 방지)
# ──────────────────────────────────────────────
def _normalize_for_similarity(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


_SENTENCE_SPLIT_RE = re.compile(r"[.!?\n]+")


def find_shared_sentences(
    items: list[tuple[str, str]], min_chars: int = 12, min_students: int = 2
) -> list[tuple[str, list[str]]]:
    """여러 학생에게 공통으로 나타나는 문장을 찾는다.

    (학생명, 텍스트) 목록에서 문장을 나누고, 같은 문장을 쓴 학생이
    min_students명 이상인 경우만 (문장, 학생명 목록)으로 돌려준다.
    """
    groups: dict[str, tuple[str, list[str]]] = {}
    for name, text in items:
        seen_here: set[str] = set()
        for raw in _SENTENCE_SPLIT_RE.split(text or ""):
            sentence = _normalize_for_similarity(raw)
            if len(sentence) < min_chars or sentence in seen_here:
                continue
            seen_here.add(sentence)
            # 표시용 대표 문장은 처음 등장한 원문(앞뒤 공백만 제거)을 쓴다.
            groups.setdefault(sentence, (raw.strip(), []))[1].append(name)
    shared = [
        (repr_text, names)
        for repr_text, names in groups.values()
        if len(names) >= min_students
    ]
    return sorted(shared, key=lambda p: (-len(p[1]), -len(p[0])))


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
