# -*- coding: utf-8 -*-
"""2026-11-24 규칙 일반화 보강(core/rules.py EXTRA_RULE_PATTERNS·문맥 규칙·예외) 단위 테스트.

모든 문장은 이 테스트를 위해 새로 지은 합성문이다(골든셋·홀드아웃 문장 아님).
"""

import pytest

from core.rulebook import RULEBOOK, apply_exceptions
from core.rules import EXTRA_RULE_PATTERNS, rule_based_filter, style_check


def _ids(text):
    return {f["rule_id"] for f in rule_based_filter(text)}


POSITIVE = [
    # (기대 rule_id, 문장)
    ("ip_right", "직접 만든 물 절약 밸브로 특허 등록을 마침."),
    ("ip_right", "휴대용 정수 장치 아이디어로 특허를 받아 화제가 됨."),
    ("ip_right", "설계한 문구 도구에 대한 특허 취득 경험을 소개함."),
    ("ip_right", "지식 재산권 출원 절차를 직접 밟음."),
    ("university_name", "하늘빛공대 기계공학과 실험실을 견학함."),
    ("university_name", "솔내의대 교수의 강연을 듣고 면역 반응을 정리함."),
    ("university_name", "가람대 화학과 교수에게 질문지를 보냄."),
    ("university_name", "J대 캠퍼스 투어에 참여함."),
    ("institution", "한빛해양과학연구소 방문 후 조류 흐름을 분석함."),
    ("institution", "푸른숲생태재단에서 주최한 강연을 들음."),
    ("institution", "대한가상로봇협회 자료를 인용해 발표함."),
    ("outside_award", "지역 복지관에서 감사패를 받은 경험을 언급함."),
    ("outside_award", "봉사 활동으로 표창을 받으며 책임감을 키움."),
    ("outside_award", "군수로부터 받은 모범 학생 상을 계기로 진로를 고민함."),
    ("scholarship", "가정 형편으로 학비 지원을 받으며 학업을 이어 감."),
    ("scholarship", "교외 장학 혜택을 받아 실험 도구를 마련함."),
    ("scholarship", "등록금 감면 대상으로 선정되어 동기를 얻음."),
    ("paper_book", "직접 집필한 여행 에세이를 출간하여 주목받음."),
    ("paper_book", "자신이 쓴 동화가 출판되어 시중 서점에서 판매됨."),
    ("paper_book", "첫 책의 인세를 기부함."),
    ("license", "국가기술자격을 준비하며 회로 지식을 익힘."),
    ("license", "드론 조종 자격을 취득한 경험을 발표에 활용함."),
    ("certification_test", "한국어 급수 시험을 준비하며 어휘를 정리함."),
    ("certification_test", "제과 실기 시험에 합격한 경험을 나눔."),
    ("lecturer", "외부 강사 최한결 선생님의 강의를 듣고 판소리 장단을 익힘."),
    ("lecturer", "김서하 작가님의 특강에서 인물 묘사 기법을 배움."),
    ("school_name", "'푸른달 축제'에서 과학 체험 부스를 운영함."),
    ("school_name", "'은하제'로 이름 붙은 행사에서 공연을 기획함."),
    ("competition", "지역 발명 경진대회에 참가해 시제품을 선보임."),
    ("competition", "청소년 코딩 챌린지에 참가해 결선에 오름."),
    ("competition", "환경 아이디어 행사 예선을 통과함."),
    ("competition", "토론 행사에서 최우수 팀으로 선정됨."),
    ("competition", "과학 행사에서 장려상을 받음."),
    ("parent_status", "변호사인 아버지와 법의 역할을 이야기함."),
    ("parent_status", "건설업계에 근무하는 어머니의 이야기를 듣고 안전 문제를 탐구함."),
    ("parent_status", "아버지께서 운영하시는 빵집 매출 자료로 함수를 세움."),
    ("parent_status", "가업을 잇기 위해 경영 원리를 공부함."),
    ("overseas", "방학 동안 해외 체류 경험을 바탕으로 발표함."),
    ("overseas", "외국 의료 봉사 경험을 소개함."),
    ("overseas", "현지 연수 중 배운 표현을 활용함."),
    ("overseas", "캄보디아 시골 학교에서 교육 나눔 활동을 함."),
    ("mooc_afterschool", "온라인 공개 강좌로 미적분을 선행 학습함."),
    ("mooc_afterschool", "방과 후 수업에서 배운 코딩을 활용함."),
    ("thesis", "연구보고서를 작성하며 실험 설계를 다듬음."),
    ("thesis_record", "'도시 열섬과 녹지 비율'을 주제로 3명이 함께 탐구함."),
    ("thesis_record", "'빛 세기와 발아율' 연구를 30시간 동안 수행함."),
    ("gifted_edu", "영재교육원에서 익힌 실험 기법을 적용함."),
]


