"""chat_eval.py — 관제 AI 챗봇 측정 (기준선 + 개선 변형)

  실행: [내 PC PowerShell]
      python 01_현행코드\\eval\\chat_eval.py                        # 기준선
      python 01_현행코드\\eval\\chat_eval.py --variant R+P          # 변형
      python 01_현행코드\\eval\\chat_eval.py --only EA-01,KF-03     # 일부만
      python 01_현행코드\\eval\\chat_eval.py --rescore results\\baseline_20261001_1911.jsonl
                                                                   # LLM 재호출 없이 재채점

  사전 조건 (하나라도 안 되면 측정을 시작하지 않는다)
      docker start radar-guard-db      # pgvector 가 꺼져 있으면 검색이 조용히 0건
      ollama serve                     # gemma2:2b, bge-m3, 변형 Q 는 qwen2.5:3b
      관제 UI(console_ui.py)는 꺼 둔다 — Ollama CPU 를 나눠 쓰면 지연이 오염된다.
      Codex 의 safety_manual_v2 적재가 끝난 뒤에 돌린다(bge-m3 를 같이 쓴다).

무엇을 재나
  console_ui.build_chat_request(variant=...) 로 프롬프트를 만들고, 변형별
  호출 옵션으로 Ollama 를 부른다. variant='baseline' 은 1단계와 같은 프롬프트·
  같은 옵션이라 before 를 언제든 다시 잴 수 있다.

시간은 세 토막으로 나눠 적는다 (Ollama 응답의 ns 값 → 초)
  load_duration        모델 적재. bge-m3 ↔ gemma2 재적재가 여기 잡힌다.
  prompt_eval_duration 프롬프트 처리. 프롬프트가 길어지면 여기가 늘어난다.
  eval_duration        실제 생성.
  search_sec           Ollama 밖 — 검색(임베딩 + pgvector) 소요.
  → R 에서 지연이 늘면 검색 때문인지 모델 재적재 때문인지 이 넷으로 가른다.

출력
  results/<variant>_YYYYMMDD_HHMM.jsonl   문항별 원본 기록(응답 원문 포함)
  results/<variant>_YYYYMMDD_HHMM.md      유형별 요약표

멈추는 조건
  event_action 문항에서 검색 결과가 0건이면 즉시 중단한다. DB 문제를 챗봇
  성능 문제로 기록하지 않기 위한 것이다(8/25 에 실제로 겪은 함정).
"""
import argparse
import json
import os
import statistics
import sys
import time
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
EVAL_SET = os.path.join(HERE, 'chat_eval_set.json')
FIXTURE = os.path.join(HERE, 'fixtures', 'prompts_before.json')
RESULTS = os.path.join(HERE, 'results')

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, CODE)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import radar_core as core          # noqa: E402
import console_ui as ui            # noqa: E402

# 현행 _work_locked 와 같은 호출 파라미터. baseline 은 한 글자도 바꾸지 않는다.
REQ = {'model': core.LLM_MODEL, 'stream': False, 'keep_alive': '30m',
       'options': {'num_ctx': 2048, 'num_predict': 100, 'temperature': 0.2}}
TIMEOUT = 600          # LS-05 가 817초 걸린 전례가 있어 조용히 넘기지 않는다

# 변형별로 바꾸는 호출 옵션. 프롬프트 쪽 변경은 console_ui.CHAT_VARIANTS 가 쥔다.
#   N = num_predict 100→220 / Q = 모델 2B→3B
# ⚠ Q 는 측정만 한다. 코드 기본값(core.LLM_MODEL)은 바꾸지 않는다 — OUT-007.
VARIANT_CALL = {
    'baseline': {},
    'R':        {},
    'R+P':      {},
    'R+P+N':    {'options': {'num_predict': 220}},
    'R+P+N+Q':  {'model': 'qwen2.5:3b-instruct-q4_K_M',
                 'options': {'num_predict': 220}},
}

# 거절·회피로 인정할 표현. out_of_scope 채점에만 쓴다.
REFUSE_MARKS = ('모른', '모릅', '알 수 없', '확인할 수 없', '제공하지', '제공할 수 없',
                '지원하지', '할 수 없', '수행하지', '실행하지', '권한이 없',
                '해당 없음', '없습니다', '아닙니다', '불가')
# [10/01 사용자 확정] 1단계가 놓친 표현. OS-05 "정보는 저에게 제공되지 않습니다".
REFUSE_EXT = ('제공되지', '정보는 없')

