"""chunk_select_dump.py — 고정 출처 조각 선택이 무엇을 고르는지 그대로 적는다

  실행: [내 PC PowerShell]  — pgvector 컨테이너만 필요하다(Ollama 불필요)
      python 01_현행코드\\eval\\chunk_select_dump.py
      python 01_현행코드\\eval\\chunk_select_dump.py --collection safety_manual_v2

무엇을 보려는 것인가
  radar_core.search_sop_documents 는 사고 종류별 고정 출처 문서가 있으면
  임베딩을 건너뛰고 그 문서의 청크를 SOP_RESPONSE_TERMS 빈도순으로 고른다.
  '용어 빈도' 라서 엉뚱한 절이 1위가 될 수 있다 — 개선계획 문제 E
  (EA-05 감전 질문에 화상 절이 뽑힘). 후보 전체를 순위·점수와 함께 적어
  원인을 눈으로 확인하고, 다음 단계 '이벤트별 chunk_id 고정' 의 재료로 쓴다.

제품 코드를 호출하지 않고 같은 규칙을 다시 쓴다
  search_sop_documents 는 상위 N개만 돌려주고 후보 전체와 점수를 버린다.
  제품 코드를 고치지 않기로 했으므로(T-CC04 공통규칙 6) 선택 규칙만 그대로
  옮겨 적는다. 규칙이 갈라지지 않는지는 마지막에 실제 함수 결과와 대조한다.

출력
  eval/results/chunk_select_<컬렉션>_<날짜>.md
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.dirname(HERE)
RESULTS = os.path.join(HERE, 'results')
sys.path.insert(0, CODE)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import psycopg2                   # noqa: E402
import radar_core as core         # noqa: E402

PREVIEW = 200


def candidates(source_file, collection):
    """해당 문서의 청크 전부. 메타데이터도 같이 가져온다."""
    with psycopg2.connect(core.CONN_STR) as cn:
        with cn.cursor() as cur:
            cur.execute(
                "SELECT e.document, e.cmetadata FROM langchain_pg_embedding e "
                "JOIN langchain_pg_collection c ON c.uuid = e.collection_id "
                "WHERE e.cmetadata->>'source_file' = %s AND c.name = %s",
                (source_file, collection))
            return cur.fetchall()


def rank(rows, terms):
    """search_sop_documents 와 같은 규칙 — 용어 빈도 내림차순."""
    scored = [(sum(doc.count(t) for t in terms), doc, meta)
              for doc, meta in rows]
    scored.sort(key=lambda r: r[0], reverse=True)
    return scored


def chunk_label(meta):
    """청크를 가리킬 이름. v2 는 chunk_id, 없으면 page 를 쓴다."""
    for key in ('chunk_id', 'id', 'page', 'page_number'):
        if meta.get(key) is not None:
            return f'{key}={meta[key]}'
    return '식별자 없음'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--collection', default='safety_manual',
                    choices=['safety_manual', 'safety_manual_v2'])
    args = ap.parse_args()
    coll = args.collection

    out = [f'# 고정 출처 조각 선택 덤프 — {coll}', '',
           '- `search_sop_documents` 가 고정 출처 문서에서 청크를 고르는 규칙은 '
           '`SOP_RESPONSE_TERMS` 용어 빈도 내림차순이다. 아래는 후보 전체를 '
           '그 점수 순으로 적은 것이고, **선택** 열이 실제로 쓰이는 청크다.',
           '- `count` 는 코드와 같은 규칙으로 정했다 — 카테고리가 여러 개면 '
           '각 1개, 하나면 2개.', '']

    mismatch = []
    for ev in sorted(core.SOP_RESPONSE_SOURCE):
        cat_raw = core.EVENT_CATEGORY.get(ev)
        cats = cat_raw if isinstance(cat_raw, (list, tuple)) else (cat_raw,)
        count = 1 if isinstance(cat_raw, (list, tuple)) else 2
        terms = core.SOP_RESPONSE_TERMS.get(ev, ())
        out += [f'## {ev}', '',
                f'- 카테고리: {", ".join(str(c) for c in cats)} · '
                f'카테고리당 선택 {count}개',
                f'- 용어: {", ".join(terms) or "없음"}', '']
        picked = []
        for cat in cats:
            src = core.SOP_RESPONSE_SOURCE[ev].get(cat)
            if not src:
                out += [f'### {cat} — 고정 출처 없음 (임베딩 검색으로 간다)', '']
                continue
            rows = candidates(src, coll)
            out += [f'### {cat} → `{src}`', '',
                    f'후보 {len(rows)}개' if rows else
                    '**후보 0개 — 이 컬렉션에 이 문서가 없다**', '']
            if not rows:
                continue
            out += ['| 순위 | 점수 | 선택 | 식별자 | 본문 앞 200자 |',
                    '|---|---|---|---|---|']
            for i, (score, doc, meta) in enumerate(rank(rows, terms), 1):
                mark = 'O' if i <= count else ''
                if i <= count:
                    picked.append(doc)
                body = doc[:PREVIEW].replace('|', '\\|').replace('\n', ' ')
                out.append(f'| {i} | {score} | {mark} | {chunk_label(meta)} | '
                           f'{body} |')
            out.append('')

        # 규칙이 갈라지지 않았는지 실제 함수 결과와 대조한다.
        vs = type('Vs', (), {'collection_name': coll,
                             'similarity_search': lambda *a, **k: []})()
        real = core.search_sop_documents(vs, ev, core.SOP_QUERY.get(ev, ''),
                                         cat_raw)
        real_txt = [d.page_content for d in real]
        if real_txt != picked:
            mismatch.append(ev)
            out += [f'⚠ **선택 규칙 불일치** — 이 덤프가 고른 것과 '
                    f'`search_sop_documents` 결과가 다르다. 덤프 '
                    f'{len(picked)}개 / 실제 {len(real_txt)}개', '']

    out += ['## 선택 규칙 대조', '',
            f'- `search_sop_documents` 결과와 비교: 불일치 {len(mismatch)}건'
            + (f' — {", ".join(mismatch)}' if mismatch else '')]

    os.makedirs(RESULTS, exist_ok=True)
    import time
    dst = os.path.join(RESULTS,
                       f'chunk_select_{coll}_{time.strftime("%Y%m%d_%H%M")}.md')
    with open(dst, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(out) + '\n')
    print(f'{coll}: 이벤트 {len(core.SOP_RESPONSE_SOURCE)}종 · '
          f'선택 규칙 불일치 {len(mismatch)}건 → {dst}')
    return 1 if mismatch else 0


if __name__ == '__main__':
    sys.exit(main())
