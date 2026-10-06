r"""sync_agent_docs.py — Claude Code 용 규칙을 Codex 용으로 복제한다.

  실행: [내 PC PowerShell] — cd 불필요
      python scripts\sync_agent_docs.py            반영
      python scripts\sync_agent_docs.py --check    검사만 (pre-commit 이 씀)

  ⚠ [8/04] 왜 만들었나:
    `AGENTS.md` + `.agents/skills/` 는 `CLAUDE.md` + `.claude/skills/` 의 Codex 용 사본인데,
    손으로 두 벌을 고치다 보니 실제로 갈라졌다 — AGENTS.md 의 §10 이 이미 없어진
    §0/§1/§2 구조를 지시하고 있었고 session-end 스킬은 74줄 차이가 났다.
    **Claude Code 와 Codex 가 서로 다른 규칙으로 움직이면 반대쪽이 틀린 문서를 쓴다.**

  정본은 `CLAUDE.md` 와 `.claude/skills/` 다. AGENTS 쪽은 생성물이다.
  단 AGENTS.md 하단의 '## Imported ...' 이후 고유 꼬리는 보존한다.

  종료코드 0 = 동기 / 1 = 불일치(--check) 또는 오류
"""
import os
import re
import shutil
import sys

# ⚠ [8/04] Windows 기본 콘솔(cp949)에서 '—' 같은 문자를 출력하면
#   UnicodeEncodeError 로 중간에 죽는다. 검증 절차가 숨은 환경변수
#   ($env:PYTHONUTF8)에 의존하면 안 되므로 진입점에서 보장한다.
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_MD, DST_MD = os.path.join(ROOT, 'CLAUDE.md'), os.path.join(ROOT, 'AGENTS.md')
SRC_SK = os.path.join(ROOT, '.claude', 'skills')
DST_SK = os.path.join(ROOT, '.agents', 'skills')
TAIL = '## Imported'          # 이 줄부터 AGENTS.md 고유 — 덮어쓰지 않는다

# Codex 는 CLAUDE.md 를 읽지 않는다. 문서 안의 자기 참조를 바꿔 준다.
SUBS = [('Claude Code와 Cowork', 'Codex와 Cowork'),
        ('Claude Code ↔ Cowork', 'Codex ↔ Cowork'),
        ('Claude Code (PowerShell)', 'Codex (PowerShell)'),
        ('Claude Code', 'Codex'),
        ('`CLAUDE.md`', '`AGENTS.md`'),
        ('CLAUDE.md', 'AGENTS.md'),
        ('.claude/skills', '.agents/skills')]


def convert(text):
    for a, b in SUBS:
        text = text.replace(a, b)
    return text


def expected_agents_md():
    src = convert(open(SRC_MD, encoding='utf-8').read())
    if os.path.exists(DST_MD):
        cur = open(DST_MD, encoding='utf-8').read()
        i = cur.find('\n' + TAIL)
        if i != -1:
            return src.rstrip() + '\n\n' + cur[i + 1:].lstrip('\n')
    return src


def walk(base):
    out = {}
    for d, _, fs in os.walk(base):
        for f in fs:
            if f.endswith('.md'):
                p = os.path.join(d, f)
                out[os.path.relpath(p, base).replace('\\', '/')] = \
                    open(p, encoding='utf-8').read()
    return out


def main():
    check = '--check' in sys.argv
    diffs = []

    # 1. AGENTS.md
    want = expected_agents_md()
    have = open(DST_MD, encoding='utf-8').read() if os.path.exists(DST_MD) else ''
    if want != have:
        diffs.append('AGENTS.md')
        if not check:
            open(DST_MD, 'w', encoding='utf-8').write(want)

    # 2. 스킬
    src, dst = walk(SRC_SK), walk(DST_SK) if os.path.isdir(DST_SK) else {}
    for rel, text in src.items():
        want = convert(text)
        if dst.get(rel) != want:
            diffs.append(f'.agents/skills/{rel}')
            if not check:
                p = os.path.join(DST_SK, rel.replace('/', os.sep))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, 'w', encoding='utf-8').write(want)
    # 정본에 없는데 사본에만 있는 것
    for rel in dst:
        if rel not in src:
            diffs.append(f'.agents/skills/{rel} (정본에 없음 — 손으로 지운다)')

    if not diffs:
        print('규칙 문서 동기  OK   AGENTS.md · .agents/skills 일치')
        return 0
    if check:
        print(f'규칙 문서 동기  NG   {len(diffs)}건 불일치')
        for d in diffs:
            print(f'  NG   {d}')
        print('\n  → python scripts\\sync_agent_docs.py 로 반영한다')
        return 1
    print(f'규칙 문서 동기  {len(diffs)}건 반영')
    for d in diffs:
        print(f'  →    {d}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
