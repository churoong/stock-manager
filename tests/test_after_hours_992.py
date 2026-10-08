"""보유 종목 시간외 단일가 알림 (docs/intraday.md 1.2, docs/infra.md 25.992)."""

from __future__ import annotations

from batch.services import after_hours as ah


def test_문턱은_장중과_같은_설정_없거나_범위밖이면_기본() -> None:
    assert ah.spike_pct('{"spike_pct": 3, "volume_multiple": 3}') == 3.0
    assert ah.spike_pct(None) == ah.DEFAULT_SPIKE_PCT
    assert ah.spike_pct('{"spike_pct": 0.001}') == ah.DEFAULT_SPIKE_PCT
    assert ah.spike_pct("깨짐") == ah.DEFAULT_SPIKE_PCT


def test_문턱_이상만_체결_없으면_안_본다() -> None:
    보유 = [(1, "005930", "삼성전자"), (2, "000660", "SK하이닉스"), (3, "035420", "NAVER")]
    시세 = {"005930": {"price": 255000.0, "change_pct": -5.0, "volume": 1200},
            "000660": {"price": 1700000.0, "change_pct": 4.9, "volume": 10}, "035420": None}  # fmt: skip
    got = ah.hits(보유, 시세, 5.0)
    assert [h["stock_id"] for h in got] == [1]
    assert got[0]["message"] == "삼성전자(005930) 시간외 단일가 -5.0% · 255,000원 · 거래량 1,200주"
    assert got[0]["data"]["threshold_pct"] == 5.0


def test_마감된_시간외의_거래일로_적는다() -> None:
    """25.1012: 예약이 01:25 KST 에 돌아 다음 날 날짜를 붙였다. 2026-10-07(수)·10-08(목) 은 거래일."""
    from datetime import UTC, date, datetime

    from batch.services import after_hours as ah

    assert ah.session_day(datetime(2026, 10, 7, 16, 25, tzinfo=UTC)) == date(2026, 10, 7)  # 10-08 01:25 KST
    assert ah.session_day(datetime(2026, 10, 7, 9, 17, tzinfo=UTC)) == date(2026, 10, 7)  # 18:17 KST 당일
    assert ah.session_day(datetime(2026, 10, 7, 7, 30, tzinfo=UTC)) is None  # 16:30 KST 진행 중
    assert ah.session_day(datetime(2026, 10, 7, 3, 0, tzinfo=UTC)) == date(2026, 10, 6)  # 12:00 KST — 전 거래일


def test_조용시간은_웹과_같이_가른다() -> None:
    from datetime import UTC, datetime

    from batch.services import after_hours as ah

    새벽 = datetime(2026, 10, 7, 16, 25, tzinfo=UTC)  # 01:25 KST
    assert ah.quiet_state(None, 새벽) == (True, True)
    assert ah.quiet_state('{"enabled": false}', 새벽) == (False, True)
    assert ah.quiet_state('{"start": "23:00", "end": "07:00", "deliver_on_release": false}', 새벽) == (True, False)
    assert ah.quiet_state('{"start": "07:00", "end": "07:00"}', 새벽) == (False, True)
    assert ah.quiet_state(None, datetime(2026, 10, 7, 9, 17, tzinfo=UTC)) == (False, True)  # 18:17 KST


def test_조용시간이면_보내지_않고_해제_뒤로_남긴다(monkeypatch) -> None:
    from datetime import UTC, datetime

    from batch.jobs import kis_flows as job
    from batch.notify import telegram

    class Rs:
        def __init__(self, rows=None, scalar=None, affected=1):  # noqa: ANN001
            self.rows, self._s, self.affected_rows = rows or [], scalar, affected

        def scalar(self):  # noqa: ANN201
            return self._s

    넣음: list[list] = []

    class Client:
        def execute(self, sql, args=None):  # noqa: ANN001, ANN201
            if "FROM positions" in sql:
                return Rs(rows=[(1, "005930", "삼성전자")])
            if "quiet_hours" in sql:
                return Rs(scalar='{"deliver_on_release": false}')
            if "alert_thresholds" in sql:
                return Rs(scalar=None)
            if sql.startswith("INSERT INTO alerts"):
                넣음.append(args)
            return Rs()

    class QC:
        def after_hours(self, code):  # noqa: ANN001, ANN201
            return {"price": 70000.0, "change_pct": -8.0, "volume": 10}

    class 고정(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return datetime(2026, 10, 7, 16, 25, tzinfo=UTC)  # 01:25 KST

    보낸: list[str] = []
    monkeypatch.setattr(job, "datetime", 고정)
    monkeypatch.setattr(telegram, "send", 보낸.append)
    out = job._after_hours(Client(), QC(), None, {})  # type: ignore[arg-type]
    assert 보낸 == [] and out["quiet"] is True and out["day"] == "2026-10-07"
    assert 넣음[0][1] == "2026-10-07" and 넣음[0][-1] == "quiet-skipped"
