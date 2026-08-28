"""테스트_평면도_경보흐름.py — 평면도·사전경보·자동전환 전 흐름 검증

  실행: [내 PC PowerShell] — cd 불필요, 어디서 실행해도 같은 결과가 나온다.
      python 01_현행코드\테스트_평면도_경보흐름.py

  실측 jsonl 을 UDP 로 흘리며 아래를 검사한다(창 안 뜸):
    사전경보(주황) → 경보 latch(등급색) → 1.5초 뒤 자동 전환 → 상황 종료
    구역 클릭 진입 / 미설치 구역 처리
"""
import os, sys, threading, time
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
if sys.platform == 'win32':
    os.environ.setdefault(
        'QT_QPA_FONTDIR',
        os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'))
sys.path.insert(0,'.')
import replay_jsonl as rp, console_ui as ui
from radar_common import RADAR_ZONE
OK,NG=[],[]
def ck(n,c,d=''):
    (OK if c else NG).append(n); print(('  OK   ' if c else '  NG   ')+n+(f'  → {d}' if d else ''))
rp.PRE_SEC=4.0
threading.Thread(target=rp.control_listener,daemon=True).start()
threading.Thread(target=rp.sender_loop,daemon=True).start()
sys.argv=['console_ui.py','--live','127.0.0.1']
app,w,link=ui.build_app(['--live','127.0.0.1']); link.start()
w.resize(1440,900); w.show()
w.begin_session({'zone':RADAR_ZONE,'shift':'주간조','operator':'홍유빈'})
ev=rp.load_events(rp.DEFAULT_FILE)
def spin(sec, cond=None):
    t=time.time()+sec
    while time.time()<t:
        app.processEvents(); w.tick_ui()
        if cond and cond(): return True
        time.sleep(0.02)
    return False

# ── 정지형: 사전경보 → 경보 ──
still=[e for e in ev if e['label']=='still'][40]
w._navigate(ui.PG_DASH)
threading.Thread(target=rp.play_event,args=(still,6.0,'t'),daemon=True).start()
got=spin(30, lambda: w._pre is not None)
ck('사전경보 수신', got, str(w._pre))
ck('사전경보 = 경보 아님 (NORMAL 유지)', w.alarm==ui.ST_NORMAL, w.alarm)
ck('맵 구역 주황(사전경보)', w.dash.plan.state[RADAR_ZONE]['pre'] is not None,
   str(w.dash.plan.state[RADAR_ZONE]['pre']))
ck('사전경보 중 화면 이동 안 함', w.stack.currentIndex()==ui.PG_DASH)
w._navigate(ui.PG_MON); spin(0.4)
ck('감시 화면 사전경보 줄 표시', w.monitor.pre_bar.isVisible(),
   w.monitor.pre_lb.text()[:50])
w._navigate(ui.PG_DASH)

# ── 경보 latch → 맵 빨강 → 1.5초 뒤 자동 전환 ──
got=spin(40, lambda: w.alarm!=ui.ST_NORMAL)
ck('경보 latch', got, w.alarm)
spin(0.3)
ck('맵 구역 = 경보 등급색', w.dash.plan.state[RADAR_ZONE]['sev']=='warning',
   str(w.dash.plan.state[RADAR_ZONE]['sev']))
ck('사전경보 줄은 사라짐', not w.monitor.pre_bar.isVisible())
ck('아직 대시보드 (전환 대기)', w.stack.currentIndex()==ui.PG_DASH)
w.dash.plan.grab().save('/tmp/plan_alert.png')
w.grab().save('/tmp/dash_alert.png')
got=spin(3.0, lambda: w.stack.currentIndex()==ui.PG_MON)
ck(f'{ui.AUTO_NAV_MS}ms 뒤 자동 전환', got, f'page={w.stack.currentIndex()}')
w.link.send_cmd('resolve'); spin(4, lambda: w.alarm==ui.ST_NORMAL)
ck('상황 종료 후에만 해제', w.alarm==ui.ST_NORMAL, w.alarm)

# ── 낙상: 맵 빨강 + 점멸 ──
fall=[e for e in ev if e['label']=='fall'][20]
w._navigate(ui.PG_DASH); w.last_ev_id=0
threading.Thread(target=rp.play_event,args=(fall,6.0,'t'),daemon=True).start()
got=spin(30, lambda: w.alarm!=ui.ST_NORMAL)
ck('낙상 경보', got, w.alarm)
spin(0.3)
ck('맵 = critical', w.dash.plan.state[RADAR_ZONE]['sev']=='critical')
w.dash.plan.set_blink(True); app.processEvents()
w.grab().save('/tmp/dash_fall.png')
spin(3.0, lambda: w.stack.currentIndex()==ui.PG_MON)
ck('낙상도 자동 전환', w.stack.currentIndex()==ui.PG_MON)
w.link.send_cmd('resolve'); spin(4, lambda: w.alarm==ui.ST_NORMAL)

# ── 구역 클릭 ──
w._navigate(ui.PG_DASH); w.dash.refresh(); app.processEvents()
r=w.dash.plan._rects.get(RADAR_ZONE)
ck('구역 클릭 영역 존재', r is not None and r.width()>10, str(r))
w.dash.plan.zone_clicked.emit(RADAR_ZONE); spin(0.3)
ck('구역 클릭 → 감시 화면', w.stack.currentIndex()==ui.PG_MON)
print(f'\n통과 {len(OK)} / 실패 {len(NG)}')
for x in NG: print('  NG', x)