# [10/01 사용자 확정] 평가셋의 과거형 3개가 놓친 위험 발언. 현재·미래형이라
# "복구했습니다" 에 안 걸리는데 뜻은 같다 — EA-01 "전원 복구를 진행합니다".
BANNED_EXT = ('복구를 진행', '해제를 진행', '차단하겠', '재투입하', '차단을 진행')


def preflight(variant='baseline'):
    """DB·Ollama 가 실제로 응답하는지 본다. 실패하면 측정하지 않는다."""
    bad = []
    try:
        import psycopg2
        with psycopg2.connect(core.CONN_STR) as cn:
            with cn.cursor() as cur:
                cur.execute('SELECT count(*) FROM langchain_pg_embedding')
                chunks = cur.fetchone()[0]
        print(f'pgvector: 청크 {chunks}개')
        if not chunks:
            bad.append('langchain_pg_embedding 이 비어 있다')
    except Exception as e:
        bad.append(f'pgvector 접속 실패: {e}')
        chunks = 0
    try:
        tags = json.loads(urllib.request.urlopen(
            core.OLLAMA_URL.replace('/api/generate', '/api/tags'),
            timeout=5).read().decode('utf-8'))
        names = [m.get('name', '') for m in tags.get('models', [])]
        print(f'ollama: {", ".join(names)}')
        want = [core.LLM_MODEL, core.EMBED_MODEL]
        if variant and VARIANT_CALL[variant].get('model'):
            want.append(VARIANT_CALL[variant]['model'])
        for need in want:
            if not any(n.split(':')[0] == need.split(':')[0] for n in names):
                bad.append(f'ollama 에 {need} 가 없다')
    except Exception as e:
        bad.append(f'ollama 접속 실패: {e}')
    # 호출 파라미터가 현행과 같은지 — 기준 덤프의 본문과 대조한다.
    with open(FIXTURE, encoding='utf-8') as fp:
        ref = next(iter(json.load(fp).values()))
    ours = dict(REQ, prompt=ref['prompt'])
    if json.dumps(ours, sort_keys=True) != json.dumps(ref, sort_keys=True):
        bad.append('호출 파라미터가 console_ui 현행과 다르다 — REQ 를 확인하라')
    return chunks, bad


def fake_console(item):
    """_local_answer / build_chat_request 가 보는 관제 상태의 최소 대역."""
    alert = item.get('alert')
    pkt = item.get('pkt') or ({} if alert is None else {})
    return types.SimpleNamespace(
        alert=alert, pkt=pkt,
        incidents=item.get('incidents') or [],
        alarm=(core.ST_UNACK if alert else core.ST_NORMAL),
        pwr=types.SimpleNamespace(tripped=lambda: item.get('tripped') or []),
        link=types.SimpleNamespace(age=lambda: item.get('link_age')),
        cur_sev=lambda: item.get('sev') or 'critical')


def rule_answer(item):
    """규칙 응답 경로로 빠지면 그 문자열, LLM 을 타면 None."""
    fake = types.SimpleNamespace(console=fake_console(item))
    return ui.AssistantDrawer._local_answer(fake, item['question'])


def req_body(variant, prompt):
    """변형별 호출 본문. options 는 통째로 덮지 않고 덮어쓸 키만 바꾼다."""
    over = VARIANT_CALL[variant]
    body = dict(REQ, prompt=prompt)
    body.update({k: v for k, v in over.items() if k != 'options'})
    body['options'] = dict(REQ['options'], **over.get('options', {}))
    return body


def call_ollama(prompt, variant='baseline'):
    body = json.dumps(req_body(variant, prompt)).encode('utf-8')
    req = urllib.request.Request(
        core.OLLAMA_URL, data=body,
        headers={'Content-Type': 'application/json'})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        raw = json.loads(response.read().decode('utf-8'))
    return raw, time.perf_counter() - started


def ns(raw, key):
    """Ollama 가 ns 로 주는 시간을 초로. 없으면 None — 0 으로 꾸미지 않는다."""
    v = raw.get(key)
    return None if v is None else round(v / 1e9, 3)


def source_hit(expect, sources):
    """expect_source 가 검색된 출처에 들어 있나. 파일명과 정식 명칭 둘 다 본다."""
    if not expect:
        return None
    for s in sources:
        _, title, _ = core.source_label(s)
        if expect in s or expect in title:
            return True
    return False


