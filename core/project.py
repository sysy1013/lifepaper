# -*- coding: utf-8 -*-
"""프로젝트 저장/복원 및 텍스트 일괄 치환 순수 함수 모음.

Streamlit 세션 상태와 분리된 순수 로직만 담아 pytest로 단독 검증 가능하게 한다.
app.py는 세션 상태를 모으고 되돌려 주는 얇은 래퍼만 담당한다.
"""

import json
from datetime import datetime

# 프로젝트 파일에 담을 세션 상태 키 (작업 상태 전체)
PROJECT_KEYS = (
    "batch_review",
    "batch_drafts",
    "review_result",
    "revised_text",
    "draft_text",
    "draft_context",
    "history",
    "ignored_words",
    "mask_words_raw",
)
PROJECT_VERSION = 1


def serialize_project(data: dict) -> str:
    """작업 상태 dict를 프로젝트 JSON 문자열로 직렬화한다."""
    payload = {
        "app": "lifepaper",
        "version": PROJECT_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "data": data,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def deserialize_project(raw: bytes | str) -> tuple[dict, str]:
    """프로젝트 JSON을 검증해 (data, error)를 반환한다.

    성공 시 error는 빈 문자열, 실패 시 한글 오류 메시지를 담는다.
    """
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return {}, "❌ 프로젝트 파일을 읽지 못했습니다. 올바른 JSON이 아닙니다."

    if not isinstance(obj, dict) or obj.get("app") != "lifepaper":
        return {}, "❌ 생기부 도우미 프로젝트 파일이 아닙니다."

    version = obj.get("version", 0)
    if not isinstance(version, int) or version > PROJECT_VERSION:
        return {}, (
            f"❌ 더 새로운 버전(v{version})의 프로젝트 파일입니다. "
            "최신 버전에서 만들어진 파일은 불러올 수 없습니다."
        )

    data = obj.get("data")
    if not isinstance(data, dict):
        return {}, "❌ 프로젝트 데이터가 손상되었습니다."

    # 알려진 키만 복원 대상으로 추린다.
    restored = {k: data[k] for k in PROJECT_KEYS if k in data}
    return restored, ""


def replace_in_entries(
    entries: list[dict], field: str, find: str, repl: str
) -> tuple[list[dict], int]:
    """entries의 field 문자열에서 find→repl 치환한 새 목록과 총 치환 건수를 반환한다.

    원본을 변형하지 않고 새 dict 목록을 만든다. find가 비어 있거나 해당
    field가 없는 항목은 건너뛴다.
    """
    new_entries: list[dict] = []
    total = 0
    for entry in entries:
        new_entry = dict(entry)
        value = entry.get(field)
        if find and isinstance(value, str) and value:
            count = value.count(find)
            if count:
                total += count
                new_entry[field] = value.replace(find, repl)
        new_entries.append(new_entry)
    return new_entries, total
