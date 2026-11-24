# -*- coding: utf-8 -*-
"""공식 근거 규칙 사전 (순수 데이터 + 예외 판정 로직, API 불필요).

모든 점검(규칙 기반 기재금지 패턴·Gemini 심사 9개 기준·문체/서식/맞춤법 점검)은
rule_id로 이 사전에 연결되어, 오류 리포트에 '공식 근거'와 '예외 조건'을 함께 보여 준다.

쪽수 표기 원칙: 2026학년도 학교생활기록부 기재요령(고등학교) **인쇄쪽** 기준.
확인된 쪽수만 쓴다 — 모든 항목 기재불가 p.18-19, 세특 입력불가 p.118(1·2학년)/p.149(3학년),
한글·영문 표기 p.28, 생성형 AI p.19. 그 밖에는 쪽수 없이 조항명만 적는다.
"""

import re

GUIDE = "2026학년도 기재요령(고등)"
GUIDE_COMMON = f"{GUIDE} p.18-19 모든 항목 기재불가 사항"
GUIDE_SETEUK = f"{GUIDE} p.118·149 세특 입력불가 사항"
GUIDE_HANGUL = f"{GUIDE} p.28 한글 표기 원칙"
GUIDE_AI = f"{GUIDE} p.19 생성형 AI 활용 유의사항"
GUIDE_WRITING = f"{GUIDE} 서술형 입력 유의사항(특수문자·문단구분기호 지양, 명사형 종결)"

EDU_INSTITUTION_EXCEPTION = (
    "교육관련기관(교육부·소속기관, 시도교육청·직속기관, 교육지원청·소속기관) 기관명은 기재 가능"
)
BOOK_TITLE_EXCEPTION = "독서 도서명·저자명 속 대학명·기관명·상호명은 허용"
THESIS_SUBJECTS = (
    "수학과제 탐구",
    "사회문제 탐구",
    "융합과학 탐구",
    "과학과제 연구",
    "사회과제 연구",
    "윤리문제 탐구",
)
THESIS_EXCEPTION = (
    "소논문 작성 가능 과목(" + "·".join(THESIS_SUBJECTS) + ")은 "
    "실적(제목·연구주제·참여인원·소요시간) 제외 시 기재 가능(특기사항 서술)"
)
ENGLISH_EXCEPTION = (
    "외국인 성명, 도서명·저자명, 일반화된 영문 약어(CEO·PD·UCC·IT·TV·SNS·PPT·AI 등), 단위는 허용"
)

