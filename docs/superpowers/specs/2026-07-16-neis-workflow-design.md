# Batch 5: NEIS 실무 워크플로 이식 설계

날짜: 2026-07-16 / 상태: 승인됨 (참고: luminousky.com teacher-utility-kit 기능 개념 이식 — 코드 미복제)

## P1. 프로젝트 저장/복원 (세션 휘발 보완)

- 사이드바 "💾 프로젝트" 섹션.
- 저장: session_state 중 {batch_review, batch_drafts, review_result, revised_text,
  draft_text, draft_context, history, ignored_words, 마스킹 원문} + version/saved_at
  → JSON 직렬화 다운로드 (생기부_프로젝트.json). 개인정보 포함 파일임을 캡션 고지.
- 복원: .json 업로더 → 버전 검증 → session_state 복원 → rerun.
  복원은 명시 버튼으로 실행(업로드 즉시 자동 복원 금지 — 진행 중 작업 보호).

## P2. 텍스트 일괄 치환

- 일괄 검토·일괄 초안 결과 아래 expander "🔁 텍스트 일괄 치환".
- 찾을 문구/바꿀 문구 입력 → 미리보기(건수: 학생별 매치 수 합) → 적용 버튼.
- 적용 대상: 일괄 검토는 revised(있는 학생), 일괄 초안은 draft. 원문(text)은 불변.
- 적용 시 record_history 기록.

## P3. NEIS 바이트 기준

- 사이드바 radio "분량 기준": 글자 수(공백 포함) / NEIS 바이트(한글 3B).
  기존 바이트 계산 로직(분량 조절기) 재사용 — core로 이동해 공유.
- NEIS_LIMITS는 자 기준 유지, 바이트 기준 선택 시 limit*3으로 환산 표시.
- 일괄 요약표 분량 열: 선택 기준으로 계산, 초과 셀은 pandas Styler로 붉은 배경.

## P4. 나이스 입력용 엑셀

- 일괄 검토 다운로드 열에 "🗂️ 나이스 입력용 엑셀" 추가:
  열 = 이름 / 내용(수정본 있으면 수정본, 없으면 원문) / 글자 수 / NEIS 바이트 / 제한 초과 여부.
- core/formatting.py `build_neis_workbook(batch, neis_limit, use_bytes)` 순수 함수 + 테스트.

## 검증

pytest 전체 + AppTest(프로젝트 저장→복원 왕복, 치환 적용) + 시뮬 재실행 후 push 배포.
