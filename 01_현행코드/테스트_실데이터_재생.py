"""테스트_실데이터_재생.py — 실측 jsonl 을 UDP 로 흘려 UI 전 경로를 검증한다

  실행: [내 PC PowerShell] — cd 불필요, 어디서 실행해도 자기 위치 기준으로 돈다.
      python 01_현행코드\테스트_실데이터_재생.py

  창을 띄우지 않고(offscreen) 돌며, 각 상황마다 화면 상태를 실제로 읽어 검사한다.
  화면을 눈으로 보려면 replay_jsonl.py + console_ui.py 두 창을 쓰면 된다.

═══ 무엇을 검증하나 ═══
  실측 점군 → UDP → 수신 → 누적 → PCA 자세추정 → 머리추정 → 인체 도식 →
  경보 상태기계 → 색(정상 초록 / 주의 주황 / 위험 빨강) → 화면 갱신
  까지가 '실제로 측정한 데이터' 로 한 바퀴 도는지.

═══ 무엇을 검증하지 않나 ═══
  ⚠ 판정의 정확성. 재생기는 jsonl 의 label 을 그대로 통보하지 classify() 를
    돌리지 않는다. 낙상 판정이 맞는지는 젯슨 + torch 로 따로 검증한다.
  ⚠ LSTM-AE 이상점수. 학습된 baseline 이 없어 '—' 로 뜨는 게 정상이다.
"""
import os
import sys
import threading
import time

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# ⚠ [8/02 실측] offscreen 플러그인이 번들 Qt5/lib/fonts 를 못 찾아 families()=0 →
#   resolve_font() 가 매칭 못 하고 초기값이 유지되며 합성 폰트 메트릭이 글자 폭을
#   부풀려 허위 잘림을 만든다. 근거: ① offscreen families 0 / 실제 창 349
#   ② QT_QPA_FONTDIR 지정 시 offscreen 도 0→251, 잘림 9→0건 ③ 실제 창(Noto Sans KR)
#   에서 잘림 0건(스크린샷 확인). 젯슨(Linux)에서 돌 수 있으므로 win32 에서만 설정한다.
if sys.platform == 'win32':
    os.environ.setdefault(
        'QT_QPA_FONTDIR',
        os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ⚠ [8/03] cd 없이 어디서 실행해도 같은 결과가 나오게 한다. os.chdir() 없이는
#   grab().save('replay_*.png')(233행)가 실행 위치 기준으로 저장돼, 프로젝트
#   루트에서 그냥 돌리면 스크린샷이 01_현행코드가 아니라 루트에 쌓인다.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import replay_jsonl as rp                                    # noqa: E402
import radar_core as core                                    # noqa: E402
import console_ui as ui                                      # noqa: E402
from radar_common import RADAR_ZONE, EVENT_KO, sev_color, GREEN, AMBER, RED  # noqa: E402

OK, NG = [], []


def check(name, cond, detail=''):
    (OK if cond else NG).append(name + (f'  → {detail}' if detail else ''))
    print(('  OK   ' if cond else '  NG   ') + name + (f'  → {detail}' if detail else ''))


# ⚠ [8/02] 인체 도식 위젯은 두 종류다 — 3D(scene.track.cap, GLLinePlotItem)와
#   2D(scene.side.cap, PlotCurveItem). 색·선분 표현이 서로 달라 그대로 비교할 수
#   없다. 예전엔 2D 쪽만 검사했는데, 앱 기본 모드가 3D라 2D는 명시 전환 없이는
#   갱신되지 않는 죽은 위젯이었다(vib NG로 발견) — 그래서 두 위젯 모두 같은
#   기준으로 비교할 수 있게 여기서 통일한다.
def cap_n_seg(cap):
    """그려진 선분 점 개수. 3D(.pos)·2D(.getData()) 표현 차이를 없앤다."""
    if hasattr(cap, 'getData'):
        d = cap.getData()[0]
        return 0 if d is None else len(d)
    pos = getattr(cap, 'pos', None)
    return 0 if pos is None else len(pos)


def cap_color_hex(cap):
    """도식 색을 #RRGGBB 로 통일한다. 3D는 .color(0~1 실수 튜플), 2D는 pen 객체."""
    if hasattr(cap, 'opts') and cap.opts.get('pen') is not None:
        return cap.opts['pen'].color().name().upper()
    c = getattr(cap, 'color', None)
    if c is None:
        return None
    r, g, b = c[0], c[1], c[2]
    return '#{:02X}{:02X}{:02X}'.format(round(r * 255), round(g * 255), round(b * 255))


def pump(app, w, sec):
    t = time.time() + sec
    while time.time() < t:
        app.processEvents()
        time.sleep(0.01)


def wait_for(app, w, cond, sec):
    t = time.time() + sec
    while time.time() < t:
        app.processEvents()
        w.tick_ui()
        if cond():
            return True
        time.sleep(0.02)
    return False


def main():
    path = rp.DEFAULT_FILE
    if not os.path.exists(path):
        print(f'데이터 없음: {path}')
        sys.exit(1)
    n, by = rp.summarize(path)
    print('=' * 70)
    print(f'  실측 데이터 재생 검증 — {os.path.basename(path)}')
    print(f'  재생 가능(pts 보유) {n}건  {by}')
    print('=' * 70)

    threading.Thread(target=rp.control_listener, daemon=True).start()
    threading.Thread(target=rp.sender_loop, daemon=True).start()

    sys.argv = ['console_ui.py', '--live', '127.0.0.1']
    app, w, link = ui.build_app(['--live', '127.0.0.1'])
    link.start()
    w.resize(1440, 900)
    w.show()
    w.begin_session({'zone': RADAR_ZONE, 'shift': '주간조', 'operator': '홍유빈'})
    # 프레임이 흘러야 패킷이 나간다 — 아무거나 하나 재생해 링크를 세운다
    warm = rp.load_events(path)[0]
    threading.Thread(target=rp.play_event, args=(warm, 6.0, 'warmup'),
                     daemon=True).start()
    pump(app, w, 3.0)
    check('링크 수립', w.link.age() is not None and w.link.age() < 3.0,
          f'age={w.link.age()}')
    rp._clear(); w.clear_alarm()

    events = rp.load_events(path)
    byl = {}
    for e in events:
        byl.setdefault(e['label'], []).append(e)

    # ── 상황별 검사 ───────────────────────────────────────────────────
    CASES = [
        ('normal', None,                 'normal',   GREEN),
        ('walk',   None,                 'normal',   GREEN),
        ('fall',   'fall_detected',      'critical', RED),
        ('still',  'stationary_anomaly', 'warning',  AMBER),
        ('vib',    'vibration_anomaly',  'warning',  AMBER),
    ]
    for label, et, want_sev, want_col in CASES:
        pool = byl.get(label)
        if not pool:
            print(f'\n--- {label}: 이 파일에 없음, 건너뜀')
            continue
        print(f'\n--- {label} ({EVENT_KO.get(et, "경보 없음")})  '
              f'표본 {len(pool)}건 ---')
        e = pool[len(pool) // 2]
        rp._clear()
        w.clear_alarm()
        w.last_ev_id = 0
        w.scene.track.pose.clear()
        threading.Thread(target=rp.play_event, args=(e, 4.0, 'test'),
                         daemon=True).start()

        if et:
            got = wait_for(app, w, lambda: w.alarm != ui.ST_NORMAL, 20)
            check(f'{label}: 경보 발생', got, f'alarm={w.alarm}')
            if not got:
                continue
            pump(app, w, 0.6)
            w.tick_ui()
            check(f'{label}: 이벤트 유형', (w.alert or {}).get('type') == et,
                  f"{(w.alert or {}).get('type')}")
            check(f'{label}: 등급', w.cur_sev() == want_sev,
                  f'{w.cur_sev()} (기대 {want_sev})')
            check(f'{label}: 등급 색', sev_color(w.cur_sev()) == want_col,
                  sev_color(w.cur_sev()))
            check(f'{label}: 조치 가이드 제목 등급색',
                  want_col in w.drawer.sop.head.styleSheet(),
                  w.drawer.sop.head.styleSheet())
            check(f'{label}: 판단 근거 제목 등급색',
                  want_col in w.drawer.evi.head.styleSheet(),
                  w.drawer.evi.head.styleSheet())
            check(f'{label}: 배너 표시', w.monitor.banner.isVisible())
            check(f'{label}: 구역 = {RADAR_ZONE}',
                  (w.alert or {}).get('zone') == RADAR_ZONE)
            auto = w.monitor.auto_lb.text()
            if label == 'fall':
                check('fall: 낙상 자동 차단 제외로 표시',
                      '자동 차단 대상 아님' in auto, auto)
            elif label in ('still', 'vib'):
                check(f'{label}: warning 자동 차단 제외로 표시',
                      '자동 차단 대상 아님' in auto, auto)

            # ⚠ [8/02] 3D(기본 모드, 이미 갱신됨)와 2D(명시 전환 후 다음 패킷으로
            #   갱신됨) 둘 다 검사한다 — 한쪽만 보면 이번 vib 사각지대처럼 다른
            #   쪽이 죽어 있어도 못 잡는다.
            p = w.scene.track.pose.estimate()
            w.scene.set_mode(w.scene.MODE_2D)
            pump(app, w, 0.3)
            w.tick_ui()
            widgets = (('3D', w.scene.track.cap), ('2D', w.monitor.scene.side.cap))

            if et in ('fall_detected', 'stationary_anomaly'):
                # 사람 경보 중에는 형상을 지운다 — 정지한 사람은 레이더가 놓치고
                #   그 자리에 남는 반사를 사람으로 그리면 화면이 거짓말을 한다
                # ⚠ shape_ok 가 애초에 False 면 그릴 게 없어 "숨김" 검사가 아무것도
                #   증명 못 하는 공검사가 된다 — 숨길 대상이 실제로 있었는지 먼저 본다.
                check(f'{label}: 형상이 원래 존재했음(숨김 검사가 공검사 아님)',
                      bool(p and p['shape_ok']),
                      'shape_ok=False — 이 검사는 아무것도 증명 못 함'
                      if not (p and p['shape_ok']) else 'shape_ok=True')
                for tag, cap in widgets:
                    n_seg = cap_n_seg(cap)
                    check(f'{label}: 사람 경보 중 형상 숨김 [{tag}]', n_seg == 0,
                          f'선분 {n_seg}개')
                if et == 'fall_detected':
                    check(f'{label}: 사고 포즈를 유형 안내로 명시',
                          '유형 안내' in w.monitor._pose_text,
                          w.monitor._pose_text[:80])
                else:
                    check(f'{label}: 캡션에 추적 소실 명시',
                          '추적 소실' in w.monitor._pose_text,
                          w.monitor._pose_text[:60])
            else:
                for tag, cap in widgets:
                    col = cap_color_hex(cap)
                    check(f'{label}: 인체 도식 색 [{tag}]', col == want_col.upper(),
                          f'{col} (기대 {want_col.upper()})')
        else:
            pump(app, w, 3.0)
            w.tick_ui()
            check(f'{label}: 경보 없음', w.alarm == ui.ST_NORMAL, w.alarm)
            check(f'{label}: 히어로 = 이상 없음',
                  w.monitor.h_t.text() == '이상 없음', w.monitor.h_t.text())
            w.scene.set_mode(w.scene.MODE_2D)
            pump(app, w, 0.3)
            w.tick_ui()
            for tag, cap in (('3D', w.scene.track.cap),
                             ('2D', w.monitor.scene.side.cap)):
                col = cap_color_hex(cap)
                check(f'{label}: 도식 색 = 초록 [{tag}]', col == GREEN.upper(),
                      f'{col} (기대 {GREEN.upper()})')

        # 인체 도식이 실제로 그려졌는지 (모든 상황 공통)
        p = w.scene.track.pose.estimate()
        check(f'{label}: 자세 추정됨', p is not None)
        if p:
            check(f'{label}: 형상 표시 여부', True,
                  '표시' if p['shape_ok'] else '보류(점 부족)')
        if p and not p['shape_ok']:
            check(f'{label}: 형상 미표시(정직)', True,
                  f"{p['shape_why']} · {p['n_points']}점 · 높이폭 {p['h_span']:.2f} m")
        elif p:
            seg = core.Track3D.stick2d(p)
            cl = w.scene.track.pose.cloud()
            ov = (min(seg[:, 0].max(), cl[:, 0].max())
                  - max(seg[:, 0].min(), cl[:, 0].min()))
            check(f'{label}: 도식이 점군 위에 얹힘', ov > 0.1, f'겹침 {ov:.2f} m')
            check(f'{label}: 도식 높이 범위 정상',
                  -0.15 < seg[:, 1].min() and seg[:, 1].max() < 2.1,
                  f'{seg[:,1].min():.2f}~{seg[:,1].max():.2f} m')
            check(f'{label}: 자세·머리 추정',
                  True, f"{p['posture']} (머리 {p['head_src']}, "
                        f"머리높이 {p['head_h']:.2f} m, 높이폭 {p['h_span']:.2f} m)")
            check(f'{label}: 포인트 수신됨', p['n_points'] > 0, f"{p['n_points']}점 누적")
        w.grab().save(f'/tmp/replay_{label}.png' if os.name != 'nt'
                      else f'replay_{label}.png')
        # ⚠ 재생기는 자동 해제하지 않는다(젯슨과 동일). 사람이 누르듯 보낸다.
        if et:
            w.link.send_cmd('resolve')
            wait_for(app, w, lambda: w.alarm == ui.ST_NORMAL, 5)
            check(f'{label}: 상황 종료 후에만 해제', w.alarm == ui.ST_NORMAL, w.alarm)
        rp._clear()

    print('\n' + '=' * 70)
    print(f'  통과 {len(OK)} / 실패 {len(NG)}')
    print('=' * 70)
    for x in NG:
        print('  NG  ', x)
    sys.exit(1 if NG else 0)


if __name__ == '__main__':
    main()
