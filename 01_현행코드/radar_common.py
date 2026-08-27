"""radar_common.py — 젯슨/노트북 공용 상수·라벨 단일 소스

  배포 위치: [젯슨] ~/radar_common.py   +   [노트북] console_ui.py 와 같은 폴더
  의존성   : 표준 라이브러리만 (numpy·torch·Qt 전부 불필요)

═══ 이 파일이 존재하는 이유 ═══
  2026-07-31 확인 결과 jetson_sender.py 와 laptop_viewer.py 에
  CEILING_H · PH_* · EVENT_LABELS · HISTORY_LEN 이 각각 복붙돼 있었다.
  한쪽만 고치면 다른 쪽이 조용히 어긋난다 — 특히 CEILING_H 는 높이 계산의
  기준이라 어긋나면 화면의 모든 높이가 틀리는데 에러는 안 난다.
  → 양쪽이 import 하는 단일 소스로 분리한다.

═══ 무엇을 여기 두고 무엇을 두지 않는가 ═══
  둔다   : 양쪽이 '같아야만' 동작하는 것 (좌표 기준, phase 문자열, 이벤트 키,
           포트, 스키마 버전, 표시 라벨)
  안 둔다: 한쪽에서만 쓰는 판정 문턱 (STAT_MISS_TOL 등은 젯슨 전용 —
           노트북이 알 필요가 없고, 알면 "노트북도 판정한다"는 오해를 부른다)
"""

import os
import re

SCHEMA_VERSION = 2          # v1: ZMQ 설계안 / v2: UDP + evidence·gates·power 실전송

# ══════════════════════════════════════════════════════════════════════
# 1. 네트워크
# ══════════════════════════════════════════════════════════════════════
DATA_PORT = 5005            # 젯슨 → 노트북 (상태·이벤트·포인트)
CTRL_PORT = 5006            # 노트북 → 젯슨 (HELLO + 버튼 명령)
SEND_HZ = 10                # 송신 주기. 레이더가 10 Hz 이므로 맞춘다.
                            #   5 Hz 로 두면 노트북 PoseEstimator(n_frames=10) 의
                            #   누적 창이 1초가 아니라 2초가 되어 보행 중 형상이
                            #   궤적으로 뭉개진다.
MAX_UDP = 60000             # UDP 페이로드 상한 (_pack 이 이하로 솎아낸다)
MIN_PTS = 40                # 포인트 다운샘플 하한
CLIENT_TTL = 15.0           # HELLO 가 이 시간 넘게 없으면 클라이언트 제거
HELLO_SEC = 2.0             # 노트북 HELLO 주기
LINK_TIMEOUT = 3.0          # 이 시간 무수신이면 화면을 'stale' 로 전환

# 노트북 → 젯슨 명령어 (control_listener 가 해석)
CMD_HELLO = 'hello'
CMD_START = 'start'         # 빈방 스캔 + 베이스라인 수집 시작
CMD_TRAIN = 'train'         # LSTM-AE 학습 시작
CMD_RESET = 'reset'         # 베이스라인 초기화
CMD_RESOLVE = 'resolve'     # 경보 상황 종료 (사람이 확인함)
CMD_RESTORE = 'restore'     # 차단된 전원 재투입 (LOTO 확인 후)
CMD_ENTER = 'enter'         # 운영자 입실 확인
CMD_EXIT = 'exit'           # 운영자 퇴실 확인

