# -*- coding: utf-8 -*-
"""규칙 기반 검출 / 문체 점검 / 유사도 검사 (API 불필요, 순수 로직)."""

import difflib
import re
from collections import Counter

# 점검 기준 학년도 표시 (2027학년도 규정은 미반영)
RULES_BASIS_LABEL = "2026학년도 기재요령(교육부훈령 제555호) 기준"

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
        "기재요령: 공인어학시험·교외 성적·석차 기재 불가 (2026 기재요령 p.18-19)",
        "score",
    ),
    # 대표적인 공인어학시험 명칭
    (
        r"(?:TOEIC|TOEFL|TEPS|IELTS|HSK|JLPT|JPT|DELE|DELF|G-?TELP|OPIc|토익|토플|텝스|아이엘츠)",
        "공인어학시험 명칭 (기재 금지)",
        "기재요령: 공인어학시험 성적 기재 불가 (2026 기재요령 p.18-19)",
        "language_test",
    ),
    # 교내·외 인증시험 (기재요령 '라'항: 공인어학시험보다 넓은 범주)
    # '매경TEST'/'TESAT'은 re.IGNORECASE를 받지만, 앞에 한글이 붙거나(매경) 5글자
    # 라틴 토큰이라 평범한 영단어의 부분문자열로 등장할 위험이 없어 (?-i:) 스코프를
    # 씌우지 않는다 (WHO/UN/MOS 같은 2~3글자 약어와 다름).
    (
        r"(?:인증시험|한자능력검정시험|매경TEST|TESAT|KBS한국어능력시험)",
        "교내외 인증시험 참여/성적 의심",
        "기재요령: 교내·외 인증시험 참여 사실이나 성적 기재 불가 (2026 기재요령 p.18-19)",
        "certification_test",
    ),
    # 모의고사 언급
    (
        r"(?:전국연합학력평가|모의고사|모의평가|학력평가)\s*(?:성적|점수|등급|결과)?",
        "모의고사 관련 표현 의심",
        "기재요령: 교외 성적·석차 기재 불가 (2026 기재요령 p.18-19)",
        "mock_exam",
    ),
    # 학교 명칭 (재학 중인 학교명 기재 금지 — 의심 수준으로 표시)
    (
        r"[가-힣]{2,}(?:여자고등학교|고등학교|여고|고교)",
        "학교 명칭 의심 (재학 중인 학교명 기재 금지)",
        "기재요령: 재학 학교명 등 특정 가능 정보 기재 불가 (2026 기재요령 p.18-19)",
        "school_name",
    ),
    # 주요 대학명
    (
        r"(?:서울대|연세대|고려대|성균관대|한양대|서강대|중앙대|경희대|이화여대|한국외대|"
        r"서울시립대|건국대|동국대|홍익대|카이스트|KAIST|포스텍|POSTECH|유니스트|UNIST|지스트|GIST)(?:학교)?",
        "특정 대학명 의심 (기재 금지)",
        "기재요령: 특정 대학·기관명 기재 불가 (2026 기재요령 p.18-19)",
        "university_name",
    ),
    # 상업적 명칭/브랜드
    (
        # '줌'(Zoom 한글 표기)은 '보여 줌' 같은 개조식 종결과 겹쳐 오탐이 잦아 규칙에서 제외.
        # Gemini LLM 단계(core/gemini.py SYSTEM_PROMPT)가 이미 '줌'을 브랜드 예시로 명시해 판단함.
        r"(?:구글|유튜브|네이버|카카오톡?|인스타그램|페이스북|틱톡|챗GPT|ChatGPT|GPT|제미나이|"
        r"Zoom|넷플릭스|아이폰|아이패드|갤럭시|파워포인트|엑셀|"
        r"오픈AI|OpenAI|마이크로소프트|Microsoft|패들렛|Padlet|삼성(?!전자고|디지털))",
        "상업적 명칭/브랜드 의심",
        "기재요령: 상업적 명칭 기재 불가 (2026 기재요령 p.18-19)",
        "brand",
    ),
    # 국제/공공기관명 (교육관련기관 외 기관명 기재 금지)
    # 라틴 약어는 (?-i:...)로 대소문자 구분을 되살려 'who', 'un' 같은 영단어 오탐을 막는다.
    (
        r"(?:유네스코|통계청|세계보건기구|세계경제포럼|"
        r"(?-i:(?<![A-Za-z])(?:UNESCO|OECD|UN|IMF|WHO)(?![A-Za-z])))",
        "국제/공공기관명 의심 (교육관련기관 외 기관명 기재 금지)",
        "기재요령: 교육관련기관 외 기관명 기재 불가 (2026 기재요령 p.18-19)",
        "institution",
    ),
    # 자격증 명칭 (자격증 취득상황 이외 항목에는 기재 금지)
    # 'MOS'는 3글자 라틴 약어라 IGNORECASE 상태로 두면 'almost', 'atmosphere' 등
    # 평범한 영단어 안의 'mos'를 잡는다 → ITQ/TOPCIT/GTQ와 같은 (?-i:) 블록에 넣는다.
    (
        r"(?:컴퓨터활용능력|정보처리기사|정보처리기능사|컴퓨터그래픽스운용기능사|"
        r"워드프로세서|한국사능력검정시험|한국어능력시험|"
        r"(?-i:(?<![A-Za-z])(?:ITQ|TOPCIT|GTQ|MOS)(?![A-Za-z])))",
        "자격증 명칭 의심 (자격증 취득상황 이외 항목 기재 금지)",
        "기재요령: 자격증 명칭·취득 사실은 해당 항목 외 기재 불가 (2026 기재요령 p.18-19)",
        "license",
    ),
    # 대회 참여·수상 실적
    (
        r"[가-힣A-Za-z]*(?:경시대회|올림피아드|콘테스트|공모전)(?:에서)?\s*"
        r"(?:수상|입상|우승|최우수상|대상|금상|은상|동상)?",
        "대회 참여·수상 표현 의심 (교외상 등 수상실적 기재 금지)",
        "기재요령: 교내외 대회 참여·성적·수상실적 기재 불가 (2026 기재요령 p.18-19, p.118·149)",
        "competition",
    ),
    # 교외상 문서 명칭 (기재요령 '다'항: 교외 기관·단체 수여 표창장·감사장·공로상 등)
    (
        r"(?:표창장|감사장|공로상)",
        "교외상 문서 명칭 의심 (교외상 기재 금지)",
        "기재요령: 교외 기관·단체가 수여한 표창장·감사장·공로상 등 기재 불가 (2026 기재요령 p.18-19)",
        "outside_award",
    ),
    # 논문 투고·등재 / 도서 출간
    (
        r"(?:논문(?:을)?\s*(?:투고|등재|게재|발표)|학회지에|도서\s*출간|출판사에서\s*출간)",
        "논문 등재·도서출간 의심",
        "기재요령: 논문 투고/등재, 도서출간 사실 기재 불가 (2026 기재요령 p.18-19)",
        "paper_book",
    ),
    # 지식재산권 출원·등록
    (
        r"(?:특허(?:출원|등록)|실용신안|상표권|디자인권|지식재산권)",
        "지식재산권 출원/등록 의심",
        "기재요령: 지식재산권 출원 또는 등록 사실 기재 불가 (2026 기재요령 p.18-19)",
        "ip_right",
    ),
    # 해외 활동 실적
    (
        r"(?:해외\s*(?:봉사|어학연수|캠프|탐방)|어학연수|교환학생(?:으로)?\s*(?:파견|선발|참여))",
        "해외 활동 실적 의심",
        "기재요령: 어학연수·해외봉사 등 해외 활동실적 기재 불가 (2026 기재요령 p.18-19)",
        "overseas",
    ),
    # 장학생·장학금
    (
        r"(?:장학생(?:으로)?\s*(?:선발|선정)|장학금(?:을)?\s*(?:수혜|받))",
        "장학생/장학금 관련 표현 의심",
        "기재요령: 장학생·장학금 관련 내용 기재 불가 (2026 기재요령 p.18-19)",
        "scholarship",
    ),
    # 온라인 강좌(MOOC)·방과후학교 — 세특 입력 불가 항목
    (
        r"(?:(?-i:(?<![A-Za-z])(?:K-MOOC|KOCW|MOOC)(?![A-Za-z]))|방과후\s*학교)",
        "MOOC/방과후학교 활동 의심 (세특 입력 불가 항목)",
        "기재요령: K-MOOC·MOOC·KOCW, 방과후학교 활동은 세특에 입력 불가 (2026 기재요령 p.118·149)",
        "mooc_afterschool",
    ),
    # 소논문(자율탐구활동 연구보고서)
    (
        r"소논문",
        "소논문 관련 표현 (세특 입력 불가, 지정 6개 과목 예외 존재)",
        "기재요령: 자율탐구활동 연구보고서(소논문) 실적은 기재 불가 (2026 기재요령 p.118·149)",
        "thesis",
    ),
    # 청소년단체 활동 (기재요령 p.78: 정규교육과정 이외의 청소년단체 활동은
    # 단체명·특기사항 모두 기재 불가) — 금지 대상은 '구체적 단체명'이므로
    # 'RCY'의 정식 명칭인 '청소년적십자'만 넣고, 봉사·헌혈 맥락에서 흔히 쓰이는
    # 맨 '적십자'는 과탐지가 되어 제외한다.
    # 'RCY'는 3글자 라틴 약어라 IGNORECASE면 'mercy' 같은 영단어의 'rcy'를 잡으므로
    # UN/WHO/MOS와 동일하게 (?-i:) + 알파벳 경계로 대문자 표기만 검출한다.
    (
        r"(?:보이스카우트|걸스카우트|청소년적십자|우주소년단|해양소년단|"
        r"(?-i:(?<![A-Za-z])RCY(?![A-Za-z])))",
        "청소년단체 활동명 의심 (정규교육과정 외 활동은 단체명 기재 금지)",
        "기재요령: 정규교육과정 이외의 청소년단체 활동은 단체명·특기사항 모두 기재 불가",
        "youth_org",
    ),
    # 대학명 일반 패턴 (고정 목록에 없는 'OO대학교/OO대학원'까지 포착)
    # 앞에 공백 없이 한글이 2~8자 붙은 경우만 잡아 '진학할 대학교' 같은 일반어를 피한다.
    (
        r"[가-힣]{2,8}(?:대학교|대학원)",
        "특정 대학명 의심 (일반 패턴)",
        "기재요령: 구체적인 특정 대학명 기재 불가 (2026 기재요령 p.18-19)",
        "university_name",
    ),
]


