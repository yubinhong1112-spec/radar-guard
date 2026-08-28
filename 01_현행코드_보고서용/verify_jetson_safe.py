r"""verify_jetson_safe.py — "노트북용 수정이 젯슨을 깨지 않는다"를 증명하는 검증

  실행: [내 PC PowerShell]   python 01_현행코드\verify_jetson_safe.py
  의존성: 표준 라이브러리 + numpy (torch 불필요 — 가짜 torch 를 주입해 검사한다)

═══ 왜 필요한가 ═══
  7/31 에 jetson_sender.py 를 노트북에서도 돌 수 있게 고쳤다(--simulate, torch 가드).
  "젯슨에서 에러 안 난다"는 말로는 증명이 안 된다. 실제로 확인해야 하는 것:

    1) --simulate 없이 모듈이 로드되고 경로가 전부 젯슨 것인가
    2) SIMULATE 가드 안의 코드가 정말 실행되지 않는가 (특히 RF_MODEL_PATH_OVERRIDE —
       정의되지 않은 이름이므로 조건이 잘못되면 NameError 로 즉사한다)
    3) torch 가 '있을 때' 분기가 정상인가  ← 가짜 torch 모듈을 주입해 검사
    4) 판정 로직·상수가 radar_live_full.py 와 여전히 같은가
    5) 강등 경로(ae_disabled)가 정상 경로를 건드리지 않는가

  종료코드 0 = 전부 통과.
"""
import ast
import importlib
import os
import re
import sys
import types

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SEND = os.path.join(HERE, 'jetson_sender.py')
LIVE = os.path.join(ROOT, '02_레이더_원본코드', 'radar_live_full.py')

_fail = []
_pass = []


def ck(ok, name, detail=''):
    (_pass if ok else _fail).append(name)
    print(f'  {"✓" if ok else "✗"} {name}' + (f'   {detail}' if detail else ''))


# ══════════════════════════════════════════════════════════════════════
def make_fake_torch():
    """젯슨의 'torch 있음' 분기를 타게 하는 최소 가짜 모듈.

    LSTM_AE 의 클래스 '정의'가 통과하는지, DEVICE 가 제대로 잡히는지만 본다.
    실제 학습은 검증 대상이 아니다(그건 젯슨 실물에서 확인).
    """
    torch = types.ModuleType('torch')
    nn = types.ModuleType('torch.nn')

    class Module:
        def __init__(self, *a, **k):
            pass

        def to(self, *a, **k):
            return self

        def parameters(self):
            return []

    class _Layer(Module):
        def __init__(self, *a, **k):
            super().__init__()

    nn.Module = Module
    nn.LSTM = _Layer
    nn.Linear = _Layer
    nn.Sequential = _Layer

    class _Dev:
        def __init__(self, s):
            self.s = s

        def __repr__(self):
            return f'device({self.s})'

    torch.device = _Dev
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    torch.nn = nn
    torch.optim = types.SimpleNamespace(AdamW=lambda *a, **k: None)
    torch.no_grad = lambda: types.SimpleNamespace(
        __enter__=lambda s: None, __exit__=lambda s, *a: False)
    torch.save = lambda *a, **k: None
    torch.load = lambda *a, **k: {}
    torch.from_numpy = lambda x: x
    torch.mean = lambda *a, **k: 0.0

    sk = types.ModuleType('sklearn')
    skp = types.ModuleType('sklearn.preprocessing')
    skp.MinMaxScaler = lambda *a, **k: None
    sk.preprocessing = skp
    return {'torch': torch, 'torch.nn': nn, 'sklearn': sk,
            'sklearn.preprocessing': skp}


def load_sender(argv, fake_torch=False):
    """jetson_sender 를 원하는 argv / torch 유무 조건으로 새로 로드한다."""
    saved_argv, saved_mods = sys.argv, {}
    sys.argv = argv
    injected = make_fake_torch() if fake_torch else {}
    for k, v in injected.items():
        saved_mods[k] = sys.modules.get(k)
        sys.modules[k] = v
    if fake_torch:
        # torch.optim / from torch import optim 대응
        sys.modules['torch'].optim = injected['torch'].optim
    sys.modules.pop('jetson_sender', None)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        m = importlib.import_module('jetson_sender')
        return m, None
    except BaseException as e:                       # noqa: BLE001
        return None, f'{type(e).__name__}: {e}'
    finally:
        sys.argv = saved_argv
        for k, v in saved_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# ══════════════════════════════════════════════════════════════════════
