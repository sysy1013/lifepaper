# -*- coding: utf-8 -*-
"""교사 판단 힌트 규칙·띄어쓰기 로컬 규칙·마침표 추정·리포트 배지 테스트.

모든 문장은 이 테스트를 위해 새로 지은 합성 문장이다(골든셋·홀드아웃 문장 재사용 금지).
"""

import pytest

from core.report import (
    JUDGMENT_BADGE,
    VERDICT_ADVICE,
    VERDICT_CHECK,
    build_issue_report,
    count_judgment_rows,
    report_table_rows,
    summarize_report,
)
from core.rulebook import RULEBOOK, apply_exceptions
from core.rules import (
    HINT_PATTERNS,
    HINT_RULE_IDS,
    collect_style_issues,
    is_hint,
    rule_based_filter,
    style_check,
)


def _hint_ids(text):
    return {f["rule_id"] for f in rule_based_filter(text) if is_hint(f)}


def _all_ids(text):
    return {f["rule_id"] for f in rule_based_filter(text)}


# ── 힌트 규칙: 양성 ──
@pytest.mark.parametrize(
    "rule_id, text",
    [
        ("hint_competition", "전국 단위의 과학 토론회에서 익힌 반박 전략을 수업에 적용함."),
        ("hint_competition", "시도 규모 발표회에서 얻은 피드백으로 실험 설계를 고침."),
        ("hint_competition", "청소년 탐구 발표회에서 태양광 패널 효율 결과를 발표함."),
        ("hint_competition", "여름 영재 캠프에 선발되어 천체 관측 기법을 익힘."),
        ("hint_competition", "학교 대표로 출전한 경험을 바탕으로 협업의 의미를 정리함."),
        ("hint_competition", "지역 정책 공모에 낸 제안서를 바탕으로 교통 문제를 분석함."),
        ("hint_afterschool", "정규 수업 이후 운영된 화학 강좌에서 적정 실험을 반복함."),
        ("hint_afterschool", "방과 이후 열린 코딩 특강에서 반복문 구조를 익힘."),
        ("hint_afterschool", "주말 강좌에서 배운 미분 개념을 수업 풀이에 활용함."),
        ("hint_outside", "학교 밖 교육기관에서 진행한 갯벌 관찰 결과를 발표함."),
        ("hint_outside", "교외 체험에서 수집한 암석 표본의 특징을 분류함."),
        ("hint_outside", "대학 주관 여름 캠프에서 배운 측정 방법을 실험에 적용함."),
        ("hint_outside", "인터넷 강의를 수강하며 정리한 함수 개념을 발표함."),
        ("hint_outside", "교내 축제에서 전시 부스를 꾸리며 굴절 원리를 소개함."),
        ("hint_outside", "청소년 수련관 프로그램에서 만든 작품을 소개함."),
        ("hint_parent", "외벌이 가정의 생활비 구조를 분석하며 가계부를 작성함."),
        ("hint_parent", "약국을 경영하시는 어머니의 일을 보며 복약 지도에 관심을 가짐."),
        ("hint_parent", "어머니의 직장에서 본 회계 장부 정리 방식을 수업에 적용함."),
        ("hint_parent", "언론계에 몸담은 아버지의 영향으로 보도 윤리를 탐구함."),
        ("hint_certification", "제빵 기능 검정에 최종 합격한 경험을 발효 단원에 연결함."),
        ("hint_certification", "역량 인증 과정을 밟으며 데이터 분석 도구를 익힘."),
        ("hint_private_edu", "어학원에서 익힌 발음 교정법을 영어 발표에 활용함."),
        ("hint_private_edu", "교습소에서 연습한 풀이 방식을 모둠원에게 설명함."),
        ("hint_brand", "국내 최대 포털에서 검색한 기사를 비교해 보도 관점을 분석함."),
        ("hint_genai", "생성형 AI에 질문해 받은 요약을 검증하며 출처를 확인함."),
        ("hint_genai", "인공지능 챗봇을 활용해 토론 반론을 미리 점검함."),
    ],
)
def test_hint_positive(rule_id, text):
    assert rule_id in _hint_ids(text), (rule_id, text, rule_based_filter(text))