def rule_based_filter(text: str, custom_words: list[str] | None = None) -> list[dict]:
    """정규표현식 + 사용자 정의 금칙어로 명백한 금지 패턴을 우선 검출한다."""
    findings = []
    seen = set()
    for pattern, reason, basis, rule_id in RULE_PATTERNS:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            word = m.group().strip()
            if word and word not in seen:
                seen.add(word)
                findings.append(
                    {
                        "word": word,
                        "reason": reason,
                        "basis": basis,
                        "rule_id": rule_id,
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
                    "rule_id": "custom_word",
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

# 물결표와 그 변형: ~(U+007E) ∼(U+223C) 〜(U+301C) ～(U+FF5E)
TILDE_CHARS = "~∼〜～"
TILDE_GUIDE = "범위는 '~' 대신 붙임표 '-'로 표기(예: 3-4시간, 2026.3.-7.)"

# 지양 대상 특수문자: 원문자·불릿류 + 물결표(변형 포함).
# 물결표는 숫자 범위(3~4시간)여도 항상 경고한다 — 현장 지침이 특수기호 지양이고,
# 문장 부호 제17항이 붙임표(-) 대체를 허용하며, 기재요령 예시도 기간을
# '2026.03.04.-2026.07.08.'처럼 붙임표로 적는다.
_SPECIAL_CHAR_RE = re.compile(r"[①-⑳★☆●○◆◇▶▷■□※" + TILDE_CHARS + r"]")
# 줄 시작의 문단 구분 번호 (1., 2), 一. 등)
_PARA_NUMBER_RE = re.compile(
    r"^\s*(?:[0-9]{1,2}[.)]|[一二三四五六七八九十]+[.)])\s", re.MULTILINE
)
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")

# 숫자~숫자 범위 (2026.3.~7. 처럼 앞 숫자 뒤에 마침표가 붙은 날짜 포함)
_TILDE_RANGE_RE = re.compile(r"(\d\.?)[ \t]*[" + TILDE_CHARS + r"][ \t]*(?=\d)")


def fix_tilde_ranges(text: str) -> str:
    """숫자~숫자 범위의 물결표를 붙임표(-)로 바꾼 문자열을 돌려준다.

    예: '3~4시간' → '3-4시간', '2026.3.~7.' → '2026.3.-7.'. 숫자 범위가 아닌 '~'는 그대로 둔다.
    """
    return _TILDE_RANGE_RE.sub(r"\1-", text or "")


def _context(text: str, i: int) -> str:
    """위치 i 주변 컨텍스트(앞뒤 15자)를 한 줄로 만들어 돌려준다."""
    return re.sub(r"\s+", " ", text[max(0, i - 15) : i + 16]).strip()


def _issue(
    rule_id: str,
    category: str,
    start: int,
    end: int,
    word: str,
    problem: str,
    suggestion: str = "",
) -> dict:
    return {
        "rule_id": rule_id,
        "category": category,
        "start": start,
        "end": end,
        "word": word,
        "problem": problem,
        "suggestion": suggestion,
    }


def _sample(words, limit: int = 5) -> str:
    uniq = list(dict.fromkeys(words))
    return ", ".join(f"「{w}」" for w in uniq[:limit])


# 각 점검 함수는 (이슈 목록, 경고 문자열 목록)을 돌려준다.
# 이슈는 위치(span)를 가진 구조화 결과(오류 리포트용), 경고 문자열은 기존 UI용이다.
_Group = tuple[list[dict], list[str]]


# ── 1) 개조식이 아닌 종결어미 ──
_BAD_ENDING_RE = re.compile(
    r"[가-힣]+(?:했다|하였다|한다|이다|입니다|합니다|했습니다|하였습니다|있다|있었다|해요|했어요)(?=[.\s]|$)"
)


def _check_endings(text: str) -> _Group:
    issues = [
        _issue(
            "style_ending",
            "문체",
            m.start(),
            m.end(),
            m.group(),
            "개조식이 아닌 종결어미",
            "명사형 어미('~함', '~임')로 통일",
        )
        for m in _BAD_ENDING_RE.finditer(text)
    ]
    if not issues:
        return [], []
    return issues, [
        f"개조식이 아닌 종결어미 {len(issues)}회 발견 — 명사형 어미('~함', '~임')로 통일 권장: "
        + _sample(i["word"] for i in issues)
    ]


# ── 2) 같은 종결 어미의 과도한 반복 ──
def _check_repeated_endings(text: str) -> _Group:
    ends = list(re.finditer(r"([가-힣])(?=\.)", text))
    if len(ends) < 5:
        return [], []
    counter = Counter(m.group(1) for m in ends)
    top_char, top_count = counter.most_common(1)[0]
    if top_count / len(ends) <= 0.6:
        return [], []
    first = next(m for m in ends if m.group(1) == top_char)
    msg = (
        f"종결 어미 '~{top_char}.'이 {top_count}/{len(ends)}문장에서 반복됨 — "
        "'~보임', '~드러남', '~기름' 등으로 다양화 권장"
    )
    return [
        _issue(
            "style_repeat",
            "문체",
            first.start(),
            first.end(),
            top_char,
            f"종결 어미 '~{top_char}.' 반복({top_count}/{len(ends)}문장)",
            "'~보임', '~드러남', '~기름' 등으로 다양화",
        )
    ], [msg]


# ── 3) 상투적(클리셰) 표현 ──
def _check_cliches(text: str) -> _Group:
    issues = []
    found = []
    for c in CLICHE_EXPRESSIONS:
        hits = list(re.finditer(re.escape(c), text))
        if hits:
            found.append(c)
        for m in hits:
            issues.append(
                _issue(
                    "style_cliche",
                    "문체",
                    m.start(),
                    m.end(),
                    c,
                    "상투적 표현",
                    "구체적 행동·산출물 서술로 대체",
                )
            )
    if not found:
        return [], []
    return issues, [
        "상투적 표현 발견 — 구체적 행동·산출물 서술로 대체 권장: "
        + ", ".join(f"「{c}」" for c in found)
    ]


# ── 4) 따옴표/괄호 짝 ──
def _check_quote_bracket_balance_issues(text: str) -> _Group:
    """따옴표 개수(홀짝)와 괄호 짝(스택 기반)을 검사한다."""
    issues: list[dict] = []
    warnings: list[str] = []

    # 1) 따옴표: 개수가 홀수면 마지막 등장 위치를 짝이 어긋난 지점으로 지목한다.
    for ch, label in _QUOTE_LABELS.items():
        positions = [i for i, c in enumerate(text) if c == ch]
        if len(positions) % 2 == 1:
            last = positions[-1]
            warnings.append(
                f"따옴표 짝 불일치 의심 — {label}({ch})이(가) 홀수 개"
                f"({len(positions)}개) 발견됨. 마지막 위치 부근: "
                f"「{_context(text, last)}」"
            )
            issues.append(
                _issue(
                    "quote_balance",
                    "서식",
                    last,
                    last + 1,
                    ch,
                    f"{label} 짝 불일치 의심({len(positions)}개)",
                    "여는·닫는 따옴표 짝 맞추기",
                )
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
        issues.append(
            _issue(
                "bracket_balance",
                "서식",
                i,
                i + 1,
                ")",
                "닫는 괄호 ')'가 여는 괄호 없이 나타남",
                "여는 괄호 추가 또는 닫는 괄호 삭제",
            )
        )
        shown += 1
    for i in stack:
        if shown >= _MAX_BRACKET_WARNINGS:
            break
        warnings.append(
            "괄호 짝 불일치 — 여는 괄호 '('가 닫히지 않음: " f"「{_context(text, i)}」"
        )
        issues.append(
            _issue(
                "bracket_balance",
                "서식",
                i,
                i + 1,
                "(",
                "여는 괄호 '('가 닫히지 않음",
                "닫는 괄호 추가 또는 여는 괄호 삭제",
            )
        )
        shown += 1

    return issues, warnings


def _check_quote_bracket_balance(text: str) -> list[str]:
    """(호환용) 따옴표/괄호 짝 경고 문자열 목록."""
    return _check_quote_bracket_balance_issues(text)[1]


# ── 5) 특수문자·문단구분기호 (물결표 포함) ──
def _check_special_characters_issues(text: str) -> _Group:
    issues: list[dict] = []
    found: list[str] = []
    has_tilde = False
    for m in _SPECIAL_CHAR_RE.finditer(text):
        ch = m.group()
        found.append(ch)
        if ch in TILDE_CHARS:
            has_tilde = True
            # 치환안: 앞뒤 낱말 단위로 잘라 숫자 범위면 붙임표로 바꾼 예를 보여 준다.
            ls = m.start()
            while ls > 0 and not text[ls - 1].isspace():
                ls -= 1
            le = m.end()
            while le < len(text) and not text[le].isspace():
                le += 1
            chunk = text[ls:le]
            fixed = fix_tilde_ranges(chunk)
            suggestion = (
                f"{chunk} → {fixed}"
                if fixed != chunk
                else "'~' 삭제 또는 붙임표 '-'·'에서 …까지'로 고쳐 쓰기"
            )
            issues.append(
                _issue(
                    "tilde",
                    "서식",
                    m.start(),
                    m.end(),
                    ch,
                    "물결표(~) 사용 — 특수기호 지양",
                    suggestion,
                )
            )
        else:
            issues.append(
                _issue(
                    "special_char",
                    "서식",
                    m.start(),
                    m.end(),
                    ch,
                    f"특수문자 「{ch}」 사용 지양",
                    "특수문자 삭제 후 문장으로 서술",
                )
            )
    for m in _PARA_NUMBER_RE.finditer(text):
        token = m.group().strip()
        s = m.start() + (len(m.group()) - len(m.group().lstrip()))
        found.append(token)
        issues.append(
            _issue(
                "special_char",
                "서식",
                s,
                s + len(token),
                token,
                f"문단구분기호(번호) 「{token}」 사용 지양",
                "번호 삭제 후 문장으로 이어 쓰기",
            )
        )
    if not found:
        return [], []
    msg = (
        f"특수문자·문단구분기호 {len(found)}회 발견 — 기재요령상 입력을 지양합니다: "
        + _sample(found)
    )
    if has_tilde:
        msg += f" / {TILDE_GUIDE}"
    return issues, [msg]


def _check_special_characters(text: str) -> list[str]:
    """(호환용) 특수문자·문단구분기호 경고 문자열 목록."""
    return _check_special_characters_issues(text)[1]


# ── 6) 한글 표기 원칙(영문 오남용) ──
def _check_english_usage_issues(text: str) -> _Group:
    issues: list[dict] = []
    flagged: dict[str, str] = {}
    for m in _LATIN_TOKEN_RE.finditer(text):
        token = m.group()
        key = token.upper()
        if key in _ALLOWED_ENGLISH_UPPER:
            continue
        flagged.setdefault(key, token)
        issues.append(
            _issue(
                "english",
                "문체",
                m.start(),
                m.end(),
                token,
                f"영문 표기 「{token}」 — 한글 표기 원칙",
                "한글 표기로 바꾸기(외국인 성명·도서명·일반화된 약어는 예외)",
            )
        )
    if not flagged:
        return [], []
    sample = ", ".join(f"「{t}」" for t in list(flagged.values())[:5])
    return issues, [
        "영문 표기 검토 필요 — 한글 표기가 원칙이며 부득이한 경우"
        "(외국인 성명/도서명·저자명/일반화된 명사 등)만 영문이 허용됨: " + sample
    ]


def _check_english_usage(text: str) -> list[str]:
    """(호환용) 영문 표기 경고 문자열 목록."""
    return _check_english_usage_issues(text)[1]


# ── 7) 허위·과장 표현 (참고용 권고, 위반 아님) ──
# 오탐을 줄이려 소수의 명백한 최상급·과장어만, 낱말 경계를 엄격히 둔다.
# '최고차항'(수학)·'최고 기온'·'완벽주의'·'천재지변'·'천재교육'은 잡지 않는다.
EXAGGERATION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?<![가-힣])최고(?:의|로|인|임)(?![가-힣])", "최고"),
    (r"(?<![가-힣])완벽(?:하게|한|히|함|하다|하며|하여)", "완벽"),
    (r"(?<![가-힣])독보적", "독보적"),
    (r"(?<![가-힣])압도적", "압도적"),
    (r"(?<![가-힣])누구보다(?:도)?", "누구보다"),
    (r"(?<![가-힣])천재(?:적|성)", "천재"),
    (r"타의\s*추종을\s*불허", "타의 추종을 불허"),
)
_EXAGGERATION_RES = tuple((re.compile(p), label) for p, label in EXAGGERATION_PATTERNS)


def _check_exaggeration(text: str) -> _Group:
    issues = []
    for rx, _label in _EXAGGERATION_RES:
        for m in rx.finditer(text):
            issues.append(
                _issue(
                    "exaggeration",
                    "과장·허위",
                    m.start(),
                    m.end(),
                    m.group(),
                    "최상급·과장 표현 — 허위·과장 기재 금지",
                    "직접 관찰한 구체적 행동·근거로 바꿔 쓰기",
                )
            )
    if not issues:
        return [], []
    issues.sort(key=lambda i: i["start"])
    return issues, [
        "허위·과장 기재 금지: 직접 관찰 근거 확인 — 최상급·과장 표현이 있습니다"
        "(위반 아닌 참고): " + _sample(i["word"] for i in issues)
    ]


# ── 8) 서식 점검 ──
_MULTI_SPACE_RE = re.compile(r"(?<=\S)[ \t]{2,}(?=\S)")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"(?<=[가-힣A-Za-z0-9)\]'\"])[ \t]+(?=[.,!?](?![.\d]))")
# 한글 바로 뒤 마침표에 공백 없이 글자가 이어짐 (소수점·날짜·영문 약어는 앞이 숫자/영문이라 제외)
_NO_SPACE_AFTER_PERIOD_RE = re.compile(r"(?<=[가-힣])\.(?=[가-힣A-Za-z])")
# '..'(정확히 2개)와 ',,'(2개 이상). 3개 이상 마침표('...', '......')와 '…'는 줄임표로 허용.
_DOUBLE_PUNCT_RE = re.compile(r"(?<!\.)\.\.(?!\.)|,{2,}")
_SENTENCE_PERIOD_RE = re.compile(r"[가-힣]\.")


