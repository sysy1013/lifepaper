# -*- coding: utf-8 -*-
"""전송 전 식별 위험 신호 검출(core/privacy.py) 긍정·부정 테스트."""

import pytest

from core.privacy import (
    KIND_EMAIL,
    KIND_NAME,
    KIND_PHONE,
    KIND_REGION,
    KIND_RRN,
    KIND_SCHOOL,
    KIND_STUDENT_NO,
    KIND_TITLE,
    scan_identifying_risks,
)


def _kinds(text, **kw):
    return [(r["kind"], r["text"]) for r in scan_identifying_risks(text, **kw)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("경기도 하천의 수질을 조사함", (KIND_REGION, "경기도")),
        ("충청남도 지역 축제를 분석함", (KIND_REGION, "충청남도")),
        ("서울특별시 인구 자료를 분석함", (KIND_REGION, "서울특별시")),
        ("수원시 장안구 하천을 조사함", (KIND_REGION, "수원시 장안구")),
        ("홍성군 홍성읍 시장을 탐방함", (KIND_REGION, "홍성군 홍성읍")),
        ("수원시청 자료를 참고함", (KIND_REGION, "수원시청")),
        ("세종대로 175 주변 상권을 조사함", (KIND_REGION, "세종대로 175")),
    ],
)
def test_region_flagged(text, expected):
    assert expected in _kinds(text)


@pytest.mark.parametrize(
    "text",
    [
        "도시 문제를 탐구하고 운동 원리를 정리함",
        "실험 시 측면과 단면을 관찰함",
        "연구 활동에서 물리 법칙을 익힘",
        "과정으로 2가지 방법을 비교함",
        "서울의 역사와 문화를 조사함",
        "필요시 자료를 보완함",
        "지역 하천의 수질을 조사함",
    ],
)
def test_region_not_flagged(text):
    assert not any(k == KIND_REGION for k, _ in _kinds(text))


def test_title_flagged():
    text = "'디지털 기술에 따른 사회 변화 조사 보고서' 작성 활동에서 탐구함"
    assert (KIND_TITLE, "'디지털 기술에 따른 사회 변화 조사 보고서'") in _kinds(text)


@pytest.mark.parametrize(
    "text", ["'협력'의 의미를 되새김", "\"공감\"을 강조함", "'인공지능윤리탐구'를 함", "《비공개1》 학생과 협업함"]
)
def test_title_not_flagged_for_emphasis(text):
    assert not any(k == KIND_TITLE for k, _ in _kinds(text))


@pytest.mark.parametrize(
    "text, word",
    [
        ("한빛고등학교 학생으로서 탐구함", "한빛고등학교"),
        ("한빛여고 축제에서 공연함", "한빛여고"),
        ("한빛고 학생회 활동을 함", "한빛고"),
        ("한빛중학교 시절 경험을 떠올림", "한빛중학교"),
    ],
)
def test_school_flagged(text, word):
    assert (KIND_SCHOOL, word) in _kinds(text)


@pytest.mark.parametrize(
    "text",
    ["최고 학생회장 후보 토론을 관찰함", "참고 축제 자료를 정리함", "고등학교 과정의 개념을 정리함", "학교 축제를 기획함"],
)
def test_school_not_flagged(text):
    assert not any(k == KIND_SCHOOL for k, _ in _kinds(text))


def test_contact_and_ids_flagged():
    text = "연락처 010-1234-5678, 메일 test@example.com, 학번 20301, 주민번호 050101-3123456"
    kinds = _kinds(text)
    assert (KIND_PHONE, "010-1234-5678") in kinds
    assert (KIND_EMAIL, "test@example.com") in kinds
    assert (KIND_STUDENT_NO, "20301") in kinds
    assert (KIND_RRN, "050101-3123456") in kinds
    # 주민번호 안에서 잡힌 짧은 신호는 겹치므로 남지 않는다.
    assert sum(1 for k, _ in kinds if k == KIND_RRN) == 1


def test_name_flagged_via_mask_candidates():
    text = "김민준 학생은 실험을 설계함. 김민준은 결과를 정리함."
    assert (KIND_NAME, "김민준") in _kinds(text)


def test_name_not_flagged_when_already_masked_or_registered():
    assert not any(k == KIND_NAME for k, _ in _kinds("《비공개1》 학생은 실험을 설계함."))
    text = "김민준 학생은 실험을 설계함. 김민준은 결과를 정리함."
    assert not any(k == KIND_NAME for k, _ in _kinds(text, existing=["김민준"]))


def test_clean_seteuk_has_no_risks():
    from tests.test_parsing import SIX_SUBJECT_TEXT

    assert scan_identifying_risks(SIX_SUBJECT_TEXT) == []
    assert scan_identifying_risks("") == []


def test_result_fields_and_offsets():
    text = "경기도 자료를 봄. 연락처 010-1234-5678"
    for r in scan_identifying_risks(text):
        assert set(r) == {"kind", "text", "start", "end", "advice"}
        assert text[r["start"]:r["end"]] == r["text"]
        assert r["advice"]


import pytest as _pytest


@_pytest.mark.parametrize(
    "text,kind",
    [
        ("수원시 장안구에 거주하는 친구와 협업함.", KIND_REGION),
        ("홍성군청에서 열린 행사에 참여함.", "지역명"),
        ("경기도의 사례를 조사함.", "지역명"),
        ("서울특별시에서 진행된 조사를 분석함.", "지역명"),
        ("홍성읍에서 자료를 수집함.", "지역명"),
        ("미래고등학교에서 활동한 경험을 정리함.", KIND_SCHOOL),
    ],
)
def test_identifier_with_trailing_particle_is_detected(text, kind):
    assert any(r["kind"] == kind for r in scan_identifying_risks(text))


@_pytest.mark.parametrize(
    "text",
    ["인구에 대한 연구에서 탐구를 수행함.", "토론에서 시작이 중요함을 확인함.", "도읍에서는 군사 구조를 비교함."],
)
def test_particle_support_adds_no_false_positive(text):
    assert not [r for r in scan_identifying_risks(text) if r["kind"] in ("지역명", "학교명")]
