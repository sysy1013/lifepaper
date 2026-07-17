# -*- coding: utf-8 -*-
"""Gemini 클라이언트 어댑터 + 프롬프트 + LLM 호출 로직 (검토/수정/품질/초안/교정/분량)."""

import json
import random
import re
import time

from google import genai
from google.genai import types as genai_types

from core.masking import apply_mask, remove_mask
from core.rules import rule_based_filter

GEMINI_MODEL = "gemini-2.5-flash"


# ──────────────────────────────────────────────
# Gemini 클라이언트 어댑터 + 호출 재시도 래퍼
# ──────────────────────────────────────────────
class _GeminiModel:
    """google-genai Client를 감싸 기존 .generate_content(prompt) 인터페이스를 유지한다."""

    def __init__(self, api_key: str, system_instruction: str, config: dict):
        self._client = genai.Client(api_key=api_key)
        self._config = genai_types.GenerateContentConfig(
            system_instruction=system_instruction, **config
        )

    def generate_content(self, prompt: str):
        return self._client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=self._config
        )


def _make_model(
    api_key: str,
    system_instruction: str,
    temperature: float,
    json_mode: bool = False,
) -> _GeminiModel:
    config: dict = {"temperature": temperature}
    if json_mode:
        config["response_mime_type"] = "application/json"
    return _GeminiModel(api_key, system_instruction, config)



_RETRYABLE_MARKERS = (
    "429", "500", "503", "quota", "rate limit", "resource exhausted",
    "deadline", "timeout", "timed out", "unavailable", "internal error",
    "overloaded", "connection",
)


def _is_retryable_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(m in msg for m in _RETRYABLE_MARKERS)


def _gemini_text(model, prompt: str, max_attempts: int = 3) -> str:
    """generate_content를 재시도(지수 백오프)와 함께 호출하고 본문 텍스트를 반환한다."""
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = model.generate_content(prompt)
            text = (response.text or "").strip()
            if not text:
                raise ValueError("Gemini가 빈 응답을 반환했습니다.")
            return text
        except Exception as e:
            last_error = e
            empty = isinstance(e, ValueError) and "빈 응답" in str(e)
            if attempt == max_attempts - 1 or not (empty or _is_retryable_error(e)):
                raise
            time.sleep(2 * (2**attempt) + random.random())
    raise last_error  # pragma: no cover — 위에서 항상 raise됨