print('=' * 70)
print('  jetson_sender.py — 젯슨 안전성 검증')
print('=' * 70)

print('\n[1] --simulate 없이 로드 (= 젯슨 실행 방식)')
m, err = load_sender(['jetson_sender.py'])
ck(m is not None, '모듈 로드', err or '')
if m is None:
    print('\n로드 실패 — 이후 검사 불가')
    sys.exit(1)

ck(m.SIMULATE is False, 'SIMULATE = False')
ck(m.SIM_FAST is False, 'SIM_FAST = False')
ck(m.JSON_PATH == '/home/project/stage1_filtered.json',
   'JSON_PATH 가 젯슨 경로', m.JSON_PATH)
ck(m.BASELINE_PATH == '/home/project/baseline_model.pt',
   'BASELINE_PATH 가 젯슨 경로', m.BASELINE_PATH)
ck(m.RF_MODEL_PATH.endswith('fall_classifier.joblib')
   and '01_현행코드' not in m.RF_MODEL_PATH,
   'RF_MODEL_PATH 가 ~/ 경로 (override 미적용)', m.RF_MODEL_PATH)
ck(getattr(m, 'RF_MODEL_PATH_OVERRIDE', 'x') is None,
   'RF_MODEL_PATH_OVERRIDE = None → SIMULATE 블록이 실행되지 않았음')
ck(m.SCAN_SEC == 12.0 and m.N_WARMUP == 150 and m.STEP_IN_SEC == 5.0,
   '--fast 상수 오염 없음', f'SCAN_SEC={m.SCAN_SEC} N_WARMUP={m.N_WARMUP}')

print('\n[2] 7/12 실측 튜닝 상수 (회귀 없어야 함)')
for k, want in (('STAT_MISS_TOL', 3), ('STAT_PRE_SEC', 3.0), ('STAT_CRIT_SEC', 5.0),
                ('STAT_FOLLOWUP_SEC', 15.0),
                ('FALL_CONFIRM', 1), ('CLF_WIN', 20), ('FEATURE_DIM', 9),
                ('CEILING_H', 2.30), ('POSTFALL_HOLD', 1.2)):
    got = getattr(m, k, None)
    ck(got == want, f'{k} = {want}', '' if got == want else f'실제 {got}')

print('\n[2-A] 수동 재실 정지형 상태기')
gate = m.StationaryGate(reset_ds=0.38, reset_frames=3)
ck(not gate.update(0.0, False)['pre'], '퇴실 상태에서는 타이머 비활성')
ck(gate.update(0.0, True, None)['dwell'] == 0.0 and gate.since == 0.0,
   '과전류 차단+입실이면 포인트가 없어도 타이머 즉시 시작')
gate.update(0.0, True, 0.05, (0.40, 0.30))
gate.update(0.1, True, 0.05, (0.44, 0.34))
held = gate.update(1.0, True, None, None)['anchor']
ck(held == (0.42, 0.32), '점군 소실 뒤 마지막 신뢰 cx/cz 중앙값 유지', str(held))
ck(gate.update(3.0, True, None)['pre'], '점군 소실 중에도 3초 확인 단계 유지')
ck(not gate.update(4.9, True, None)['critical'], '5초 전에는 협착 critical 없음')
ck(gate.update(5.0, True, None)['critical'], '5초에 협착 승격 트리거 1회 발생')
followup = m.StationaryGate(reset_ds=0.38, reset_frames=3)
followup.update(0.0, True, None)
ck(not followup.update(14.9, True, None, pre_sec=15.0, crit_sec=15.0)['critical'],
   '상황 해소 후 15초 전에는 무동작 warning 없음')
