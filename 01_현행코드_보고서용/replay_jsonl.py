"""replay_jsonl.py — 실측 jsonl 을 젯슨인 척 재생한다 [노트북]

  실행: [내 PC PowerShell]  — 창 2개
      창1:  python replay_jsonl.py
      창2:  python console_ui.py --live 127.0.0.1

  주요 옵션
      python replay_jsonl.py --list                 어떤 파일에 뭐가 몇 건 있는지
      python replay_jsonl.py --seq fall,still,normal    재생 순서 지정
      python replay_jsonl.py --file ../03_데이터/이벤트_학습용/events_final.jsonl
      python replay_jsonl.py --fast                재생 속도 2배
      python replay_jsonl.py --once                한 바퀴만 돌고 종료

═══ 이게 sim_jetson.py 와 뭐가 다른가 ═══
  sim_jetson : 난수로 만든 가짜 점군. 프로토콜·UI 경로 검증용.
  replay     : **실제로 측정한 점군**(03_데이터/이벤트_학습용/*.jsonl)을 그대로
               흘려보낸다. 그래서 노트북 쪽 표시 계층 — 누적, PCA 자세추정,
               머리 추정, 인체 도식, 높이·움직임 수치, 경보 화면 — 이 실전과
               같은 입력으로 돈다.

═══ 이 파일이 하지 않는 것 (매우 중요) ═══
  ⚠ 판정하지 않는다. 낙상인지 아닌지는 jsonl 에 사람이 붙여 둔 label 을
    그대로 읽어 통보할 뿐이다. classify() 는 젯슨에 있고 여기서 돌지 않는다.
    즉 이 재생으로 검증되는 것은 **'판정 결과를 화면이 제대로 보여주는가'**
    이지 '판정이 맞는가' 가 아니다. 후자는 젯슨 + torch 가 필요하다.

  ⚠ LSTM-AE 이상점수(ae_score)는 학습된 baseline 이 있어야 나온다.
    여기서는 threshold 를 보내되 점수는 evidence 에 담지 않는다 —
    없는 값을 지어내면 화면의 '이상도' 가 거짓말이 된다.

═══ 데이터에 대해 알아 둘 것 ═══
  events_*.jsonl 의 프레임은 전부 centroid 수준 값(cx,cy,cz,height,n,dop_std…)을
  갖지만, 원시 점군(pts)은 **일부 프레임에만** 있다. pts 없는 프레임은
  건너뛴다(점군이 없으면 인체 도식을 그릴 수 없고, 없는 점을 만들어 내면
  그 순간 이 재생은 검증 도구가 아니라 그림 도구가 된다).
    실측(2026-08-01): events_still.jsonl 이 pts 보유량이 가장 많다
      fall 42 · still 154 · normal 150 · walk 90 · wave 90 · vib 20 건
"""
import argparse
import glob
import json
import os
import random
import socket
import sys
import threading
import time
from collections import deque

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from radar_common import (
    SCHEMA_VERSION, DATA_PORT, CTRL_PORT, SEND_HZ, MAX_UDP, MIN_PTS, CLIENT_TTL,
    CMD_HELLO, CMD_RESOLVE, CMD_RESTORE, CMD_RESET, CMD_START, CMD_TRAIN,
    CEILING_H, HISTORY_LEN, ZONE_IDS, RADAR_ZONE, ZONE_KO,
    CURR_LIMIT, VOLT_MIN, EVENT_SEV, AUTO_TRIP_EVENTS, PH_LIVE,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', '03_데이터', '이벤트_학습용')
DEFAULT_FILE = os.path.join(DATA_DIR, 'events_still.jsonl')

# ⚠⚠ 녹화된 pts 는 '필터 전' 원시 점군이다.
#   jetson_sender 는 파이프라인 앞단(1082행)에서 y < NEAR_FIELD_MIN_RANGE 인 점을
#   버린다 — 센서 자기 자신·마운트·케이블에서 오는 근접 반사다.
NEAR_FIELD_MIN_RANGE = 0.5      # jetson_sender.NEAR_FIELD_MIN_RANGE 와 같은 값


def prefilter(pts):
    """젯슨이 classify 전에 하는 전처리와 동일. 여기서 안 하면 재생이 실전과 달라진다."""
    return [p for p in pts if p.get('y', 0.0) >= NEAR_FIELD_MIN_RANGE]

# jsonl 라벨 → 젯슨 이벤트 타입.  None = 경보 없음(정상 상황 재생)
LABEL_EVENT = {
    'fall':  'fall_detected',
    'still': 'stationary_anomaly',
    'vib':   'vibration_anomaly',
    'shock': 'electric_shock_risk',
    'pinch': 'pinching',
    'normal': None, 'walk': None, 'wave': None, 'fast_sit': None,
}
# 경보가 뜨면 사람이 [상황 종료] 를 누를 때까지 그대로 유지한다.
#   0 이면 무한 대기(기본). --auto-resolve N 으로만 자동 해제를 켠다.
AUTO_RESOLVE_SEC = 0.0
GAP_SEC = 2.0            # 이벤트 사이 간격
PRE_SEC = 8.0            # 정지형 경보 전 PRE-ALERT 카운트다운 재생 길이 [초]

_lock = threading.RLock()
_clients = {}
S = {
    'phase': PH_LIVE, 'threshold': 0.0250,
    'ev_active': False, 'ev_type': None, 'ev_types': [], 'ev_items': {}, 'ev_rev': 0,
    'ev_sev': 'normal', 'ev_conf': 0.0,
    'ev_zone': RADAR_ZONE, 'ev_id': 0, 'ev_ts': 0.0,
    'ev_evidence': None, 'ev_gates': None, 'ev_rejected': [],
    'frame': None, 'label': '-', 'src': '-', 'pre_alert': '',
    'breaker': {z: 'ON' for z in ZONE_IDS},
    'breaker_reason': {z: None for z in ZONE_IDS},
    'cz_h': deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN),
    'ds_h': deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN),
    'sc_h': deque([0.0] * HISTORY_LEN, maxlen=HISTORY_LEN),
    'logs': deque(maxlen=20), 'incidents': deque(maxlen=20),
    'req': set(),
}


