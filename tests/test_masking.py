# -*- coding: utf-8 -*-
from core.masking import (
    apply_mask,
    build_mask_map,
    detect_pii,
    extend_mask_map,
    remove_mask,
    suggest_mask_candidates,
)


def test_build_mask_map_orders_longer_words_first():
    # 짧은 단어가 긴 단어의 부분 문자열일 때, 긴 단어부터 치환되어야 한다.
    mask_map = build_mask_map(["홍", "홍길동"])
    words = [w for w, _ in mask_map]
    assert words == ["홍길동", "홍"]
    # 토큰은 순서대로 부여된다.
    assert mask_map[0][1] == "《비공개1》"
    assert mask_map[1][1] == "《비공개2》"


def test_build_mask_map_dedupes_and_strips():
    mask_map = build_mask_map(["  홍길동  ", "홍길동", "", "   "])
    assert len(mask_map) == 1
    assert mask_map[0][0] == "홍길동"


def test_apply_remove_roundtrip():
    mask_map = build_mask_map(["홍길동", "20301"])
    text = "홍길동 학생(20301)은 성실하다."
    masked = apply_mask(text, mask_map)
    assert "홍길동" not in masked
    assert "20301" not in masked
    assert remove_mask(masked, mask_map) == text


def test_empty_map_is_noop():
    text = "아무 내용"
    assert apply_mask(text, []) == text
    assert remove_mask(text, []) == text


# ── suggest_mask_candidates ──
def test_suggest_detects_repeated_name():
    texts = ["김철수 학생은 성실함.", "발표에서 김철수 학생이 탐구 과정을 설명함."]
    assert "김철수" in suggest_mask_candidates(texts)


def test_suggest_detects_student_number():
    assert "20301" in suggest_mask_candidates(["학번 20301 학생의 활동 기록"])


def test_suggest_excludes_stopword():
    # '이해'는 성씨로 시작하지만 흔한 단어 → 불용어로 제외
    texts = ["내용을 이해함.", "깊이 이해하는 모습을 이해함."]
    assert "이해" not in suggest_mask_candidates(texts)


def test_suggest_excludes_existing_entries():
    texts = ["김철수 학생.", "김철수 발표함."]
    assert "김철수" not in suggest_mask_candidates(texts, existing=["김철수"])


def test_suggest_respects_max_5():
    names = ["김철수", "이영희", "박민수", "최지훈", "정하늘", "강도현", "조은별"]
    texts = [" ".join(names), " ".join(names)]  # 각 이름 2회 등장
    assert len(suggest_mask_candidates(texts)) <= 5


def test_suggest_single_occurrence_name_not_suggested():
    # 한 번만 등장하고 "학생"도 뒤따르지 않는 이름은 제안하지 않는다 (오탐 감소).
    assert "김철수" not in suggest_mask_candidates(["김철수 발표함."])


def test_suggest_single_occurrence_name_followed_by_student_is_suggested():
    # 1회만 등장해도 "학생"이 바로 뒤에 이어지면(공백 포함) 제안한다.
    texts = ["김철수 학생은 성실함.", "이영희 학생이 발표를 진행함."]
    result = suggest_mask_candidates(texts)
    assert "김철수" in result
    assert "이영희" in result


# ── detect_pii (자동 마스킹 탐지) ──
def test_detect_pii_repeated_name():
    # "학생"이 뒤따르지 않아도 2회 이상 독립 단어로 등장하면 탐지한다.
    texts = ["김철수 성실함.", "발표에서 김철수 탐구 과정을 설명함."]
    assert "김철수" in detect_pii(texts)


def test_detect_pii_name_with_student_suffix_once():
    assert "김철수" in detect_pii(["김철수 학생이 발표를 진행함."])


def test_detect_pii_student_number():
    assert "20301" in detect_pii(["학번 20301 학생의 활동 기록"])


def test_detect_pii_phone_number():
    assert "010-1234-5678" in detect_pii(["연락처는 010-1234-5678 입니다."])


def test_detect_pii_rrn():
    assert "010101-3234567" in detect_pii(["주민등록번호 010101-3234567 확인"])


def test_detect_pii_email():
    assert "student@example.com" in detect_pii(["메일 student@example.com 로 제출함."])


def test_detect_pii_excludes_stopword():
    assert "이해" not in detect_pii(["내용을 이해함.", "깊이 이해함."])


