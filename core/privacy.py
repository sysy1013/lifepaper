# -*- coding: utf-8 -*-
"""전송 전 개인정보·식별 위험 신호 검출 (순수 로직, API 불필요).

Gemini로 보내기 직전(마스킹 적용 후) 텍스트에서 학생·학교를 특정할 수 있는 단서를 찾는다.
차단이 아닌 '확인 요청'용이므로 **정밀도 우선** — 일반명사 오탐을 피하려고 형태가 분명한
단서만 잡는다(예: '수원시 장안구'처럼 두 단계 주소, '○○시청', 'OO로 123' 도로명 주소).
"""

import re

from core.masking import (
    _EMAIL_PATTERN,
    _PHONE_PATTERN,
    _RRN_PATTERN,
    _STUDENT_NO_PATTERN,
    suggest_mask_candidates,
)

KIND_REGION = "지역명"
KIND_TITLE = "고유 제목"
KIND_SCHOOL = "학교명 의심"
KIND_NAME = "이름 의심"
KIND_STUDENT_NO = "학번 의심"
KIND_PHONE = "전화번호"
KIND_EMAIL = "이메일"
KIND_RRN = "주민등록번호"

_ADVICE = {
    KIND_REGION: "지역명은 학교 소재지를 드러낼 수 있음 — '지역 하천'처럼 일반화하거나 마스킹 목록에 등록",
    KIND_TITLE: "고유한 과제·프로젝트·행사 제목은 학생·학교를 특정할 수 있음 — 필요한 경우만 남기고 일반화 검토",
    KIND_SCHOOL: "학교명·별칭은 기재 불가이자 식별 정보 — 삭제하거나 마스킹 목록에 등록",
    KIND_NAME: "사람 이름으로 보임 — 마스킹 목록에 등록(자동 마스킹이 놓친 경우)",
    KIND_STUDENT_NO: "학번으로 보이는 5자리 숫자 — 마스킹 목록에 등록하거나 삭제",
    KIND_PHONE: "전화번호 — 삭제",
    KIND_EMAIL: "이메일 주소 — 삭제",
    KIND_RRN: "주민등록번호 — 즉시 삭제",
}

# 시도명 (정식 명칭·'○○도' 형태만. '서울'처럼 단독 약칭은 일반 주제어와 겹쳐 제외)
_PROVINCE_RE = re.compile(
    r"(?<![가-힣])(?:[가-힣]{2,3}(?:특별시|광역시|특별자치시|특별자치도)|"
    r"(?:경기|강원|충청[남북]|전라[남북]|경상[남북]|제주)도)(?![가-힣])"
)
# 두 단계 주소: '수원시 장안구', '홍성군 홍성읍', '청주시 흥덕구'.
# 두 번째 단위는 2글자 이상 + 구/읍/면(3글자 이상)이거나 '중구·동구' 같은 방위 구만 인정해
# '인구·연구·탐구·장면·측면' 같은 일반명사 오탐을 막는다.
_TWO_LEVEL_RE = re.compile(
    r"(?<![가-힣])[가-힣]{1,4}(?:시|군)[ \t]?"
    r"(?:[가-힣]{2,4}(?:구|읍|면)|[중동서남북]구)(?![가-힣])"
)
# 관공서형 지명: '수원시청', '홍성군청', '강남구청'
_OFFICE_RE = re.compile(r"(?<![가-힣])[가-힣]{2,4}(?:시청|군청|구청)(?![가-힣])")
# 읍 단위: '홍성읍' (면·동·리는 '측면·운동·원리' 등 일반명사와 겹쳐 단독으로는 잡지 않음)
_EUP_RE = re.compile(r"(?<![가-힣])[가-힣]{2,3}읍(?![가-힣])")
# 도로명 주소: '세종대로 175', '중앙로12번길 3' (숫자 뒤에 '가지·단계' 같은 한글이 이어지면 제외)
_ROAD_RE = re.compile(
    r"(?<![가-힣])[가-힣0-9]{1,8}(?:로|길)(?:[0-9]{1,3}번길)?[ \t]?[0-9]{1,4}(?:-[0-9]{1,4})?(?![가-힣0-9])"
)
_REGION_STOP = frozenset({"도읍"})