RULEBOOK: dict[str, dict] = {
    # ── 규칙 기반 기재금지 패턴 (core/rules.py RULE_PATTERNS) ──
    "score": {
        "title": "성적·점수 표기",
        "summary": "공인어학시험·인증시험·모의고사 성적(원점수·등급·백분위 등) 기재 불가",
        "exceptions": "체육 교과 세특의 PAPS 등급 등 기재요령이 허용한 경우는 예외",
        "source": GUIDE_COMMON,
    },
    "language_test": {
        "title": "공인어학시험",
        "summary": "공인어학시험 참여 사실·성적·수상 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "certification_test": {
        "title": "교내·외 인증시험",
        "summary": "교내·외 인증시험 참여 사실·성적 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "mock_exam": {
        "title": "모의고사·전국연합학력평가",
        "summary": "모의고사·전국연합학력평가 성적 및 관련 수상 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "school_name": {
        "title": "재학 학교 식별 정보",
        "summary": "학교명·재단명·학교 축제명·학교 별칭 등 재학 고교를 알 수 있는 내용 기재 불가",
        "exceptions": "인적·학적사항 등 기재요령이 정한 항목은 예외",
        "source": GUIDE_COMMON,
    },
    "university_name": {
        "title": "특정 대학명",
        "summary": "구체적 특정 대학명 기재 불가",
        "exceptions": BOOK_TITLE_EXCEPTION,
        "source": GUIDE_COMMON,
    },
    "brand": {
        "title": "상호명·상업적 명칭",
        "summary": "상호명·특정 브랜드 기재 불가",
        "exceptions": BOOK_TITLE_EXCEPTION,
        "source": GUIDE_COMMON,
    },
    "institution": {
        "title": "기관명(기구·단체·조직)",
        "summary": "교육관련기관 외 구체적 기관명 기재 불가",
        "exceptions": EDU_INSTITUTION_EXCEPTION + " / " + BOOK_TITLE_EXCEPTION,
        "source": GUIDE_COMMON,
    },
    "license": {
        "title": "자격증 명칭·취득 사실",
        "summary": "자격증 명칭·취득 사실은 '자격증 취득 및 국가직무능력표준 이수상황' 외 항목 기재 불가",
        "exceptions": "해당 항목(자격증 취득상황)에는 기재 가능",
        "source": GUIDE_COMMON,
    },
    "competition": {
        "title": "대회 참여·수상",
        "summary": "교내·외 대회 참여 사실·성적·수상 기재 불가(대회명을 행사로 바꾼 편법 기재 포함)",
        "exceptions": "교내 대회 수상은 '수상경력' 항목에만 기재",
        "source": GUIDE_COMMON + " / " + GUIDE_SETEUK,
    },
    "outside_award": {
        "title": "교외상",
        "summary": "교외 기관·단체(장)가 수여한 상(표창장·감사장·공로상 포함) 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "paper_book": {
        "title": "논문·도서 출간",
        "summary": "논문 학회지 투고·등재·학회 발표 사실, 도서 출간 사실 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "ip_right": {
        "title": "지식재산권",
        "summary": "특허·실용신안·상표·디자인 출원 또는 등록 사실 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "overseas": {
        "title": "해외 활동실적",
        "summary": "어학연수·해외봉사 등 해외 활동실적 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "scholarship": {
        "title": "장학생·장학금",
        "summary": "장학생·장학금 관련 내용 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "mooc_afterschool": {
        "title": "MOOC·방과후학교",
        "summary": "K-MOOC·MOOC·KOCW 등, 방과후학교 활동은 세특 입력 불가",
        "exceptions": "",
        "source": GUIDE_SETEUK,
    },
    "thesis": {
        "title": "연구보고서(소논문)",
        "summary": "연구보고서(소논문) 관련 사항은 세특 입력 불가",
        "exceptions": THESIS_EXCEPTION,
        "source": GUIDE_SETEUK,
    },
    "youth_org": {
        "title": "청소년단체 활동",
        "summary": "정규교육과정 이외의 청소년단체 활동은 단체명 기재 불가",
        "exceptions": "",
        "source": f"{GUIDE} 창의적 체험활동 청소년단체 활동 기재 원칙",
    },
    "parent_status": {
        "title": "부모 사회·경제적 지위",
        "summary": "부모(친인척 포함)의 사회·경제적 지위 암시(직종·직업·직장·직위명) 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "lecturer": {
        "title": "특정 강사명",
        "summary": "학생이 참여한 강의의 특정 강사명 기재 불가",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    "custom_word": {
        "title": "사용자 정의 금칙어",
        "summary": "교사가 등록한 금칙어(학교명·별칭·축제명 등)",
        "exceptions": "",
        "source": "사용자 정의 금칙어 (재학 학교 식별 정보 블라인드: " + GUIDE_COMMON + ")",
    },
    "gemini_other": {
        "title": "Gemini 문맥 심사",
        "summary": "Gemini가 문맥상 기재요령 위반 소지를 지적한 표현",
        "exceptions": "",
        "source": GUIDE_COMMON,
    },
    # ── 문체·서식·맞춤법 (core/rules.py collect_style_issues) ──
    "style_ending": {
        "title": "명사형 종결",
        "summary": "서술형 문장은 명사형 어미('~함', '~임')로 종결",
        "exceptions": "",
        "source": GUIDE_WRITING,
    },
    "style_repeat": {
        "title": "종결 어미 반복",
        "summary": "같은 종결 어미의 과도한 반복은 가독성을 떨어뜨림(권고)",
        "exceptions": "",
        "source": "문체 권고(공식 금지 사항 아님)",
    },
    "style_cliche": {
        "title": "상투적 표현",
        "summary": "구체적 행동·산출물 서술 권장(권고)",
        "exceptions": "",
        "source": "문체 권고(공식 금지 사항 아님)",
    },
    "quote_balance": {
        "title": "따옴표 짝",
        "summary": "여는 따옴표와 닫는 따옴표는 짝을 이뤄야 함",
        "exceptions": "",
        "source": "문장 부호(문체부 고시 제2014-39호) 큰따옴표·작은따옴표 항",
    },
    "bracket_balance": {
        "title": "괄호 짝",
        "summary": "여는 괄호와 닫는 괄호는 짝을 이뤄야 함",
        "exceptions": "",
        "source": "문장 부호(문체부 고시 제2014-39호) 소괄호 항",
    },
    "special_char": {
        "title": "특수문자·문단구분기호",
        "summary": "서술형 입력 시 특수문자·문단구분기호(번호) 지양",
        "exceptions": "",
        "source": GUIDE_WRITING,
    },
    "tilde": {
        "title": "물결표(~)",
        "summary": "특수기호 지양 — 범위는 '~' 대신 붙임표 '-'로 표기",
        "exceptions": "",
        "source": "문장 부호(문체부 고시 제2014-39호) 제17항(물결표 대신 붙임표 사용 가능) / "
        f"{GUIDE} 기간 표기 예시(2026.03.04.-2026.07.08.)",
    },
    "english": {
        "title": "한글 표기 원칙",
        "summary": "문자는 한글로 입력, 부득이한 경우만 영문",
        "exceptions": ENGLISH_EXCEPTION,
        "source": GUIDE_HANGUL,
    },
    "exaggeration": {
        "title": "허위·과장 표현",
        "summary": "단순 사실 과장·부풀림, 허위 기재 금지 — 직접 관찰 근거 확인",
        "exceptions": "교사가 직접 관찰한 구체적 근거가 있으면 표현을 구체화",
        "source": f"{GUIDE} 신뢰도 저하 행위 금지(과장·허위 기재) / {GUIDE_AI}",
    },
    "format_spaces": {
        "title": "연속 공백",
        "summary": "낱말 사이 공백은 한 칸",
        "exceptions": "",
        "source": "한글 맞춤법 제2항(문장의 각 단어는 띄어 씀) — 서식 점검",
    },
    "format_space_before_punct": {
        "title": "문장부호 앞 공백",
        "summary": "마침표·쉼표 등은 앞말에 붙여 씀",
        "exceptions": "",
        "source": "문장 부호(문체부 고시 제2014-39호) — 서식 점검",
    },
    "format_space_after_period": {
        "title": "마침표 뒤 공백 누락",
        "summary": "문장을 끝낸 마침표 뒤에는 한 칸 띄어 다음 문장을 씀",
        "exceptions": "소수점(3.5)·날짜(2026.3.)·영문 약어는 제외",
        "source": "문장 부호(문체부 고시 제2014-39호) — 서식 점검",
    },
    "format_double_punct": {
        "title": "중복 문장부호",
        "summary": "'..', ',,'처럼 같은 부호가 겹침",
        "exceptions": "줄임표(…, ……, ..., ......)는 제외",
        "source": "문장 부호(문체부 고시 제2014-39호) — 서식 점검",
    },
    "format_missing_period": {
        "title": "문장 끝 마침표 누락",
        "summary": "다른 문장은 마침표로 끝나는데 이 문장만 마침표가 없음",
        "exceptions": "",
        "source": "문장 부호(문체부 고시 제2014-39호) 마침표 항 — 서식 점검",
    },
    "spelling": {
        "title": "맞춤법(로컬 규칙)",
        "summary": "한글 맞춤법 근거가 분명한 흔한 오류",
        "exceptions": "",
        "source": "한글 맞춤법(문체부 고시 제2017-12호)",
    },
    "uncertain_char": {
        "title": "판독 불확실 글자",
        "summary": "이미지 전사 시 읽기 불확실한 글자(⟦?⟧)",
        "exceptions": "",
        "source": "이미지 전사 결과 — 원본 이미지와 대조 필요",
    },
    "proofread": {
        "title": "오탈자·맞춤법(Gemini)",
        "summary": "Gemini 맞춤법 검사가 지적한 표기 오류",
        "exceptions": "AI 판단이므로 교사가 최종 확인",
        "source": "한글 맞춤법(문체부 고시 제2017-12호) — AI 검사",
    },
}

# Gemini 심사 9개 기준 → rule_id (reason/basis 문자열의 키워드로 매핑)
_GEMINI_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("소논문", "thesis"),
    ("연구보고서", "thesis"),
    ("MOOC", "mooc_afterschool"),
    ("KOCW", "mooc_afterschool"),
    ("방과후", "mooc_afterschool"),
    ("상업", "brand"),
    ("브랜드", "brand"),
    ("교외상", "outside_award"),
    ("대회", "competition"),
    ("수상", "competition"),
    ("대학", "university_name"),
    ("강사", "lecturer"),
    ("기관", "institution"),
    ("부모", "parent_status"),
    ("해외", "overseas"),
    ("논문", "paper_book"),
    ("출판", "paper_book"),
    ("특허", "ip_right"),
    ("장학", "scholarship"),
    ("학교 명칭", "school_name"),
    ("학교명", "school_name"),
    ("어학", "language_test"),
    ("모의고사", "mock_exam"),
    ("성적", "score"),
    ("점수", "score"),
)


