"""sop_supplement.py — 경보 SOP 의 'AI 매뉴얼 보충' 1~2줄 생성·검사

  실행: [내 PC PowerShell]  — pgvector + Ollama 필요
      python 01_현행코드\\eval\\sop_supplement.py --model qwen2.5:3b-instruct-q4_K_M
      python 01_현행코드\\eval\\sop_supplement.py --model gemma2:2b

왜 만드나
  경보 화면은 '즉시조치(사람 작성, INSTANT_ACTION) + AI 매뉴얼 보충 1~2줄
  (근거 chunk_id, 사람 승인) + 실측 브리핑(코드)' 으로 간다(10/06 사용자 결정).
  T-CC04·05 에서 현행 프롬프트(즉시조치 + 발췌 → 4줄)는 즉시조치를 그대로
  베끼거나(4/6종 동일 문장) 뜻을 뒤집었다(과전류 '교체 금지' → '교체').
  그래서 AI 칸은 **즉시조치에 없는 매뉴얼 내용만** 만든다.

제품 코드를 쓰지 않는다
  이 프롬프트는 제품에 아직 없다(제품 반영은 T-CC07). SopEngineV2._gen_facts 는
  4줄 SOP 용이라 쓰지 않고 여기서 새로 조립한다. 제품 파일은 읽기만 한다
  (INSTANT_ACTION·pinned_chunks).

자동 검사 6종 — 전부 '표시' 이고 통과/탈락을 코드가 단정하지 않는다
  1 근거 실재 : chunk_id 가 그 경보의 pinned_chunks 안에 있나
  2 근거 일치 : 문장의 내용어가 인용 청크에 몇 % 나오나 (50% 미만 = 근거 약함)
  3 중복 의심 : INSTANT_ACTION 문장과 문자 2-gram 자카드 최댓값 ≥ 0.5
  4 금지어   : sop_eval_set.json 의 must_not_include 적중
  5 길이     : 40자 초과
  6 구 기준  : '4∼5' · '4~5' · '1분간 100회' 적중 (E-14 의 옛 심폐소생술 수치)

출력
  eval/results/sop_supp_<모델>_<날짜>.jsonl / .md
"""
import argparse
import ctypes
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, 'results')
RULES = os.path.join(HERE, 'sop_eval_set.json')

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, CODE)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import radar_core as core      # noqa: E402
import console_ui as ui        # noqa: E402

MODELS = ('qwen2.5:3b-instruct-q4_K_M', 'gemma2:2b')
COLLECTION = 'safety_manual_v2'
MAX_CHARS = 40                 # 항목 한 문장 길이 상한
DUP_THRESHOLD = 0.5            # 검사 3 자카드 임계값
GROUND_THRESHOLD = 0.5         # 검사 2 내용어 포함률 임계값
OLD_CPR_TERMS = ('4∼5', '4~5', '1분간 100회')
SCHEMA = {'type': 'object',
          'properties': {'items': {
              'type': 'array',
              'items': {'type': 'object',
                        'properties': {'text': {'type': 'string'},
                                       'chunk_id': {'type': 'string'}},
                        'required': ['text', 'chunk_id']},
              'maxItems': 2}},
          'required': ['items']}


