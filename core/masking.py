# -*- coding: utf-8 -*-
"""개인정보 마스킹 (Gemini API 전송 전 치환, 결과에서 복원)."""

import re
from collections import Counter

# 마스킹 자동 제안에 쓰는 대표 성씨 (~40자)
_SURNAMES = "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민"

# 성씨로 시작하지만 이름이 아닐 가능성이 높은 흔한 단어 (오탐 제외용)
_NAME_STOPWORDS = {
    "김치", "이해", "이상", "이후", "이내", "이번", "박수", "정도", "정리",
    "조사", "주제", "안내", "문제", "문장", "성적", "성장", "신청", "권장",
    "장점", "고민", "최고", "최선",
    "전체", "전원", "전반", "최종", "최대", "최소", "안전", "조원", "주변",
    "작성", "성실", "성취", "구성", "구분", "고려", "남은", "심화",
}

# 이름 뒤에 붙을 수 있는 조사 (오탐 판별용 — 떼어낸 형태가 흔한 명사면 이름이 아니다)
_JOSA_TAIL = ("은", "는", "이", "가", "을", "를", "의", "와", "과", "도", "만", "에", "서", "로")


def _looks_like_common_word(token: str) -> bool:
    """'전체가', '정리를'처럼 조사가 붙은 일반 명사인지 판정한다."""
    if token in _NAME_STOPWORDS:
        return True
    if len(token) >= 3 and token[-1] in _JOSA_TAIL and token[:-1] in _NAME_STOPWORDS:
        return True
    return False

# 성씨 + 1~2글자, 한글 경계로 둘러싸인 독립 단어
_NAME_PATTERN = re.compile(rf"(?<![가-힣])[{_SURNAMES}][가-힣]{{1,2}}(?![가-힣])")
# 성씨 + 1~2글자 뒤에 (공백 있을 수도 있음) "학생"이 바로 이어지는 경우
_NAME_STUDENT_PATTERN = re.compile(rf"(?<![가-힣])([{_SURNAMES}][가-힣]{{1,2}})(?=\s*학생)")
# 성씨 + 1~2글자 뒤에 (공백 있을 수도 있음) "학생/군/양"이 이어지는 경우 (자동 탐지용)
_NAME_TITLE_PATTERN = re.compile(
    rf"(?<![가-힣])([{_SURNAMES}][가-힣]{{1,2}})(?=\s*(?:학생|군|양))"
)
# 성씨 + 1~2글자 뒤에 조사가 바로 붙은 경우 (예: 김철수는, 김철수가) — 이름 부분만 포착.
# 한국어 산문에서 이름은 대부분 조사와 붙어 쓰이므로 자동 탐지에서는 이 형태도 센다.
_NAME_PARTICLE_PATTERN = re.compile(
    rf"(?<![가-힣])([{_SURNAMES}][가-힣]{{1,2}})"
    r"(?=(?:은|는|이|가|을|를|의|와|과|도|만|께|에게|이나|나)(?![가-힣]))"
)
# "이름: 이영희", "제출자 이영희"처럼 이름임이 분명한 문맥 (1회만 나와도 인정)
_NAME_LABEL_PATTERN = re.compile(
    rf"(?:이름|성명|학생명|제출자|작성자)\s*[:：]?\s*([{_SURNAMES}][가-힣]{{1,2}})(?![가-힣])"
)
# "3학년 2반 이영희", "2반 15번 이영희"처럼 학반·번호 뒤에 오는 이름 (1회만 나와도 인정)
_NAME_CLASS_PATTERN = re.compile(
    rf"\d+\s*반\s*(?:\d+\s*번)?\s*([{_SURNAMES}][가-힣]{{1,2}})(?![가-힣])"
)
# 5자리 학번
_STUDENT_NO_PATTERN = re.compile(r"\b\d{5}\b")
# 휴대전화 번호
_PHONE_PATTERN = re.compile(r"01[016-9][-\s]?\d{3,4}[-\s]?\d{4}")
# 주민등록번호
_RRN_PATTERN = re.compile(r"\d{6}[-\s]?[1-4]\d{6}")
# 이메일 주소
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# 자동 탐지 시 이름 후보의 최대 개수 (오탐으로 본문이 훼손되는 것을 막기 위한 상한)
_AUTO_NAME_LIMIT = 10