def rule_id_for_finding(finding: dict) -> str:
    """검출 결과(dict)의 rule_id를 돌려준다. 없으면 reason/basis 키워드로 추정한다."""
    rid = finding.get("rule_id")
    if rid in RULEBOOK:
        return rid
    if finding.get("reason") == "사용자 정의 금칙어":
        return "custom_word"
    blob = f"{finding.get('reason', '')} {finding.get('basis', '')}"
    for kw, rid in _GEMINI_KEYWORDS:
        if kw in blob:
            return rid
    return "gemini_other"


# ── 예외 판정 ──
# 교육관련기관: 교육부·(시도)교육청·교육지원청 및 그 소속·직속기관
_EDU_INSTITUTION_RE = re.compile(
    r"(?:교육부|[가-힣]*교육청|[가-힣]*교육지원청|[가-힣]*교육연구정보원|[가-힣]*교육연수원|"
    r"[가-힣]*교육과학연구원|[가-힣]*학생교육원|한국교육과정평가원|한국교육개발원|한국교육학술정보원)"
)

# 도서명 표기: 『…』, 「…」(책·작품), <…>, 《…》 안에 있는 표현
_BOOK_TITLE_RE = re.compile(r"『[^』\n]*』|《[^》\n]*》|<[^>\n]*>|〈[^〉\n]*〉")


