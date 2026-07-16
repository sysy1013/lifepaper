# Batch 3: 초안 2버전 비교 + 예시 세특 문체 학습 설계

날짜: 2026-07-16 / 상태: 승인됨

## F5. 초안 2버전 동시 생성 (단일 초안 전용)

- 모드 2에 st.toggle("⚖️ 2버전 생성 후 비교 선택") — 기본 꺼짐 (API 비용 2배 고지).
- 켜진 상태로 초안 생성 시 run_parallel(2)로 독립 생성 (temperature 0.7 분화).
- session_state["draft_variants"] = [v1, v2]; 좌우 컬럼 비교 렌더 + 글자 수,
  각각 "이 버전 선택" 버튼 → draft_text/draft_context 설정, variants 제거,
  이력 기록, 기존 후속 플로우(분량·피드백·품질·오탈자) 합류.
- 일괄 초안은 대상 아님.

## F7. 예시 세특 문체 학습 (few-shot)

- 모드 2에 업로더 "예시 세특 (선택·문체 참고용)" — txt/docx/hwp/hwpx 최대 2개.
- core/gemini.py: 초안 프롬프트 조립을 순수 함수
  `build_draft_prompt(subject, major, performance, self_eval, target_len, style_examples)`로
  분리 (테스트 가능). style_examples 있으면
  "[문체 참고 예시 n]" 섹션 + "문체·어미·구성 방식만 참고하고 예시의 내용·사실은
  절대 가져오지 말 것" 지시 추가.
- generate_draft_with_gemini / refine_draft_with_gemini에 style_examples 전달
  (refine은 draft_context 경유). 예시 텍스트도 apply_mask 처리.

## 검증

pytest(프롬프트 조립·기존 전체) + AppTest(토글·비교·선택 플로우) + 실API 스모크
(2버전 상이 확인, 예시 문체 반영 정성 확인) 후 push 배포.
