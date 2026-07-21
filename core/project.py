# -*- coding: utf-8 -*-
"""텍스트 일괄 치환 순수 함수 모음.

Streamlit 세션 상태와 분리된 순수 로직만 담아 pytest로 단독 검증 가능하게 한다.
"""


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