def suggest_mask_candidates(
    texts: list[str], existing: list[str] | None = None
) -> list[str]:
    """생기부 텍스트에서 개인정보로 보이는 표현(이름·학번)을 자동 추출한다.

    - 이름 후보: 성씨 + 1~2 한글, 독립 단어로 등장하며 전체에서 2회 이상 반복하거나,
      (공백 있을 수도 있는) "학생"이 바로 뒤에 이어지는 경우.
    - 학번 후보: 5자리 숫자.
    빈도 높은 순으로 최대 5개, 중복·기존 등록어·불용어는 제외한다.
    """
    existing = existing or []
    name_counts: Counter = Counter()
    student_suffix_counts: Counter = Counter()
    number_counts: Counter = Counter()
    for text in texts:
        if not text:
            continue
        for m in _NAME_PATTERN.findall(text):
            if m not in _NAME_STOPWORDS:
                name_counts[m] += 1
        for m in _NAME_STUDENT_PATTERN.findall(text):
            if m not in _NAME_STOPWORDS:
                student_suffix_counts[m] += 1
        for m in _STUDENT_NO_PATTERN.findall(text):
            number_counts[m] += 1

    def _excluded(word: str) -> bool:
        # 기존 등록어와 포함 관계면 제외 (부분/상위 문자열 모두)
        for e in existing:
            if not e:
                continue
            if word == e or word in e or e in word:
                return True
        return False

    candidates: list[tuple[str, int]] = []
    # 이름은 2회 이상 반복되었거나, "학생"이 바로 뒤에 이어지는 경우 후보로 인정
    # (실제 생기부에서 이름은 반복되거나 "OOO 학생" 형태로 1회만 등장하기도 함)
    all_names = set(name_counts) | set(student_suffix_counts)
    for word in all_names:
        cnt = name_counts.get(word, 0)
        if (cnt >= 2 or word in student_suffix_counts) and not _excluded(word):
            candidates.append((word, max(cnt, student_suffix_counts.get(word, 0))))
    # 학번은 1회라도 등장하면 후보
    for word, cnt in number_counts.items():
        if not _excluded(word):
            candidates.append((word, cnt))

    candidates.sort(key=lambda x: -x[1])
    return [w for w, _ in candidates][:5]


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


def mask_findings(
    findings: list[dict], mask_map: list[tuple[str, str]]
) -> list[dict]:
    """검출 목록의 모든 문자열 필드를 마스킹한 새 목록을 반환한다.

    word뿐 아니라 reason·basis·suggestion_1·suggestion_2 등 모든 문자열 값을
    치환한다. 문자열이 아닌 값은 그대로 두며, 입력은 변형하지 않는다.
    """
    return [
        {
            k: (apply_mask(v, mask_map) if isinstance(v, str) else v)
            for k, v in f.items()
        }
        for f in findings
    ]