# ══════════════════════════════════════════════════════════════════════
# 2. 물리 · 좌표계
# ══════════════════════════════════════════════════════════════════════
#  센서는 천장에 하방(nadir) 설치. 레이더 원점 = 센서.
#    y  = 센서로부터 아래로의 거리(range)  →  height = CEILING_H - y
#    x, z = 바닥평면(수평)
#  ⚠ CEILING_H 를 바꾸면 젯슨·노트북 양쪽을 같이 바꿔야 한다. 그래서 여기 있다.
CEILING_H = 2.30            # 센서 설치 높이 [m] (부스 스펙: 기둥 2.4m, 센서면 2.3m)
FRAME_INNER_HALF = 0.72     # 제작도: 전체 1.50m, 3030 기둥 안쪽 1.44m 정사각형
ENTRY_BAND = 0.18           # 외곽 출입 판정 밴드 폭 [m]
OCCUPANCY_CORE_HALF = FRAME_INNER_HALF - ENTRY_BAND  # 중앙 1.08m 정사각형
HISTORY_LEN = 120           # 그래프 히스토리 길이 (양쪽 동일해야 x축이 맞는다)

# ══════════════════════════════════════════════════════════════════════
# 3. Phase (문자열이 양쪽에서 정확히 일치해야 함)
# ══════════════════════════════════════════════════════════════════════
PH_READY = 'READY'          # 대기 — 사용자가 'Start' 누르길 기다림
PH_WARMUP = 'WARMUP'        # 빈방 스캔 → 정상 베이스라인 N프레임 수집
PH_WAIT_TRAIN = 'WAIT_TRAIN'  # 수집 완료 — 사용자가 'Train' 누르길 기다림
PH_TRAINING = 'TRAINING'    # LSTM-AE 학습 중 (~20-30초)
PH_WAIT_ARM = 'WAIT_ARM'    # 학습 완료 — 사용자가 감시 시작을 누르길 기다림
PH_LIVE = 'LIVE'            # 실시간 감시

PHASE_ORDER = [PH_READY, PH_WARMUP, PH_WAIT_TRAIN, PH_TRAINING, PH_WAIT_ARM, PH_LIVE]
PHASE_KO = {
    PH_READY:      '대기',
    PH_WARMUP:     '기준 수집',
    PH_WAIT_TRAIN: '학습 대기',
    PH_TRAINING:   '학습 중',
    PH_WAIT_ARM:   '감시 대기',
    PH_LIVE:       '감시 중',
}
# 준비 화면에서 각 단계에 근무자가 실제로 해야 할 행동. 화면에 이것만 띄운다.
PHASE_ACTION = {
    PH_READY:      '준비되면 아래 버튼을 누르세요',
    PH_WARMUP:     '빈 방 스캔 중 — 감지 구역 밖으로 나가 주세요',
    PH_WAIT_TRAIN: '구역 안에 서서 버튼을 누르세요',
    PH_TRAINING:   '학습이 끝날 때까지 감지 구역 밖에서 기다려 주세요',
    PH_WAIT_ARM:   '구역 밖으로 나온 뒤 감시를 시작하세요',
    PH_LIVE:       '감시가 시작됐습니다',
}

