r"""Radar-Guard 운영 이벤트를 날짜별 JSONL로 기록하고 조회한다.

실행 환경: Windows PowerShell / 노트북 관제 프로그램
표준 라이브러리만 사용하며 DB·Ollama와 연결하지 않는다.
"""

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
import threading


KST = timezone(timedelta(hours=9))
KINDS = frozenset({
    "enter", "exit", "alert", "ack", "resolve",
    "restore_request", "trip", "link",
})
SOURCES = frozenset({"operator", "jetson", "system"})
_FILE_RE = re.compile(r"events_(\d{8})\.jsonl$")
_RANGE_RE = re.compile(
    r"(?:(오전|오후)\s*)?(\d{1,2})시\s*(부터|~|에서)\s*"
    r"(?:(오전|오후)\s*)?(\d{1,2})시(?:까지)?"
)
_RECENT_RE = re.compile(r"최근\s*(\d+)\s*(시간|분)")
_PERIOD_HINT_RE = re.compile(
    r"\d+\s*시|오전|오후|최근|어제|오늘|그제|내일|지난|이번|아까|새벽|점심|저녁"
)
_LAST_ALERT_RE = re.compile(r"마지막\s*경보|최근\s*경보|가장\s*최근\s*경보")


def _as_kst(value):
    if not isinstance(value, datetime):
        raise TypeError("시각은 datetime이어야 합니다")
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _hour(value, marker=None, inherited=False):
    value = int(value)
    if marker is None:
        return value if 0 <= value <= 23 else None
    if not 1 <= value <= 12:
        return None
    if inherited and marker == "오전" and value == 12:
        return 12
    if marker == "오전":
        return None if value == 12 else value
    return 12 if value == 12 else value + 12


def _today(now):
    start = datetime.combine(now.date(), time.min, KST)
    return start, now, "오늘"


def _unsupported_period(text, now):
    range_match = _RANGE_RE.search(text)
    hint = range_match or _PERIOD_HINT_RE.search(text)
    if hint is None:
        return None
    result = {"type": "unsupported_period", "raw": hint.group()}
    if range_match:
        first_marker, first_value = range_match.group(1), range_match.group(2)
        first_hour = _hour(first_value, first_marker)
        if first_hour is not None:
            start = datetime.combine(now.date(), time(first_hour), KST)
            if start > now:
                result["reason"] = "아직 오지 않은 시간"
    return result


class EventLog:
    """한 인스턴스 안의 쓰기를 직렬화하는 파일 기반 운영 기록."""

    def __init__(self, data_dir=None, started_on=None):
        root = Path(__file__).resolve().parents[1]
        self.data_dir = Path(data_dir) if data_dir is not None else root / "03_데이터" / "운영기록"
        if started_on is None:
            self.started_on = datetime.now(KST).date()
        elif isinstance(started_on, datetime):
            self.started_on = _as_kst(started_on).date()
        elif isinstance(started_on, date):
            self.started_on = started_on
        else:
            raise TypeError("started_on은 date 또는 datetime이어야 합니다")
        self._lock = threading.Lock()

    def record(self, kind, zone="", event_type="", detail="", source="system", ts=None):
        """이벤트 한 줄을 기록한다. 쓰기 실패와 잘못된 값은 False다."""
        if kind not in KINDS or source not in SOURCES:
            return False
        if not all(isinstance(value, str) for value in (zone, event_type, detail)):
            return False
        try:
            when = _as_kst(ts) if ts is not None else datetime.now(KST)
        except (TypeError, ValueError, OverflowError):
            return False
        record = {
            "ts": when.isoformat(timespec="seconds"),
            "kind": kind,
            "zone": zone,
            "event_type": event_type,
            "detail": detail,
            "source": source,
        }
        path = self.data_dir / f"events_{when:%Y%m%d}.jsonl"
        try:
            with self._lock:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except (OSError, TypeError, ValueError):
            return False
        return True

    def _paths_for_period(self, start, end):
        day = start.date()
        last_day = end.date()
        paths = []
        while day <= last_day:
            path = self.data_dir / f"events_{day:%Y%m%d}.jsonl"
            if path.is_file():
                paths.append(path)
            day += timedelta(days=1)
        return paths

    @staticmethod
    def _read_paths(paths, start=None, end=None):
        events = []
        skipped = 0
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                skipped += 1
                continue
            for line in lines:
                try:
                    item = json.loads(line)
                    when = datetime.fromisoformat(item["ts"])
                    if not isinstance(item, dict) or when.tzinfo is None or item.get("kind") not in KINDS:
                        raise ValueError("잘못된 레코드")
                    when = when.astimezone(KST)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    skipped += 1
                    continue
                if start is not None and when < start:
                    continue
                if end is not None and when >= end:
                    continue
                events.append(item)
        events.sort(key=lambda item: item["ts"])
        return events, skipped

    def _period_events(self, start, end):
        start, end = _as_kst(start), _as_kst(end)
        if end < start:
            return [], 0, 0
        paths = self._paths_for_period(start, end)
        events, skipped = self._read_paths(paths, start, end)
        return events, skipped, len(paths)

    def count(self, kind, start, end, event_type=None, zone=None):
        events = self.list_events(kind, start, end)
        return sum(
            (event_type is None or item.get("event_type") == event_type)
            and (zone is None or item.get("zone") == zone)
            for item in events
        )

    def last(self, kind, event_type=None):
        paths = sorted(self.data_dir.glob("events_*.jsonl")) if self.data_dir.is_dir() else []
        paths = [path for path in paths if _FILE_RE.fullmatch(path.name)]
        events, _ = self._read_paths(paths)
        matches = [item for item in events
                   if item.get("kind") == kind
                   and (event_type is None or item.get("event_type") == event_type)]
        return matches[-1] if matches else None

    def list_events(self, kind, start, end):
        events, _, _ = self._period_events(start, end)
        return [item for item in events if item.get("kind") == kind]

    def read_stats(self, start, end):
        events, skipped, files = self._period_events(start, end)
        return {"files": files, "records": len(events), "skipped": skipped}