def detect_pii(texts: list[str], existing: list[str] | None = None) -> list[str]:
    """전송 텍스트에서 개인정보로 판단되는 표현을 보수적으로 탐지한다.

    - 인명: 성씨 + 1~2 한글의 독립 단어가 2회 이상 등장하거나 뒤에 "학생/군/양"이
      이어지는 경우 (불용어 제외, 최대 _AUTO_NAME_LIMIT개).
    - 학번: 5자리 숫자. 전화번호·주민등록번호·이메일: 패턴 일치 전부 (상한 없음).
    이미 등록된 단어(existing)와 포함 관계인 표현은 제외하며,
    긴 표현이 먼저 치환되도록 길이 내림차순으로 반환한다.
    """
    existing = existing or []
    name_counts: Counter = Counter()
    suffix_names: set[str] = set()
    others: list[str] = []

    for text in texts:
        if not text:
            continue
        for m in _NAME_PATTERN.findall(text):
            if m not in _NAME_STOPWORDS:
                name_counts[m] += 1
        # 조사가 붙은 형태도 같은 이름의 출현으로 센다 (김철수는/김철수가 → 2회)
        for m in _NAME_PARTICLE_PATTERN.findall(text):
            if m not in _NAME_STOPWORDS:
                name_counts[m] += 1
        for m in _NAME_TITLE_PATTERN.findall(text):
            if m not in _NAME_STOPWORDS:
                suffix_names.add(m)
        # 이름표('이름: OOO')·학반 표기('3학년 2반 OOO')는 1회만 나와도 이름으로 본다.
        # 수행평가 제출물은 표지에 이름이 한 번만 적히는 경우가 많다.
        for pattern in (_NAME_LABEL_PATTERN, _NAME_CLASS_PATTERN):
            for m in pattern.findall(text):
                if not _looks_like_common_word(m):
                    suffix_names.add(m)
        for pattern in (
            _STUDENT_NO_PATTERN,
            _PHONE_PATTERN,
            _RRN_PATTERN,
            _EMAIL_PATTERN,
        ):
            others.extend(pattern.findall(text))

    def _excluded(word: str) -> bool:
        # 기존 등록어와 포함 관계면 제외 (부분/상위 문자열 모두)
        for e in existing:
            if not e:
                continue
            if word == e or word in e or e in word:
                return True
        return False

    names = [
        w
        for w in sorted(
            set(name_counts) | suffix_names,
            key=lambda w: -max(name_counts.get(w, 0), 1 if w in suffix_names else 0),
        )
        if (name_counts.get(w, 0) >= 2 or w in suffix_names) and not _excluded(w)
    ][:_AUTO_NAME_LIMIT]

    found: list[str] = names + [w for w in others if not _excluded(w)]
    ordered = sorted({w for w in found if w}, key=len, reverse=True)
    # 서로 포함 관계인 탐지 결과는 긴 쪽만 남긴다 (예: 주민번호 안에서 잡힌 전화번호 오탐).
    result: list[str] = []
    for w in ordered:
        if not any(w in longer for longer in result):
            result.append(w)
    return result


def extend_mask_map(
    texts: list[str],
    base_map: list[tuple[str, str]],
    enabled: bool = True,
) -> tuple[list[tuple[str, str]], list[str]]:
    """base_map에 자동 탐지 항목을 덧붙인 확장 맵과, 새로 추가된 표현 목록을 반환한다."""
    if not enabled:
        return base_map, []
    existing = [w for w, _ in base_map]
    detected = detect_pii(texts, existing=existing)
    if not detected:
        return base_map, []
    # base_map이 이미 쓰고 있는 토큰 번호 뒤를 이어서 부여한다.
    start = len(base_map)
    extended = list(base_map) + [
        (w, f"《비공개{start + i + 1}》") for i, w in enumerate(detected)
    ]
    extended.sort(key=lambda pair: len(pair[0]), reverse=True)
    return extended, detected


def remove_mask_deep(obj, mask_map: list[tuple[str, str]]):
    """중첩 구조(dict/list) 안의 모든 문자열에서 마스킹 토큰을 원래 단어로 되돌린다.

    품질 진단처럼 응답이 자유 서술 JSON이라 모델이 마스킹 토큰을 그대로 인용할 수
    있는 경우에 쓴다. 원본 구조는 바꾸지 않고 새 객체를 반환한다.
    """
    if isinstance(obj, str):
        return remove_mask(obj, mask_map)
    if isinstance(obj, dict):
        return {k: remove_mask_deep(v, mask_map) for k, v in obj.items()}
    if isinstance(obj, list):
        return [remove_mask_deep(v, mask_map) for v in obj]
    return obj