# ══════════════════════════════════════════════════════════════════════
# 4. 이벤트
# ══════════════════════════════════════════════════════════════════════
EVENT_LABELS = {            # 젯슨 콘솔 로그용 (영문 — 폰트 의존 없음)
    'fall_detected':       'FALL DETECTED',
    'fall_suspected':      'FALL SUSPECTED (RULE/RF DISAGREEMENT)',
    'stationary_anomaly':  'STATIONARY ANOMALY (VERIFY: SHOCK/ENTRAPMENT)',
    'electric_shock_risk': 'ELECTRIC SHOCK RISK',
    'electric_shock_risk_confirmed': 'ELECTRIC SHOCK CONFIRMED',
    'leakage_current':     'LEAKAGE CURRENT',
    'pinching_suspected':  'PINCHING SUSPECTED',
    'overcurrent':         'EQUIPMENT OVERCURRENT',
    'voltage_drop':        'EQUIPMENT VOLTAGE DROP',
    'pinching':            'PINCHING / ENTRAPMENT',
    'vibration_anomaly':   'VIBRATION ANOMALY',
}
EVENT_KO = {                # 노트북 관제 화면용
    'fall_detected':       '낙상',
    'fall_suspected':      '낙상 의심',
    'stationary_anomaly':  '장시간 무동작',
    'electric_shock_risk': '감전 위험',
    'electric_shock_risk_confirmed': '감전 발생',
    'leakage_current':     '설비 이상 · 누설전류',
    'pinching_suspected':  '협착 의심',
    'pinching':            '협착 · 끼임',
    'vibration_anomaly':   '설비 진동 이상',
    'overcurrent':         '설비 이상 · 과전류',
    'voltage_drop':        '설비 이상 · 전압 강하',
    'normal':              '정상',
}
# ══ 구역 · 장비 배치 ═══════════════════════════════════════════════════
#  [7/31 수정] 레이더는 실물 1대다. 그런데 화면은 A·B·C 를 모두 '투입' 으로 보여주고
#  있었고 EVENT_ZONE 은 낙상을 'C' 로 보고했다 — 정작 판정 게이트(DANGER_ZONES)는
#  'A' 하나뿐이다. 즉 A 에 있는 사람이 넘어지면 C 구역 경보가 떴다.
#  → 레이더 설치 구역을 단일 소스로 두고, 레이더 유래 이벤트는 전부 그 구역으로 보고한다.
#    미설치 구역은 숨기지 않고 '장비 미설치' 로 명시한다(과대표현 금지).
RADAR_ZONE = 'A'                       # 레이더 #1 설치 구역 (변전실)
ZONE_EQUIPPED = {'A': True, 'B': False, 'C': False}
ZONE_DEVICE = {
    'A': f'레이더 #1 · 천장 {CEILING_H:.2f} m',
    'B': '장비 미설치',
    'C': '장비 미설치',
}

EVENT_ZONE = {                         # 레이더·전기 유래 이벤트 → 레이더 설치 구역
    'fall_detected':       RADAR_ZONE,
    'fall_suspected':      RADAR_ZONE,
    'stationary_anomaly':  RADAR_ZONE,
    'electric_shock_risk': RADAR_ZONE,
    'electric_shock_risk_confirmed': RADAR_ZONE,
    'leakage_current':     RADAR_ZONE,
    'pinching_suspected':  RADAR_ZONE,
    'pinching':            RADAR_ZONE,
    'vibration_anomaly':   RADAR_ZONE,
}


def zone_equipped(z):
    return ZONE_EQUIPPED.get(z, False)


# ══ SOP 벡터DB 접속 ════════════════════════════════════════════════════
#  ⚠ [7/31 버그수정] 코드 전체가 'postgres:password' 를 하드코딩하고 있었지만
#    실제 컨테이너 계정은 admin 이다 (sop_doctor.py 확인). 즉 SOP 검색은
#    카테고리 이전에 **접속 단계부터** 실패하고 있었다.
#  → 하드코딩하지 않고 이 순서로 찾는다: 환경변수 → 컨테이너 env → 기본값.
PG_CONTAINER = 'radar-guard-db'
_pg_cache = None