# ── 힌트 규칙: 음성(평범한 수업 문장) ──
@pytest.mark.parametrize(
    "text",
    [
        "전국 단위 통계 자료를 분석해 지역별 인구 변화를 정리함.",
        "수업 후 교실을 정리하며 실험 도구를 점검함.",
        "학교 밖 청소년 지원 정책의 공백을 조사해 개선안을 제시함.",
        "부모의 직업 분포를 다룬 통계표에서 상관관계를 읽어 냄.",
        "생성형 AI의 저작권 문제를 주제로 찬반 토론을 진행함.",
        "대학원 진학 경로를 조사해 진로 계획을 구체화함.",
        "입학원서 작성법을 조사하며 자기소개 문장을 다듬음.",
        "사교육비 문제를 다룬 기사를 읽고 교육 격차를 분석함.",
        "할아버지와 텃밭을 가꾸며 토양 산성도를 측정함.",
        "모둠별 발표에서 근거 자료의 신뢰성을 비교함.",
        "자격 요건의 의미를 비교하며 직업 윤리를 토론함.",
        "세계 지도를 보며 대륙별 기후 특징을 정리함.",
    ],
)
def test_hint_negative(text):
    assert not _hint_ids(text), (text, rule_based_filter(text))


def test_hint_finding_fields():
    f = [x for x in rule_based_filter("교외 체험에서 표본을 모아 분류함.") if is_hint(x)][0]
    assert f["level"] == "hint" and f["severity"] == "주의"
    assert "확인" in f["judgment"] and "교사 판단 필요" in f["reason"]


def test_include_hints_false_excludes_hints():
    text = "주말 강좌에서 배운 개념을 발표함. 구글 자료를 인용함."
    with_h = rule_based_filter(text)
    without = rule_based_filter(text, include_hints=False)
    assert any(is_hint(f) for f in with_h)
    assert not any(is_hint(f) for f in without)
    assert any(f["rule_id"] == "brand" for f in without)


def test_hint_does_not_duplicate_violation_span():
    # 위반 패턴('방과 후 강좌')이 잡은 범위는 힌트로 다시 내지 않는다
    findings = rule_based_filter("방과 후 강좌에서 회로를 설계함.")
    assert any(f["rule_id"] == "mooc_afterschool" for f in findings)
    assert not any(f["rule_id"] == "hint_afterschool" for f in findings)


def test_every_hint_has_rulebook_judgment_entry():
    for rid in HINT_RULE_IDS:
        assert RULEBOOK[rid].get("judgment") is True, rid
    assert len(HINT_PATTERNS) == len(HINT_RULE_IDS)


# ── 부모 직업명 + 호칭 (조사 없이) — 위반 패턴 ──
@pytest.mark.parametrize("text", ["변호사 삼촌의 조언으로 계약서를 분석함.", "약사 어머니와 함께 성분표를 읽음."])
def test_occupation_adjacent_parent_violation(text):
    assert "parent_status" in _all_ids(text)


@pytest.mark.parametrize("text", ["학생 대표 어머니회 활동을 조사함.", "의사 선생님의 강연 내용을 요약함."])
def test_occupation_adjacent_parent_negative(text):
    assert "parent_status" not in _all_ids(text)


def test_afterschool_no_space_violation():
    assert "mooc_afterschool" in _all_ids("방과후 수업에서 로봇 제어를 익힘.")


# ── 띄어쓰기 로컬 규칙 ──
def _spacing(text):
    return [(i["word"], i["suggestion"], bool(i.get("judgment")))
            for i in collect_style_issues(text) if i["rule_id"] == "spacing"]