def avail_mb():
    class S(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    s = S()
    s.dwLength = ctypes.sizeof(S)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return int(s.ullAvailPhys / (1024 * 1024))


def rules():
    with open(RULES, encoding='utf-8') as fp:
        d = json.load(fp)
    return {k: v for k, v in d.items() if not k.startswith('_')}


def chunks_for(chunk_ids):
    """지정 chunk_id 의 본문을 지정 순서대로. 하나라도 없으면 중단."""
    import psycopg2
    with psycopg2.connect(core.CONN_STR) as cn:
        with cn.cursor() as cur:
            cur.execute(
                "SELECT e.cmetadata->>'chunk_id', e.document "
                'FROM langchain_pg_embedding e JOIN langchain_pg_collection c '
                "ON c.uuid = e.collection_id WHERE c.name = %s "
                "AND e.cmetadata->>'chunk_id' = ANY(%s)",
                (COLLECTION, list(chunk_ids)))
            got = dict(cur.fetchall())
    missing = [c for c in chunk_ids if c not in got]
    if missing:
        raise SystemExit(f"중단 — {COLLECTION} 에 없는 chunk_id: {missing}")
    return [(c, got[c]) for c in chunk_ids]


def instant_lines(ev):
    """그 경보의 INSTANT_ACTION 문장 목록(단계 구분 없이 평평하게)."""
    return [a for _, acts in core.INSTANT_ACTION.get(ev, []) for a in acts]


# ── 검사 ────────────────────────────────────────────────────────────
# 조사·어미를 근사 제거한다. 완벽한 형태소 분석이 아니라 '근거 일치' 를 눈으로
# 볼 때 참고하는 수치일 뿐이고, 50% 미만도 탈락이 아니라 표시다.
JOSA = ('으로서', '으로써', '에서는', '라고', '으로', '에서', '에게', '까지',
        '부터', '보다', '처럼', '마다', '이나', '과의', '와의', '은', '는',
        '이', '가', '을', '를', '의', '에', '도', '만', '과', '와', '로', '고')
EOMI = ('하십시오', '하지마라', '한다', '된다', '하라', '하고', '하며', '해야',
        '하지', '시오', '으십', '합니다', '됩니다', '한', '된', '함', '됨')


def content_words(text):
    """2글자 이상 한글 덩어리에서 조사·어미를 근사 제거한 집합."""
    out = set()
    for w in re.findall(r'[가-힣]{2,}', text):
        for suf in JOSA + EOMI:
            if len(w) > len(suf) + 1 and w.endswith(suf):
                w = w[:-len(suf)]
                break
        if len(w) >= 2:
            out.add(w)
    return out


def ground_ratio(text, chunk_text):
    """문장 내용어 중 인용 청크에 실제로 나오는 비율."""
    words = content_words(text)
    if not words:
        return None
    flat = chunk_text.replace(' ', '')
    hit = sum(1 for w in words if w in chunk_text or w in flat)
    return round(hit / len(words), 3)


def bigrams(s):
    t = re.sub(r'\s+', '', s)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def jaccard(a, b):
    A, B = bigrams(a), bigrams(b)
    if not A or not B:
        return 0.0
    return round(len(A & B) / len(A | B), 3)


def inspect(item, ev, pinned, chunk_map, actions, banned):
    """검사 6종. 전부 수치·표시이고 단정하지 않는다."""
    text = (item.get('text') or '').strip()
    cid = (item.get('chunk_id') or '').strip()
    exists = cid in pinned
    gr = ground_ratio(text, chunk_map[cid]) if exists else None
    dups = sorted(((jaccard(text, a), a) for a in actions), reverse=True)
    top_dup, dup_with = (dups[0] if dups else (0.0, ''))
    return {
        'text': text, 'chunk_id': cid, 'chars': len(text),
        'chk1_source_exists': exists,
        'chk2_ground_ratio': gr,
        'chk2_weak': (gr is not None and gr < GROUND_THRESHOLD),
        'chk3_dup_max': top_dup,
        'chk3_dup_with': dup_with,
        'chk3_dup_suspect': top_dup >= DUP_THRESHOLD,
        'chk4_banned_hits': [w for w in banned if w in text],
        'chk5_too_long': len(text) > MAX_CHARS,
        'chk6_old_cpr': [w for w in OLD_CPR_TERMS if w in text],
    }


def approvable(c):
    """승인 후보 = 검사1 통과 · 4·6 위반 0 · 3 중복 아님 · 2 약함 아님."""
    return (c['chk1_source_exists'] and not c['chk4_banned_hits']
            and not c['chk6_old_cpr'] and not c['chk3_dup_suspect']
            and not c['chk2_weak'])


def build_prompt(ev, excerpt, actions):
    label = ui.EVENT_KO.get(ev, ev)
    acts = '\n'.join(f'- {a}' for a in actions) or '등록된 조치 없음'
    return (
        f'너는 산업 현장 안전관리 보조자다. "{label}" 경보 화면에는 아래 '
        f'[이미 화면에 있는 즉시조치]가 그대로 표시된다.\n'
        f'[이미 화면에 있는 즉시조치]\n{acts}\n'
        f'[공식 매뉴얼 발췌]\n{excerpt}\n'
        f'할 일: [공식 매뉴얼 발췌]에 있고 [이미 화면에 있는 즉시조치]에는 '
        f'없는 **현장에서 할 조치**만 골라 최대 2개 쓴다.\n'
        f'규칙:\n'
        f'- 즉시조치의 내용을 반복하거나 바꿔 말하지 마라.\n'
        f'- 정의·원리 설명·통계·예방 설계·교육 내용은 쓰지 마라. '
        f'지금 현장에서 할 행동만 쓴다.\n'
        f'- 각 항목은 한 문장으로 {MAX_CHARS}자 이내.\n'
        f'- 발췌에 없는 수치·절차·장비를 만들지 마라.\n'
        f'- 각 항목에 근거가 된 chunk_id 를 정확히 하나 적어라.\n'
        f'- 조건에 맞는 내용이 없으면 items 를 빈 목록으로 두어라.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', default=MODELS[0], choices=MODELS)
    ap.add_argument('--num-ctx', type=int, default=3072)
    ap.add_argument('--num-predict', type=int, default=400)
    ap.add_argument('--min-mem', type=int, default=1500,
                    help='측정 전 요구 가용 메모리(MB). 낮추면 모델이 '
                         '페이징된다 — 이번 산출물은 문장 품질만 쓰므로 '
                         '지연에만 영향이 있다. 결과에 기록된다')
    args = ap.parse_args()

    mem_start = avail_mb()
    print(f'가용 메모리 시작 {mem_start} MB · 모델 {args.model} · '
          f'num_ctx {args.num_ctx} · num_predict {args.num_predict}')
    if mem_start < args.min_mem:
        print(f'중단: 가용 메모리 {mem_start} MB < {args.min_mem} MB',
              file=sys.stderr)
        return 2
    if args.min_mem < 1500:
        print(f'⚠ 메모리 하한을 {args.min_mem} MB 로 낮췄다 — 지연 수치를 '
              '쓰지 마라. 문장 품질은 영향 없다.')

    R = rules()
    rows = []
    for ev in ui.SopEngineV2.PREPARE_EVENTS:
        pinned = R[ev]['pinned_chunks']
        banned = R[ev].get('must_not_include') or []
        pairs = chunks_for(pinned)
        chunk_map = dict(pairs)
        actions = instant_lines(ev)
        excerpt = '\n'.join(f'[{cid}]\n{doc}' for cid, doc in pairs)
        prompt = build_prompt(ev, excerpt, actions)
        body = json.dumps({
            'model': args.model, 'prompt': prompt, 'stream': False,
            'keep_alive': '30m', 'format': SCHEMA,
            'options': {'num_ctx': args.num_ctx,
                        'num_predict': args.num_predict,
                        'temperature': 0.0},
        }).encode('utf-8')
        req = urllib.request.Request(
            core.OLLAMA_URL, data=body,
            headers={'Content-Type': 'application/json'})
        started = time.perf_counter()
        err, items, raw = None, [], {}
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw = json.loads(resp.read().decode('utf-8'))
            items = json.loads(raw.get('response') or '{}').get('items') or []
        except Exception as e:
            err = f'{type(e).__name__}: {e}'
        elapsed = time.perf_counter() - started
        checks = [inspect(it, ev, pinned, chunk_map, actions, banned)
                  for it in items if isinstance(it, dict)]
        rows.append({
            'event': ev, 'event_ko': ui.EVENT_KO.get(ev, ev),
            'model': args.model, 'collection': COLLECTION,
            'pinned_chunks': pinned, 'instant_actions': actions,
            'prompt': prompt, 'prompt_chars': len(prompt),
            'excerpt_chars': len(excerpt),
            'num_ctx': args.num_ctx, 'num_predict': args.num_predict,
            'raw_response': raw.get('response'),
            'items': checks, 'error': err,
            'elapsed': round(elapsed, 3),
            'eval_count': raw.get('eval_count'),
            'done_reason': raw.get('done_reason'),
            'approvable': [c for c in checks if approvable(c)],
            'min_mem': args.min_mem, 'mem_start_mb': mem_start,
            'mem_now_mb': avail_mb(),
        })
        ok = len(rows[-1]['approvable'])
        print(f"  {ev:30s} {elapsed:6.1f}초 항목 {len(checks)}개 "
              f"승인후보 {ok}개 프롬프트 {len(prompt)}자"
              + (f' 오류 {err}' if err else ''))

    mem_end = avail_mb()
    os.makedirs(RESULTS, exist_ok=True)
    base = os.path.join(RESULTS, f"sop_supp_{args.model.split(':')[0]}_"
                                 f"{time.strftime('%Y%m%d_%H%M')}")
    with open(base + '.jsonl', 'w', encoding='utf-8') as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + '\n')

    out = [f'# 경보 SOP AI 매뉴얼 보충 — {args.model}', '',
           f'- {time.strftime("%Y-%m-%d %H:%M")} · 가용 메모리 '
           f'{mem_start} → {mem_end} MB (하한 {args.min_mem} MB)',
           f'- 컬렉션 {COLLECTION} · 지정 조각 본문 전체(자르지 않음) · '
           f'num_ctx {args.num_ctx} · num_predict {args.num_predict} · '
           'temperature 0',
           f'- 검사 임계값: 근거 일치 {GROUND_THRESHOLD:.0%} 미만 = 약함 · '
           f'중복 자카드(문자 2-gram) {DUP_THRESHOLD} 이상 = 의심 · '
           f'길이 {MAX_CHARS}자 초과 · 구 기준 수치 '
           f'{", ".join(OLD_CPR_TERMS)}',
           '- 검사는 전부 **표시**다. 통과·탈락을 코드가 단정하지 않는다.']
    if args.min_mem < 1500:
        out.append('- ⚠ 메모리 하한을 낮춰 측정했다 — **지연 수치를 쓰지 '
                   '말 것**. 문장 품질은 영향 없다.')
    out += ['', '## 요약', '',
            '| 이벤트 | 생성 항목 | 승인 후보 | 초 | eval_count | 프롬프트자 |',
            '|---|---|---|---|---|---|']
    for r in rows:
        out.append(f"| {r['event']} | {len(r['items'])} | "
                   f"{len(r['approvable'])} | {r['elapsed']:.1f} | "
                   f"{r['eval_count']} | {r['prompt_chars']} |")

    out += ['', '## 항목별', '']
    for r in rows:
        out += [f"### {r['event']} ({r['event_ko']})", '',
                f"- 지정 조각: {', '.join(r['pinned_chunks'])}",
                f"- 발췌 {r['excerpt_chars']}자 · 프롬프트 {r['prompt_chars']}자",
                '']
        if r['error']:
            out += [f"**생성 실패** — `{r['error']}`", '']
            continue
        if not r['items']:
            out += ['**항목 없음** (모델이 빈 목록을 반환)', '']
            continue
        for i, c in enumerate(r['items'], 1):
            out += [f"**{i}. {c['text']}**  — 근거 `{c['chunk_id']}`"
                    + ('  ← 승인 후보' if approvable(c) else ''),
                    '',
                    '| 검사 | 결과 |', '|---|---|',
                    f"| 1 근거 실재 | {'있음' if c['chk1_source_exists'] else '**없음**'} |",
                    f"| 2 근거 일치 | {c['chk2_ground_ratio']}"
                    + (' **(약함)**' if c['chk2_weak'] else '') + ' |',
                    f"| 3 중복 의심 | 자카드 {c['chk3_dup_max']}"
                    + (' **(의심)**' if c['chk3_dup_suspect'] else '')
                    + f" · 가장 닮은 즉시조치: {c['chk3_dup_with']} |",
                    '| 4 금지어 | '
                    + (f"**{', '.join(c['chk4_banned_hits'])}**"
                       if c['chk4_banned_hits'] else '없음') + ' |',
                    f"| 5 길이 | {c['chars']}자"
                    + (' **(초과)**' if c['chk5_too_long'] else '') + ' |',
                    '| 6 구 기준 수치 | '
                    + (f"**{', '.join(c['chk6_old_cpr'])}**"
                       if c['chk6_old_cpr'] else '없음') + ' |',
                    '']
        if not r['approvable']:
            out += ['**후보 없음** — 사람이 기대목록에서 고르거나 보충 생략', '']

    out += ['', '## 프롬프트 원문', '']
    for r in rows:
        out += [f"### {r['event']}", '', '```', r['prompt'], '```', '']

    with open(base + '.md', 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(out) + '\n')
    tot = sum(len(r['items']) for r in rows)
    app = sum(len(r['approvable']) for r in rows)
    print(f'항목 {tot}개 · 승인 후보 {app}개 · 가용 메모리 끝 {mem_end} MB')
    print(f'기록: {base}.jsonl / {base}.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