def pg_conn_str(container=PG_CONTAINER):
    """SOP DB 접속 문자열. 컨테이너 env 에서 실제 계정을 읽어 온다(캐시)."""
    global _pg_cache
    env = os.environ.get('RADAR_PG')
    if env:
        return env
    if _pg_cache:
        return _pg_cache
    try:
        import subprocess
        out = subprocess.run(
            ['docker', 'inspect', container,
             '--format', '{{range .Config.Env}}{{println .}}{{end}}'],
            capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            e = dict(l.split('=', 1) for l in out.stdout.splitlines() if '=' in l)
            u, pw = e.get('POSTGRES_USER', 'postgres'), e.get('POSTGRES_PASSWORD')
            db = e.get('POSTGRES_DB', u)
            if pw:
                _pg_cache = f'postgresql://{u}:{pw}@localhost:5432/{db}'
                return _pg_cache
    except Exception:
        pass
    return 'postgresql://postgres:password@localhost:5432/radar_guard'
# ══ SOP 검색 카테고리 ══════════════════════════════════════════════════
#  ⚠ [7/31 버그수정] 이전 값은 로마자('03_naksan_eunggeupcheo')였는데 DB 에 실제로
#    들어 있는 값은 한글('03_낙상_응급처치')이다. filter 가 4/4 전부 불일치라
#    카테고리 검색이 **항상 0건**이었고, 코드가 예외를 삼켜 화면엔 조용히
#    아무것도 안 떴다. README 7/27 "LLM 생성 문장 0개" 의 실체가 이것이다.
#
#  sop_doctor.py 로 DB 에서 직접 읽은 실측값 (2026-07-31, 총 634청크):
#      01_감전_LOTO        278    02_협착_끼임      138
#      03_낙상_응급처치      83    04_예지보전        73
#      05_위험성평가_비상    62
#  → 이 목록을 바꿀 때는 반드시 sop_doctor.py 를 먼저 돌려 실물과 맞출 것.
SOP_CATEGORIES = ['00_응급처치_공통', '01_감전_대응', '01_감전_예방',
                  '02_협착_예방', '03_낙상_예방',
                  '04_예지보전', '05_위험성평가_비상', '09_색인제외']

#  ══ 왜 바꿨나 (8/25 sop_doctor 실측) ══════════════════════════════════
#    낙상 질의에 M-59 "넘어짐 위험성 평가"가 1등으로 나왔다. 원인은 검색이 아니라
#    카테고리 구성이었다 — 03_낙상_응급처치 83청크 중 응급처치 문서는 18청크(22%)뿐이고
#    나머지 65청크가 예방 문서였다. 01_감전은 대응 6.1%, 02_협착은 대응 0% 였다.
#    사고 후 조치를 묻는데 후보의 94%가 예방 문서면 k 를 늘려도 못 고친다.
#    → 카테고리를 예방/대응으로 쪼개 필터가 예방 문서를 구조적으로 배제하게 한다.
#    DB 재태깅은 sop_retag.py 로 먼저 했다. 순서를 뒤집으면 검색이 조용히 0건이 된다.
#
#    H-187(산업재해 형태별 응급처치)은 골절·절단·전기화상·뇌진탕을 한 권에 담고 있어
#    사고 종류를 가리지 않는다 → 00_응급처치_공통 으로 두고 여러 이벤트가 함께 쓴다.
#    본문 실측(8/25): 절단 13회 · 골절 18회 · 출혈 14회 · 화상 14회 · 척추 3회.
#    ⚠ 압좌증후군(crush syndrome) 처치는 없다 — 장시간 끼임 후 재관류 위험은 미커버.
#
#    OSHA3120(147청크, 옛 01_감전_LOTO 의 53%)은 미국 OSHA 2002 영문 원문이다.
#    한글 질의에 불리하고 뽑히면 화면에 영어가 뜬다 → 09_색인제외 로 격리.
#    같은 카테고리에 '조치 내용이 없는 메타 청크'도 넣는다 — 프로젝트 자체 SOP 의
#    "RAG 검색용 핵심어" 나열과 출처 목록 청크. 질의어를 그대로 담고 있어 실제
#    점검 절차 청크를 이기고 1등으로 뽑힌다(8/25 확인). sop_retag.py --quarantine.
#    삭제가 아니다. 아래 매핑에 넣으면 즉시 되살아난다.
#
#  ⚠ 값의 형태가 검색 건수를 바꾼다 — radar_core.search_sop_documents() 751행:
#      count = 1 if isinstance(category, (list, tuple)) else 2
#    문자열이면 그 카테고리에서 2건, 튜플이면 원소당 1건. 원소 1개짜리 튜플은
#    1건만 가져오므로 단일 카테고리는 반드시 문자열로 쓴다.
EVENT_CATEGORY = {
    # 사고 확정 → 응급처치 문서만. 예방 문서(M-59 등)를 후보에서 뺀 것이 이번 변경의 핵심.
    'fall_detected':       '00_응급처치_공통',
    # 의심 단계는 아직 사고가 아니다. 대응 문서 대신 예방·점검 문서를 준다.
    'fall_suspected':      '03_낙상_예방',
    # 감전은 E-14(감전시 응급조치) + H-187 4.5(4) 전기화상 조합이 맞다.
    #   H-187 본문에 '감전' 이라는 단어는 0회지만 "심정지 발생 시 심폐소생술",
    #   "모든 전기화상 환자는 반드시 병원 이송" 이 들어 있다(8/25 본문 확인).
    'electric_shock_risk': ('01_감전_대응', '00_응급처치_공통'),
    'electric_shock_risk_confirmed': ('01_감전_대응', '00_응급처치_공통'),
    # 설비 전기 이상은 사람 사고가 아니다 → 점검·차단 절차 문서.
    'leakage_current':     '01_감전_예방',
    'overcurrent':         '01_감전_예방',
    'voltage_drop':        '01_감전_예방',
    # 협착 확정: H-187 4.3 절단부 처치(지혈대 금지·재접합용 보관) + B-M-37 비상정지.
    #   끼임 사고의 실제 상해가 절단·골절·출혈이라 H-187 이 실제로 맞는 문서다.
    'pinching':            ('00_응급처치_공통', '02_협착_예방'),
    'pinching_suspected':  '02_협착_예방',
    'vibration_anomaly':   '04_예지보전',
    # 장시간 무동작은 감전인지 협착인지 현장 확인 전까지 알 수 없다.
    #   [7/31 채택] 민석 버전의 접근을 가져옴 — 필터를 없애 전체 634청크를 뒤지는
    #   것보다, 관련된 두 카테고리에서 각 1건씩 가져오는 편이 정확하다.
    #   (EVENT_LABELS 의 'VERIFY: SHOCK/ENTRAPMENT' 와 의미가 맞는다)
    #   접근 전 전원 차단이 먼저이므로 대응 문서는 감전 쪽을 쓴다.
    'stationary_anomaly':  ('01_감전_대응', '02_협착_예방'),
}
# ══════════════════════════════════════════════════════════════════════
# 4-B. 정지형 사전경보(PRE-ALERT) 파싱
# ══════════════════════════════════════════════════════════════════════
#  젯슨은 과전류 차단 후 정지 3초(STAT_PRE_SEC)부터 5초(STAT_CRIT_SEC) 사이에
#  pre_alert 문자열을 실어 보낸다:
#     "PRE-ALERT  Zone A: no-motion 12s  --  MOVE to cancel  (18s to CRITICAL)"
#  ⚠ [8/01] 그런데 노트북 화면이 이걸 한 번도 읽지 않고 있었다. 즉 근무자는
#    20초 동안 아무것도 모르다가 갑자기 경보를 맞았다. 사전경보의 존재 이유가
#    '움직이면 취소된다' 인데, 그 사실을 화면이 알려주지 않으면 의미가 없다.
#  → 여기서 한 번만 파싱한다. (문자열 포맷이 바뀌면 이 함수만 고치면 된다)
_PRE_RE = re.compile(
    r'Zone\s+(?P<zone>\w+).*?no-motion\s+(?P<dwell>\d+)\s*s'
    r'(?:.*?\((?P<left>\d+)\s*s to CRITICAL\))?', re.I | re.S)


def parse_pre_alert(msg):
    """pre_alert 문자열 → {'zone','dwell','left','text'} · 없으면 None."""
    if not msg:
        return None
    m = _PRE_RE.search(str(msg))
    if not m:
        return None
    zone = m.group('zone')
    dwell = int(m.group('dwell'))
    left = int(m.group('left')) if m.group('left') else None
    text = (f'무동작 {dwell}초 · {left}초 뒤 경보' if left is not None
            else f'무동작 {dwell}초')
    return {'zone': zone, 'dwell': dwell, 'left': left, 'text': text}

ZONE_IDS = ['A', 'B', 'C']
ZONE_KO = {'A': '변전실', 'B': '가공', 'C': '조립'}

# ── 경보 우선순위 (ISA-18.2) ──
#   지금까지 모든 이벤트가 critical 한 단계였다. 낙상과 진동이상이 같은 강도로
#   울리면 며칠 만에 근무자가 빨강을 무시한다(alarm fatigue). 등급을 나눈다.
SEV_RANK = {'normal': 0, 'warning': 1, 'critical': 2}
SEV_KO = {'normal': '정상', 'warning': '주의', 'critical': '위험'}

# ── 이벤트 유형별 경보 등급 (표시·우선순위 전용) ──────────────────────
#  [8/01] 그동안 정지형 이상도 critical 이라 낙상과 똑같이 빨갛게 울렸다.
#    낙상은 게이트 5개가 통과한 '확정 사건' 이고, 정지형 이상은 젯슨 라벨
#    'STATIONARY ANOMALY (VERIFY: SHOCK/ENTRAPMENT)' 이 말하듯 '가서 확인해야
#    하는 사건' 이다. 성격이 다른데 같은 강도로 울리면 며칠 만에 빨강이 무시된다.
#    → 확정 사고 = critical(빨강) / 확인 필요 = warning(주황) 으로 나눈다.
#
#  ⚠⚠ 매우 중요 — 이 표는 '차단기 동작' 과 무관하다.
#    jetson_sender 의 차단 조건은 _latch_event() 안의 clf['severity'] 이고,
#    그건 classify() 가 돌려주는 값이다. 이 표는 classify 를 거치지 않는
#    정지형 2차 경보의 '표시 등급' 에만 쓰인다. 이 표를 바꿔도 차단은
#    그대로 동작한다. (반대로 classify 의 severity 를 건드리면 차단이 바뀐다)
EVENT_SEV = {
    'fall_detected':       'critical',   # 게이트 5개 통과 = 확정
    'fall_suspected':      'warning',    # 규칙 양성/RF 음성 — 확인 전 차단하지 않음
    'electric_shock_risk': 'critical',
    'electric_shock_risk_confirmed': 'critical',
    'leakage_current':     'critical',
    'pinching_suspected':  'warning',
    'pinching':            'critical',
    'overcurrent':         'critical',
    'voltage_drop':        'critical',
    'stationary_anomaly':  'warning',    # 확인 필요 — 사람일 수도, 정지 작업일 수도
    'vibration_anomaly':   'warning',    # 설비 이상 — 사람 사고가 아니다
    'normal':              'normal',
}

# 자동 차단은 전기·협착 사건에만 적용한다.
# 낙상은 구조 접근이 우선이며 작업 대상 설비 회로를 자동 차단하지 않는다.
AUTO_TRIP_EVENTS = frozenset({
    'overcurrent', 'voltage_drop', 'leakage_current', 'electric_shock_risk',
    'electric_shock_risk_confirmed', 'pinching',
})


def event_sev(et, default='critical'):
    return EVENT_SEV.get(et, default)

# ══════════════════════════════════════════════════════════════════════
# 5. 판단 근거 표시 메타 (L2 카드)
# ══════════════════════════════════════════════════════════════════════
#  젯슨 classify() 가 보내는 gates 의 키는 영어다. 노트북이 이걸 그대로 표에
#  뿌리면 근무자 화면에 'impulse' 가 뜬다. 여기서 한 번만 번역한다.
#  'why' 는 LLM 이 아니라 사람이 실측 근거로 쓴 문장 — 이게 LLM 문장보다
#  정확하고 즉시 뜬다. (LLM 은 "왜 경보를 안 울렸나" 같은 조합 설명에만 쓴다)
GATE_META = {
    'impulse':  {'ko': '충격 세기', 'why': '조용하다 갑자기 격해짐',
                 'src': '실측 낙상 2.3~7.9 / 달리기는 전반부부터 높아 비율 낮음'},
    'h_drop':   {'ko': '높이 변화', 'why': '아래로 떨어짐',
                 'src': '실측 낙상 최소 0.447m / 팔 휘두름 0.424m 미달'},
    'horiz':    {'ko': '수평 퍼짐', 'why': '무너지며 옆으로 퍼짐',
                 'src': '실측 낙상 0.75~1.26m / 제자리 빠른앉기 0.35~0.79m'},
    'ds_last':  {'ko': '이후 정지', 'why': '넘어진 뒤 움직임 없음',
                 'src': '실측 낙상 최대 0.96 / 달리기는 지속'},
    'ds_broad': {'ko': '지속 프레임', 'why': '한순간 튄 게 아님',
                 'src': '낙상=0.5초 사건이라 2~5프레임 / 노이즈 플래시는 0~1'},
}
# 기각 후보 키 → 한글
REJECT_KO = {'fast_sit': '빠른 앉기', 'vibration': '설비 진동',
             'walk': '보행', 'wave': '팔 흔들기'}

# evidence 필드 표시명 (판단 근거 팝업 하단 원시값 표)
EVIDENCE_KO = {
    'dopstd_max': '움직임 최대', 'dopstd_mean': '움직임 평균',
    'ds_first': '전반부 평균', 'ds_last': '후반부 평균',
    'ds_broad': '격렬 프레임 수', 'impulse_ratio': '임펄스비',
    'h_drop': '높이 변화폭', 'h_desc': '피크 전후 하강',
    'horiz_range': '수평 이동폭', 'zacc_amp': '수직 가속 peak',
    'n_mean': '포인트 평균', 'n_p75': '포인트 75분위',
    'height_start': '시작 높이', 'height_end': '종료 높이',
    'ae_score': '이상점수', 'ae_thr': '정상 기준',
}

# ══════════════════════════════════════════════════════════════════════
# 6. 전기 설비 (차단기)
# ══════════════════════════════════════════════════════════════════════
#  ⚠ 판정과 차단 실행은 젯슨이 한다. 노트북은 표시와 '복구 요청'만 한다.
#    (링크가 끊겨도 젯슨이 독립 차단 — fail-safe. 이 상수는 표시 기준선용)
# ⚠ [8/21·8/24 실측] 8V LED 모의회로:
# 정상 0.0020~0.0050A / 1kΩ 분기 ON 0.0115~0.0125A /
# 50Ω 분기 ON 0.1505~0.1535A / 전압 7.73~8.124V.
CURR_LIMIT = 0.10           # [A] 정상 최대와 이상 최소 사이 147.5mA 공백
VOLT_MIN = 7.50             # [V] 스위치 ON 실측 최저 7.73V 아래
POWER_CONFIRM = 2           # 1Hz 2회 연속 확인 후 전기 이상 확정
LEAK_LIMIT = 0.008          # [A] 정상 최대 5.0mA와 1kΩ 분기 최소 11.5mA 사이
LEAK_CONFIRM = 2            # 1Hz 2회 연속 확인 후 누설전류 모의 확정
VIB_DS_THRESH = 0.20        # 설비 진동 판정 dop_std 임계

# [8/20] 차단 범위는 구역 전체가 아니라 작업 대상 설비 회로 1개로 한정한다.
#        근거: LOTO 원칙(격리 대상 = 작업 대상 설비의 에너지).
#        실측 배선: 릴레이 CH0 부하 = LED + INA226 션트뿐. 조명·비상등은 CH0 밖.
BREAKER_SCOPE = '작업 대상 설비 회로'

# ══════════════════════════════════════════════════════════════════════
# 7. 관제 팔레트 (다크)
# ══════════════════════════════════════════════════════════════════════
#  [7/31] 테두리 대신 '배경 밝기 단계'로 영역을 구분한다.
#    이전엔 패널 7개에 전부 1px 테두리가 있어 화면이 선으로 조각났다.
#    테두리는 '선택됨 · 경보 · 포커스' 에만 쓴다.
#  [8/01] 남보라(#0a0a1e·#00ccff) 계열은 채도가 높아 '사이버펑크 데모'로 읽혔다.
#    산업 관제 소프트웨어의 관례(slate 계열 중성 다크 + 낮은 채도 강조색)로 교체한다.
#    ⚠ 상수 '이름'은 그대로다. 이 파일만 바꾸면 기존 화면도 같이 바뀐다.
BG = '#090D18'          # 창 배경 (가장 어두움)
PANEL = '#111827'       # 일반 패널  (테두리 없이 이 밝기로 구분)
PANEL_HI = '#151D2E'    # 강조 패널 · 카드 안의 카드
PANEL_LO = '#0D1420'    # 눌린 상태 · 비활성
EDGE = '#263247'        # 테두리 (강조가 필요한 곳에만)
TXT = '#F8FAFC'         # 본문
DIM = '#94A3B8'         # 보조 — 8pt DIM 은 실제로 안 읽혔으므로 10pt 이상에만 쓴다
FAINT = '#64748B'       # 비활성 · 미설치
CYAN = '#22D3EE'        # 주 액션 · 선택
GREEN = '#22C55E'       # 정상 · 확인
AMBER = '#F59E0B'       # 주의 · 모의값 · 연결 대기
RED = '#EF4444'         # 위험 · 경보 (이것 외에는 절대 쓰지 않는다)
GRID = '#1E293B'
SEV_COLOR = {'normal': GREEN, 'warning': AMBER, 'critical': RED}

# ── 파생 색 (경보 배경 · 성공 배경 등) — 하드코딩 금지용 ──
BG_ALERT = '#2A1214'     # 경보 패널 배경 (RED 계열 저채도)
BG_ALERT_HI = '#3B1518'  # 경보 배너 점멸 ON
BG_OK = '#0E2A1B'        # 성공/완료 패널 배경
BG_WARN = '#2A2008'      # 주의 패널 배경
BG_SEL = '#0E2A33'       # 선택·호버 배경 (CYAN 계열)
SEV_BG = {'normal': BG_OK, 'warning': BG_WARN, 'critical': BG_ALERT}
SEV_BG_HI = {'normal': '#123A26', 'warning': '#3A2C08', 'critical': BG_ALERT_HI}
RADIUS = 10              # 패널 모서리 [px]
RADIUS_SM = 6            # 버튼 · 입력 모서리

# ══════════════════════════════════════════════════════════════════════
# 8. 디자인 토큰 — 간격 · 타이포
# ══════════════════════════════════════════════════════════════════════
#  간격은 4의 배수만 쓴다. 이전엔 (12,9,12,11) (10,16,10,16) (13,12,13,13) 처럼
#  값이 전부 달라서 리듬이 없었다 — 사람 눈은 이 불규칙을 '조잡함'으로 읽는다.
SP_XS, SP_S, SP_M, SP_L, SP_XL = 4, 8, 12, 16, 24

#  폰트 5단계. 이전엔 8pt / 11pt 두 단계뿐이라 위계가 없었다.
FS_HERO = 30      # 주 수치 (높이 1.15)
FS_TITLE = 17     # 상태 제목 (이상 없음)
FS_BODY = 12      # 본문 · 버튼
FS_LABEL = 10     # 라벨  ← 8pt 금지
FS_CAPTION = 9    # 캡션


def sev_color(sev):
    return SEV_COLOR.get(sev, DIM)


def gate_ko(key):
    return GATE_META.get(key, {}).get('ko', key)


def gate_why(key):
    return GATE_META.get(key, {}).get('why', '')


def event_ko(et):
    return EVENT_KO.get(et, str(et))


def zone_ko(z):
    return ZONE_KO.get(z, '')


if __name__ == '__main__':
    print(f'radar_common  schema_version={SCHEMA_VERSION}')
    print(f'  포트    : data {DATA_PORT} / ctrl {CTRL_PORT} @ {SEND_HZ}Hz')
    print(f'  천장    : {CEILING_H} m')
    print(f'  phase   : {" -> ".join(PHASE_ORDER)}')
    print(f'  이벤트  : {len(EVENT_KO)}종 / 게이트 {len(GATE_META)}종')