def is_education_institution(word: str) -> bool:
    """교육관련기관명(기재 가능 예외)이면 True."""
    return bool(_EDU_INSTITUTION_RE.fullmatch((word or "").strip()))


def inside_book_title(text: str, start: int, end: int) -> bool:
    """text[start:end]가 도서명 표기(『』《》<>〈〉) 안에 있으면 True."""
    for m in _BOOK_TITLE_RE.finditer(text or ""):
        if m.start() < start and end <= m.end():
            return True
    return False


def is_thesis_subject(subject: str) -> bool:
    """소논문 작성 가능 6과목이면 True (공백 차이는 무시)."""
    norm = re.sub(r"\s+", "", subject or "")
    return any(norm == re.sub(r"\s+", "", s) for s in THESIS_SUBJECTS)


def apply_exceptions(
    rule_id: str, verdict: str, word: str, text: str, start: int, end: int, subject: str
) -> tuple[str, str, bool]:
    """예외 조건을 반영해 (판정, 예외 안내, 제외 여부)를 돌려준다.

    - 교육관련기관명: 기관명 규칙에서 제외(기재 가능)
    - 도서명 속 기관·대학·상호명: 제외(허용)
    - 소논문 가능 6과목의 '소논문' 검출: '확인 필요'로 낮추고 예외 문구 표시
    """
    entry = RULEBOOK.get(rule_id, {})
    exception = entry.get("exceptions", "")
    if rule_id in ("institution", "gemini_other") and is_education_institution(word):
        return verdict, EDU_INSTITUTION_EXCEPTION, True
    if rule_id in ("institution", "university_name", "brand") and inside_book_title(
        text, start, end
    ):
        return verdict, BOOK_TITLE_EXCEPTION, True
    if rule_id == "thesis" and is_thesis_subject(subject):
        return "확인 필요", THESIS_EXCEPTION, False
    return verdict, exception, False
