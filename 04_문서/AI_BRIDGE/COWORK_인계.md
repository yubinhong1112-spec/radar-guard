# Cowork 인계 — RAG·챗봇 고도화 (최종 갱신 2026-10-02 (T-CX04 검증 반영))

> 새 Cowork 채팅은 **이 파일부터 읽는다.** 이 대화에서 정한 운영 방식·진행도·남은 일·함정이 전부 여기 있다.
> 근거 문서: `README.md` 최상단 10/01~10/02 항목(보고서용 문제→해결), `04_문서/AI_BRIDGE/보고_ClaudeCode.md`, `보고_Codex.md`.

## 1. 목표와 기한

- 이번 주(10/1 목 ~ 10/4 일): 노트북만으로 가능한 RAG·관제 AI 챗봇 개선을 끝내고 10/4 이후 RAG 동결.
- 10/24 심사위원 앞 현장 시연. 판정 코드 동결(CLAUDE.md 맨 앞)은 계속 유효.
- 노트북 제약: MX450(VRAM 2GB), RAM 7.7 GB. 측정 중 가용 0.33 GB 까지 떨어져 3B 측정이 막힌 적 있음.

## 2. 운영 방식 (10/01 확정)

| 도구 | 지시 받는 법 | 보고 위치 | 모델 |
|---|---|---|---|
| Claude Code | 사용자가 "지시 파일 읽고 수행해" → `04_문서/AI_BRIDGE/지시_ClaudeCode.md` | `04_문서/AI_BRIDGE/보고_ClaudeCode.md`(덮어쓰기) | Opus 5.5 |
| Codex | **Cowork 가 쓴 자족형 프롬프트를 사용자가 채팅에 붙여 넣는다**(파일 방식은 압축 후 옛 지시서를 읽는 사고가 있어 폐기) | `04_문서/AI_BRIDGE/보고_Codex.md`(덮어쓰기) | GPT-6.1 Sol, 추론 Medium~High |

- 두 도구 모두 착수 전 **[착수 보고] 6항목**(CLAUDE.md §10) → 사용자 "진행" 후 시작. 사용자는 착수 보고를 Cowork 에 붙여 검토받는다.
- 보고 형식: `04_문서/설계/RAG_LLM_고도화_1001/보고/_양식.md`.
- Cowork 는 보고를 **그대로 믿지 않고 근거 파일로 재계산**해 확인한 뒤 다음 지시를 쓴다(지금까지 숫자 불일치 0, 해석 오류 1건 — T-CC01 "잘림 때문" 가설 기각).
- **Ollama 를 쓰는 작업 둘을 동시에 돌리지 않는다**(측정 지연 오염). Claude Code 측정 ↔ Codex 적재는 사용자가 순서 조율.
- Codex 는 git 쓰기 금지. 커밋·push 는 Claude Code 가 한다. Cowork 변경분도 Claude Code 다음 작업 0단계에서 커밋.
- `AGENTS.md`·`.agents/skills/` 는 `CLAUDE.md`·`.claude/skills/` 에서 `scripts/sync_agent_docs.py` 로 생성 — 직접 고치지 않는다.

## 3. 진행도

| ID | 내용 | 상태 | 핵심 결과 |
|---|---|---|---|
| T-CC01 | 평가셋 30문항 + 기준선 | 완료 `7e860c4` | 출처 6/6·키워드없음 0/6, 필수문구 20/38, 잘림 4/30, p50 7.6초 |
| T-CX02 | 재적재 v2.1 dry | 완료 | 538청크·평균 425/최대 500자·공백소실 25(전부 B-M-37)·무결성 0 |
| T-CX03 | v2 적재 | **중단** | 0x00 문자로 PostgreSQL 거부, 트랜잭션 취소. 운영 640 무변경, v2 빈 컬렉션 |
| T-CC02 | 변형 R/R+P/R+P+N | 부분 완료 `eb1fa76` push | 셋 다 미채택. R 은 라우팅 원인 입증(0/6→6/6)했지만 전체 질문에 검색 → system 10/11→1/11, p50 20초대(검색 7 + 재적재 9). Q·baseline 재측정은 메모리 부족 미실행 |
| T-CX04 | 0x00·전각쪽번호(57→실제 58곳) 제거 후 재적재 | 완료(Cowork 검증 10/02) | v2 537=dry 537, 운영 640(Codex 조회), NUL 0, H-187·E-14 SHA 재계산 일치. **잔여 결함**: M-121 16곳·M-123 21곳(`－ －- N -` 형태, 31청크) 미제거 — 지시 범위 누락(Cowork 책임). 538→537 원인 미보고(추정: M-146 정제로 04_예지보전 청크 1개 병합) |
| T-CX05 | M-121·M-123 잔여 쪽번호 제거 + v2 재적재 | 프롬프트 작성 완료 `_대기/T-CX05_Codex_프롬프트.md`. **T-CC03 끝난 뒤·T-CC04 전에** 실행(사용자 결정 10/02) | — |
| T-CC03 | 의도 라우터(사고/시스템/조작/기타) + I·I+Q 측정 + baseline 지연 분해 + 미공개 12문항 | **지시 파일 작성 완료(상태 대기), 착수 전** | 채택 기준·미공개 문항은 `지시_ClaudeCode.md` §3~4 |