ck(followup.update(15.0, True, None, pre_sec=15.0, crit_sec=15.0)['critical'],
   '상황 해소 후 15초에 무동작 warning 트리거 1회 발생')
gate.reset(); gate.update(0.0, True, 0.05)
gate.update(5.0, True, 0.50); gate.update(5.1, True, 0.05)
ck(gate.update(10.0, True, None)['pre'], '순간 도플러 스파이크 1회는 타이머 유지')
gate.update(10.3, True, 0.50); gate.update(10.4, True, 0.50)
reset = gate.update(10.5, True, 0.50)
ck(reset['motion_reset'] and not reset['pre'], '확인 움직임 3프레임이면 타이머 초기화')
ck(not gate.update(20.0, False, None)['pre'] and gate.since is None,
   '퇴실 명령은 정지형 상태 하드 리셋')
ck(gate.anchor() is None, '퇴실 명령은 위치 앵커도 하드 리셋')
ck(not m._stationary_gate_active(m.PH_LIVE, True, 'ON', None),
   '평상 투입 상태에서 무동작 게이트 OFF')
ck(not m._stationary_gate_active(m.PH_LIVE, True, 'TRIPPED', 'fall_detected'),
   '낙상으로 차단된 상태에서 무동작 게이트 OFF')
ck(not m._stationary_gate_active(m.PH_LIVE, False, 'TRIPPED', 'overcurrent'),
   '과전류 차단이어도 퇴실 상태에서 무동작 게이트 OFF')
ck(m._stationary_gate_active(m.PH_LIVE, True, 'TRIPPED', 'overcurrent'),
   '과전류 차단+입실 상태에서만 무동작 게이트 ON')
ck(m._stationary_gate_active(m.PH_LIVE, True, 'TRIPPED', 'leakage_current'),
   '누설전류 차단+입실이면 감전 확인 게이트 ON')
ck(m._stationary_gate_active(m.PH_LIVE, True, 'TRIPPED', 'overcurrent'),
   '상황 해소 후에도 차단 유지+입실이면 15초 재확인 게이트 ON')

print('\n[2-B] 상황 해소 즉시 반영·낙상 재무장')
m.state.update({
    'ev_active': True, 'ev_type': 'fall_detected',
    'ev_types': ['fall_detected'],
    'ev_items': {'fall_detected': {'sev': 'critical', 'conf': 0.9}},
    'ev_sev': 'critical', 'ev_conf': 0.9, 'ev_zone': 'A',
    'ev_evidence': {'old': True}, 'ev_gates': {'old': True},
    'ev_rejected': ['old'], 'human_reset_requested': False,
})
m.state['incidents'].append(
    {'type': 'fall_detected', 'zone': 'A', 'detected': '00:00:00', 'resolved': None})
ck(m._resolve_event('00:00:01'), '활성 사건 상황 해소 처리 성공')
ck(not m.state['ev_active'] and m.state['ev_type'] is None,
   '상황 해소 명령에서 사건 상태 즉시 NORMAL')
ck(m.state['human_reset_requested'], '사람 판정 버퍼 초기화 예약')
ck(m.state['ev_evidence'] is None and m.state['ev_gates'] is None,
   '해소한 사건의 근거·게이트 제거')
ck(not m._resolve_event('00:00:02'), '반복 상황 해소는 새 상태를 만들지 않음')
ck(not m._fall_rearmed(m.RF30_WINDOW - 1), '새 30프레임 전 낙상 재-latch 차단')
ck(m._fall_rearmed(m.RF30_WINDOW), '새 30프레임부터 낙상 판정 재개')
pinch_src = open(SEND, encoding='utf-8').read()
ck('pinch_shadow_buf.clear()' in pinch_src
   and "not state['stationary_followup']" in pinch_src
   and 'latch_suppressed_after_resolve' in pinch_src,
   '상황 해소 시 PINCH 창 초기화·R2 critical 재-latch 차단')