def _strip_code_fence(raw: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()


def _gemini_json(model, prompt: str, parse_attempts: int = 2):
    """JSON 응답을 기대하는 호출. 파싱 실패 시 전체 호출을 1회 더 재시도한다."""
    for attempt in range(parse_attempts):
        raw = _strip_code_fence(_gemini_text(model, prompt))
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            if attempt == parse_attempts - 1:
                raise


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
- 각 항목은 다음 6개의 키를 반드시 포함한다:
  "word": 원문에서 발견된 표현 그대로 (원문 텍스트와 완전히 동일한 문자열)
  "reason": 위반 사유 (위 8가지 기준 중 해당 항목)
  "basis": 해당 위반이 근거하는 기재요령 심사 기준(위 8가지 중 해당 항목명)을 짧게
  "severity": "위반"(명백한 기재 금지) 또는 "주의"(맥락상 문제 소지)
  "suggestion_1": 학생의 희망 진로와 연결한 대체 표현 1
  "suggestion_2": 학생의 희망 진로와 연결한 대체 표현 2
- 위반 사항이 없으면 빈 배열 []을 반환한다.

[출력 예시]
[{"word": "의사인 아버지", "reason": "부모의 직업 암시", "basis": "부모의 직업/사회적 지위 암시 표현", "severity": "위반", "suggestion_1": "가족의 헌신을 보며", "suggestion_2": "생명 존중의 가치를 배우며"}]
"""


def analyze_with_gemini(text: str, major: str, api_key: str) -> list[dict]:
    """전체 텍스트와 희망 진로를 Gemini에 전달하여 위반 표현 목록(JSON)을 받아온다."""
    model = _make_model(api_key, SYSTEM_PROMPT, temperature=0.2, json_mode=True)

    user_prompt = (
        f"[학생의 희망 진로/학과]\n{major if major.strip() else '미입력'}\n\n"
        f"[심사 대상 생기부 텍스트]\n{text}\n\n"
        "위 텍스트를 심사하여 JSON 배열로만 답하라."
    )

    parsed = _gemini_json(model, user_prompt)
    if not isinstance(parsed, list):
        raise ValueError("Gemini 응답이 JSON 배열 형식이 아닙니다.")

    results = []
    for item in parsed:
        if isinstance(item, dict) and item.get("word"):
            results.append(
                {
                    "word": str(item.get("word", "")).strip(),
                    "reason": str(item.get("reason", "")).strip(),
                    "basis": str(item.get("basis", "")).strip(),
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
    """검토 결과(위반 목록)를 반영한 수정본 전문을 생성한다.

    1차 수정본에 규칙 기반 검사(rule_based_filter)로 걸리는 금지 표현이 남아있으면,
    해당 표현을 알려주고 한 번 더 수정을 요청한다(2차 시도).
    """
    model = _make_model(api_key, REWRITE_SYSTEM_PROMPT, temperature=0.3)
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
    result = _gemini_text(model, prompt)

    remaining = rule_based_filter(result, [])
    if remaining:
        words = ", ".join(f"「{f['word']}」" for f in remaining)
        retry_prompt = (
            f"{prompt}\n\n"
            f"[1차 수정본]\n{result}\n\n"
            f"[여전히 남아있는 금지 표현]\n{words}\n\n"
            "위 표현을 반드시 제거하거나 대체하여 수정된 전체 본문만 출력하라."
        )
        result = _gemini_text(model, retry_prompt)

    return result


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
    model = _make_model(api_key, QUALITY_SYSTEM_PROMPT, temperature=0.3, json_mode=True)
    prompt = (
        f"[학생의 희망 진로/학과]\n{major.strip() or '미입력'}\n\n"
        f"[평가 대상 세특 텍스트]\n{text}\n\n"
        "위 텍스트를 루브릭으로 평가하여 JSON으로만 답하라."
    )
    parsed = _gemini_json(model, prompt)
    if not isinstance(parsed, dict) or "scores" not in parsed:
        raise ValueError("Gemini 응답이 기대한 JSON 형식이 아닙니다.")
    return parsed


def quality_avg(q: dict) -> float | None:
    scores = q.get("scores", [])
    if not scores:
        return None
    return sum(float(s.get("score", 0)) for s in scores) / len(scores)


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


# NEIS 항목별 작성 원칙 (세특 외 항목은 성격에 맞는 서술 지침을 추가로 주입)
CATEGORY_GUIDES: dict[str, str] = {
    "세특": (
        "1. 활동 동기 → 탐구 과정 → 배우고 느낀 점 → 후속 성장의 흐름으로 구성한다.\n"
        "2. 해당 과목에 대한 역량과 사고의 깊이가 드러나도록 서술한다.\n"
        "3. 진로와 자연스럽게 연결하되 과도하게 억지로 엮지 않는다."
    ),
    "자율·자치활동": (
        "1. 학급·학교 공동체 활동에 참여하는 태도와 자치 활동에서의 역할 수행을 중심으로 쓴다.\n"
        "2. 리더십·협력·배려가 드러나는 구체적 장면을 담는다.\n"
        "3. 행사명을 나열하지 말고, 그 안에서 맡은 역할과 실제 기여를 중심으로 서술한다.\n"
        "4. 과목 역량보다 공동체 안에서의 성장과 태도 변화에 초점을 둔다."
    ),
    "동아리활동": (
        "1. 동아리 활동의 지속성과 주도성이 드러나도록 쓴다.\n"
        "2. 부원과의 협업 과정, 동아리 안에서의 역할을 구체적으로 담는다.\n"
        "3. 동아리 주제에 대한 심화 탐구와 산출물을 중심으로 서술한다.\n"
        "4. 학업·교과와의 억지스러운 연계는 지양하고 동아리 활동 자체의 의미를 살린다."
    ),
    "진로활동": (
        "1. 자기이해 → 진로 탐색 → 진로 설계의 흐름으로 구성한다.\n"
        "2. 진로 관련 활동에서의 능동성과 탐색 과정을 구체적으로 서술한다.\n"
        "3. 탐색 과정에서 나타난 관심의 변화·확장을 담는다.\n"
        "4. 특정 대학·직업을 단정 짓지 말고 열린 탐색의 관점으로 쓴다."
    ),
    "행동특성 및 종합의견": (
        "1. 담임 교사가 1년간 종합 관찰한 시점에서 서술한다.\n"
        "2. 인성·학업 태도·대인 관계·성장 변화를 종합적으로 담는다.\n"
        "3. 장점 중심으로 쓰되 근거 있는 구체적 사례를 함께 제시한다.\n"
        "4. 추천서에 준하는 신뢰성 있는 서술로, 막연한 칭찬을 지양한다."
    ),
}


def category_for_neis_item(neis_item: str) -> str:
    """사이드바 NEIS 항목 라벨을 작성 항목(작성 원칙) 카테고리로 매핑한다.

    라벨의 부분 문자열로 판별하여 향후 라벨 문구가 바뀌어도 견디도록 한다.
    알 수 없는 항목은 기본값 '세특'으로 처리한다.
    """
    item = neis_item or ""
    if "행동특성" in item:
        return "행동특성 및 종합의견"
    if "자율" in item or "자치" in item:
        return "자율·자치활동"
    if "동아리" in item:
        return "동아리활동"
    if "진로" in item:
        return "진로활동"
    return "세특"


def _style_example_parts(style_examples: list[str] | None) -> list[str]:
    """문체 참고 예시가 있으면 예시 섹션 + 내용 차용 금지 지시 파트 목록을 반환한다."""
    examples = [ex.strip() for ex in (style_examples or []) if ex and ex.strip()]
    if not examples:
        return []
    parts = [f"[문체 참고 예시 {i}]\n{ex}" for i, ex in enumerate(examples, start=1)]
    parts.append(
        "위 문체 참고 예시는 문체·어미·구성 방식만 참고하고, "
        "예시에 담긴 내용·사실·활동은 절대 가져오지 않는다."
    )
    return parts


def build_draft_prompt(
    subject: str,
    major: str,
    performance: str,
    self_eval: str,
    target_len: int,
    style_examples: list[str] | None = None,
    category: str = "세특",
    observations: str = "",
) -> str:
    """세특 초안 생성용 프롬프트 본문을 조립한다 (순수 함수)."""
    parts = [
        f"[작성 항목]\n{category}",
        f"[과목명]\n{subject.strip() or '미입력'}",
        f"[학생의 희망 진로/학과]\n{major.strip() or '미입력'}",
        f"[목표 분량]\n공백 포함 약 {target_len}자",
    ]
    if performance.strip():
        parts.append(f"[수행평가 활동 내용 (교사 입력)]\n{performance.strip()}")
    if observations.strip():
        memo_lines = "\n".join(
            f"- {line.strip()}" for line in observations.splitlines() if line.strip()
        )
        parts.append(f"[교사 관찰 메모 (학기 중 기록)]\n{memo_lines}")
        parts.append(
            "교사 관찰 메모의 사실들을 자연스러운 서사로 통합하여 반영하라. "
            "메모에 없는 사실을 지어내지 않는다."
        )
    if self_eval.strip():
        parts.append(f"[학생 자기평가서 원문]\n{self_eval.strip()}")
        parts.append("학생 자기평가서 내용을 우선 활용하고, 수행평가 내용으로 보완하여 작성하라.")
    elif not observations.strip():
        parts.append("학생 자기평가서가 없으므로 수행평가 활동 내용을 기반으로 작성하라.")

    if category != "세특" and category in CATEGORY_GUIDES:
        parts.append(f"[이 항목의 작성 원칙]\n{CATEGORY_GUIDES[category]}")
        parts.append(
            "위 항목의 성격에 맞게 서술하되, 기재 금지 사항과 개조식 문체 원칙은 동일하게 지킨다."
        )

    parts.extend(_style_example_parts(style_examples))

    parts.append("위 자료를 바탕으로 세특 초안 본문만 출력하라.")

    return "\n\n".join(parts)


def generate_draft_with_gemini(
    subject: str,
    major: str,
    performance: str,
    self_eval: str,
    target_len: int,
    api_key: str,
    style_examples: list[str] | None = None,
    category: str = "세특",
    observations: str = "",
) -> str:
    """입력 자료를 바탕으로 세특 초안을 생성한다."""
    model = _make_model(api_key, DRAFT_SYSTEM_PROMPT, temperature=0.7)
    prompt = build_draft_prompt(
        subject,
        major,
        performance,
        self_eval,
        target_len,
        style_examples,
        category,
        observations,
    )
    return _gemini_text(model, prompt)


def refine_draft_with_gemini(
    draft: str,
    feedback: str,
    target_len: int,
    api_key: str,
    context: dict | None = None,
) -> str:
    """기존 초안에 교사 피드백을 반영하여 재작성한다.

    context가 있으면 최초 생성에 쓰인 원자료(과목·수행평가·자기평가서)를 함께 전달해
    재생성을 반복해도 원자료에 없는 사실이 끼어들지 않도록 한다.
    """
    model = _make_model(api_key, DRAFT_SYSTEM_PROMPT, temperature=0.7)
    category = (context or {}).get("category") or "세특"
    parts = [f"[작성 항목]\n{category}"]
    if context:
        if context.get("subject"):
            parts.append(f"[과목명]\n{context['subject']}")
        if context.get("performance"):
            parts.append(f"[수행평가 활동 내용 (원자료)]\n{context['performance']}")
        if context.get("observations"):
            parts.append(f"[교사 관찰 메모 (원자료)]\n{context['observations']}")
        if context.get("self_eval"):
            parts.append(f"[학생 자기평가서 원문 (원자료)]\n{context['self_eval']}")
    parts.append(f"[기존 세특 초안]\n{draft}")
    parts.append(f"[교사 피드백]\n{feedback.strip()}")
    parts.append(f"[목표 분량]\n공백 포함 약 {target_len}자")
    if category != "세특" and category in CATEGORY_GUIDES:
        parts.append(f"[이 항목의 작성 원칙]\n{CATEGORY_GUIDES[category]}")
    if context:
        parts.extend(_style_example_parts(context.get("style_examples")))
    parts.append(
        "기존 초안을 교사 피드백에 맞게 수정하여 세특 초안 본문만 출력하라. "
        "피드백과 무관한 부분은 최대한 유지하고, "
        "원자료에 없는 새로운 사실을 추가하지 않는다."
    )
    return _gemini_text(model, "\n\n".join(parts))


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
    model = _make_model(api_key, PROOFREAD_SYSTEM_PROMPT, temperature=0.1, json_mode=True)
    parsed = _gemini_json(
        model, f"[검사 대상 텍스트]\n{text}\n\n오탈자·표기 오류를 JSON 배열로만 답하라."
    )
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
    model = _make_model(api_key, ADJUST_SYSTEM_PROMPT, temperature=0.3)
    prompt = (
        f"[원문 — 공백 포함 {len(text)}자]\n{text}\n\n"
        f"[목표 분량]\n공백 포함 {target_len}자 (±5% 이내)\n\n"
        "조절된 본문만 출력하라."
    )
    result = _gemini_text(model, prompt)

    if target_len and abs(len(result) - target_len) / target_len > 0.10:
        direction = "더 줄여라" if len(result) > target_len else "더 늘려라"
        retry_prompt = (
            f"[원문 — 공백 포함 {len(result)}자]\n{result}\n\n"
            f"[목표 분량]\n공백 포함 {target_len}자 (±5% 이내). "
            f"현재 {len(result)}자이므로 {direction}.\n\n"
            "조절된 본문만 출력하라."
        )
        result = _gemini_text(model, retry_prompt)
    return result
