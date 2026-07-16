# Batch 2: 자동 마스킹 제안 · 명렬표 업로드 · 오탐 무시 · docx 설계

날짜: 2026-07-16 / 상태: 승인됨 (1→4→2→3, 이후 5·7 예정)

## F1. 마스킹 자동 제안

- `core/masking.py`에 `suggest_mask_candidates(texts: list[str], existing: list[str]) -> list[str]`:
  한국 성씨(상위 ~40개) + 한글 1~2자 이름 패턴(단어 경계), 5자리 학번 숫자 감지.
  보수적: 소형 스톱워드(일반 명사 오탐) 제외, existing 제외, 최대 5건.
- UI(양 모드): 입력 텍스트 로드 후 후보 발견 시 st.info + "마스킹 목록에 추가" 버튼 —
  사이드바 마스킹 text_area에 key 부여, session_state로 append 후 rerun.
  자동 적용 금지 — 교사 확인 필수.

## F4. 반 전체 명렬표 업로드 (.csv/.xlsx)

- 검토 모드 업로더에 csv/xlsx 허용. 표 1개 업로드 시: pandas로 읽고
  이름 열·내용 열 selectbox (자동 추정: 첫 문자열 열=이름, 평균 길이 최대 열=내용).
- `core/parsing.py`에 `parse_roster_table(df, name_col, text_col) -> list[tuple[str, str]]`
  (빈 행 제거, 이름 중복은 unique_names).
- 행별로 기존 일괄 검토 파이프라인에 투입. requirements.txt에 openpyxl 추가.

## F2. 오탐 무시 목록

- `core/rules.py`에 `filter_ignored(findings, ignored: set[str]) -> list`.
- 검토 결과(단일·일괄) 아래 multiselect "오탐으로 표시" (옵션=검출어) →
  session_state["ignored_words"] set에 누적. 모든 렌더링·요약표·수정본 생성 입력에서 필터.
- 사이드바에 현재 무시 목록 표시 + 비우기 버튼.

## F3. .docx 지원

- `core/parsing.py`에 `extract_docx_text(data: bytes) -> str` — zipfile로
  word/document.xml 열어 ElementTree로 w:t 텍스트 추출 (신규 의존성 없음, 문단 개행 유지).
- read_uploaded_file 확장 + 업로더 type에 docx 추가 (검토·자기평가서 양쪽).

## 검증

pytest 신규(마스킹 제안·roster·filter_ignored·docx 픽스처) + 기존 39 전체 +
AppTest 스모크 + 실API 회귀 스크립트. 전부 통과 후 push 배포.