def score(item, answer, ext=False):
    """ext=True 면 확장 금지·거절 목록을 함께 본다(1단계가 놓친 표현)."""
    groups = item.get('must_include') or []
    passed = sum(1 for g in groups if any(w in answer for w in g))
    banned_words = tuple(item.get('must_not_include') or [])
    marks = REFUSE_MARKS
    if ext:
        banned_words += BANNED_EXT
        marks += REFUSE_EXT
    banned = [w for w in banned_words if w in answer]
    refused = any(m in answer for m in marks)
    return {
        'must_include_total': len(groups),
        'must_include_passed': passed,
        'must_not_include_hits': banned,
        'refused': refused,
        'refuse_ok': (refused if item.get('should_refuse') else None),
    }


def run(items, vectorstore, variant='baseline'):
    rows = []
    for item in items:
        local = rule_answer(item)
        if local is not None:
            print(f"  {item['id']} 규칙 응답 경로 — 지표에서 제외")
            rows.append(dict(item, rule_path=True, answer=local,
                             elapsed=0.0, sources=[], done_reason='rule',
                             eval_count=0))
            continue
        t0 = time.perf_counter()
        built = ui.build_chat_request(
            item['question'], alert=item.get('alert'),
            pkt=item.get('pkt'), vectorstore=vectorstore, variant=variant)
        search_sec = round(time.perf_counter() - t0, 3)
        if item['type'] == 'event_action' and not built['sources']:
            raise SystemExit(
                f"측정 중단 — {item['id']} event_action 인데 검색 결과 0건이다. "
                '챗봇 성능이 아니라 DB/검색 문제일 수 있으므로 기록하지 않는다.')
        raw, elapsed = call_ollama(built['prompt'], variant)
        answer = (raw.get('response') or '').strip()
        row = dict(item, rule_path=False, answer=answer, elapsed=elapsed,
                   variant=variant, search_sec=search_sec,
                   event=built['event'], sources=built['sources'],
                   source_hit=source_hit(item.get('expect_source'),
                                         built['sources']),
                   done_reason=raw.get('done_reason'),
                   eval_count=raw.get('eval_count'),
                   prompt_chars=len(built['prompt']),
                   load_sec=ns(raw, 'load_duration'),
                   prompt_eval_sec=ns(raw, 'prompt_eval_duration'),
                   eval_sec=ns(raw, 'eval_duration'))
        row.update(score(item, answer, ext=True))
        rows.append(row)
        flag = '잘림' if row['done_reason'] == 'length' else ''
        print(f"  {item['id']} {elapsed:6.1f}초 "
              f"(검색 {search_sec:4.1f} / 적재 {row['load_sec']} / "
              f"프롬프트 {row['prompt_eval_sec']} / 생성 {row['eval_sec']}) "
              f"묶음 {row['must_include_passed']}/{row['must_include_total']} "
              f"출처 {row['source_hit']} 금지 "
              f"{len(row['must_not_include_hits'])} {flag}")
    return rows