@pytest.mark.parametrize("rule_id, text", POSITIVE)
def test_generalized_rule_detects(rule_id, text):
    assert rule_id in _ids(text), (rule_id, text, rule_based_filter(text))


NEGATIVE = [
    "특허 제도의 장단점을 비교하는 토론을 진행함.",
    "특허를 받은 기술의 원리를 조사해 발표함.",
    "현대 사회의 문제를 시대별로 비교함.",
    "최대 확대 배율로 세포를 관찰함.",
    "전문대학과 일반대학의 차이를 조사함.",
    "진학하고 싶은 대학의 학과 정보를 스스로 탐색함.",
    "선임연구원 인터뷰 내용을 요약함.",
    "학생자치위원회 회의록을 정리하며 안건을 분류함.",
    "도교육청에서 제공한 자료를 분석함.",
    "관련협회 자료 유형을 분류함.",
    "모둠원에게 감사한 마음을 전함.",
    "장학 제도의 경제적 효과를 그래프로 분석함.",
    "최근 출간된 『빛의 과학』을 읽고 감상을 정리함.",
    "학급 문집을 편집하며 맞춤법을 점검함.",
    "자격 요건을 갖춘 후보를 비교하는 토론을 함.",
    "유명한 작가님의 글을 필사하며 문체를 익힘.",
    "이번 작가님 강연 소개 자료를 읽음.",
    "'환경 문제'를 주제로 토론함.",
    "'공유 경제'라는 주제로 발표함.",
    "결승점까지의 거리를 계산함.",
    "부모 세대와 자녀 세대의 소비 성향을 비교함.",
    "할머니를 도와드린 경험에서 복지 정책에 관심을 가짐.",
    "외국 노동자 거주 지역의 변화를 조사함.",
    "제주 현지 탐방 결과를 지도로 표현함.",
    "실험 오차를 3% 이내로 줄이기 위해 측정을 반복함.",
    "용액의 농도를 10%로 맞추어 반응 속도를 비교함.",
    "'물의 순환'을 주제로 4명씩 모둠을 이루어 토의함.",
    "탐구 보고서를 작성하며 자료 출처를 밝힘.",
]


@pytest.mark.parametrize("text", NEGATIVE)
def test_generalized_rule_no_false_positive(text):
    assert rule_based_filter(text) == [], (text, rule_based_filter(text))


def test_score_percent_with_exam_context_still_detected():
    assert "score" in _ids("국어 백분위 97%를 기록함.")


def test_paps_grade_excluded_for_pe_subject():
    text = "PAPS 근지구력 2등급을 받은 뒤 훈련 계획을 세움."
    f = [x for x in rule_based_filter(text) if x["rule_id"] == "score"]
    assert f
    word = f[0]["word"]
    start = text.find(word)
    _, note, excluded = apply_exceptions("score", "기재 불가", word, text, start, start + len(word), "체육")
    assert excluded and "PAPS" in note
    verdict, _, excluded = apply_exceptions("score", "기재 불가", word, text, start, start + len(word), "")
    assert not excluded and verdict == "확인 필요"
    _, _, excluded = apply_exceptions("score", "기재 불가", word, text, start, start + len(word), "국어")
    assert not excluded


def test_non_paps_grade_not_excluded():
    text = "국어 1등급을 받음."
    _, _, excluded = apply_exceptions("score", "기재 불가", "국어 1등급", text, 0, 6, "체육")
    assert not excluded


def test_english_foreign_name_sequence_not_flagged():
    assert not any("영문" in w for w in style_check("Virginia Woolf의 수필을 읽고 화자의 시선을 정리함."))
    assert not any("영문" in w for w in style_check("『The Old Man and the Sea』를 원문으로 읽음."))


def test_english_single_word_still_flagged():
    assert any("영문" in w for w in style_check("Excel로 자료를 정리함."))


def test_new_rule_ids_in_rulebook_with_basis():
    for *_, rule_id in EXTRA_RULE_PATTERNS:
        assert rule_id in RULEBOOK
        assert RULEBOOK[rule_id]["source"]
    assert "thesis_record" in RULEBOOK


def test_education_institution_not_flagged_by_suffix_rule():
    assert "institution" not in _ids("서울특별시교육과학연구원 자료를 활용함.")