def _jong(ch: str) -> int:
    """한글 음절의 종성 인덱스(0=받침 없음). 한글 음절이 아니면 -1."""
    code = ord(ch) - 0xAC00
    return code % 28 if 0 <= code < 11172 else -1


_JONG_N, _JONG_L, _JONG_LM, _JONG_M = 4, 8, 10, 16


def _check_format(text: str) -> _Group:
    issues: list[dict] = []
    warnings: list[str] = []

    def add(rule_id, rx, problem, suggestion, label):
        hits = list(rx.finditer(text))
        for m in hits:
            issues.append(
                _issue(rule_id, "서식", m.start(), m.end(), m.group(), problem, suggestion)
            )
        if hits:
            # 공백 문제는 _context가 공백을 접어 버리므로 문제 위치를 '⎵'로 드러낸다.
            def show(m):
                if m.group().strip():
                    return _context(text, m.start())
                before = re.sub(r"\s+", " ", text[max(0, m.start() - 10) : m.start()])
                after = re.sub(r"\s+", " ", text[m.end() : m.end() + 10])
                return f"{before.lstrip()}{'⎵' * len(m.group())}{after.rstrip()}"

            ctx = _sample(show(m) for m in hits)
            warnings.append(f"서식 점검 — {label} {len(hits)}곳: {ctx}")

    add("format_spaces", _MULTI_SPACE_RE, "연속 공백", "공백 한 칸으로", "연속 공백(2칸 이상)")
    add(
        "format_space_before_punct",
        _SPACE_BEFORE_PUNCT_RE,
        "문장부호 앞 공백",
        "부호를 앞말에 붙여 쓰기",
        "문장부호 앞 공백",
    )
    add(
        "format_space_after_period",
        _NO_SPACE_AFTER_PERIOD_RE,
        "마침표 뒤 공백 누락",
        "마침표 뒤 한 칸 띄우기",
        "마침표 뒤 공백 누락",
    )
    add(
        "format_double_punct",
        _DOUBLE_PUNCT_RE,
        "중복 문장부호",
        "부호 하나만 남기기",
        "중복 문장부호",
    )

    # 문장 끝 마침표 누락: 다른 문장이 마침표로 끝나는 글에서, 줄 끝이 명사형 종결
    # (받침 ㅁ·ㄻ — '~함', '~임', '~만듦')이나 '~다'인데 부호가 없을 때만 본다.
    # 줄바꿈된 문장 중간('연계하여')이나 마침표를 전혀 쓰지 않는 한 줄 메모는 잡지 않는다.
    if _SENTENCE_PERIOD_RE.search(text):
        hits = []
        pos = 0
        for line in text.split("\n"):
            stripped = line.rstrip()
            if stripped:
                last = stripped[-1]
                if _jong(last) in (_JONG_M, _JONG_LM) or last == "다":
                    end = pos + len(stripped)
                    start = end - 1
                    issues.append(
                        _issue(
                            "format_missing_period",
                            "서식",
                            start,
                            end,
                            last,
                            "문장 끝 마침표 누락",
                            f"「{last}」 뒤에 마침표(.) 추가",
                        )
                    )
                    hits.append(_context(text, start))
            pos += len(line) + 1
        if hits:
            warnings.append(
                f"서식 점검 — 문장 끝 마침표 누락 {len(hits)}곳: " + _sample(hits)
            )

    return issues, warnings