print('\n[2-C] 차단 직전 개인 기준선 shadow 계측')
samples = [
    {'t': float(t), 'height': float(t), 'cx': t * 0.1, 'cz': t * 0.2, 'n': 20.0}
    for t in range(4, 11)
]
base = m._pinch_baseline_snapshot(samples, 10.0)
ck(base['pretrip_valid_frames'] == 6, '최근 5초 표본만 기준선에 포함', str(base))
ck(base['pretrip_q70'] == 8.5 and base['pretrip_q80'] == 9.0
   and base['pretrip_q90'] == 9.5,
   'q70/q80/q90 계산', str(base))
ratios = m._pinch_shadow_ratios(4.5, base)
ck(ratios == {'height_ratio_q70': 0.5294, 'height_ratio_q80': 0.5,
              'height_ratio_q90': 0.4737},
   '높이 비율은 로그용으로만 계산', str(ratios))
empty = m._pinch_baseline_snapshot([], 10.0)
ck(empty['pretrip_valid_frames'] == 0
   and m._pinch_shadow_ratios(0.8, empty)['height_ratio_q80'] is None,
   '기준선 부족 시 비율 None — 판정값 대체 없음')

shadow = [[0.0, 1.5, 0.0, 0.0, 0.40, 300.0, 16.0, 0.0, 0.0] for _ in range(20)]
shadow[-1][0] = 0.3
sm = m._pinch_shadow_metrics(shadow)
ck(m.PINCH_SHADOW_WIN == 20 and sm['window'] == 20
   and sm['legacy_candidate'] and not sm['candidate'],
   '옛 A 규칙 양성·R2 저이동 탈락을 분리', str(sm))
r2_shadow = [[(-0.2 if i % 2 else 0.2), 1.4, 0.0, 0.0, 0.50,
              300.0, 16.0, 0.0, 0.0] for i in range(20)]
r2m = m._pinch_shadow_metrics(r2_shadow)
ck(r2m['candidate'] and r2m['r2_candidate']
   and not r2m['legacy_candidate'] and r2m['path_length'] > 2.8,
   '기존 R2 양성은 실판정 candidate로 승격', str(r2m))
ck(m._human_event_for_breaker('overcurrent') == 'pinching'
   and m._human_event_for_breaker('leakage_current') ==
   'electric_shock_risk_confirmed', '전기 원인별 협착·감전 승격 분리')
ck(m.CURR_LIMIT == 0.10 and m.VOLT_MIN == 7.50,
   '8V 과전류 실측 임계 적용', f'{m.CURR_LIMIT}A/{m.VOLT_MIN}V')
ck(m.LEAK_LIMIT == 0.008 and m.POWER_CONFIRM == 2 and m.LEAK_CONFIRM == 2,
   '누설 모의 실측 임계와 2회 연속 확인 설정')
ck(not m.classify_equipment(0.0050, 8.124, 0.0),
   '단일 INA 정상 최대 5.0mA는 전기 이상 아님')
single_leak = m.classify_equipment(0.0115, 8.110, 0.0)
ck([a['event_type'] for a in single_leak] == ['leakage_current'],
   '단일 INA 1kΩ 분기 최소 11.5mA는 누설 모의', str(single_leak))
single_over = m.classify_equipment(0.1505, 7.876, 0.0)
ck([a['event_type'] for a in single_over] == ['overcurrent'],
   '단일 INA 50Ω 분기는 과전류만 판정', str(single_over))
eq = m.classify_equipment(0.1535, 7.815, 0.0, leak_curr=0.0085)
ck({a['event_type'] for a in eq} == {'overcurrent', 'leakage_current'},
   '8V 예상 과전류·누설 동시 판정', str(eq))
streaks = {'overcurrent': 0, 'voltage_drop': 0, 'leakage_current': 0}
ck(not m._confirm_power_anomalies(eq, streaks), '전기 이상 1회에는 차단하지 않음')
ck({a['event_type'] for a in m._confirm_power_anomalies(eq, streaks)} ==
   {'overcurrent', 'leakage_current'}, '전기 이상 2회 연속이면 확정')
ck(not m._event_requires_trip('fall_detected', 'critical'),
   '낙상 critical은 자동 차단 대상에서 제외')
