"""chat_eval.py — 관제 AI 챗봇 기준선 측정 (현행 동작 그대로)

  실행: [내 PC PowerShell]
      python 01_현행코드\\eval\\chat_eval.py
      python 01_현행코드\\eval\\chat_eval.py --only EA-01,KF-03   # 일부만

  사전 조건 (둘 중 하나라도 안 되면 측정을 시작하지 않는다)
      docker start radar-guard-db      # pgvector 가 꺼져 있으면 검색이 조용히 0건
      ollama serve                     # gemma2:2b, bge-m3 필요
      관제 UI(console_ui.py)는 꺼 둔다 — Ollama CPU 를 나눠 쓰면 지연이 오염된다.

무엇을 재나
  console_ui.build_chat_request 로 **현행과 같은 프롬프트**를 만들고, 현행과
  같은 파라미터로 Ollama 를 호출해 문항별 결과를 적는다. 챗봇 코드를 바꾸지
  않는다 — 이 숫자가 다음 단계 개선의 before 가 된다.

출력
  results/baseline_YYYYMMDD_HHMM.jsonl   문항별 원본 기록(응답 원문 포함)
  results/baseline_YYYYMMDD_HHMM.md      유형별 요약표

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

# 현행 _work_locked 와 같은 호출 파라미터. 한 글자도 바꾸지 않는다.
REQ = {'model': core.LLM_MODEL, 'stream': False, 'keep_alive': '30m',
       'options': {'num_ctx': 2048, 'num_predict': 100, 'temperature': 0.2}}
TIMEOUT = 30

# 거절·회피로 인정할 표현. out_of_scope 채점에만 쓴다.
REFUSE_MARKS = ('모른', '모릅', '알 수 없', '확인할 수 없', '제공하지', '제공할 수 없',
                '지원하지', '할 수 없', '수행하지', '실행하지', '권한이 없',
                '해당 없음', '없습니다', '아닙니다', '불가')


def preflight():
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
        for need in (core.LLM_MODEL, core.EMBED_MODEL):
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


def call_ollama(prompt):
    body = json.dumps(dict(REQ, prompt=prompt)).encode('utf-8')
    req = urllib.request.Request(
        core.OLLAMA_URL, data=body,
        headers={'Content-Type': 'application/json'})
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        raw = json.loads(response.read().decode('utf-8'))
    return raw, time.perf_counter() - started


def source_hit(expect, sources):
    """expect_source 가 검색된 출처에 들어 있나. 파일명과 정식 명칭 둘 다 본다."""
    if not expect:
        return None
    for s in sources:
        _, title, _ = core.source_label(s)
        if expect in s or expect in title:
            return True
    return False


def score(item, answer):
    groups = item.get('must_include') or []
    passed = sum(1 for g in groups if any(w in answer for w in g))
    banned = [w for w in (item.get('must_not_include') or []) if w in answer]
    refused = any(m in answer for m in REFUSE_MARKS)
    return {
        'must_include_total': len(groups),
        'must_include_passed': passed,
        'must_not_include_hits': banned,
        'refused': refused,
        'refuse_ok': (refused if item.get('should_refuse') else None),
    }


def run(items, vectorstore):
    rows = []
    for item in items:
        local = rule_answer(item)
        if local is not None:
            print(f"  {item['id']} 규칙 응답 경로 — 지표에서 제외")
            rows.append(dict(item, rule_path=True, answer=local,
                             elapsed=0.0, sources=[], done_reason='rule',
                             eval_count=0))
            continue
        built = ui.build_chat_request(
            item['question'], alert=item.get('alert'),
            pkt=item.get('pkt'), vectorstore=vectorstore)
        if item['type'] == 'event_action' and not built['sources']:
            raise SystemExit(
                f"측정 중단 — {item['id']} event_action 인데 검색 결과 0건이다. "
                '챗봇 성능이 아니라 DB/검색 문제일 수 있으므로 기록하지 않는다.')
        raw, elapsed = call_ollama(built['prompt'])
        answer = (raw.get('response') or '').strip()
        row = dict(item, rule_path=False, answer=answer, elapsed=elapsed,
                   event=built['event'], sources=built['sources'],
                   source_hit=source_hit(item.get('expect_source'),
                                         built['sources']),
                   done_reason=raw.get('done_reason'),
                   eval_count=raw.get('eval_count'))
        row.update(score(item, answer))
        rows.append(row)
        flag = '잘림' if row['done_reason'] == 'length' else ''
        print(f"  {item['id']} {elapsed:5.1f}초 "
              f"묶음 {row['must_include_passed']}/{row['must_include_total']} "
              f"출처 {row['source_hit']} {flag}")
    return rows


def summarize(rows, chunks):
    scored = [r for r in rows if not r['rule_path']]
    out = ['# 챗봇 기준선 — ' + time.strftime('%Y-%m-%d %H:%M'), '',
           f'- 평가셋 {len(rows)}문항 중 규칙 응답 경로로 제외 '
           f'{len(rows) - len(scored)}문항 → 측정 대상 {len(scored)}문항',
           f'- 모델 {REQ["model"]} · num_predict {REQ["options"]["num_predict"]}'
           f' · stream {REQ["stream"]} · DB 청크 {chunks}개',
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
    out += ['', '## 문항별', '',
            '| id | 유형 | 질문 | 출처 적중 | 묶음 | 금지 | 잘림 | 초 | eval_count |',
            '|---|---|---|---|---|---|---|---|---|']
    for r in scored:
        out.append(
            f"| {r['id']} | {r['type']} | {r['question']} | {r['source_hit']} | "
            f"{r['must_include_passed']}/{r['must_include_total']} | "
            f"{len(r['must_not_include_hits'])} | "
            f"{'예' if r['done_reason'] == 'length' else '아니오'} | "
            f"{r['elapsed']:.1f} | {r['eval_count']} |")
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='측정할 id 를 콤마로 지정')
    args = ap.parse_args()

    chunks, bad = preflight()
    if bad:
        for line in bad:
            print(f'중단: {line}', file=sys.stderr)
        return 2

    with open(EVAL_SET, encoding='utf-8') as fp:
        items = json.load(fp)['items']
    if args.only:
        keep = {s.strip() for s in args.only.split(',')}
        items = [i for i in items if i['id'] in keep]
    print(f'{len(items)}문항 측정 시작')

    vectorstore = core.PGVector(
        connection_string=core.CONN_STR,
        embedding_function=core.OllamaEmbeddings(model=core.EMBED_MODEL),
        collection_name='safety_manual')

    print('워밍업 1회 (결과 버림)…')
    warm = ui.build_chat_request('낙상 사고 응급처치 알려줘',
                                 vectorstore=vectorstore)
    call_ollama(warm['prompt'])

    rows = run(items, vectorstore)

    os.makedirs(RESULTS, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M')
    base = os.path.join(RESULTS, f'baseline_{stamp}')
    with open(base + '.jsonl', 'w', encoding='utf-8') as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + '\n')
    with open(base + '.md', 'w', encoding='utf-8') as fp:
        fp.write(summarize(rows, chunks))
    print(f'기록: {base}.jsonl / {base}.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