# ── 9) 맞춤법 로컬 규칙 (정밀도 우선, 근거가 분명한 흔한 오류만) ──
# (정규식, 교정안, 근거). 근거 조항이 없는 표준어 문제는 표준국어대사전 표제어로 적는다.
SPELLING_RULES: tuple[tuple[str, str, str], ...] = (
    # 한글 맞춤법 제27항 [붙임 2]: 어원이 분명하지 않은 '며칠'은 소리대로 적음 ('몇일' 아님)
    (r"몇일", "며칠", "한글 맞춤법 제27항 [붙임 2]"),
    # 한글 맞춤법 제35항 [붙임 2]: '되어'가 줄면 '돼', '되었-'이 줄면 '됐-' ('됬'은 없는 표기)
    (r"됬", "됐", "한글 맞춤법 제35항 [붙임 2]"),
    # 같은 조항: 낱말 끝의 '되'는 '되어'의 준말 '돼'로 적음 ('안 되' → '안 돼')
    (r"안\s?되(?=$|[\s.,!?])", "안 돼/안돼", "한글 맞춤법 제35항 [붙임 2]"),
    # 표준국어대사전 '설레다': 명사형은 '설렘' ('설레이다·설레임'은 비표준)
    (r"설레임", "설렘", "표준국어대사전 '설레다'(명사형 '설렘')"),
    # 표준국어대사전 '금세': '금시에'의 준말. 명사 '금새(물건값)'는 조사가 붙으므로 제외
    (r"금새(?=$|[\s.,!?])", "금세", "표준국어대사전 '금세'('금시에'의 준말)"),
    # 표준국어대사전 '왠지': '왜인지'의 준말 ('웬지'는 없는 말)
    (r"웬지", "왠지", "표준국어대사전 '왠지'('왜인지'의 준말)"),
    # 표준국어대사전 '어떡하다': '어떻게 하다'의 준말 ('어떻해'는 없는 말)
    (r"어떻해", "어떻게/어떡해", "표준국어대사전 '어떡하다'('어떻게 하다'의 준말)"),
    # 한글 맞춤법 제2항: 문장의 각 단어는 띄어 씀 ('있다'는 독립된 용언).
    # 사전에 한 단어로 오른 '재미있다·맛있다·관계있다' 등은 목록에 넣지 않는다.
    (r"(심도|의미|가치|깊이|관심)있(?=[가-힣])", r"\1 있", "한글 맞춤법 제2항(단어별 띄어쓰기)"),
    # 한글 맞춤법 제42항: 의존 명사 '것'은 띄어 씀 + 제2항 '같다'는 독립된 단어
    (r"것같(?=다|은|음|아|았|습)", "것 같", "한글 맞춤법 제2항·제42항"),
)
_SPELLING_RES = tuple((re.compile(p), fix, basis) for p, fix, basis in SPELLING_RULES)

