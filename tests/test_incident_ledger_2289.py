"""운영 사고 대장 — 실패 묶음과 복구 시간 (docs/health.md 7.3, docs/infra.md 25.2289)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

from batch.jobs import weekly_summary as ws
from batch.services import incident_ledger as il

뿌리 = Path(__file__).resolve().parent.parent


def _r(job: str, status: str, when: str, market: str | None = "KR", err: str | None = None) -> ws.Run:
    return ws.Run(job, market, status, f"2026-11-{when}:00+00:00", err, ())


def test_실패_묶음_하나가_사고_한_건이고_다음_성공이_닫는다() -> None:
    runs = [
        _r("daily_kr", "success", "16T23:30"),
        _r("daily_kr", "failed", "17T23:30", err="TursoError: stream closed\n둘째 줄"),
        _r("daily_kr", "failed", "18T00:10", err="KeyError: 'x'"),
        _r("daily_kr", "skipped", "18T00:20"),  # 건너뜀은 닫지 않는다
        _r("daily_kr", "partial", "18T00:35"),
        _r("daily_kr", "success", "18T23:30"),
    ]
    found = il.incidents(runs)
    assert found == [{"job": "daily_kr", "market": "KR", "opened": "2026-11-17T23:30:00+00:00",
                      "closed": "2026-11-18T00:35:00+00:00", "failures": 2, "minutes": 65, "cause": "TursoError",
                      "days": 1, "same_code": None}]  # fmt: skip


def test_작업과_시장마다_따로이고_차례에_기대지_않는다() -> None:
    runs = [
        _r("daily_us", "failed", "17T13:30", "US", "X: 1"),
        _r("daily_kr", "failed", "17T23:30", "KR", "Y: 2"),
        _r("daily_kr", "success", "18T00:30", "KR"),  # 국내 성공은 미국 사고를 닫지 않는다
        _r("scores", "failed", "18T01:00", None, None),
        _r("daily_us", "success", "19T13:30", "US"),
        _r("daily_kr", "failed", "20T23:30", "KR", "Z: 3"),  # 닫힌 뒤 다시 실패 — 새 사고
    ]
    뒤집힘 = il.incidents(runs[::-1])
    assert 뒤집힘 == il.incidents(runs)
    assert [(x["job"], x["market"], x["minutes"], x["cause"]) for x in 뒤집힘] == [
        ("daily_us", "US", 2880, "X"), ("daily_kr", "KR", 60, "Y"), ("scores", None, None, "까닭 없음"),
        ("daily_kr", "KR", None, "Z")]  # fmt: skip
    assert [x["closed"] is None for x in 뒤집힘] == [False, False, True, True]


def test_같은_작업_이름이어도_시장이_다르면_따로다() -> None:
    """25.2290 — 처음 시험은 작업 이름이 시장마다 달라 이름만으로 갈렸다. 운영에는 같은 이름에 시장만 다른 작업이
    많다(verdicts·etf·analyze_extra …) — 미국 성공이 국내 사고를 닫으면 복구가 거짓으로 빨라진다"""
    runs = [_r("verdicts", "failed", "17T23:30", "KR", "E: 1"), _r("verdicts", "success", "18T13:30", "US"),
            _r("verdicts", "failed", "18T14:00", "US", "E: 2")]  # fmt: skip
    found = il.incidents(runs)
    assert [(x["market"], x["closed"], x["failures"]) for x in found] == [("KR", None, 1), ("US", None, 1)]


def test_죽은_실행을_정리한_줄은_사고를_연다() -> None:
    """25.2290 — 강제 종료·한도로 끝맺지 못해 정리된 줄(`skipped` + 정리 머리)은 관문의 무사고 칸이 사고로 센다
    (25.2253). 대장도 같은 잣대 — 예정된 건너뜀·한도 건너뜀은 여전히 열지 않는다"""
    from batch.core import db

    죽음 = f"{db.REAPED_PREFIX}. 6시간 뒤 정리했다"
    runs = [_r("daily_kr", "skipped", "17T23:30", err=죽음), _r("daily_kr", "skipped", "18T00:00", err="휴장일"),
            _r("daily_kr", "success", "18T23:30")]  # fmt: skip
    found = il.incidents(runs)
    assert len(found) == 1 and found[0]["failures"] == 1 and found[0]["minutes"] == 1440
    assert found[0]["cause"].startswith(db.REAPED_PREFIX)
    assert il.incidents([_r("daily_kr", "skipped", "17T23:30", err="한도"), _r("daily_kr", "skipped", "18T23:30")]) == []


def test_복구_시간을_못_잰_사고도_그_밖_수에_든다() -> None:
    runs = []
    for k, job in enumerate(("a", "b", "c", "d", "e")):
        if k < 3:  # 시간대가 섞여 복구 시간을 못 잰다 — 목록에는 없지만 수에는 든다
            runs += [ws.Run(job, None, "failed", "2026-11-17T23:30:00", "E: 1", ()),
                     ws.Run(job, None, "success", "2026-11-18T00:30:00+00:00", None, ())]  # fmt: skip
        else:
            runs += [_r(job, "failed", "17T23:30", None, "E: 1"), _r(job, "success", "18T00:30", None)]
    줄 = il.lines(il.incidents(runs))
    assert 줄[0].startswith("운영 사고(실패 묶음 → 복구): 5건") and len(줄) == 4 and 줄[-1] == "  … 그 밖 3건"
    하나 = il.lines(il.incidents(runs[:2]))
    assert 하나 == ["운영 사고(실패 묶음 → 복구): 1건", "  … 그 밖 1건"]


def test_원인_머리의_운영_꼴() -> None:
    """대부분의 작업은 `str(exc)` 를 적는다 — 예외 이름이 아니라 문장 머리가 온다(25.2290 — 문서를 사실대로)"""
    assert il._갈래("모의 잔고 조회 실패 EGW00123 기간이 만료된 token 입니다") == "모의 잔고 조회 실패 EGW00123 기간이 만료된 token 입니다"[:40]
    assert il._갈래("발송 실패: 텔레그램 400") == "발송 실패"
    assert il._언제("2026-11-17T23:27:41+00:00") == "11-18 08:27" and il._언제("2026-11-17T23:27:41") == "2026-11-17T23:27"
    assert il._언제("모르는 때 — 아주 긴 글자열") == "모르는 때 — 아주 긴 글자열"[:16]


def test_도는_중과_확인_실행은_열지도_닫지도_않는다() -> None:
    runs = [_r("daily_kr", "running", "17T23:00"), _r("daily_kr", "dryrun", "17T23:10"),
            _r("daily_kr", "failed", "17T23:30", err="E: 1"), _r("daily_kr", "dryrun", "17T23:40"),
            _r("daily_kr", "running", "17T23:50")]  # fmt: skip
    found = il.incidents(runs)
    assert len(found) == 1 and found[0]["closed"] is None and found[0]["failures"] == 1
    assert il.incidents([_r("daily_kr", "success", "17T23:30"), _r("daily_kr", "skipped", "18T23:30")]) == []
    assert il.OPEN_STATUS == ("failed",) and il.CLOSE_STATUS == ("success", "partial")


def test_원인은_머리만_싣는다() -> None:
    assert il._갈래("RuntimeError: 계좌 12345678 을 못 읽음") == "RuntimeError"
    assert il._갈래(None) == "까닭 없음" and il._갈래("  ") == "까닭 없음"
    assert il._갈래("가" * 100) == "가" * il.CAUSE_MAX == "가" * 40
    assert il._갈래("첫 줄\n둘째 줄: 본문") == "첫 줄"


def test_시각을_못_읽으면_복구_시간만_모름이다() -> None:
    runs = [ws.Run("x", None, "failed", "가 — 모르는 때", "E: 1", ()), ws.Run("x", None, "success", "나 — 그 뒤", None, ())]
    assert il.incidents(runs)[0]["minutes"] is None and il.incidents(runs)[0]["closed"] == "나 — 그 뒤"
    # 시간대가 섞여도 죽지 않는다
    섞임 = [ws.Run("x", None, "failed", "2026-11-17T23:30:00", "E: 1", ()),
            ws.Run("x", None, "success", "2026-11-18T00:30:00+00:00", None, ())]  # fmt: skip
    assert il.incidents(섞임)[0]["minutes"] is None


def test_요약_줄() -> None:
    assert il.lines([]) == ["운영 사고(실패 묶음 → 복구): 0건"]
    found = il.incidents([
        _r("daily_kr", "failed", "17T23:30", "KR", "TursoError: x"), _r("daily_kr", "success", "18T00:35", "KR"),
        _r("daily_us", "failed", "17T13:30", "US", "KisFailed: y"), _r("daily_us", "success", "19T13:30", "US"),
        _r("scores", "failed", "18T01:00", None, "E: z"), _r("scores", "success", "18T01:20", None),
        _r("signals", "failed", "20T01:00", None, "Open: w"),
        _r("metrics", "failed", "16T01:00", None, "M: v"), _r("metrics", "success", "16T04:00", None),
    ])  # fmt: skip
    줄 = il.lines(found)
    # 닫힌 넷: 20·65·180·2880분 → 중앙값(아래쪽) 65분, 가장 긴 것 2일
    assert 줄[0] == "운영 사고(실패 묶음 → 복구): 5건 · 복구 중앙값 65분 · 가장 긴 것 2.0일 · 아직 열림 1건"
    # 시각은 한국 시각(25.2290) — UTC 01:00 = KST 10:00
    assert 줄[1] == "  signals: 11-20 10:00 부터 실패·중단 1번 · 아직 복구 안 됨 · Open"  # 열린 것이 맨 앞
    assert 줄[2] == "  daily_us(US): 11-17 22:30 부터 실패·중단 1번 · 복구 2.0일 · KisFailed"
    assert 줄[3] == "  metrics: 11-16 10:00 부터 실패·중단 1번 · 복구 3.0시간 · M"
    assert 줄[4] == "  … 그 밖 2건" and len(줄) == 5 and il.TOP_N == 3
    assert il._분(119) == "119분" and il._분(120) == "2.0시간" and il._분(2879) == "48.0시간" and il._분(2880) == "2.0일"


def _s(job: str, status: str, when: str, sha: str | None, err: str | None = None) -> ws.Run:
    return ws.Run(job, "KR", status, f"2026-11-{when}:00+00:00", err, (), None, sha)


def test_걸친_날_수는_한국_날짜로_센다() -> None:
    """25.2295 — 영향의 첫 조각. 국내 일일 배치 08:27 KST = 전날 23:27 UTC: UTC 날짜로 세면 같은 한국 날의 재시도
    (23:27 UTC 와 이튿날 00:10 UTC)가 이틀로 갈린다"""
    하루 = il.incidents([_r("daily_kr", "failed", "17T23:27", err="E: 1"), _r("daily_kr", "failed", "18T00:10", err="E: 2"),
                       _r("daily_kr", "success", "18T00:40")])  # fmt: skip
    assert 하루[0]["failures"] == 2 and 하루[0]["days"] == 1
    # 반대 경계: UTC 로는 같은 날(14:00·15:30)이지만 한국 날짜는 둘(23:00 · 이튿날 00:30)
    둘 = il.incidents([_r("x", "failed", "17T14:00"), _r("x", "failed", "17T15:30"), _r("x", "success", "17T16:00")])
    assert 둘[0]["days"] == 2
    사흘 = il.incidents([_r("daily_kr", "failed", "17T23:27"), _r("daily_kr", "failed", "18T23:27"),
                       # 건너뜀은 날 수에 들지 않는다 — 실패가 없는 한국 날짜(11-20 01:00)에 둔다(25.2298: 처음엔
                       # 실패가 선 날에 둬 세어도 3 이었다)
                       _r("daily_kr", "skipped", "19T16:00"),
                       _r("daily_kr", "failed", "20T23:27")])  # fmt: skip
    assert 사흘[0]["days"] == 3 and 사흘[0]["closed"] is None
    # 정리된 줄(끝맺지 못한 기록)은 날 수에 든다 — 사고를 여는 줄이다(25.2298)
    from batch.core import db

    죽음 = f"{db.REAPED_PREFIX}. 정리"
    정리 = il.incidents([_r("z", "skipped", "17T01:00", err=죽음), _r("z", "skipped", "18T01:00", err=죽음)])
    assert 정리[0]["days"] == 2 and 정리[0]["failures"] == 2
    # 미국 작업은 미국 동부 날짜로 센다(25.2298) — 13:27·15:05 UTC 는 한국 22:27·이튿날 00:05 지만 한 미국 거래일
    미국 = il.incidents([_r("daily_us", "failed", "17T13:27", "US"), _r("daily_us", "failed", "17T15:05", "US"),
                       _r("daily_us", "success", "18T13:27", "US")])  # fmt: skip
    assert 미국[0]["days"] == 1
    # 같은 시각이 국내(또는 시장 없는) 작업이면 한국 날짜 둘
    assert il.incidents([_r("q", "failed", "17T13:27", None), _r("q", "failed", "17T15:05", None)])[0]["days"] == 2
    # 미국 동부 자정(05:00 UTC, 겨울)을 사이에 두면 미국도 이틀
    assert il.incidents([_r("daily_us", "failed", "17T04:30", "US"),
                         _r("daily_us", "failed", "17T05:30", "US")])[0]["days"] == 2  # fmt: skip
    # 닫힌 뒤 새 사고는 날 수를 처음부터 센다(앞 사고의 날이 남지 않는다)
    다시 = il.incidents([_r("y", "failed", "16T01:00"), _r("y", "success", "16T02:00"),
                       _r("y", "failed", "18T01:00"), _r("y", "failed", "19T01:00")])  # fmt: skip
    assert [x["days"] for x in 다시] == [1, 2]
    # 줄: 이틀부터 적는다(`SPAN_SHOW_DAYS`) — 하루 안의 사고에는 붙지 않는다
    assert il.SPAN_SHOW_DAYS == 2
    assert "실패·중단 2번 · 복구" in il.lines(하루)[1] and "한 날" not in il.lines(하루)[1]
    # 달력 폭이 아니라 **날의 수**다(25.2298 — "N일에 걸침" 은 금·월 이틀 실패에서 "복구 4.0일" 과 맞서 읽혔다)
    assert "실패·중단 2번 · 실패·중단한 날 2일 · 복구" in il.lines(둘)[1]
    assert "실패·중단 3번 · 실패·중단한 날 3일 · 아직 복구 안 됨" in il.lines(사흘)[1]
    assert "걸침" not in "".join(il.lines(사흘))
    # 날 수 칸이 None 인 사고를 줘도 줄은 깨지지 않는다
    assert "한 날" not in il.lines([{**사흘[0], "days": None}])[1]
    # 시간대 없는 값은 원문 날짜 그대로
    assert il._날("2026-11-17T23:27:00") == "2026-11-17" and il._날("2026-11-17T23:27:00+00:00") == "2026-11-18"


def test_같은_코드로_복구됐는지를_가른다() -> None:
    """25.2295 — 첫 실패의 코드와 닫은 실행의 코드. 같으면 바깥 까닭이 지나간 것, 다르면 그 사이 코드가 바뀜"""
    같음 = il.incidents([_s("a", "failed", "17T01:00", "abc"), _s("a", "failed", "17T02:00", "zzz"),
                       _s("a", "success", "17T03:00", "abc")])  # fmt: skip
    assert 같음[0]["same_code"] is True  # **첫** 실패의 코드와 견준다 — 가운데 줄이 아니다
    다름 = il.incidents([_s("a", "failed", "17T01:00", "abc"), _s("a", "partial", "17T03:00", "def")])
    assert 다름[0]["same_code"] is False
    # 어느 한쪽이라도 모르면 모름 — 지문 이전 줄·로컬 실행·정리된 줄. 빈 글도 모름이다
    for 앞, 뒤 in ((None, "abc"), ("abc", None), (None, None), ("", "")):
        assert il.incidents([_s("a", "failed", "17T01:00", 앞), _s("a", "success", "17T03:00", 뒤)])[0]["same_code"] is None
    열림 = il.incidents([_s("a", "failed", "17T01:00", "abc")])
    assert 열림[0]["same_code"] is None
    # 닫힌 뒤 새 사고는 제 첫 실패의 코드로 다시 견준다(앞 사고의 코드가 남지 않는다)
    둘 = il.incidents([_s("a", "failed", "17T01:00", "abc"), _s("a", "success", "17T02:00", "abc"),
                     _s("a", "failed", "18T01:00", "def"), _s("a", "success", "18T02:00", "def")])  # fmt: skip
    # 둘째 사고는 def → def 라 참이다 — 앞 사고의 abc 를 물려받으면 거짓이 된다(25.2298: 처음엔 def → ghi 라
    # 물려받아도 거짓이어서 못 잡았다)
    assert [x["same_code"] for x in 둘] == [True, True]
    # 부분 성공으로 닫히면서 코드가 같은 경우
    assert il.incidents([_s("a", "failed", "17T01:00", "abc"), _s("a", "partial", "17T03:00", "abc")])[0]["same_code"] is True
    # 배치 코드 지문(`code`, 25.2302)이 둘 다 있으면 그것으로 — 문서만 바꾼 커밋(sha 다름·code 같음)은 "같은 코드"
    def _k(st: str, when: str, sha: str | None, code: str | None) -> ws.Run:
        return ws.Run("a", "KR", st, f"2026-11-{when}:00+00:00", None, (), None, sha, code)

    assert il.incidents([_k("failed", "17T01:00", "abc", "c1"), _k("success", "17T03:00", "def", "c1")])[0]["same_code"] is True
    assert il.incidents([_k("failed", "17T01:00", "abc", "c1"), _k("success", "17T03:00", "abc", "c2")])[0]["same_code"] is False
    # 한쪽만 code 가 있으면(지문이 생기기 전 줄) 커밋으로 견준다 — 갈래를 섞지 않는다
    assert il.incidents([_k("failed", "17T01:00", "abc", None), _k("success", "17T03:00", "abc", "c2")])[0]["same_code"] is True
    assert il.incidents([_k("failed", "17T01:00", None, "c1"), _k("success", "17T03:00", "abc", None)])[0]["same_code"] is None
    # `sha` 속성이 없는 줄(다른 호출자)도 깨지지 않는다
    from types import SimpleNamespace as NS

    없음 = [NS(job_name="a", market=None, status="failed", started_at="2026-11-17T01:00:00+00:00", error_text="E"),
            NS(job_name="a", market=None, status="success", started_at="2026-11-17T02:00:00+00:00", error_text=None)]  # fmt: skip
    assert il.incidents(없음)[0]["same_code"] is None
    # 줄
    assert il.lines(같음)[1].endswith("· 복구 2.0시간 · 같은 코드로 복구 · 까닭 없음")
    assert il.lines(다름)[1].endswith("· 복구 2.0시간 · 코드가 바뀐 뒤 복구 · 까닭 없음")
    assert "코드" not in il.lines(열림)[1]
    # 옛 꼴(새 칸 없는 사고)을 줄로 만들어도 깨지지 않는다
    옛 = [{"job": "a", "market": None, "opened": "2026-11-17T01:00:00+00:00", "closed": None, "failures": 1,
          "minutes": None, "cause": "E"}]  # fmt: skip
    assert il.lines(옛)[1] == "  a: 11-17 10:00 부터 실패·중단 1번 · 아직 복구 안 됨 · E"


def test_실행_기록에서_코드를_읽는다() -> None:
    """이음매 — `load_runs` 가 `step_log.fingerprint.sha` 를 `Run.sha` 로 싣는다. 꼴이 틀리면 None"""
    import json

    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    for i, 로그 in enumerate([{db.RUN_FINGERPRINT_KEY: {"sha": "abc123", "code": "c0ffee"}},
                             {db.RUN_FINGERPRINT_KEY: {"sha": None, "code": 7}},
                             {db.RUN_FINGERPRINT_KEY: "글"}, {"warnings": []}, None, {db.RUN_FINGERPRINT_KEY: {"sha": 7}},
                             {db.RUN_FINGERPRINT_KEY: {"sha": ""}}, [1], "글"]):  # fmt: skip
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, status, started_at, step_log) VALUES (?,?,?,?,?,?)",
            [f"j{i}", "KR", "2026-11-17", "failed", f"2026-11-17T0{i}:00:00+00:00", json.dumps(로그) if 로그 is not None else None],
        )
    assert [r.sha for r in ws.load_runs(mem, "2026-11-01")] == ["abc123", None, None, None, None, None, None, None, None]  # type: ignore[arg-type]
    assert [r.code for r in ws.load_runs(mem, "2026-11-01")][:2] == ["c0ffee", None]  # type: ignore[arg-type]
    # 실제로 닫는 길이 그 칸을 채운다 — 지문의 이름이 바뀌면 여기서 깨진다
    assert "sha" in db.run_fingerprint() and db.RUN_FINGERPRINT_KEY == "fingerprint"


def test_요약_실행이_대장을_싣는다(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """움직임 시험(25.2295) — 소스 글 대조가 아니라 `run(dry_run=True)` 가 찍는 요약에 사고 줄이 있다"""
    import json

    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.close = lambda: None  # type: ignore[method-assign]
    now = datetime.now(UTC)
    from datetime import timedelta

    for 상태, 앞, 코드 in (("failed", 50, "abc"), ("failed", 49, "abc"), ("success", 48, "def")):
        t = (now - timedelta(hours=앞)).isoformat(timespec="seconds")
        mem.conn.execute(
            "INSERT INTO batch_runs (job_name, market, trade_date, status, started_at, error_text, step_log)"
            " VALUES ('daily_kr','KR',?,?,?,?,?)",
            [t[:10], 상태, t, "TursoError: x" if 상태 == "failed" else None, json.dumps({db.RUN_FINGERPRINT_KEY: {"sha": 코드}})],
        )
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    monkeypatch.setattr(ws, "load_losses", lambda c, now, through, 끝: (0, 0, []))
    찍힘: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: 찍힘.append(" ".join(str(x) for x in a)))
    assert ws.run(dry_run=True) == 0
    글 = "\n".join(찍힘)
    assert "운영 사고(실패 묶음 → 복구): 1건 · 복구 중앙값 2.0시간" in 글
    assert "daily_kr(KR): " in 글 and "실패·중단 2번" in 글 and "코드가 바뀐 뒤 복구 · TursoError" in 글
    # 대장이 던져도 요약은 나간다
    monkeypatch.setattr(ws.incident_ledger, "incidents", lambda runs: 1 / 0)
    찍힘.clear()
    assert ws.run(dry_run=True) == 0
    assert "운영 사고 절을 만들지 못했습니다 (ZeroDivisionError)" in "\n".join(찍힘)


def test_주간_요약에_실린다() -> None:
    now = datetime(2026, 11, 21, 22, 41, tzinfo=UTC)
    runs = [_r("daily_kr", "failed", "17T23:30", "KR", "TursoError: x"), _r("daily_kr", "success", "18T00:35", "KR")]
    글 = ws.compose(runs=runs, last_success={}, now=now, reads_used=None,
                   incident_lines=il.lines(il.incidents(runs)))  # fmt: skip
    assert "운영 사고(실패 묶음 → 복구): 1건 · 복구 중앙값 65분 · 가장 긴 것 65분" in 글
    # 국내 일일 배치 08:30 KST = 전날 23:30 UTC — 한국 날짜로 실린다(예전엔 하루 앞 밤 시각으로 읽혔다)
    assert "daily_kr(KR): 11-18 08:30 부터 실패·중단 1번 · 복구 65분 · TursoError" in 글
    # 안 주면 절이 없다(예전 요약 그대로)
    assert "운영 사고" not in ws.compose(runs=runs, last_success={}, now=now, reads_used=None)
    # 이음매 — 요약 실행이 대장을 만들어 넘긴다
    글 = inspect.getsource(ws.run)
    assert "사고 = incident_ledger.lines(incident_ledger.incidents(runs))" in 글 and "incident_lines=사고" in 글
    assert "운영 사고 절을 만들지 못했습니다" in 글  # 이 절이 깨져도 요약은 나간다(다른 곁다리 절과 같은 꼴)


def test_읽지도_쓰지도_않는다_그리고_문서() -> None:
    글 = inspect.getsource(il)
    for 금지 in ("execute", "client", "SELECT", "INSERT", "UPDATE"):
        assert 금지 not in 글, 금지
    문서 = (뿌리 / "docs" / "health.md").read_text()
    for 조각 in ("### 7.3 운영 사고 대장", "25.2289", f"{il.CAUSE_MAX}자 `CAUSE_MAX`", f"긴 것부터 {il.TOP_N}건(`TOP_N`)", "새 표를 만들지 않는다",
               "건너뜀·도는 중·확인 실행은 사고를 열지도 닫지도 않는다", "아직 열림", "한국 시각", "끝맺지 못한 기록",
               "영향", "문장 머리", "25.2295", "이틀(`SPAN_SHOW_DAYS`)부터", "같은 코드로 복구", "코드가 바뀐 뒤 복구",
               "그 고침 덕인지는 모른다", "실패·중단 N번", "25.2298", "미국 동부 날짜", "실패·중단한 날 N일", "달력 폭이 아니다",
               "문서만 바꾼 커밋", "25.2300", "**무응답**", "**검산 틀린 날**", "앞 다섯(`OTHER_TOP_N`)"):  # fmt: skip
        assert 조각 in 문서, 조각


def test_실행_밖의_무응답과_검산_틀린_날() -> None:
    """25.2300 — 아예 안 돈 실행(무응답)과 장부 검산이 틀린 날은 실행 줄에 없다. 건수와 이름만 싣는다"""
    assert il.other_lines([], []) == ["무응답·검산 틀린 날: 없음"]
    줄 = il.other_lines([("daily_kr", "KR", "2026-11-18"), ("daily_us", "US", "2026-11-17"), ("daily_kr", "KR", "2026-11-18")],
                       [("H", "2026-11-18"), ("B", "2026-11-18"), ("H", "2026-11-19")])  # fmt: skip
    # 겹친 값은 한 번 · 날짜 차례
    assert 줄[0] == "무응답(기대 시각까지 성공 없음): 2건 — daily_us(US) 11-17 · daily_kr(KR) 11-18"
    # 검산은 **날** 수 — 같은 날 두 계좌는 하루(관문 무사고 칸과 같은 날 단위)
    assert 줄[1] == "검산 틀린 날: 2일 · 계좌 B, H · 11-18, 11-19"
    assert il.other_lines([], [("H", "2026-11-18")]) == ["검산 틀린 날: 1일 · 계좌 H · 11-18"]
    많음 = il.other_lines([("j", "KR", f"2026-11-{d:02d}") for d in range(10, 17)], [])
    assert 많음[0].endswith("j(KR) 11-14 … 그 밖 2건") and 많음[0].startswith("무응답(기대 시각까지 성공 없음): 7건")
    # 경계: 다섯이면 꼬리가 없고 여섯이면 "그 밖 1건"
    assert "그 밖" not in il.other_lines([("j", "KR", f"2026-11-{d:02d}") for d in range(10, 15)], [])[0]
    assert il.other_lines([("j", "KR", f"2026-11-{d:02d}") for d in range(10, 16)], [])[0].endswith("… 그 밖 1건")
    assert il.other_lines([], [("H", f"2026-11-{d:02d}") for d in range(10, 16)])[0].endswith("… 그 밖 1일")
    assert il.OTHER_TOP_N == 5
    많은날 = il.other_lines([], [("H", f"2026-11-{d:02d}") for d in range(10, 17)])
    assert 많은날[0] == "검산 틀린 날: 7일 · 계좌 H · 11-10, 11-11, 11-12, 11-13, 11-14 … 그 밖 2일"


def test_요약_실행이_무응답과_검산을_읽어_싣는다(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """이음매(25.2300) — 실제 스키마에서 `run(dry_run=True)` 가 두 표를 7일 창으로 읽는다. 창 밖·다른 갈래는 빠진다"""
    from datetime import timedelta

    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.close = lambda: None  # type: ignore[method-assign]
    now = datetime.now(UTC)
    안 = (now - timedelta(days=2)).isoformat(timespec="seconds")
    밖 = (now - timedelta(days=20)).isoformat(timespec="seconds")
    # 달력 알림(job 'calendar')은 무응답이 아니다 — 갈래를 안 거르면 따로 한 건이 된다
    for 작업, 종류, 언제, 날 in (("daily_kr", "missing", 안, 안[:10]), ("calendar", "calendar", 안, 안[:10]),
                              ("daily_kr", "missing", 밖, 밖[:10])):  # fmt: skip
        mem.conn.execute("INSERT INTO health_alerts (job, market, local_date, kind, message, data, created_at)"
                         " VALUES (?, 'KR', ?, ?, 'm', '{}', ?)", [작업, 날, 종류, 언제])  # fmt: skip
    for 계좌, 날, ok in (("H", 안[:10], 0), ("B", 안[:10], 1), ("H", 밖[:10], 0)):
        mem.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw,"
                         " pnl_krw, twr_index, positions_json, recon_ok, updated_at) VALUES (?, ?, 1, 1, 0, 1, 0, 1.0, '[]', ?, 't')",
                         [계좌, 날, ok])  # fmt: skip
    monkeypatch.setattr(ws, "TursoClient", lambda: mem)
    monkeypatch.setattr(ws, "load_losses", lambda c, now, through, 끝: (0, 0, []))
    찍힘: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: 찍힘.append(" ".join(str(x) for x in a)))
    assert ws.run(dry_run=True) == 0
    글 = "\n".join(찍힘)
    assert f"무응답(기대 시각까지 성공 없음): 1건 — daily_kr(KR) {안[5:10]}" in 글
    assert f"검산 틀린 날: 1일 · 계좌 H · {안[5:10]}" in 글
    # 읽기가 깨져도 대장 줄은 남는다
    진짜 = mem.execute

    def 깨짐(sql, params=None):  # type: ignore[no-untyped-def]
        if sql == ws.RECON_BAD_SQL:
            raise RuntimeError("x")
        return 진짜(sql, params)

    monkeypatch.setattr(mem, "execute", 깨짐)
    찍힘.clear()
    assert ws.run(dry_run=True) == 0
    글 = "\n".join(찍힘)
    assert "운영 사고(실패 묶음 → 복구)" in 글 and "무응답·검산 줄을 만들지 못했습니다 (RuntimeError)" in 글