def log(msg):
    ts = time.strftime('%H:%M:%S')
    with _lock:
        S['logs'].append(f'[{ts}] {msg}')
    print(f'[REPLAY {ts}] {msg}')


# ══════════════════════════════════════════════════════════════════════
# 데이터 적재
# ══════════════════════════════════════════════════════════════════════
def load_events(path):
    """pts 가 있는 프레임을 하나라도 가진 이벤트만 돌려준다."""
    out = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            frames = []
            for fr in (d.get('frames') or []):
                pts = prefilter(fr.get('pts') or [])
                if pts:
                    frames.append(dict(fr, pts=pts))
            if not frames:
                continue
            out.append({'label': d.get('label'), 'person': d.get('person', '?'),
                        'ts': d.get('ts', ''), 'frames': frames})
    return out


def summarize(path):
    ev = load_events(path)
    by = {}
    for e in ev:
        by[e['label']] = by.get(e['label'], 0) + 1
    return len(ev), by


def cmd_list():
    print('원시 점군(pts)을 가진 이벤트 수 — 재생 가능한 것만 셈\n')
    print(f'{"파일":38s} {"합계":>5s}  라벨별')
    print('-' * 78)
    for p in sorted(glob.glob(os.path.join(DATA_DIR, '*.jsonl'))):
        n, by = summarize(p)
        pretty = ' '.join(f'{k}:{v}' for k, v in sorted(by.items()))
        print(f'{os.path.basename(p):38s} {n:5d}  {pretty}')
    print('\n재생 가능한 라벨 → 경보 매핑')
    for k, v in LABEL_EVENT.items():
        print(f'   {k:9s} → {v or "(경보 없음 · 정상 재생)"}')


