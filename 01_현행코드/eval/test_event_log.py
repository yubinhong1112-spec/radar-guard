r"""event_log.py 표준 라이브러리 회귀 검사.

실행 환경: Windows PowerShell
  python 01_현행코드\eval\test_event_log.py
"""

from datetime import date, datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
import sys
import tempfile
import threading


CODE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_DIR))

from event_log import EventLog, answer, parse_period, parse_query  # noqa: E402


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 5, 18, 0, 0, tzinfo=KST)
passed = 0
failures = []


def check(name, condition):
    global passed
    try:
        result = condition() if callable(condition) else condition
        if not result:
            raise AssertionError("조건이 거짓입니다")
        passed += 1
    except Exception as error:  # 테스트 러너는 실패를 모아 마지막에 종료코드로 알린다.
        failures.append(f"{name}: {type(error).__name__}: {error}")


def dt(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=KST)


def period_hours(text):
    result = parse_period(text, NOW)
    return None if result is None else (result[0].hour, result[1].hour, result[2])


def run_file_tests(root):
    log = EventLog(root / "roundtrip", started_on=date(2026, 10, 5))
    check("정상 기록", log.record("enter", "A", "", "입실 버튼", "operator", dt(5, 9, 12)))
    check("날짜별 파일 생성", (root / "roundtrip" / "events_20261005.jsonl").is_file())
    item = json.loads((root / "roundtrip" / "events_20261005.jsonl").read_text(encoding="utf-8").splitlines()[0])
    check("레코드 필드", set(item) == {"ts", "kind", "zone", "event_type", "detail", "source"})
    check("서울 오프셋", item["ts"] == "2026-10-05T09:12:00+09:00")
    check("목록 밖 kind 거부", not log.record("unknown"))
    check("목록 밖 source 거부", not log.record("alert", source="camera"))
    check("문자열 아닌 상세 거부", not log.record("alert", detail=3))

    blocked = root / "blocked"
    blocked.write_text("folder가 아님", encoding="utf-8")
    check("쓰기 실패 False", not EventLog(blocked / "child").record("link"))

    check("추가 이벤트 기록 1", log.record("alert", "A", "fall_detected", "경보", "jetson", dt(5, 10)))
    check("추가 이벤트 기록 2", log.record("alert", "B", "pinching", "경보", "jetson", dt(5, 11)))
    check("추가 이벤트 기록 3", log.record("exit", "A", "", "퇴실 버튼", "operator", dt(5, 12)))
    check("count kind", log.count("alert", dt(5, 9), dt(5, 12)) == 2)
    check("count event_type", log.count("alert", dt(5, 9), dt(5, 12), event_type="pinching") == 1)
    check("count zone", log.count("alert", dt(5, 9), dt(5, 12), zone="A") == 1)
    check("list_events", [item["event_type"] for item in log.list_events("alert", dt(5, 9), dt(5, 12))] == ["fall_detected", "pinching"])
    check("last", log.last("alert")["event_type"] == "pinching")
    check("last filter", log.last("alert", "fall_detected")["zone"] == "A")

    cross = EventLog(root / "cross")
    check("자정 전 기록", cross.record("enter", source="operator", ts=dt(4, 23, 59)))
    check("자정 후 기록", cross.record("enter", source="operator", ts=dt(5, 0, 1)))
    check("날짜 경계 조회", cross.count("enter", dt(4, 23, 58), dt(5, 0, 2)) == 2)
    check("날짜 파일 2개", len(list((root / "cross").glob("events_*.jsonl"))) == 2)

    path = root / "roundtrip" / "events_20261005.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write("{깨진 줄\n")
    stats = log.read_stats(dt(5, 0), dt(6, 0))
    check("깨진 줄 건너뜀", stats["skipped"] == 1)
    check("정상 줄 유지", stats["records"] == 4)

    concurrent = EventLog(root / "concurrent")
    def writer(prefix):
        for number in range(50):
            concurrent.record("link", detail=f"{prefix}-{number}", ts=dt(5, 13))
    threads = [threading.Thread(target=writer, args=(prefix,)) for prefix in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events = concurrent.list_events("link", dt(5, 12), dt(5, 14))
    check("동시 쓰기 100줄", len(events) == 100)
    check("동시 쓰기 무손실", len({item["detail"] for item in events}) == 100)
    check("동시 쓰기 깨진 줄 0", concurrent.read_stats(dt(5, 12), dt(5, 14))["skipped"] == 0)


def run_period_tests():
    today = parse_period("오늘 입실", NOW)
    yesterday = parse_period("어제 경보", NOW)
    check("오늘", today[0] == dt(5, 0) and today[1] == NOW and today[2] == "오늘")
    check("어제", yesterday[0] == dt(4, 0) and yesterday[1] == dt(5, 0))
    check("시각 부터까지", period_hours("9시부터 12시까지") == (9, 12, "9시부터 12시까지"))
    check("시각 물결", period_hours("9시~12시") == (9, 12, "9시~12시"))
    check("시각 에서", period_hours("9시에서 12시") == (9, 12, "9시에서 12시"))
    check("오후 상속", period_hours("오후 2시부터 5시까지") == (14, 17, "오후 2시부터 5시까지"))
    check("오전 12시 종료", period_hours("오전 9시부터 12시까지") == (9, 12, "오전 9시부터 12시까지"))
    recent_hours = parse_period("최근 3시간 경보", NOW)
    recent_minutes = parse_period("최근 30분 경보", NOW)
    check("최근 N시간", recent_hours[:2] == (NOW - timedelta(hours=3), NOW))
    check("최근 N분", recent_minutes[:2] == (NOW - timedelta(minutes=30), NOW))
    check("끝 시각 12시간 보정 후 현재로 자름", period_hours("12시부터 9시까지") == (12, 18, "12시부터 9시까지 (현재 18:00까지)"))
    check("미래 구간 거부", parse_period("19시부터 20시까지", NOW) is None)
    for phrase in ("지난주 화요일", "아까", "점심때", "내일", "새벽", "다음주 월요일"):
        check(f"미지원 기간 {phrase}", parse_period(phrase, NOW) is None)


def run_query_tests():
    cases = [
        ("오늘 입실 몇 건?", "event_count", "enter"),
        ("9시부터 12시까지 출입 몇 번?", "event_count", "enter"),
        ("최근 2시간 입실 건수", "event_count", "enter"),
        ("오늘 퇴실 몇 건?", "event_count", "exit"),
        ("9시~12시 퇴실 횟수", "event_count", "exit"),
        ("최근 30분 퇴실 몇 번", "event_count", "exit"),
        ("오늘 경보 몇 번?", "event_count", "alert"),
        ("최근 2시간 경보 몇 건?", "event_count", "alert"),
        ("오늘 경보 종류", "alert_types", "alert"),
        ("9시에서 12시 경보 종류", "alert_types", "alert"),
        ("마지막 경보 알려줘", "last_alert", "alert"),
        ("최근 경보가 뭐야?", "last_alert", "alert"),
        ("가장 최근 경보", "last_alert", "alert"),
    ]
    for text, query_type, kind in cases:
        result = parse_query(text, NOW)
        check(f"질문 파서 {text}", result is not None and result["type"] == query_type and result["kind"] == kind and "period_text" in result)

    for text in ("현재 차단 회로는?", "지금 차단된 회로 알려줘", "어느 회로가 차단됐어?"):
        check(f"현재 차단 질문 {text}", parse_query(text, NOW) == {"type": "current_trip"})

    for text in ("온도 알려줘", "습도 알려줘", "경보 원인은?", "입실자 이름", "전원 켜줘"):
        check(f"무관 질문 {text}", parse_query(text, NOW) is None)

    unsupported = parse_query("지난주 화요일 입실 몇 건?", NOW)
    check("미지원 기간 질문 명시", unsupported["type"] == "unsupported_period" and unsupported["raw"] == "지난")

    default = parse_query("입실 몇 건?", NOW)
    check("기간 기본 오늘", default["period_text"] == "오늘" and default["start"] == dt(5, 0) and default["end"] == NOW)


def run_answer_tests(root):
    empty = EventLog(root / "no-files", started_on=date(2026, 10, 5))
    enter_query = parse_query("오늘 입실 몇 건?", NOW)
    check("기록 부재 안내", answer(enter_query, empty) == "해당 구간 기록 없음 — 기록 기능은 10월 5일부터 동작합니다.")

    log = EventLog(root / "answers", started_on=date(2026, 10, 5))
    log.record("alert", "A", "fall_detected", source="jetson", ts=dt(5, 10))
    log.record("alert", "B", "pinching", source="jetson", ts=dt(5, 11))
    log.record("enter", "A", source="operator", ts=dt(5, 9))
    log.record("exit", "A", source="operator", ts=dt(5, 12))
    check("입실 운영자 확인", "운영자 확인 기준 1건" in answer(enter_query, log))
    check("퇴실 운영자 확인", "운영자 확인 기준 1건" in answer(parse_query("오늘 퇴실 몇 건?", NOW), log))
    check("0건과 부재 구분", answer(parse_query("최근 30분 입실 몇 건?", NOW), log) == "최근 30분 입실은 운영자 확인 기준 0건입니다.")
    check("경보 건수 답변", answer(parse_query("오늘 경보 몇 건?", NOW), log) == "오늘 경보는 2건입니다.")
    kinds = answer(parse_query("오늘 경보 종류", NOW), log)
    check("경보 종류 답변", "fall_detected 1건" in kinds and "pinching 1건" in kinds)
    last = answer(parse_query("마지막 경보", NOW), log)
    check("마지막 경보 답변", "pinching" in last and "B" in last)
    check("현재 차단 위임", answer({"type": "current_trip"}, log) == "현재 차단 회로는 화면 상태에서 확인해야 합니다.")
    check("미지원 답변", answer(None, log) == "지원하지 않는 질문입니다.")


def run_period_regression_tests(root):
    now = dt(6, 11)

    morning = parse_query("9시부터 12시까지 입실 몇 건?", now)
    check("표1 오전 범위 유지", morning["type"] == "event_count" and morning["start"] == dt(6, 9) and morning["end"] == now)
    check("표1 현재 시각 표시", morning["period_text"] == "9시부터 12시까지 (현재 11:00까지)")

    afternoon = parse_query("오후 2시~5시 퇴실 건수", now)
    check("표2 시작 미래 거절", afternoon == {"type": "unsupported_period", "raw": "오후 2시~5시", "reason": "아직 오지 않은 시간"})

    cross = parse_query("오전 9시부터 오후 1시까지 입실 몇 건", now)
    check("표3 오전오후 교차", cross["start"] == dt(6, 9) and cross["end"] == now)
    check("표3 현재 시각 표시", cross["period_text"] == "오전 9시부터 오후 1시까지 (현재 11:00까지)")

    noon = parse_query("12시부터 1시까지 경보 몇 번", now)
    check("표4 보정 후 시작 미래", noon == {"type": "unsupported_period", "raw": "12시부터 1시까지", "reason": "아직 오지 않은 시간"})

    clipped = parse_period("8시부터 15시까지", now)
    check("구간 끝 자르기", clipped[0] == dt(6, 8) and clipped[1] == now and "현재 11:00까지" in clipped[2])
    future = parse_query("15시부터 16시까지 경보 몇 건", now)
    check("별도 시작 미래 거절", future["type"] == "unsupported_period" and future["reason"] == "아직 오지 않은 시간")

    cross_one = parse_period("오전 9시부터 오후 1시까지", NOW)
    cross_two = parse_period("오전 11시부터 오후 2시까지", NOW)
    check("오전오후 교차 9-13", cross_one[:2] == (dt(5, 9), dt(5, 13)))
    check("오전오후 교차 11-14", cross_two[:2] == (dt(5, 11), dt(5, 14)))

    corrected_one = parse_period("12시부터 1시까지", NOW)
    corrected_two = parse_period("11시~2시", NOW)
    check("끝 시각 보정 12-13", corrected_one[:2] == (dt(5, 12), dt(5, 13)))
    check("끝 시각 보정 11-14", corrected_two[:2] == (dt(5, 11), dt(5, 14)))
    check("오전 12시 거절", parse_query("오전 12시부터 오전 1시까지 입실 몇 건", now)["type"] == "unsupported_period")

    default = parse_query("입실 몇 번 했어?", now)
    check("기간 없음만 오늘", default["period_text"] == "오늘" and default["start"] == dt(6, 0) and default["end"] == now)

    unsupported_answer = answer(afternoon, EventLog(root / "unsupported"))
    check("미지원 기간 안내", "'오후 2시~5시' 구간은 해석하지 못했습니다" in unsupported_answer and "예: 9시부터 12시까지" in unsupported_answer)

    clipped_log = EventLog(root / "clipped")
    clipped_log.record("enter", source="operator", ts=dt(6, 9))
    clipped_answer = answer(morning, clipped_log)
    check("답변 현재 시각 표시", "(현재 11:00까지)" in clipped_answer and "운영자 확인 기준 1건" in clipped_answer)


def run_signature_tests():
    check("record 시그니처", str(inspect.signature(EventLog.record)) == "(self, kind, zone='', event_type='', detail='', source='system', ts=None)")
    check("count 시그니처", str(inspect.signature(EventLog.count)) == "(self, kind, start, end, event_type=None, zone=None)")
    check("last 시그니처", str(inspect.signature(EventLog.last)) == "(self, kind, event_type=None)")
    check("list_events 시그니처", str(inspect.signature(EventLog.list_events)) == "(self, kind, start, end)")
    check("parse_period 시그니처", str(inspect.signature(parse_period)) == "(text, now)")
    check("parse_query 시그니처", str(inspect.signature(parse_query)) == "(text, now)")
    check("answer 시그니처", str(inspect.signature(answer)) == "(query, log)")


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        run_file_tests(root)
        run_period_tests()
        run_query_tests()
        run_answer_tests(root)
        run_period_regression_tests(root)
        run_signature_tests()
    print(f"통과 {passed}건 / 실패 {len(failures)}건")
    for failure in failures:
        print(f"실패: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
