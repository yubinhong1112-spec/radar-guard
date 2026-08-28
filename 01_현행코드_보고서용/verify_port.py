r"""verify_port.py — 판정 로직 이식이 무손실인지 증명하는 회귀 검증

  실행 위치: [내 PC PowerShell]  (어디서 실행해도 됨 — 경로를 스스로 찾는다)
      python 01_현행코드\verify_port.py

  의존성: numpy 만 (torch·Qt 불필요)

═══ 무엇을 증명하는가 ═══
  jetson_sender.py 의 classify() 는 radar_live_full.py 에서 통째로 복사한 것이다.
  "복사했다"는 주장은 검증 없이는 주장일 뿐이다. 이 스크립트는 두 함수를
  같은 입력에 돌려 출력이 한 건도 다르지 않음을 실측 데이터로 확인한다.

  1) 실측 회귀 : events_final.jsonl 등의 20프레임 실측 창 전부
  2) 합성 회귀 : 경계값 근처를 노린 난수 창 (실측이 못 덮는 구석)
  3) 상수 대조 : 7/12 수정분이 복구됐는지

  ⚠ 이건 "낙상을 잘 잡는가"의 검증이 아니다. 그건 실측 라벨로 따로 한다.
    여기서 증명하는 것은 "옮기는 과정에서 아무것도 변하지 않았다" 뿐이다.
"""
import json
import os
import re
import sys
import glob

import numpy as np

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(ROOT, '02_레이더_원본코드', 'radar_live_full.py')
SEND = os.path.join(HERE, 'jetson_sender.py')
DATA_DIR = os.path.join(ROOT, '03_데이터', '이벤트_학습용')

CEILING_H = 2.30
FALL_ZACC_MIN = 0


def load_fn(path, names):
    """torch·Qt 없이 함수만 떼어내 독립 네임스페이스에서 실행 가능하게 만든다."""
    src = open(path, encoding='utf-8').read()
    ns = {'np': np, 'CEILING_H': CEILING_H, 'FALL_ZACC_MIN': FALL_ZACC_MIN,
          'RF_OK': False, 'RF_MODEL': None}
    for n in names:
        m = re.search(r'(?m)^def %s\(.*?(?=^def |^class |^# ═)' % re.escape(n), src, re.S)
        if not m:
            raise SystemExit(f'{os.path.basename(path)} 에서 {n}() 를 못 찾음')
        exec(compile(m.group(0), path, 'exec'), ns)
    return ns


def windows_from_events(paths, win=20):
    """실측 이벤트 파일 → feat_win 리스트.
    feat = [cx, cy, cz, mean_dop, dop_std, int_mean, n_pts, z_vel, z_accel]"""
    out = []
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                fr = d.get('frames') or []
                if len(fr) < 6:
                    continue
                feats, prev_cy, prev_zv, ema = [], None, 0.0, 0.0
                for i, x in enumerate(fr):
                    cy = float(x.get('cy', 0.0))
                    zv = (prev_cy - cy) if prev_cy is not None else 0.0
                    dt = 0.1
                    za = (zv - prev_zv) / dt if i else 0.0
                    ema = 0.6 * ema + 0.4 * za
                    feats.append([float(x.get('cx', 0)), cy, float(x.get('cz', 0)),
                                  float(x.get('dop_mean', 0)), float(x.get('dop_std', 0)),
                                  float(x.get('inten', 0)), float(x.get('n', 0)),
                                  zv, ema])
                    prev_cy, prev_zv = cy, zv
                for s in range(0, max(1, len(feats) - win + 1)):
                    w = feats[s:s + win]
                    if len(w) >= 6:
                        out.append((w, d.get('label', '?')))
    return out