# ══════════════════════════════════════════════════════════════════════
# 제어 수신 (console_ui 의 HELLO / 버튼)
# ══════════════════════════════════════════════════════════════════════
def control_listener():
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sk.bind(('0.0.0.0', CTRL_PORT))
    print(f'[REPLAY] 제어 포트 {CTRL_PORT} 대기')
    known = set()
    while True:
        try:
            data, addr = sk.recvfrom(4096)
            msg = json.loads(data.decode('utf-8'))
        except Exception:
            continue
        with _lock:
            _clients[(addr[0], DATA_PORT)] = time.time()
        if addr[0] not in known:
            known.add(addr[0])
            log(f'뷰어 연결됨: {addr[0]}')
        cmd = msg.get('cmd')
        if cmd in (None, CMD_HELLO):
            continue
        with _lock:
            if cmd == CMD_RESTORE:
                zs = msg.get('zones') or [z for z, v in S['breaker'].items()
                                          if v != 'ON']
                for z in zs:
                    S['breaker'][z] = 'ON'
                    S['breaker_reason'][z] = None
                log(f'BREAKER RESTORE {zs}')
            elif cmd == CMD_RESOLVE:
                S['req'].add(CMD_RESOLVE)
            elif cmd in (CMD_RESET, CMD_START, CMD_TRAIN):
                pass          # 재생기에는 학습 단계가 없다 (항상 LIVE)
        print(f'[REPLAY] CMD {cmd} <- {addr[0]}')


def _targets():
    now = time.time()
    with _lock:
        for a in [a for a, t in _clients.items() if now - t > CLIENT_TTL]:
            _clients.pop(a, None)
        return list(_clients.keys())


# ══════════════════════════════════════════════════════════════════════
# 송신
# ══════════════════════════════════════════════════════════════════════
def _pack(base, pts):
    """MAX_UDP 를 넘지 않게 점을 솎아 낸다 (jetson_sender._pack 과 같은 방식)."""
    p = list(pts)
    while True:
        d = dict(base, points=p)
        raw = json.dumps(d, separators=(',', ':')).encode('utf-8')
        if len(raw) <= MAX_UDP or len(p) <= MIN_PTS:
            return raw
        p = p[::2]


def sender_loop():
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n = 0
    while True:
        time.sleep(1.0 / SEND_HZ)
        n += 1
        with _lock:
            fr = S['frame']
            if fr is None:
                continue
            pts = [{'x': p.get('x', 0.0), 'y': p.get('y', 0.0),
                    'z': p.get('z', 0.0), 'i': p.get('intensity', 0.0)}
                   for p in (fr.get('pts') or [])]
            height = fr.get('height')
            if height is None:
                height = CEILING_H - fr.get('cy', CEILING_H)
            full = (n % SEND_HZ == 0)
            zs = {z: ('ALERT' if (z == S['ev_zone'] and S['ev_active'])
                      else 'NORMAL') for z in ZONE_IDS}
            base = {
                'schema_version': SCHEMA_VERSION, 'seq': n, 'ts': time.time(),
                'full': full,
                'phase': PH_LIVE, 'warmup_count': 150,
                'threshold': S['threshold'],
                'data_ok': True, 'data_age': 0.1, 'scan_left': None,
                'pre_alert': S['pre_alert'],
                'replay': S['src'], 'replay_label': S['label'],
                'centroid': {'cx': fr.get('cx', 0.0), 'cy': fr.get('cy', 0.0),
                             'cz': fr.get('cz', 0.0)},
                'height': round(float(height), 3),
                'n_pts': int(fr.get('n', len(pts))),
                'dop_std': round(float(fr.get('dop_std', 0.0)), 3),
                'zone_state': zs,
                'power': {'curr': round(random.gauss(1.0, 0.03), 3),
                          'volt': round(random.gauss(220.0, 0.4), 2),
                          'src': 'sim'},
                'breaker': {'state': dict(S['breaker']),
                            'reason': dict(S['breaker_reason'])},
                'ev': {'active': S['ev_active'], 'type': S['ev_type'],
                       'types': list(S.get('ev_types') or []), 'rev': S.get('ev_rev', 0),
                       'items': dict(S.get('ev_items') or {}),
                       'sev': S['ev_sev'], 'conf': S['ev_conf'],
                       'zone': S['ev_zone'], 'id': S['ev_id'],
                       'ts': S['ev_ts'], 'evidence': S['ev_evidence'],
                       'gates': S['ev_gates'], 'rejected': S['ev_rejected']},
                'cfg': {'N_WARMUP': 150, 'SCAN_SEC': 12.0,
                        'CEILING_H': CEILING_H, 'CURR_LIMIT': CURR_LIMIT,
                        'VOLT_MIN': VOLT_MIN},
            }
            if full:
                base['cz'] = list(S['cz_h'])
                base['ds'] = list(S['ds_h'])
                base['sc'] = list(S['sc_h'])
                base['logs'] = list(S['logs'])
                base['incidents'] = list(S['incidents'])
        raw = _pack(base, pts)
        for addr in _targets():
            try:
                sk.sendto(raw, addr)
            except OSError:
                pass


