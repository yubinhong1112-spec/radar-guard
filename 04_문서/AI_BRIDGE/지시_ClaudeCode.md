# 지시 — Claude Code

> 이 파일은 Cowork 가 쓴다. Claude Code 는 읽고 수행만 한다(수정 금지 — 이견은 보고 파일 '판단 요청'에).
> 사용자 입력은 "지시 파일 읽고 수행해" 한 줄이다. 아래 `상태`를 먼저 본다.

| 항목 | 값 |
|---|---|
| 작업 ID | **T-CC05** — 경보 SOP 6종: 고정 조각 기반 초안 생성·채점 |
| 상태 | **대기** — 사용자가 채점 기준 초안을 승인했다고 말한 뒤에만 착수 보고. (진행중=이어서 / 완료=아무것도 하지 말고 "T-CC05 완료 상태"라고 답) |
| 계획 문서 | `04_문서/설계/RAG_LLM_고도화_1001/개선계획_1003_1021.md` §5·§7 |
| 근거·기준 | `04_문서/설계/RAG_LLM_고도화_1001/경보SOP_근거_채점기준_초안_1005.md`, `01_현행코드/eval/sop_eval_set_draft.json` |
| 병렬 상대 | Codex T-CX06(`event_log.py`, Ollama·DB 미사용). 파일 겹침 없음. `event_log.py`·`eval/test_event_log.py`·`보고_Codex.md` 는 열지 않는다 |
| 이전 작업 | T-CC04 완료(`168748d`, `e4bf7b8`). 지시: `_이전/지시_ClaudeCode_T-CC04.md` |

## 공통 규칙
1. 착수 보고 6항목 → 사용자 "진행" 후 시작. 2. 동결 해시 표 대조 + 시작==종료. 3. 경로 지정 커밋, `git add -A` 금지.
4. 실행 안 한 것을 완료라 쓰지 않는다. 5. 지시서에 없는 변형 추가 금지. 6. **제품 코드(`console_ui.py`·`radar_core.py`·`radar_common.py`·`facility.py`) 무변경.**
7. 측정 전 가용 메모리 ≥ 2000 MB. 미달이면 멈춰 보고.

## 배경 (10/05 결정, 계획 §5)
경보 SOP 는 '개발 단계에서 고정 조각으로 AI 초안 생성 → 채점 → 사용자 승인 → 파일 고정' 으로 바꾼다. 이번 작업은 그 **초안 생성과 채점**이다. 승인(10/07)과 제품 반영(T-CC07)은 다음이다.

## 1. 0단계 커밋 (Cowork 변경분, 경로 지정)
`04_문서/설계/RAG_LLM_고도화_1001/개선계획_1003_1021.md`, `04_문서/설계/RAG_LLM_고도화_1001/경보SOP_근거_채점기준_초안_1005.md`, `01_현행코드/eval/sop_eval_set_draft.json`, `04_문서/AI_BRIDGE/지시_ClaudeCode.md`, `04_문서/AI_BRIDGE/_이전/지시_ClaudeCode_T-CC04.md`, `04_문서/AI_BRIDGE/_대기/T-CX06_Codex_프롬프트.md`, `04_문서/AI_BRIDGE/COWORK_인계.md`.
- 사용자가 승인했다고 하면 `sop_eval_set_draft.json` 을 `sop_eval_set.json` 으로 `git mv` 한다(사용자가 고친 내용이 있으면 그대로 반영).

## 2. `eval/sop_eval.py` 확장 (eval 안에서만)
- `--pinned` 옵션: 채점 기준 파일의 `pinned_chunks` 로 `safety_manual_v2` 에서 해당 chunk_id 의 본문을 **지정 순서대로** 가져와 ctx 로 쓴다(검색 함수 미사용). 없는 chunk_id 가 있으면 즉시 중단·보고.
- `--ctx-limit {700,full}`: 제품 `_gen_facts` 는 발췌를 `ctx[:700]` 으로 자른다. `full` 은 지정 조각 전체가 들어가게 한다. 제품 코드를 고치지 말고 eval 안에서 처리하되(예: 슬라이스만 무시하는 str 하위 클래스 주입), **발췌 길이 외 프롬프트가 제품과 바이트 동일**함을 diff 로 증명해 보고에 싣는다. 증명 못 하면 `full` 조건은 버리고 보고.
- `--rules <경로>`: 채점 기준 파일 지정(기본 `sop_eval_set.json`).
- 생성 옵션은 제품 그대로(temperature 0, num_predict 160, num_ctx 1536, facts none).

## 3. 실행 (조건 사이 `ollama stop`)
| # | 모델 | 조각 | ctx |
|---|---|---|---|
| 1 | qwen2.5:3b-instruct-q4_K_M | 고정(--pinned) | 700 |
| 2 | qwen2.5:3b-instruct-q4_K_M | 고정 | full |
| 3 | gemma2:2b | 고정 | 700 |
| 4 | gemma2:2b | 고정 | full |
| 5 | qwen2.5:3b-instruct-q4_K_M | 현행 검색(v2 컬렉션) | 700 | ← 고정 조각 효과 비교용

각 조건 6종 전부. 채점은 승인된 `sop_eval_set.json`(없으면 `--rules eval/sop_eval_set_draft.json`, 보고에 "초안 기준"이라 명기).

## 4. 그 외 (같은 커밋 가능)
- `테스트_v1결함_재발검사.py`: 실패가 있으면 종료코드 1 (T-CC04 판단 요청 4 — 승인). 테스트 스크립트만, 판정 로직 무관.
- `scripts/sync_agent_docs.py:4` docstring 을 raw string 으로(SyntaxWarning 제거). 생성물(`AGENTS.md` 등) 변화가 없어야 한다.

## 5. 보고 (`보고_ClaudeCode.md` 덮어쓰기)
- 핵심 표: 6종 × 5조건의 필수 통과 수/금지 위반/형식 통과/시간.
- **5조건 × 6종 생성문 전문**(원문 그대로). 사용자가 이 중에서 고른다.
- 경보마다 '추천 후보' 1개: 필수 최다 → 금지 0 → 형식 통과 → 짧은 것 순. 동점이면 둘 다 적는다. 추천은 제안일 뿐 확정은 사용자.
- 고정 조각 vs 현행 검색(조건 1 vs 5) 차이.
- 끝나기 전: 제품 코드 무변경 확인, ui-verify 4종, `test_chat_prompt_same.py`, `test_sop_collection_filter.py`, 해시, 커밋·push.