def test_detect_pii_excludes_existing():
    texts = ["김철수 학생.", "김철수 발표함."]
    assert "김철수" not in detect_pii(texts, existing=["김철수"])


def test_detect_pii_returns_longest_first():
    texts = ["김철수 학생(20301)의 연락처는 010-1234-5678 이다."]
    result = detect_pii(texts)
    assert len(result) >= 3
    assert [len(w) for w in result] == sorted((len(w) for w in result), reverse=True)


# ── extend_mask_map ──
def test_extend_mask_map_disabled_returns_base_unchanged():
    base = build_mask_map(["홍길동"])
    extended, added = extend_mask_map(["김철수 학생"], base, enabled=False)
    assert extended is base
    assert added == []


def test_extend_mask_map_adds_non_colliding_tokens():
    base = build_mask_map(["홍길동"])
    extended, added = extend_mask_map(["김철수 학생이 발표함."], base)
    assert "김철수" in added
    tokens = [t for _, t in extended]
    assert len(tokens) == len(set(tokens))  # 토큰 충돌 없음
    assert "《비공개1》" in tokens and "《비공개2》" in tokens
    assert ("홍길동", "《비공개1》") in extended


def test_extend_mask_map_keeps_longest_first():
    base = build_mask_map(["홍"])
    extended, _ = extend_mask_map(["김철수 학생(20301) 기록"], base)
    lengths = [len(w) for w, _ in extended]
    assert lengths == sorted(lengths, reverse=True)


def test_extend_mask_map_roundtrip_with_manual_and_auto_words():
    base = build_mask_map(["홍길동"])
    text = "홍길동 담당. 김철수 학생(20301)은 성실함."
    extended, added = extend_mask_map([text], base)
    masked = apply_mask(text, extended)
    assert "홍길동" not in masked
    assert "김철수" not in masked
    assert "20301" not in masked
    assert remove_mask(masked, extended) == text
    assert "김철수" in added and "20301" in added


def test_extend_mask_map_no_detection_returns_base():
    base = build_mask_map(["홍길동"])
    extended, added = extend_mask_map(["특이사항 없음."], base)
    assert added == []
    assert extended == base


def test_detect_pii_name_with_attached_particles():
    """조사가 붙은 이름(김철수는/김철수가)도 같은 이름의 출현으로 세어 탐지한다."""
    assert "김철수" in detect_pii(["김철수는 탐구함. 김철수가 발표함."])


def test_detect_pii_particle_name_single_occurrence_not_detected():
    """조사가 붙었더라도 1회만 등장하면 보수적으로 탐지하지 않는다."""
    assert detect_pii(["이영희는 발표함."]) == []


def test_detect_pii_particle_form_does_not_flag_common_words():
    """조사가 붙은 일반 명사(이해는/박수를)는 불용어로 걸러 오탐하지 않는다."""
    assert detect_pii(["이해는 중요함. 이해가 필요함. 박수를 침. 박수가 나옴."]) == []


def test_extend_mask_map_roundtrip_with_particle_name():
    """조사 붙은 이름을 자동 마스킹해도 원문이 정확히 복원된다."""
    base = build_mask_map(["홍길동"])
    text = "홍길동과 김철수는 발표함. 김철수가 정리함."
    extended, added = extend_mask_map([text], base)
    assert "김철수" in added
    masked = apply_mask(text, extended)
    assert "김철수" not in masked
    assert remove_mask(masked, extended) == text


def test_remove_mask_deep_restores_nested_quality_result():
    """품질 진단처럼 중첩된 응답 안의 마스킹 토큰도 모두 복원한다."""
    from core.masking import remove_mask_deep

    m = build_mask_map(["이영희"])
    q = {
        "scores": [{"criterion": "구체성", "score": 4, "comment": "《비공개1》의 탐구가 구체적임"}],
        "overall": "《비공개1》 학생은 우수함",
        "improvements": ["《비공개1》의 성장 서술 보완"],
    }
    out = remove_mask_deep(q, m)
    assert "비공개" not in str(out)
    assert out["scores"][0]["comment"] == "이영희의 탐구가 구체적임"
    assert out["scores"][0]["score"] == 4  # 비문자열 값은 그대로


def test_remove_mask_deep_passthrough_for_non_strings():
    from core.masking import remove_mask_deep

    assert remove_mask_deep(5, build_mask_map(["a"])) == 5
    assert remove_mask_deep(None, []) is None