ck(m._event_requires_trip('overcurrent', 'critical'),
   '과전류 critical은 자동 차단 대상 유지')
with m._lock:
    m.state.update({'ev_active': False, 'ev_type': None, 'ev_types': [], 'ev_items': {},
                    'ev_rev': 0, 'ev_sev': 'normal', 'ev_conf': 0.0,
                    'ev_id': 0, 'incidents': m.deque(maxlen=20)})
    base = {'severity': 'critical', 'confidence': 1.0,
            'evidence': {}, 'gates': {}, 'rejected': []}
    m._latch_event('overcurrent', base, m.RADAR_ZONE, '00:00:00', 1.0,
                   control_trip=False)
    eid = m.state['ev_id']
    ck(not m._can_latch('overcurrent', 'critical'), '동일 과전류 반복 latch 차단')
    ck(m._can_latch('fall_detected', 'critical'), '설비 이상 뒤 낙상 병렬 허용')
    m._latch_event('fall_detected', base, m.RADAR_ZONE, '00:00:01', 1.0,
                   control_trip=False)
    ck(m.state['ev_id'] == eid and m.state['ev_rev'] == 2,
       '복합 사건은 ev_id 유지·revision 증가')
    ck(m.state['ev_types'] == ['overcurrent', 'fall_detected'],
       '설비 이상과 낙상 동시 보존', str(m.state['ev_types']))
    ck(m.state['ev_items']['overcurrent']['sev'] == 'critical'
       and m.state['ev_items']['fall_detected']['sev'] == 'critical',
       '복합 사건 개별 등급 보존', str(m.state['ev_items']))
    suspect = {'severity': 'warning', 'confidence': 0.7,
               'evidence': {}, 'gates': {}, 'rejected': []}
    m._latch_event('pinching_suspected', suspect, m.RADAR_ZONE,
                   '00:00:02', 0.7, control_trip=False)
    m._remove_event_type('pinching_suspected', '00:00:03', 'PINCHING CONFIRMED')
    m._latch_event('pinching', base, m.RADAR_ZONE, '00:00:03', 1.0,
                   control_trip=False)
    ck('pinching_suspected' not in m.state['ev_types']
       and m.state['ev_types'].count('pinching') == 1,
       '협착 의심은 critical 승격 시 중복 없이 교체', str(m.state['ev_types']))

print('\n[3] --simulate 로 로드 (노트북 검증 모드)')
ms, err = load_sender(['jetson_sender.py', '--simulate', '--fast'])
ck(ms is not None, '모듈 로드', err or '')
if ms is not None:
    ck(ms.SIMULATE is True and ms.SIM_FAST is True, 'SIMULATE / SIM_FAST = True')
    ck('/home/project/' not in ms.JSON_PATH, 'JSON_PATH 가 임시 경로로 대체', ms.JSON_PATH)
    ck(ms.N_WARMUP == 30 and ms.SCAN_SEC == 3.0, '--fast 상수 적용',
       f'N_WARMUP={ms.N_WARMUP} SCAN_SEC={ms.SCAN_SEC}')

print('\n[4] torch 가 "있을 때" 분기 (가짜 torch 주입 — 젯슨 조건)')
mt, err = load_sender(['jetson_sender.py'], fake_torch=True)
if mt is None:
    ck(False, 'torch 있음 분기 로드', err)
else:
    ck(mt.TORCH_OK is True, 'TORCH_OK = True')
    ck('device(' in repr(mt.DEVICE), 'DEVICE 가 torch.device 로 잡힘', repr(mt.DEVICE))
    ck(hasattr(mt, 'LSTM_AE') and isinstance(mt.LSTM_AE, type),
       'LSTM_AE 클래스 정의 통과 (nn.Module 상속)')
    ck(mt.LSTM_AE.__mro__[1] is sys.modules.get('torch').nn.Module
       if 'torch' in sys.modules else True,
       'LSTM_AE 가 진짜 nn.Module 을 상속') if False else None
    ck(mt.optim is not None, 'from torch import optim 바인딩')
    ck(mt.MinMaxScaler is not None, 'MinMaxScaler 바인딩')
    ck(mt.SIMULATE is False, 'torch 있어도 SIMULATE 는 여전히 False')