# 따옴표·꺾쇠로 감싼 고유 제목 (8자 이상 + 낱말 2개 이상 — '협력' 같은 강조 따옴표는 제외)
_TITLE_RE = re.compile(
    r"'([^'\n]{8,60})'|\"([^\"\n]{8,60})\"|‘([^’\n]{8,60})’|“([^”\n]{8,60})”|"
    r"「([^」\n]{8,60})」|『([^』\n]{8,60})』|<([^>\n]{8,60})>|《([^》\n]{8,60})》|〈([^〉\n]{8,60})〉"
)
_MASK_TOKEN_RE = re.compile(r"《비공개\d+》")

# 학교명: 'OO고등학교', 'OO여고', 'OO중학교', 'OO초등학교'
_SCHOOL_FULL_RE = re.compile(
    r"(?<![가-힣])[가-힣]{2,8}(?:여자고등학교|고등학교|중학교|초등학교|여고|여중|고교)(?![가-힣])"
)
# 학교 약칭 + 학교 단서: 'OO고 학생회', 'OO고 축제'
_SCHOOL_SHORT_RE = re.compile(
    r"(?<![가-힣])([가-힣]{1,4}(?:고|중|초))[ \t]?(?=(?:학생회|축제|체육대회|교지|교가|교훈|동문|재학생|졸업생))"
)
_SCHOOL_SHORT_STOP = frozenset(
    {"최고", "재고", "참고", "광고", "사고", "원고", "보고", "경고", "신고", "그리고", "중고", "도중", "최초"}
)


def _add(out, kind, m_text, start, end):
    out.append(
        {"kind": kind, "text": m_text, "start": start, "end": end, "advice": _ADVICE[kind]}
    )


def scan_identifying_risks(text: str, existing: list[str] | None = None) -> list[dict]:
    """전송 텍스트에서 식별 위험 신호를 찾아 [{kind, text, start, end, advice}]로 돌려준다.

    existing: 이미 마스킹 목록에 등록된 단어(이름 후보에서 제외).
    같은 위치가 여러 규칙에 걸리면 긴 것 하나만 남긴다. 결과는 위치 순 정렬.
    """
    text = text or ""
    out: list[dict] = []

    for rx in (_PROVINCE_RE, _TWO_LEVEL_RE, _OFFICE_RE, _EUP_RE, _ROAD_RE):
        for m in rx.finditer(text):
            if m.group() in _REGION_STOP:
                continue
            _add(out, KIND_REGION, m.group(), m.start(), m.end())

    for m in _TITLE_RE.finditer(text):
        inner = next(g for g in m.groups() if g is not None)
        if " " not in inner.strip() or _MASK_TOKEN_RE.fullmatch(inner.strip()):
            continue
        _add(out, KIND_TITLE, m.group(), m.start(), m.end())

    for m in _SCHOOL_FULL_RE.finditer(text):
        _add(out, KIND_SCHOOL, m.group(), m.start(), m.end())
    for m in _SCHOOL_SHORT_RE.finditer(text):
        word = m.group(1)
        if word in _SCHOOL_SHORT_STOP:
            continue
        _add(out, KIND_SCHOOL, word, m.start(1), m.end(1))

    for kind, rx in (
        (KIND_RRN, _RRN_PATTERN),
        (KIND_PHONE, _PHONE_PATTERN),
        (KIND_EMAIL, _EMAIL_PATTERN),
        (KIND_STUDENT_NO, _STUDENT_NO_PATTERN),
    ):
        for m in rx.finditer(text):
            _add(out, kind, m.group(), m.start(), m.end())

    # 이름 후보는 기존 마스킹 제안 로직(반복 등장·'OOO 학생' 형태)을 재사용한다.
    for name in suggest_mask_candidates([text], existing or []):
        if name.isdigit():
            continue  # 5자리 숫자는 위에서 학번으로 처리
        # '정리함·조사함'처럼 개조식 명사형 종결(받침 ㅁ)로 끝나는 말은 이름이 아니다.
        last = ord(name[-1]) - 0xAC00
        if 0 <= last < 11172 and last % 28 in (10, 16):
            continue
        for m in re.finditer(rf"(?<![가-힣]){re.escape(name)}", text):
            _add(out, KIND_NAME, name, m.start(), m.end())

    # 겹치는 신호는 긴 것 하나만 남긴다 (예: 주민번호 안의 전화번호 오탐, 학교명 안의 지역명).
    out.sort(key=lambda r: (r["start"], -(r["end"] - r["start"])))
    result: list[dict] = []
    for r in out:
        if result and r["start"] < result[-1]["end"]:
            if (r["end"] - r["start"]) > (result[-1]["end"] - result[-1]["start"]):
                result[-1] = r
            continue
        result.append(r)
    return result
