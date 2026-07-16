# Phase 1: Gemini 재시도 + 일괄 병렬화 설계

날짜: 2026-07-16 / 상태: 승인됨

## 목적

일괄 모드(반 전체 검토·초안 생성)에서 일시적 API 오류(429/503)로 학생 1명이
통째로 실패하는 문제 제거 + 순차 호출로 수 분 걸리는 처리 시간 단축.

## 범위

app.py 단일 파일 내 수정. 모듈 분리는 Phase 5에서.

### 1. 재시도 래퍼

- 함수 `_generate_with_retry(model, prompt, max_attempts=3)`:
  `model.generate_content(prompt)` 호출을 감싼다.
- 재시도 대상: HTTP 429/500/503 계열(google.api_core 예외 및 메시지 매칭),
  타임아웃, 빈 응답.
- JSON 파싱 함수(analyze, quality, proofread)는 파싱 실패
  (`json.JSONDecodeError`, ValueError) 시에도 재호출 — 별도 상위 재시도 1회.
- 백오프: 2s → 4s + 0~1s 지터.
- 최종 실패: 예외 전파 (기존 UI 에러 표시 유지).
- 적용 지점: analyze_with_gemini, rewrite_with_gemini,
  assess_quality_with_gemini, generate_draft_with_gemini,
  refine_draft_with_gemini, proofread_with_gemini, adjust_length_with_gemini.

### 2. 일괄 병렬화

- 대상: 모드1 일괄 검토 루프, 모드2 일괄 초안 루프.
- `concurrent.futures.ThreadPoolExecutor(max_workers=4)`.
- 파일 읽기(`read_uploaded_file`)는 제출 전 메인 스레드에서 수행
  (Streamlit UploadedFile 스레드 안전성 회피).
- 결과 순서는 입력 파일 순서 유지 (인덱스 매핑).
- progress bar는 as_completed 완료 카운트로 갱신.
- 개별 실패는 기존과 동일하게 error 필드에 기록, 전체 중단 없음.

## 검증

1. 로컬 실행 (launch.json "lifepaper", port 8501).
2. txt 파일 3개 일괄 검토 → 전원 성공 + 순서 유지 확인.
3. 단일 검토·초안 경로 회귀 확인.

## 이후 페이즈 (별도 스펙)

2: SDK 마이그레이션(google-genai) / 3: 일괄 수정본·품질·오탈자 /
4: refine 원본 맥락 전달 / 5: 모듈 분리 + pytest / 6: 이력·개인별 리포트.