# '것'과 붙어 한 단어가 된 명사(날것·탈것·들것·별것·헌것·군것질)는 제외
_GEOT_COMPOUND_PREV = frozenset("날탈들별헌군")


def _check_spelling(text: str) -> _Group:
    """맞춤법 로컬 규칙 검사. 경고에는 교정안과 근거 조항을 함께 표기한다."""
    issues: list[dict] = []

    def add(start, end, wrong, fix, basis):
        issues.append(
            _issue(
                "spelling",
                "맞춤법",
                start,
                end,
                wrong,
                f"맞춤법: 「{wrong}」 → 「{fix}」 ({basis})",
                fix,
            )
        )
        issues[-1]["basis"] = basis

    for rx, fix, basis in _SPELLING_RES:
        for m in rx.finditer(text):
            add(m.start(), m.end(), m.group(), m.expand(fix), basis)

    # 한글 맞춤법 제11항 [붙임 1]: 모음이나 'ㄴ' 받침 뒤의 '렬·률'은 '열·율'로,
    # 그 밖의 받침 뒤에서는 '렬·률'로 적음 (백분율·실패율 / 합격률·성공률).
    # 사람 이름(김율 등) 오탐을 피하려고 앞에 한글이 2자 이상 있는 낱말만 본다.
    for m in re.finditer(r"(?<=[가-힣])([가-힣])(율|률)", text):
        prev, suffix = m.group(1), m.group(2)
        jong = _jong(prev)
        expected = "율" if jong in (0, _JONG_N) else "률"
        if suffix != expected:
            add(
                m.start(),
                m.end(),
                m.group(),
                prev + expected,
                "한글 맞춤법 제11항 [붙임 1]",
            )

    # 한글 맞춤법 제42항: 의존 명사 '수'는 띄어 씀 ('할수 있다' → '할 수 있다').
    # '-ㄹ수록'(어미)과 '수없이'(한 단어 부사)는 제외.
    for m in re.finditer(r"([가-힣])([ \t]?)수([ \t]?)(있|없(?!이)|밖에)", text):
        prev, sp1, sp2, tail = m.groups()
        if _jong(prev) != _JONG_L:
            continue
        if sp1 == "" or (sp2 == "" and tail != "밖에"):
            fixed = f"{prev} 수" + ("" if tail == "밖에" else " ") + tail
            add(m.start(), m.end(), m.group(), fixed, "한글 맞춤법 제42항(의존 명사)")

    # 한글 맞춤법 제42항: 관형형 뒤 의존 명사 '것'은 띄어 씀 ('궁금한것' → '궁금한 것').
    for m in re.finditer(r"([가-힣])것(?!질)", text):
        prev = m.group(1)
        if _jong(prev) in (_JONG_N, _JONG_L) and prev not in _GEOT_COMPOUND_PREV:
            add(m.start(), m.end(), m.group(), f"{prev} 것", "한글 맞춤법 제42항(의존 명사)")

    if not issues:
        return [], []
    issues.sort(key=lambda i: i["start"])
    sample = ", ".join(
        f"「{i['word']}」→「{i['suggestion']}」({i['basis']})" for i in issues[:5]
    )
    return issues, [f"맞춤법 점검(로컬 규칙) {len(issues)}건 — {sample}"]