# ══════════════════════════════════════════════════════════════════════
# 재생 시나리오
# ══════════════════════════════════════════════════════════════════════
def _evidence_from(frames):
    """프레임 궤적에서 '측정된 것만' 뽑아 evidence 를 만든다.

    ⚠ 없는 값은 만들지 않는다. ae_score/ae_thr 은 학습된 baseline 이 있어야
      나오는 값이라 재생에서는 넣지 않는다 → 화면의 '이상도' 는 '—' 로 뜬다.
      숫자가 비는 건 정직한 상태이고, 지어낸 숫자는 판단 근거를 오염시킨다.
    """
    hs = [f.get('height', CEILING_H - f.get('cy', CEILING_H)) for f in frames]
    ds = [f.get('dop_std', 0.0) for f in frames]
    xs = [f.get('cx', 0.0) for f in frames]
    zs = [f.get('cz', 0.0) for f in frames]
    half = max(1, len(ds) // 2)
    first = sum(ds[:half]) / half
    last = sum(ds[half:]) / max(1, len(ds) - half)
    horiz = max(max(xs) - min(xs), max(zs) - min(zs))
    return {
        'height_start': round(hs[0], 3), 'height_end': round(hs[-1], 3),
        'h_drop': round(max(hs) - min(hs), 3),
        'horiz_range': round(horiz, 3),
        'dopstd_max': round(max(ds), 3),
        'ds_first': round(first, 3), 'ds_last': round(last, 3),
        'impulse_ratio': round(max(ds) / max(first, 1e-3), 2),
        'n_mean': round(sum(f.get('n', 0) for f in frames) / len(frames), 1),
    }


def _gates_from(ev):
    """판단 근거 표. ⚠ 임계값은 jetson_sender.classify 의 실측 튜닝값과 같은 수다.
    여기서 '판정' 을 하는 게 아니라, 재생 중인 데이터가 그 게이트를 통과하는지
    화면에 그대로 보여 주는 것뿐이다."""
    g = {
        'impulse':  (ev['impulse_ratio'], 2.2, '>=', '비율'),
        'h_drop':   (ev['h_drop'], 0.43, '>=', 'm'),
        'horiz':    (ev['horiz_range'], 0.6, '>=', 'm'),
        'ds_last':  (ev['ds_last'], 1.0, '<=', 'm/s'),
    }
    out = {}
    for k, (v, thr, cmp_, unit) in g.items():
        out[k] = {'value': v, 'thr': thr, 'cmp': cmp_, 'unit': unit,
                  'pass': (v >= thr) if cmp_ == '>=' else (v <= thr)}
    return out


def _fire(et, frames, label):
    ev = _evidence_from(frames)
    gates = _gates_from(ev) if et == 'fall_detected' else None
    npass = sum(1 for d in (gates or {}).values() if d['pass'])
    conf = round(0.55 + 0.10 * npass, 2) if gates else 0.80
    sev = EVENT_SEV.get(et, 'critical')
    with _lock:
        S.update({'ev_active': True, 'ev_type': et, 'ev_sev': sev,
                  'ev_types': [et], 'ev_rev': 1,
                  'ev_conf': conf, 'ev_zone': RADAR_ZONE,
                  'ev_id': S['ev_id'] + 1, 'ev_ts': time.time(),
                  'ev_evidence': ev, 'ev_gates': gates, 'ev_rejected': []})
        # 자동 차단은 전기·협착 critical에만 적용한다.
        if et in AUTO_TRIP_EVENTS and sev == 'critical' and S['breaker'][RADAR_ZONE] == 'ON':
            S['breaker'][RADAR_ZONE] = 'TRIPPED'
            S['breaker_reason'][RADAR_ZONE] = et
        S['incidents'].append({'type': et, 'zone': RADAR_ZONE,
                               'detected': time.strftime('%H:%M:%S'),
                               'resolved': None})
    log(f'ALERT Zone {RADAR_ZONE}: {et} [{sev}] '
        f'(재생 label={label}, conf={conf:.0%})'
        + (f' / BREAKER TRIP Zone {RADAR_ZONE}'
           if et in AUTO_TRIP_EVENTS and sev == 'critical' else ''))


def _clear():
    with _lock:
        if not S['ev_active']:
            return
        S.update({'ev_active': False, 'ev_type': None, 'ev_sev': 'normal',
                  'ev_conf': 0.0, 'ev_evidence': None, 'ev_gates': None,
                  'pre_alert': ''})
        for i in reversed(S['incidents']):
            if i.get('resolved') is None:
                i['resolved'] = time.strftime('%H:%M:%S')
                break
        tz = [z for z, v in S['breaker'].items() if v != 'ON']
    log(f'RESOLVED Zone {RADAR_ZONE} — 차단 유지 {tz}, 재투입은 수동')


def play_event(e, speed, src):
    """이벤트 하나를 프레임 단위로 흘린다. 마지막 프레임에서 경보를 낸다."""
    frames = e['frames']
    et = LABEL_EVENT.get(e['label'])
    with _lock:
        S['label'] = e['label']
        S['src'] = src
    for i, fr in enumerate(frames):
        with _lock:
            S['frame'] = fr
            h = fr.get('height', CEILING_H - fr.get('cy', CEILING_H))
            S['cz_h'].append(round(float(h), 3))
            S['ds_h'].append(round(float(fr.get('dop_std', 0.0)), 3))
            S['sc_h'].append(0.0)
        time.sleep(1.0 / (SEND_HZ * speed))
    if et == 'stationary_anomaly':
        # ⚠ 젯슨은 과전류 차단 후 정지 3초(STAT_PRE_SEC)부터 5초(STAT_CRIT_SEC)까지
        #   PRE-ALERT 를 흘린 뒤에야 경보를 latch 한다. 재생기가 그 구간을
        #   건너뛰면 화면의 사전경보 표시를 영영 검증할 수 없다.
        #   → 같은 문자열 포맷으로 카운트다운을 재생한다(압축 배속 적용).
        t0 = time.time()
        total = PRE_SEC / speed
        while True:
            el = time.time() - t0
            if el >= total:
                break
            dwell = int(10 + (30 - 10) * (el / total))
            with _lock:
                S['pre_alert'] = (f'PRE-ALERT  Zone {RADAR_ZONE}: '
                                  f'no-motion {dwell}s  --  MOVE to cancel  '
                                  f'({30 - dwell}s to CRITICAL)')
                if CMD_RESOLVE in S['req']:
                    S['req'].discard(CMD_RESOLVE)
            time.sleep(0.1)
        with _lock:
            S['pre_alert'] = ''
    if et:
        _fire(et, frames, e['label'])
        # ⚠⚠ 자동 해제는 없다. 젯슨도 CMD_RESOLVE 를 받아야만 푼다.
        #   (v1 재생기는 12초 뒤 스스로 풀었다 — 근무자가 아무것도 안 했는데
        #    화면이 정상으로 돌아가는 건 이 시스템이 해서는 안 되는 일이다.
        #    ISA-18.2, 그리고 console_ui 의 '자동 해제는 없다' 원칙과도 어긋난다)
        #   --auto-resolve N 을 준 경우에만 N초 뒤 푼다(무인 테스트용).
        t_end = (time.time() + AUTO_RESOLVE_SEC) if AUTO_RESOLVE_SEC else None
        while True:
            with _lock:
                if CMD_RESOLVE in S['req']:
                    S['req'].discard(CMD_RESOLVE)
                    log('상황 종료 수신 — 다음 재생으로')
                    break
            if t_end and time.time() >= t_end:
                log(f'--auto-resolve {AUTO_RESOLVE_SEC:g}s 경과 — 자동 해제')
                break
            time.sleep(0.1)
        _clear()
    time.sleep(GAP_SEC / speed)


def scenario(path, seq, speed, once, seed):
    events = load_events(path)
    if not events:
        print(f'[REPLAY] {path} 에 재생 가능한(pts 보유) 이벤트가 없습니다.')
        return
    rnd = random.Random(seed)
    by = {}
    for e in events:
        by.setdefault(e['label'], []).append(e)
    src = os.path.basename(path)
    n, summary = len(events), {k: len(v) for k, v in by.items()}
    log(f'{src} 적재: {n}건 {summary}')
    log(f'재생 순서: {" → ".join(seq)}  (속도 x{speed:g})')
    missing = [s for s in seq if s not in by]
    if missing:
        log(f'⚠ 이 파일에 없는 라벨: {missing} — 건너뜁니다')
    while True:
        for lab in seq:
            pool = by.get(lab)
            if not pool:
                continue
            e = rnd.choice(pool)
            log(f'▶ {lab} 재생 ({len(e["frames"])}프레임 · person {e["person"]} '
                f'· {e["ts"]})')
            play_event(e, speed, src)
        if once:
            log('한 바퀴 완료 — 종료')
            return


def main():
    ap = argparse.ArgumentParser(
        description='실측 jsonl 을 젯슨인 척 재생한다 (console_ui.py --live 127.0.0.1)')
    ap.add_argument('--file', default=DEFAULT_FILE, help='재생할 jsonl')
    ap.add_argument('--seq', default='normal,fall,still,vib',
                    help='재생 순서 (쉼표 구분)')
    ap.add_argument('--fast', action='store_true', help='2배속')
    ap.add_argument('--speed', type=float, default=None, help='배속 직접 지정')
    ap.add_argument('--once', action='store_true', help='한 바퀴만')
    ap.add_argument('--seed', type=int, default=None, help='샘플 선택 고정')
    ap.add_argument('--list', action='store_true', help='파일별 재생 가능 건수')
    ap.add_argument('--auto-resolve', type=float, default=0.0, metavar='초',
                    help='경보를 N초 뒤 자동 해제 (무인 테스트용. 기본은 무한 대기)')
    a = ap.parse_args()
    global AUTO_RESOLVE_SEC
    AUTO_RESOLVE_SEC = a.auto_resolve

    if a.list:
        cmd_list()
        return
    if not os.path.exists(a.file):
        print(f'파일 없음: {a.file}\n  --list 로 사용 가능한 파일을 보세요.')
        sys.exit(1)

    speed = a.speed if a.speed else (2.0 if a.fast else 1.0)
    seq = [s.strip() for s in a.seq.split(',') if s.strip()]

    print('=' * 66)
    print('  Radar-Guard | 실측 데이터 재생기 (판정하지 않음 · 라벨을 재생)')
    print('=' * 66)
    print(f'  파일    : {a.file}')
    print(f'  구역    : {RADAR_ZONE} {ZONE_KO.get(RADAR_ZONE, "")}')
    print(f'  UDP OUT : *:{DATA_PORT}   UDP IN : 0.0.0.0:{CTRL_PORT}   {SEND_HZ}Hz')
    print('  노트북  : python console_ui.py --live 127.0.0.1')
    print('  경보 해제: ' + (f'{AUTO_RESOLVE_SEC:g}초 뒤 자동'
                              if AUTO_RESOLVE_SEC else
                              '관제 화면에서 [상황 종료] 를 눌러야 풀림 (자동 해제 없음)'))
    print('=' * 66)
    threading.Thread(target=control_listener, daemon=True).start()
    threading.Thread(target=sender_loop, daemon=True).start()
    try:
        scenario(a.file, seq, speed, a.once, a.seed)
    except KeyboardInterrupt:
        print('\n[REPLAY] 종료')


if __name__ == '__main__':
    main()