@pytest.mark.parametrize(
    "text, word, fix",
    [
        ("학생들 을 모둠으로 나누어 실험함.", "학생들 을", "학생들을"),
        ("선배들 과 함께 자료를 검토함.", "선배들 과", "선배들과"),
        ("실험 결과 를 표로 정리함.", "결과 를", "결과를"),
        ("도서관 에서 자료를 찾음.", "도서관 에서", "도서관에서"),
        ("탐구 주제 로 환경을 선택함.", "주제 로", "주제로"),
        ("온도, 습도등 여러 조건을 기록함.", "습도등", "습도 등"),
        ("물질등의 성질을 비교함.", "물질등", "물질 등"),
    ],
)
def test_spacing_positive(text, word, fix):
    assert (word, fix, False) in _spacing(text)


@pytest.mark.parametrize(
    "text",
    [
        "남은 과제를 마저 해결하며 개념을 정리함.",
        "이 과정에서 동등한 비율을 계산함.",
        "남녀 평등과 불평등 문제를 토론함.",
        "신호등 체계를 분석하고 가로등 배치를 제안함.",
        "갈등 상황을 중재하며 합의안을 도출함.",
        "1등과 2등의 차이를 비교함.",
        "고등학교 교육과정의 변화를 조사함.",
        "배운 내용을 바탕으로 이 원리를 설명함.",
        "수학과 과학의 연결 고리를 찾아 발표함.",
        "그 과정을 순서대로 정리함.",
    ],
)
def test_spacing_negative(text):
    assert _spacing(text) == [], (text, _spacing(text))


def test_spacing_ambiguous_particle_is_judgment():
    hits = _spacing("국어 과 교사의 시범을 관찰함.")
    assert hits and all(j for _w, _f, j in hits)


def test_an_doeda_is_judgment():
    hits = _spacing("측정이 잘 안되어 장비를 다시 보정함.")
    assert hits and hits[0][2] is True


def test_spacing_warning_message_mentions_spacing():
    assert any("띄어" in w for w in style_check("친척들 과 대화함."))


# ── 마침표 누락(추정) ──
def test_missing_period_single_noun_sentence_flagged_as_inferred():
    issues = [i for i in collect_style_issues("세포 분열 과정을 모형으로 재현함") if i["rule_id"] == "format_missing_period"]
    assert issues and "추정" in issues[0]["problem"]


def test_missing_period_report_verdict_is_advice_with_note():
    rows = [r for r in build_issue_report("세포 분열 과정을 모형으로 재현함", []) if r["rule_id"] == "format_missing_period"]
    assert rows and rows[0]["verdict"] == VERDICT_ADVICE
    assert "추정" in rows[0]["exception"]


def test_missing_period_not_flagged_when_present():
    assert not any(i["rule_id"] == "format_missing_period"
                   for i in collect_style_issues("세포 분열 과정을 모형으로 재현함."))


# ── 리포트: 교사 판단 배지·요약 ──
def test_report_hint_rows_badge_and_summary():
    text = "주말 강좌에서 배운 개념을 발표함. 구글 자료를 인용함."
    findings = rule_based_filter(text)
    rows = build_issue_report(text, findings)
    hint_rows = [r for r in rows if r["rule_id"] == "hint_afterschool"]
    assert hint_rows and hint_rows[0]["verdict"] == VERDICT_CHECK and hint_rows[0]["judgment"]
    brand_rows = [r for r in rows if r["rule_id"] == "brand"]
    assert brand_rows and not brand_rows[0]["judgment"]
    table = report_table_rows(hint_rows)
    assert JUDGMENT_BADGE in table[0]["판정"] and JUDGMENT_BADGE in table[0]["오류 내용"]
    assert count_judgment_rows(rows) == len([r for r in rows if r["judgment"]]) >= 1
    summary = summarize_report(rows)
    assert f"{JUDGMENT_BADGE} {count_judgment_rows(rows)}건" in summary