def parse_period(text, now):
    """지원하는 한국어 기간만 [start, end) 구간으로 바꾼다."""
    if not isinstance(text, str):
        return None
    now = _as_kst(now)
    match = _RANGE_RE.search(text)
    if match:
        first_marker, first_value, _, second_marker, second_value = match.groups()
        first_hour = _hour(first_value, first_marker)
        inherited = second_marker is None and first_marker is not None
        second_hour = _hour(second_value, second_marker or first_marker, inherited)
        if first_hour is None or second_hour is None:
            return None
        if first_marker is None and second_marker is None and second_hour < first_hour:
            second_hour += 12
        if second_hour > 23 or second_hour <= first_hour:
            return None
        start = datetime.combine(now.date(), time(first_hour), KST)
        end = datetime.combine(now.date(), time(second_hour), KST)
        if start > now:
            return None
        period_text = match.group()
        if end > now:
            end = now
            period_text += f" (현재 {now:%H:%M}까지)"
        return start, end, period_text

    match = _RECENT_RE.search(text)
    if match:
        amount = int(match.group(1))
        if amount <= 0:
            return None
        delta = timedelta(hours=amount) if match.group(2) == "시간" else timedelta(minutes=amount)
        return now - delta, now, match.group()

    match = re.search(r"오늘|어제", text)
    if match:
        if match.group() == "오늘":
            start, end, _ = _today(now)
        else:
            end = datetime.combine(now.date(), time.min, KST)
            start = end - timedelta(days=1)
        return start, end, match.group()
    return None


def parse_query(text, now):
    """승인된 운영 질문 네 종류만 조회 명령으로 바꾼다."""
    if not isinstance(text, str):
        return None
    if re.search(r"(?:현재|지금).*(?:차단).*(?:회로)|차단된\s*회로|어느\s*회로.*차단", text):
        return {"type": "current_trip"}

    last_alert = _LAST_ALERT_RE.search(text)
    period_source = _LAST_ALERT_RE.sub("경보", text) if last_alert else text
    period = parse_period(period_source, now)
    if period is None:
        now = _as_kst(now)
        unsupported = _unsupported_period(period_source, now)
        if unsupported is not None:
            return unsupported
        start, end, period_text = _today(now)
    else:
        start, end, period_text = period
    base = {"start": start, "end": end, "period_text": period_text}

    if last_alert:
        return {**base, "type": "last_alert", "kind": "alert"}
    count_word = re.search(r"몇\s*(?:건|번)|건수|횟수", text)
    if "퇴실" in text and count_word:
        return {**base, "type": "event_count", "kind": "exit"}
    if ("입실" in text or "출입" in text) and count_word:
        return {**base, "type": "event_count", "kind": "enter"}
    if "경보" in text and "종류" in text:
        return {**base, "type": "alert_types", "kind": "alert"}
    if "경보" in text and count_word:
        return {**base, "type": "event_count", "kind": "alert"}
    return None


def answer(query, log):
    """조회 명령에 정해진 문장만 반환한다."""
    if query is None:
        return "지원하지 않는 질문입니다."
    if query.get("type") == "current_trip":
        return "현재 차단 회로는 화면 상태에서 확인해야 합니다."
    if query.get("type") == "unsupported_period":
        reason = f" 이유: {query['reason']}." if query.get("reason") else ""
        return (f"'{query['raw']}' 구간은 해석하지 못했습니다.{reason} "
                "예: 9시부터 12시까지, 오후 2시~5시, 최근 2시간")

    start, end = query["start"], query["end"]
    period_text = query["period_text"]
    if log.read_stats(start, end)["files"] == 0:
        return (f"해당 구간 기록 없음 — 기록 기능은 "
                f"{log.started_on.month}월 {log.started_on.day}일부터 동작합니다.")

    query_type = query["type"]
    kind = query["kind"]
    if query_type == "event_count":
        number = log.count(kind, start, end)
        if kind == "enter":
            return f"{period_text} 입실은 운영자 확인 기준 {number}건입니다."
        if kind == "exit":
            return f"{period_text} 퇴실은 운영자 확인 기준 {number}건입니다."
        return f"{period_text} 경보는 {number}건입니다."

    events = log.list_events("alert", start, end)
    if query_type == "alert_types":
        if not events:
            return f"{period_text} 경보는 0건입니다."
        counts = {}
        for item in events:
            name = item.get("event_type") or "미분류"
            counts[name] = counts.get(name, 0) + 1
        summary = ", ".join(f"{name} {counts[name]}건" for name in sorted(counts))
        return f"{period_text} 경보 종류는 {summary}입니다."
    if query_type == "last_alert":
        if not events:
            return f"{period_text} 마지막 경보 기록은 없습니다."
        item = events[-1]
        return (f"{period_text} 마지막 경보는 {item['ts']}, "
                f"{item.get('event_type') or '미분류'}, {item.get('zone') or '구역 미지정'}입니다.")
    return "지원하지 않는 질문입니다."