def synth_windows(n=4000, seed=7):
    """경계값 근처를 노린 합성 창 — 실측이 못 덮는 조건 조합을 강제로 만든다."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        L = int(rng.integers(6, 21))
        base = rng.uniform(0.05, 1.2)
        ds = np.abs(rng.normal(base, 0.5, L))
        if rng.random() < 0.5:                       # 임펄스 주입
            ds[rng.integers(1, L)] = rng.uniform(0.8, 3.0)
        cy = np.cumsum(rng.normal(0, 0.18, L)) + rng.uniform(0.3, 1.8)
        cx = np.cumsum(rng.normal(0, 0.12, L))
        cz = np.cumsum(rng.normal(0, 0.12, L))
        n_p = np.abs(rng.normal(rng.uniform(2, 40), 6, L))
        w = []
        prev_cy, prev_zv, ema = None, 0.0, 0.0
        for i in range(L):
            zv = (prev_cy - cy[i]) if prev_cy is not None else 0.0
            za = (zv - prev_zv) / 0.1 if i else 0.0
            ema = 0.6 * ema + 0.4 * za
            w.append([float(cx[i]), float(cy[i]), float(cz[i]),
                      float(rng.normal(0, 0.3)), float(ds[i]),
                      float(rng.uniform(100, 900)), float(n_p[i]), zv, ema])
            prev_cy, prev_zv = cy[i], zv
        out.append((w, 'synth'))
    return out


def norm(d):
    """비교용 정규화 — dict 순서·float 미세오차 무시."""
    return json.dumps(d, sort_keys=True, default=lambda o: round(float(o), 9))


def main():
    print('=' * 68)
    print('  판정 로직 이식 회귀 검증  (radar_live_full.py  vs  jetson_sender.py)')
    print('=' * 68)
    fns = ['_rf_features', '_rf_veto', 'classify']
    A = load_fn(LIVE, fns)
    B = load_fn(SEND, fns)

    files = sorted(glob.glob(os.path.join(DATA_DIR, 'events_*.jsonl')))
    real = windows_from_events(files)
    syn = synth_windows()
    print(f'\n[입력] 실측 창 {len(real):,}개 (파일 {len(files)}개)  +  합성 창 {len(syn):,}개')

    bad = 0
    checked = 0
    verdicts = {}
    for tag, data in (('실측', real), ('합성', syn)):
        sub_bad = 0
        for w, label in data:
            score = float(np.mean([f[4] for f in w])) * 0.03
            thr = 0.025
            try:
                ra = A['classify'](w, score, thr)
                rb = B['classify'](w, score, thr)
            except Exception as e:
                print(f'  ! 예외 {type(e).__name__}: {e}')
                sub_bad += 1
                continue
            checked += 1
            verdicts[ra['event_type']] = verdicts.get(ra['event_type'], 0) + 1
            if norm(ra) != norm(rb):
                sub_bad += 1
                if sub_bad <= 3:
                    print(f'  ★ 불일치 ({label})\n    live  : {norm(ra)[:180]}'
                          f'\n    sender: {norm(rb)[:180]}')
        print(f'  {tag} {len(data):,}건 → 불일치 {sub_bad}건')
        bad += sub_bad

    print(f'\n[판정 분포] {verdicts}')
    print(f'[결과] 총 {checked:,}건 비교, 불일치 {bad}건 '
          f'→ {"✅ 이식 무손실" if bad == 0 else "❌ 이식 손상"}')

    # ── evidence/gates 가 실제로 채워지는지 ──
    ok_ev = sum(1 for w, _ in real[:500] if B['classify'](w, 0.04, 0.025)['gates'])
    print(f'\n[evidence/gates] 실측 500건 중 gates 채워진 건수: {ok_ev} '
          f'({"OK" if ok_ev > 400 else "확인 필요"})')

    # classify() 무손실과 sender 전용 운영 게이트를 분리한다.
    # STAT_PRE/CRIT는 과전류 차단 뒤에만 쓰는 sender 상태기 값이므로
    # 레이더 정본과 달라도 classify 이식 손상으로 판정하지 않는다.
    print('\n[상수 대조] classify 관련 정본')
    src_l = open(LIVE, encoding='utf-8').read()
    src_s = open(SEND, encoding='utf-8').read()
    const_bad = 0
    for k in ('FALL_CONFIRM', 'CLF_WIN', 'FEATURE_DIM',
              'RECOVER_FRAMES', 'POSTFALL_HOLD'):
        def val(s):
            m = re.search(r'(?m)^%s\s*=\s*([0-9.]+|True|False)' % k, s)
            return m.group(1) if m else '?'
        a, b = val(src_l), val(src_s)
        same = (a == b)
        const_bad += (0 if same else 1)
        print(f'   {"=" if same else "★"} {k:16s} live={a:8s} sender={b}')
    print(f'   → 상수 불일치 {const_bad}건')
    for k in ('STAT_PRE_SEC', 'STAT_CRIT_SEC'):
        print(f'   · sender 운영 {k:12s}={val(src_s)} (정본 비교 제외)')

    return 0 if (bad == 0 and const_bad == 0) else 1


if __name__ == '__main__':
    sys.exit(main())