def check_spelling(text: str) -> list[dict]:
    """맞춤법 로컬 규칙 이슈 목록 (테스트·리포트용)."""
    return _check_spelling(text)[0]


_STYLE_CHECKS = (
    _check_endings,
    _check_repeated_endings,
    _check_cliches,
    _check_quote_bracket_balance_issues,
    _check_special_characters_issues,
    _check_english_usage_issues,
    _check_exaggeration,
    _check_format,
    _check_spelling,
)


def collect_style_issues(text: str) -> list[dict]:
    """문체·서식·맞춤법·과장 점검 결과를 위치(span)를 가진 구조화 이슈 목록으로 돌려준다.

    각 이슈: rule_id, category(문체/서식/맞춤법/과장·허위), start, end, word,
    problem, suggestion (맞춤법 이슈는 basis도 포함).
    """
    text = text or ""
    issues: list[dict] = []
    for check in _STYLE_CHECKS:
        issues.extend(check(text)[0])
    return issues


def style_check(text: str) -> list[str]:
    """문체·표기 점검 경고 목록을 반환한다 (collect_style_issues와 같은 점검의 문자열판).

    개조식 어미 위반, 어미 반복, 상투적 표현, 따옴표/괄호 짝, 특수문자(물결표 포함)·
    문단구분기호, 한글 표기 원칙(영문 오남용), 허위·과장 표현, 서식, 맞춤법 로컬 규칙.
    """
    text = text or ""
    warnings: list[str] = []
    for check in _STYLE_CHECKS:
        warnings.extend(check(text)[1])
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