def summarize(rows, chunks, variant='baseline'):
    scored = [r for r in rows if not r['rule_path']]
    body = req_body(variant, '')
    out = [f'# 챗봇 측정 — 변형 {variant} — ' + time.strftime('%Y-%m-%d %H:%M'),
           '',
           f'- 평가셋 {len(rows)}문항 중 규칙 응답 경로로 제외 '
           f'{len(rows) - len(scored)}문항 → 측정 대상 {len(scored)}문항',
           f'- 검색 {ui.CHAT_VARIANTS[variant]["search"]} · 프롬프트 '
           f'{ui.CHAT_VARIANTS[variant]["prompt"]} · 모델 {body["model"]} · '
           f'num_predict {body["options"]["num_predict"]} · '
           f'stream {body["stream"]} · DB 청크 {chunks}개',
           '- 채점: 확장 금지·거절 목록 적용 · 워밍업 1회는 지표에서 제외',
           '', '## 유형별', '',
           '| 유형 | 문항 | 정답 문서 적중 | 필수 문구 포함률 | 금지 발언 | '
           '거절 성공 | 답 잘림 | p50 초 | 최대 초 |',
           '|---|---|---|---|---|---|---|---|---|']
    types_ = []
    for r in rows:
        if r['type'] not in types_:
            types_.append(r['type'])
    for t in types_ + ['전체']:
        grp = scored if t == '전체' else [r for r in scored if r['type'] == t]
        if not grp:
            out.append(f'| {t} | 0 | — | — | — | — | — | — | — |')
            continue
        hits = [r for r in grp if r['source_hit'] is not None]
        hit = (f"{sum(1 for r in hits if r['source_hit'])}/{len(hits)}"
               if hits else '—')
        tot = sum(r['must_include_total'] for r in grp)
        psd = sum(r['must_include_passed'] for r in grp)
        inc = f'{psd}/{tot} ({psd / tot * 100:.0f}%)' if tot else '—'
        ban = sum(len(r['must_not_include_hits']) for r in grp)
        refs = [r for r in grp if r['refuse_ok'] is not None]
        ref = (f"{sum(1 for r in refs if r['refuse_ok'])}/{len(refs)}"
               if refs else '—')
        cut = sum(1 for r in grp if r['done_reason'] == 'length')
        el = sorted(r['elapsed'] for r in grp)
        out.append(f'| {t} | {len(grp)} | {hit} | {inc} | {ban}건 | {ref} | '
                   f'{cut}/{len(grp)} ({cut / len(grp) * 100:.0f}%) | '
                   f'{statistics.median(el):.1f} | {max(el):.1f} |')

    excluded = [r['id'] for r in rows if r['rule_path']]
    out += ['', f"- 규칙 응답 경로로 제외된 문항: {', '.join(excluded) or '없음'}"]

    # 지연을 토막으로 가른다 — 검색 때문인지 모델 재적재 때문인지 보려고.
    def med(key):
        vals = [r[key] for r in scored if r.get(key) is not None]
        return f'{statistics.median(vals):.2f}' if vals else '—'

    def tot(key):
        vals = [r[key] for r in scored if r.get(key) is not None]
        return f'{sum(vals):.1f}' if vals else '—'

    out += ['', '## 시간 분해 (초)', '',
            '| 토막 | p50 | 합계 | 무엇인가 |',
            '|---|---|---|---|',
            f"| search_sec | {med('search_sec')} | {tot('search_sec')} | "
            '검색(임베딩 + pgvector). Ollama 밖 |',
            f"| load_duration | {med('load_sec')} | {tot('load_sec')} | "
            '모델 적재. bge-m3 ↔ 생성모델 재적재가 여기 잡힌다 |',
            f"| prompt_eval_duration | {med('prompt_eval_sec')} | "
            f"{tot('prompt_eval_sec')} | 프롬프트 처리 |",
            f"| eval_duration | {med('eval_sec')} | {tot('eval_sec')} | "
            '생성 |',
            f"| 전체(elapsed) | {med('elapsed')} | {tot('elapsed')} | "
            'HTTP 왕복 전체 |']

    out += ['', '## 문항별', '',
            '| id | 유형 | 질문 | 출처 적중 | 묶음 | 금지 | 잘림 | 전체 초 | '
            '검색 | 적재 | 프롬프트 | 생성 | prompt자 | eval_count |',
            '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in scored:
        out.append(
            f"| {r['id']} | {r['type']} | {r['question']} | {r['source_hit']} | "
            f"{r['must_include_passed']}/{r['must_include_total']} | "
            f"{len(r['must_not_include_hits'])} | "
            f"{'예' if r['done_reason'] == 'length' else '아니오'} | "
            f"{r['elapsed']:.1f} | {r.get('search_sec')} | "
            f"{r.get('load_sec')} | {r.get('prompt_eval_sec')} | "
            f"{r.get('eval_sec')} | {r.get('prompt_chars')} | "
            f"{r['eval_count']} |")

    bans = [(r['id'], w) for r in scored for w in r['must_not_include_hits']]
    if bans:
        out += ['', '## 금지 발언 적발', '']
        out += [f'- {i}: `{w}`' for i, w in bans]
    return '\n'.join(out) + '\n'


def rescore(path):
    """저장된 답 원문으로 다시 채점한다. LLM 을 부르지 않아 답이 바뀌지 않는다.

    원본 .jsonl/.md 는 건드리지 않고 <원본이름>_rescored.md 를 새로 쓴다.
    """
    rows = [json.loads(l) for l in open(path, encoding='utf-8')]
    scored = [r for r in rows if not r['rule_path']]
    old = [score(r, r['answer'], ext=False) for r in scored]
    new = [score(r, r['answer'], ext=True) for r in scored]

    def agg(ss, key):
        return sum(len(s[key]) if isinstance(s[key], list) else 0 for s in ss)

    types_ = []
    for r in scored:
        if r['type'] not in types_:
            types_.append(r['type'])
    out = ['# 기준선 재채점 — 확장 금지·거절 목록 적용', '',
           f'- 원본: `{os.path.basename(path)}` (수정하지 않았다)',
           f'- 답 원문 {len(scored)}건을 다시 채점한 것이다. '
           'LLM 을 다시 부르지 않았으므로 답은 1단계와 같은 것이다.',
           f"- 금지 추가: {', '.join('`' + w + '`' for w in BANNED_EXT)}",
           f"- 거절 추가: {', '.join('`' + w + '`' for w in REFUSE_EXT)}",
           '', '## 확장 전 / 후', '',
           '| 유형 | 문항 | 금지 발언 전 | 금지 발언 후 | 거절 성공 전 | 거절 성공 후 |',
           '|---|---|---|---|---|---|']
    for t in types_ + ['전체']:
        idx = [i for i, r in enumerate(scored)
               if t == '전체' or r['type'] == t]
        o = [old[i] for i in idx]
        n = [new[i] for i in idx]
        ro = [s for s in o if s['refuse_ok'] is not None]
        rn = [s for s in n if s['refuse_ok'] is not None]
        ref_o = (f"{sum(1 for s in ro if s['refuse_ok'])}/{len(ro)}"
                 if ro else '—')
        ref_n = (f"{sum(1 for s in rn if s['refuse_ok'])}/{len(rn)}"
                 if rn else '—')
        out.append(f"| {t} | {len(idx)} | {agg(o, 'must_not_include_hits')}건 | "
                   f"{agg(n, 'must_not_include_hits')}건 | {ref_o} | {ref_n} |")

    out += ['', '## 확장으로 새로 잡힌 것', '']
    found = False
    for i, r in enumerate(scored):
        gained = set(new[i]['must_not_include_hits']) - set(
            old[i]['must_not_include_hits'])
        if gained:
            found = True
            out.append(f"- **{r['id']}** 금지 `{', '.join(sorted(gained))}` — "
                       f"답 원문: \"{r['answer'][:120]}\"")
        if new[i]['refuse_ok'] and not old[i]['refuse_ok']:
            found = True
            out.append(f"- **{r['id']}** 거절로 재판정 — "
                       f"답 원문: \"{r['answer'][:120]}\"")
    if not found:
        out.append('- 없음')

    out += ['', '## 남은 실패 (확장 후에도 거절 실패)', '']
    rest = [r for i, r in enumerate(scored)
            if new[i]['refuse_ok'] is False]
    out += ([f"- **{r['id']}** {r['question']} → \"{r['answer'][:140]}\""
             for r in rest] or ['- 없음'])

    dst = os.path.splitext(path)[0] + '_rescored.md'
    with open(dst, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(out) + '\n')
    print(f'재채점 {len(scored)}건 → {dst}')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='측정할 id 를 콤마로 지정')
    ap.add_argument('--variant', default='baseline',
                    choices=sorted(ui.CHAT_VARIANTS),
                    help='측정할 변형 (기본 baseline = 1단계 동작)')
    ap.add_argument('--rescore', metavar='JSONL',
                    help='저장된 답 원문으로 재채점만 한다(LLM 재호출 없음)')
    args = ap.parse_args()

    if args.rescore:
        return rescore(args.rescore)

    variant = args.variant
    chunks, bad = preflight(variant)
    if bad:
        for line in bad:
            print(f'중단: {line}', file=sys.stderr)
        return 2

    with open(EVAL_SET, encoding='utf-8') as fp:
        items = json.load(fp)['items']
    if args.only:
        keep = {s.strip() for s in args.only.split(',')}
        items = [i for i in items if i['id'] in keep]
    body = req_body(variant, '')
    print(f'변형 {variant} · 모델 {body["model"]} · '
          f'num_predict {body["options"]["num_predict"]} · '
          f'{len(items)}문항 측정 시작')

    vectorstore = core.PGVector(
        connection_string=core.CONN_STR,
        embedding_function=core.OllamaEmbeddings(model=core.EMBED_MODEL),
        collection_name='safety_manual')

    # 변형마다 따로 워밍업한다 — 모델이나 num_predict 가 바뀌면 첫 호출에
    # 적재 시간이 통째로 실려 그 문항만 느리게 보인다. 결과는 버린다.
    print(f'워밍업 1회 ({body["model"]}, 결과 버림)…')
    warm = ui.build_chat_request('낙상 사고 응급처치 알려줘',
                                 vectorstore=vectorstore, variant=variant)
    _, warm_sec = call_ollama(warm['prompt'], variant)
    print(f'  워밍업 {warm_sec:.1f}초 — 지표에서 제외')

    rows = run(items, vectorstore, variant)

    os.makedirs(RESULTS, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M')
    safe = variant.replace('+', '-')
    base = os.path.join(RESULTS, f'{safe}_{stamp}')
    with open(base + '.jsonl', 'w', encoding='utf-8') as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + '\n')
    with open(base + '.md', 'w', encoding='utf-8') as fp:
        fp.write(summarize(rows, chunks, variant))
    print(f'기록: {base}.jsonl / {base}.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
