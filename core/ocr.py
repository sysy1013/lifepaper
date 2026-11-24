# -*- coding: utf-8 -*-
"""이미지 입력(학생부 캡처·사진·스캔) 전처리·업로드 한도 검증 (순수 로직, API 불필요).

전사(글자 읽기)는 core/gemini.py의 transcribe_images_with_gemini가 담당한다.
여기서는 Gemini로 보내기 전 이미지 용량을 줄이고, 업로드 한도를 검증한다.
"""

import io

# Gemini 인라인 요청은 20MB 미만이어야 하므로(공식 문서) 여유를 두고 18MB로 제한한다.
UNCERTAIN_MARK = "⟦?⟧"

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_TOTAL_BYTES = 18 * 1024 * 1024
MAX_FILES = 10
MAX_LONG_SIDE = 3000
JPEG_QUALITY = 90

ALLOWED_EXTENSIONS = ("png", "jpg", "jpeg", "webp", "pdf")
IMAGE_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def mime_for_name(name: str) -> str:
    """파일명 확장자로 MIME 타입을 돌려준다 (허용되지 않은 확장자면 ValueError)."""
    ext = _ext(name)
    if ext not in IMAGE_MIME_BY_EXT:
        raise ValueError(
            f"지원하지 않는 파일 형식입니다: {name} "
            "(png·jpg·jpeg·webp 이미지 또는 pdf만 올릴 수 있습니다)"
        )
    return IMAGE_MIME_BY_EXT[ext]


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f}MB"


def validate_uploads(files: list[tuple[str, int]]) -> None:
    """(파일명, 바이트 크기) 목록이 업로드 한도를 지키는지 검증한다.

    위반 시 한국어 메시지의 ValueError를 낸다. 통과하면 None.
    한도: 최대 10개, 파일당 15MB, 합계 18MB, 확장자 png/jpg/jpeg/webp/pdf.
    """
    if not files:
        raise ValueError("읽을 이미지(또는 PDF)를 먼저 올려 주세요.")
    if len(files) > MAX_FILES:
        raise ValueError(
            f"한 번에 최대 {MAX_FILES}개까지 올릴 수 있습니다. (현재 {len(files)}개)"
        )
    total = 0
    for name, size in files:
        mime_for_name(name)  # 확장자 검증
        if size > MAX_FILE_BYTES:
            raise ValueError(
                f"파일 하나는 {_mb(MAX_FILE_BYTES)} 이하만 올릴 수 있습니다: "
                f"{name} ({_mb(size)})"
            )
        total += size
    if total > MAX_TOTAL_BYTES:
        raise ValueError(
            f"한 번에 올리는 파일 합계는 {_mb(MAX_TOTAL_BYTES)} 이하여야 합니다. "
            f"(현재 {_mb(total)}) 파일 수를 줄이거나 나눠서 읽어 주세요."
        )


def prepare_image(data: bytes, mime: str) -> tuple[bytes, str]:
    """Gemini 전송용으로 이미지를 정리한다.

    - PDF는 변환하지 않고 그대로 통과한다(용량 한도는 validate_uploads가 검증).
    - 이미지: EXIF 회전 보정 → 긴 변 3000px 초과 시 축소 → RGBA/P 등은 RGB로 변환
      → JPEG(품질 90)로 재인코딩해 (bytes, "image/jpeg")를 돌려준다.
    - 이미지를 열 수 없으면 한국어 메시지의 ValueError.
    """
    if mime == "application/pdf":
        return data, mime

    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError(
            "이미지 파일을 열 수 없습니다. 손상되었거나 지원하지 않는 형식입니다."
        ) from e

    img = ImageOps.exif_transpose(img)

    long_side = max(img.size)
    if long_side > MAX_LONG_SIDE:
        scale = MAX_LONG_SIDE / long_side
        new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(new_size, Image.LANCZOS)

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        # 투명 배경은 흰색으로 합성한다(검은 배경이 되면 글자가 안 보일 수 있음).
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue(), "image/jpeg"


def count_uncertain(transcript: str) -> int:
    """전사본에 남은 판독 불확실 표시(⟦?⟧) 개수."""
    return (transcript or "").count(UNCERTAIN_MARK)