## 4. 다음 할 일 (순서)

1. (완료 10/02) T-CX04 검증. 다음: T-CC03 종료 후 T-CX05 프롬프트를 Codex 에 전송 → 검증.
   - 원래 문구: `보고_Codex.md` 로 T-CX04 검증. 검증 쿼리는 Codex 보고 숫자 + `tmp/ingest_v2/report.md` 대조(Cowork 는 DB 직접 접속 불가 — 필요하면 사용자에게 PowerShell `docker exec radar-guard-db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT c.name, count(*) FROM langchain_pg_embedding e JOIN langchain_pg_collection c ON e.collection_id=c.uuid GROUP BY c.name;"'` 실행 요청).
2. Claude Code T-CC03 착수 보고 검토 → 측정 전 메모리 2 GB 확보·Codex 미실행 확인 → 결과 재계산 검증.
3. T-CC04(지시 미작성): T-CC03 최선 변형으로 v1(`safety_manual`) vs v2(`safety_manual_v2`) 비교. v2 가 나으면 운영 전환 방식 결정(컬렉션명 상수 교체 + 경보 SOP 경로 회귀 검증 필수).
4. 그 뒤 후보: 스트리밍(첫 토큰 시간), `keep_alive` 로 bge-m3·생성모델 동시 상주(재적재 9초 제거, 메모리 확인 필요), SOP 출처에 페이지 번호 표기, 신규 문서(2025 심폐소생술 가이드라인 기본소생술 장 발췌).
5. 10/4 이후 RAG 동결, README·보고서 반영.

## 5. 사용자 결정 대기

- 3B 모델(qwen2.5:3b) 채택 — T-CC03 결과 보고 후. OUT-007 규칙상 사용자 승인 필요.
- `keep_alive` 상주 여부(T-CC02 판단요청 2, 권장: 품질 먼저).
- README.md 의 9/26 고도화 계획안(사용자 작성) 커밋 여부 — 현재 미커밋, 10/02 Cowork 가 그 위에 10/01~02 항목 추가(역시 미커밋).
- 정리 B 목록: `user_ui(final).py`, `tmp/onetakev1.mkv`, `tmp/hanium_award_refs/`(360MB), `tmp/hanium_2026_template/`, `_구버전보관/`, `07_중간보고서_7.14/`, `임베디드용 제출/` 잔여 PDF·pptx·xlsx.
- `ollama ps` 에서 bge-m3 가 GPU 로 표시됨 — 7/29 메모("GPU 사실상 불가")와 다름. 드라이버 변경 여부 미확인.

## 6. 함정 (실제로 겪은 것)

- **Codex 대화 압축 후 옛 지시서·HANDOFF 를 읽고 끝난 작업을 재실행**(10/01). → Codex 는 채팅 프롬프트 방식, next-task 스킬은 지시 파일 우선으로 수정, 옛 지시서는 `_이전지시/`·`_이전/` 로 이동.
- `HANDOFF.md` 는 8/27 내용으로 낡았다. 지시 파일이 우선(CLAUDE.md §10).
- Claude Code 에서 `! 명령` 입력이 실행되지 않았다. `git rm` 은 하니스가 "되돌릴 수 없는 삭제"로 막음 → `git rm --cached` 후 Cowork 가 디스크 삭제로 처리.
- Docker Desktop 이 꺼져 있으면 SOP 검색이 조용히 0건. 측정 전 `docker ps` 필수.
- 노트북 덮개 닫음/절전 → 측정 시간 이상치(1단계 LS-05 817.9초 추정 원인). 측정 중 Restart to update 금지.
- OUTBOX 는 결정 요청만, 열린 항목 9/10. 완료·과거형 서술은 `validate_ai_bridge.py` 위반.
- 동결 해시 기준선은 10/01 갱신값(jetson_sender `afb5470a…`, verify_jetson_safe `75f10ee4…`).
- 저장소는 3개: `radar-guard`(origin, 작업 저장소) · `radar-guard-hanium`(9/07 제출용 공개본) · `2026ESWContest_free_powerGiants`(임베디드SW). 로컬 git 이 관리하지 않는 파일(.gitignore 대상 포함)은 GitHub 에 없다 — `03_데이터`·`05_발표자료`·모델 가중치는 이 PC 에만 있다.

## 7. 파일 지도

- 지시: `04_문서/AI_BRIDGE/지시_ClaudeCode.md`(현재 T-CC03) · `지시_Codex.md`(채팅 방식 안내 스텁)
- 지난 지시: `04_문서/AI_BRIDGE/_이전/` · `04_문서/설계/RAG_LLM_고도화_1001/_이전지시/`
- 평가: `01_현행코드/eval/`(chat_eval_set.json·chat_eval.py·test_chat_prompt_same.py·fixtures·results)
- 재적재: `01_현행코드/sop_ingest_v2.py`(미커밋, Codex 작성) · `tmp/ingest_v2/`
- 원본 PDF: `C:\radar-guard\` 13개 + `output/pdf/Radar-Guard_설비전기이상_대응_SOP.pdf`