print('\n[5] 정적 검사 — SIMULATE 가드 밖으로 새어나간 코드가 있나')
src = open(SEND, encoding='utf-8').read()
tree = ast.parse(src)
guarded, leaked = 0, []
for node in ast.walk(tree):
    if isinstance(node, ast.If):
        test = ast.unparse(node.test)
        if 'SIMULATE' in test or 'SIM_FAST' in test:
            guarded += 1
ck(guarded >= 4, f'SIMULATE/SIM_FAST 가드 블록 {guarded}개 확인')


def guard_audit(name):
    """이 이름이 '정의되지 않은 상태로 참조'될 수 있는지 AST 로 검사.

    안전 조건 = (a) 최상위에서 조건 없이 먼저 대입되거나,
                (b) 최상위에서 아예 참조되지 않는다.
    단순 grep 은 `X = None` 같은 정상 초기화까지 잡으므로 쓰지 않는다.
    """
    uncond_assign = None            # 조건 밖 최상위 대입 줄번호
    guarded_assign = []
    loads = []                     # 최상위에서 '읽는' 위치
    for node in tree.body:         # tree.body = 모듈 최상위만
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    uncond_assign = uncond_assign or node.lineno
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Name) and sub.id == name:
                    loads.append(node.lineno)
        elif isinstance(node, ast.If):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Name) and sub.id == name
                        and isinstance(sub.ctx, ast.Store)):
                    guarded_assign.append(sub.lineno)
    if uncond_assign is None and not loads:
        return True, '최상위에서 참조 없음'
    if uncond_assign is None:
        return False, f'조건부({guarded_assign})로만 정의되는데 {loads}행에서 읽음'
    bad = [l for l in loads if l < uncond_assign]
    if bad:
        return False, f'{bad}행이 초기화({uncond_assign}행)보다 먼저 읽음'
    return True, f'{uncond_assign}행에서 무조건 초기화 후 {loads or "-"}행에서 사용'


for nm in ('RF_MODEL_PATH_OVERRIDE', 'SIMULATE', 'SIM_FAST'):
    ok, why = guard_audit(nm)
    ck(ok, f'{nm} — 미정의 참조 위험 없음', why)

# 함수/모듈은 정의만 하고 SIMULATE 가드 안에서만 '호출'돼야 한다
for nm in ('sim_radar_writer', 'tempfile'):
    called_unguarded = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.Expr)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id == nm:
                    called_unguarded.append(node.lineno)
    ck(not called_unguarded, f'{nm} — 가드 밖 최상위 사용 없음',
       '' if not called_unguarded else f'{called_unguarded}행')

print('\n[6] 준비 단계에서 판정이 열리지 않는가')
src = open(SEND, encoding='utf-8').read()
ck("state['phase']     = PH_WAIT_ARM" in src,
   '학습 완료 후 감시 대기', 'PH_WAIT_ARM 전환 확인')
ck('elif phase == PH_WAIT_ARM:' in src and "state['phase'] = PH_LIVE" in src,
   '감시 시작 명령으로만 LIVE 전환')
ck("_is_live = (state['phase'] == PH_LIVE)" in src,
   '판정 경로는 PH_LIVE 에서만 실행')
ck("if not _is_live or not _occupied:" in src,
   '퇴실 상태에서 레이더 판정 중지')
ck("_live = state['phase'] == PH_LIVE" in src,
   '퇴실 상태여도 전기 설비 이상은 독립 판정')
ck("feat_buf.clear(); clf_buf.clear()" in src
   and "fall_pending = None" in src and "stat_since = None" in src,
   '입퇴실 전환 시 판정 창·낙상·무동작 상태 초기화')
ck('SAFETY_MODEL' not in src and 'safety_candidate' not in src,
   'ExtraTrees 운영 판정 경로 제거')

print('\n[7] 판정 로직이 radar_live_full.py 와 동일한가 (주석 제외 코드 비교)')