def test_report_judgment_from_rulebook_when_field_missing():
    # Gemini·구버전 결과처럼 judgment 키가 없어도 rule_id로 배지를 붙인다
    text = "교외 체험에서 표본을 분류함."
    rows = build_issue_report(text, [{"word": "교외 체험", "rule_id": "hint_outside", "severity": "주의", "reason": "x"}])
    assert rows[0]["judgment"] and rows[0]["verdict"] == VERDICT_CHECK


def test_report_spacing_judgment_row_is_check():
    rows = [r for r in build_issue_report("측정이 잘 안되어 보정함.", []) if r["rule_id"] == "spacing"]
    assert rows and rows[0]["verdict"] == VERDICT_CHECK and rows[0]["judgment"]


# ── 기존 예외 회귀 (물결표·외국인 성명·PAPS) ──
def test_tilde_still_flagged():
    assert any("물결" in w or "특수문자" in w for w in style_check("실험을 3~4시간 진행함."))


def test_foreign_name_still_allowed():
    assert not any("영문" in w for w in style_check("Marie Curie의 생애를 읽고 연구 윤리를 정리함."))


def test_paps_exception_still_applies():
    text = "PAPS 심폐지구력 1등급을 받아 운동 계획을 세움."
    f = [x for x in rule_based_filter(text) if x["rule_id"] == "score"][0]
    start = text.find(f["word"])
    _v, _note, excluded = apply_exceptions("score", "기재 불가", f["word"], text, start, start + len(f["word"]), "체육")
    assert excluded


# ── Gemini 프롬프트·후처리 ──
class _Resp:
    def __init__(self, text):
        self.text = text


class _Model:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def generate_content(self, prompt, model_name=None):
        self.prompts.append(prompt)
        return _Resp(self.response)


def _stub(monkeypatch, response):
    holder = {"model": _Model(response)}

    def fake_make_model(api_key, system_instruction, temperature, json_mode=False, **k):
        holder["system"] = system_instruction
        return holder["model"]

    monkeypatch.setattr("core.gemini._make_model", fake_make_model)
    return holder


def test_system_prompt_has_paraphrase_guidance():
    from core import gemini

    for kw in ("우회·변형 표현 판단 지침", "특허 출원", "본선", "국가기술자격", "학급 문집", "도서명"):
        assert kw in gemini.SYSTEM_PROMPT, kw
    system, user = gemini.build_analyze_prompts("주말 강좌에서 배운 개념을 발표함.", "", True)
    assert system.startswith(gemini.SYSTEM_PROMPT) and "[판단 참고 예시" in user


def test_hint_maps_to_fewshot_category(monkeypatch):
    from core import gemini

    seen = {}

    def fake_select(text, k=6, hint_categories=()):
        seen["hints"] = set(hint_categories)
        return []

    monkeypatch.setattr("core.fewshot.select_fewshots", fake_select)
    gemini.build_analyze_prompts("교외 체험에서 표본을 모아 분류함.", "", True)
    assert "mooc_afterschool" in seen["hints"]


def test_gemini_brand_inside_book_title_is_dropped(monkeypatch):
    import json

    from core import gemini

    item = {"word": "구글", "reason": "상업적 명칭", "basis": "브랜드 (2026 기재요령 p.18-19)",
            "severity": "위반", "suggestion_1": "a", "suggestion_2": "b", "cited_rule": "brand"}
    _stub(monkeypatch, json.dumps([item], ensure_ascii=False))
    assert gemini.analyze_with_gemini("『구글의 철학』을 읽고 조직 문화를 정리함.", "", "key") == []
    _stub(monkeypatch, json.dumps([item], ensure_ascii=False))
    out = gemini.analyze_with_gemini("구글 지도를 활용해 거리를 계산함.", "", "key")
    assert out and out[0]["word"] == "구글"


def test_rewrite_retry_ignores_hints(monkeypatch):
    from core import gemini

    h = _stub(monkeypatch, "주말 강좌에서 배운 개념을 발표함.")
    gemini.rewrite_with_gemini("원문임.", [], "", "key")
    assert len(h["model"].prompts) == 1  # 힌트만 남으면 재요청하지 않음
