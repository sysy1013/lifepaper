# -*- coding: utf-8 -*-
"""이미지 입력: core/ocr.py 전처리·한도 검증 + Gemini 전사/대조 (스텁, 실제 API 호출 없음)."""

import io
import json

import pytest
from PIL import Image

import core.gemini as gemini
from core.ocr import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_LONG_SIDE,
    MAX_TOTAL_BYTES,
    UNCERTAIN_MARK,
    count_uncertain,
    mime_for_name,
    prepare_image,
    validate_uploads,
)
from core.parsing import split_by_subject

MB = 1024 * 1024


def _png(size=(40, 20), mode="RGB", color=(255, 0, 0)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


# ── prepare_image ──
def test_prepare_image_reencodes_to_jpeg():
    data, mime = prepare_image(_png(), "image/png")
    assert mime == "image/jpeg"
    img = _open(data)
    assert img.format == "JPEG"
    assert img.size == (40, 20)


def test_prepare_image_downscales_long_side():
    data, _ = prepare_image(_png(size=(6000, 1500)), "image/png")
    img = _open(data)
    assert max(img.size) == MAX_LONG_SIDE
    assert img.size == (3000, 750)


def test_prepare_image_keeps_small_image_size():
    data, _ = prepare_image(_png(size=(MAX_LONG_SIDE, 100)), "image/png")
    assert _open(data).size == (MAX_LONG_SIDE, 100)


def test_prepare_image_rgba_becomes_rgb_on_white():
    data, _ = prepare_image(_png(mode="RGBA", color=(0, 0, 0, 0)), "image/png")
    img = _open(data)
    assert img.mode == "RGB"
    # 투명 배경은 흰색으로 합성된다 (JPEG 손실 감안).
    assert all(c > 240 for c in img.getpixel((5, 5)))


def test_prepare_image_applies_exif_rotation():
    # 가로 40×20 이미지에 'Orientation=6(시계방향 90° 회전 필요)' EXIF를 붙이면
    # 보정 후 세로 20×40이 되어야 한다.
    img = Image.new("RGB", (40, 20), (0, 128, 255))
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    data, _ = prepare_image(buf.getvalue(), "image/jpeg")
    assert _open(data).size == (20, 40)


def test_prepare_image_pdf_passthrough():
    raw = b"%PDF-1.4 fake"
    assert prepare_image(raw, "application/pdf") == (raw, "application/pdf")


def test_prepare_image_broken_data_raises_korean_message():
    with pytest.raises(ValueError, match="이미지 파일을 열 수 없습니다"):
        prepare_image(b"not an image", "image/png")


# ── validate_uploads / mime_for_name ──
def test_validate_uploads_ok():
    validate_uploads([("a.png", 1 * MB), ("b.pdf", 2 * MB), ("c.JPG", 3 * MB)])


def test_validate_uploads_empty_raises():
    with pytest.raises(ValueError, match="먼저 올려"):
        validate_uploads([])


def test_validate_uploads_too_many_files():
    with pytest.raises(ValueError, match="최대 10개"):
        validate_uploads([(f"{i}.png", 10) for i in range(MAX_FILES + 1)])


def test_validate_uploads_file_too_large():
    with pytest.raises(ValueError, match="파일 하나는"):
        validate_uploads([("big.png", MAX_FILE_BYTES + 1)])


def test_validate_uploads_total_too_large():
    with pytest.raises(ValueError, match="합계"):
        validate_uploads([("a.png", 10 * MB), ("b.png", 9 * MB)])
    assert 10 * MB + 9 * MB > MAX_TOTAL_BYTES


def test_validate_uploads_bad_extension():
    with pytest.raises(ValueError, match="지원하지 않는 파일 형식"):
        validate_uploads([("scan.heic", 10)])


def test_mime_for_name():
    assert mime_for_name("x.webp") == "image/webp"
    assert mime_for_name("x.JPEG") == "image/jpeg"
    assert mime_for_name("x.pdf") == "application/pdf"


def test_count_uncertain():
    assert count_uncertain(f"가{UNCERTAIN_MARK}나{UNCERTAIN_MARK}") == 2
    assert count_uncertain("") == 0


# ── Gemini 전사 / 대조 (스텁) ──
class _Resp:
    def __init__(self, text):
        self.text = text


class _RecordingModels:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def generate_content(self, model=None, contents=None, config=None):
        self.calls.append({"contents": contents, "config": config})
        return _Resp(self.reply)


class _RecordingClient:
    last = None

    def __init__(self, api_key=None):
        self.models = _RecordingModels(type(self).reply)
        type(self).last = self


@pytest.fixture
def fake_client(monkeypatch):
    monkeypatch.setattr("core.gemini.time.sleep", lambda *a, **k: None)

    def install(reply):
        cls = type("_C", (_RecordingClient,), {"reply": reply})
        monkeypatch.setattr(gemini.genai, "Client", cls)
        return cls

    return install


def test_transcribe_prompt_forbids_correction_and_sets_format():
    p = gemini.TRANSCRIBE_SYSTEM_PROMPT
    assert "교정 금지" in p
    assert "절대 고치지" in p
    assert "⟦?⟧" in p
    assert "과목명: 내용" in p
    assert "(1학기)과목명: 내용" in p
    assert "전사본만 출력" in p


def test_transcribe_sends_parts_and_returns_text(fake_client):
    cls = fake_client("정보: 궁금한것은 질문함.")
    out = gemini.transcribe_images_with_gemini([(b"\xff\xd8jpeg", "image/jpeg")], "key")
    assert out == "정보: 궁금한것은 질문함."
    call = cls.last.models.calls[0]
    contents = call["contents"]
    assert isinstance(contents, list) and len(contents) == 2
    part = contents[0]
    assert part.inline_data.mime_type == "image/jpeg"
    assert part.inline_data.data == b"\xff\xd8jpeg"
    assert "⟦?⟧" in contents[1]
    cfg = call["config"]
    assert cfg.temperature == 0
    assert cfg.thinking_config.thinking_budget == 0
    assert "교정 금지" in cfg.system_instruction


def test_transcribe_uses_high_media_resolution_when_available(fake_client):
    cls = fake_client("본문")
    gemini.transcribe_images_with_gemini([(b"x", "image/png")], "key")
    cfg = cls.last.models.calls[0]["config"]
    if hasattr(gemini.genai_types, "MediaResolution"):
        assert cfg.media_resolution == gemini.genai_types.MediaResolution.MEDIA_RESOLUTION_HIGH


def test_transcribe_strips_code_fence(fake_client):
    fake_client("```\n정보: 본문\n```")
    assert gemini.transcribe_images_with_gemini([(b"x", "image/png")], "k") == "정보: 본문"


def test_transcribe_without_images_does_not_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("이미지 없이 Gemini를 호출하면 안 된다")

    monkeypatch.setattr(gemini.genai, "Client", boom)
    with pytest.raises(ValueError):
        gemini.transcribe_images_with_gemini([], "key")


def test_transcribe_credit_error_is_friendly(monkeypatch):
    class _Models:
        def generate_content(self, **k):
            raise RuntimeError("Your prepayment credits are depleted")

    class _C:
        def __init__(self, api_key=None):
            self.models = _Models()

    monkeypatch.setattr(gemini.genai, "Client", _C)
    with pytest.raises(gemini.GeminiCreditError):
        gemini.transcribe_images_with_gemini([(b"x", "image/png")], "key")


def test_verify_transcription_parses_mismatches(fake_client):
    reply = json.dumps(
        [{"transcript": "궁금한겻", "image": "궁금한것", "note": "글자 다름"}, {"x": 1}],
        ensure_ascii=False,
    )
    cls = fake_client(reply)
    out = gemini.verify_transcription_with_gemini(
        [(b"x", "image/png")], "궁금한겻은", "key"
    )
    assert out == [{"transcript": "궁금한겻", "image": "궁금한것", "note": "글자 다름"}]
    contents = cls.last.models.calls[0]["contents"]
    assert "궁금한겻은" in contents[-1]
    assert cls.last.models.calls[0]["config"].response_mime_type == "application/json"


def test_verify_transcription_masks_transcript_and_restores(fake_client):
    reply = json.dumps(
        [{"transcript": "《비공개1》 학생은", "image": "《비공개1》 학생온", "note": "x"}],
        ensure_ascii=False,
    )
    cls = fake_client(reply)
    out = gemini.verify_transcription_with_gemini(
        [(b"x", "image/png")], "홍길동 학생은 탐구함", "key", [("홍길동", "《비공개1》")]
    )
    sent = cls.last.models.calls[0]["contents"][-1]
    assert "홍길동" not in sent and "《비공개1》" in sent
    assert out[0]["transcript"] == "홍길동 학생은"
    assert out[0]["image"] == "홍길동 학생온"


def test_verify_requires_images_and_transcript():
    with pytest.raises(ValueError):
        gemini.verify_transcription_with_gemini([], "본문", "k")
    with pytest.raises(ValueError):
        gemini.verify_transcription_with_gemini([(b"x", "image/png")], "  ", "k")


# ── 학기 접두 split ──
def test_split_by_subject_allows_semester_prefix():
    text = (
        "(1학기)정보: 문제를 창의적으로 접근함.\n"
        "(2학기)물리학: 운동량 보존을 설명함.\n"
        "사회와 문화: 설문을 설계함."
    )
    assert split_by_subject(text) == [
        ("정보", "문제를 창의적으로 접근함."),
        ("물리학", "운동량 보존을 설명함."),
        ("사회와 문화", "설문을 설계함."),
    ]


def test_split_by_subject_semester_prefix_with_space():
    text = "(1학기) 정보: 가나다라마.\n(1학기) 수학: 바사아자차."
    assert [s for s, _ in split_by_subject(text)] == ["정보", "수학"]


def test_split_by_subject_other_paren_prefix_not_header():
    # '(참고)' 같은 다른 괄호 접두는 학기 접두가 아니므로 헤더로 보지 않는다.
    text = "(참고)정보: 가나다.\n(참고)수학: 라마바."
    assert split_by_subject(text) == [("", text)]