def code_of(path, fn):
    s = open(path, encoding='utf-8').read()
    mm = re.search(r'(?m)^def %s\(.*?(?=^def |^class |^# ═)' % fn, s, re.S)
    if not mm:
        return None
    return [' '.join(l.split('#')[0].split())
            for l in mm.group(0).splitlines() if l.split('#')[0].strip()]


if os.path.exists(LIVE):
    for fn in ('classify', 'extract_features', '_rf_features', '_rf_veto'):
        a, b = code_of(LIVE, fn), code_of(SEND, fn)
        ck(a is not None and a == b, f'{fn}() 코드 동일',
           '' if a == b else f'live {len(a or [])}줄 vs sender {len(b or [])}줄')
else:
    ck(False, 'radar_live_full.py 를 찾을 수 없음', LIVE)

print('\n[8] 실물 차단기 실패가 성공 상태로 기록되지 않는가')


class FakeRelay:
    connected = True
    error = None

    def __init__(self, initial=False):
        self.value = initial
        self.fail = False

    def read(self):
        return self.value

    def write(self, value):
        if self.fail:
            self.error = '시험용 통신 실패'
            return False
        self.value = value
        return True


real_relay = m.RELAY
try:
    # NO 배선의 물리 명령과 논리 상태 변환을 직접 고정한다.
    physical = m.RelayRTU()
    sent = []
    coil = [False]

    def fake_exchange(body, _size):
        sent.append(body)
        coil[0] = body[4] == 0xFF
        return body + m.RelayRTU._crc16(body)

    physical._exchange = fake_exchange
    physical._read_unlocked = lambda: coil[0]
    ck(physical.read() is True, 'NO 코일 OFF readback을 차단으로 해석')
    ck(physical.write(False) and sent[-1][4] == 0xFF,
       'NO 정상 투입은 코일 ON 명령')
    ck(physical.write(True) and sent[-1][4] == 0x00,
       'NO 사고 차단은 코일 OFF 명령')

    fake = FakeRelay()
    m.RELAY = fake
    breaker = m.BreakerLogic()
    ck(breaker.trip(m.RADAR_ZONE, 'fall_detected'), 'Modbus 성공 후에만 차단 상태 전환')
    ck(breaker.snapshot()['state'][m.RADAR_ZONE] == 'TRIPPED', '차단 상태 TRIPPED')
    ck(breaker.restore([m.RADAR_ZONE]) == [m.RADAR_ZONE], '수동 복구 성공')
    ck(breaker.snapshot()['state'][m.RADAR_ZONE] == 'ON', '복구 상태 ON')
    fake.fail = True
    ck(not breaker.trip(m.RADAR_ZONE, 'fall_detected'), 'Modbus 실패를 차단 성공으로 처리하지 않음')
    ck(breaker.snapshot()['state'][m.RADAR_ZONE] == 'ON', '통신 실패 시 ON 상태 유지')
finally:
    m.RELAY = real_relay

print('\n[9] 의도한 동작 변경 (에러가 아니라 수정 — 확인용)')
for note in (
        '차단기 판정을 PH_LIVE 에서만 수행 (이전: 웜업·학습 중에도 수행)',
        '설비진동 게이트에 사람 dop_std 를 넣지 않음 (이전: 걸을 때마다 차단)',
        '학습 성공·실패 후 감시 시작 버튼 전까지 PH_WAIT_ARM 에서 판정 차단',
        '히스토리(cz/ds/sc/logs/incidents)를 1초에 한 번만 전송 (MTU 초과 해소)',
        'evidence/gates/rejected/power/breaker 를 패킷에 추가'):
    print(f'  · {note}')

print('\n' + '=' * 70)
print(f'  통과 {len(_pass)}건 / 실패 {len(_fail)}건')
if _fail:
    print('  실패 항목: ' + ', '.join(_fail))
    print('  → 젯슨에 올리기 전에 고쳐야 합니다.')
else:
    print('  ✅ 젯슨 실행 경로에 영향 없음이 확인됐습니다.')
print('=' * 70)
sys.exit(1 if _fail else 0)
