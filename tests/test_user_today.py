"""사용자의 "오늘" 은 KST 다 (docs/infra.md 25.125).

**있었던 일.** 보유 평가(`jobs/portfolio`)와 매도 플래그(`jobs/sell_flags`)가 "오늘" 을
`datetime.now(UTC).date()` 로 잡았다. 국내 일일 배치는 **08:27 KST** 에 도는데 그때 UTC 는
아직 **전날 23:27** 이다 — 예외가 아니라 국내 배치가 도는 **매 회** 그렇다.

세 가지가 어긋났다.

1. `positions.as_of_date` · `sell_flags.as_of_date` 가 늘 하루 전 날짜로 적힌다
2. 보유 기간(`held_days`)이 하루 짧다 — 근거표에 "첫 매수 …부터 N일" 로 찍히고
   기간초과 플래그가 하루 늦게 걸린다
3. 근거표의 **"언제 기준"** 칸이 어제 날짜다. CLAUDE.md 절대 규칙이 걸린 자리다
   ("근거에 쓴 모든 수치는 … **언제 기준인지** … 화면에서 펼쳐 볼 수 있어야 한다")

그리고 리포트의 "새로 걸림" 표시에는 **경주**가 있었다. `sell_flags.run` 이 찍은
`first_seen_date` 와 리포트가 대 보는 날짜가 둘 다 UTC 라, 배치가 **자정 UTC
(=09:00 KST)** 를 넘기면 둘이 갈라져 NEW 표시가 통째로 사라진다. 국내 배치는 개장
10분 전(08:50 KST = 23:50 UTC)까지 시작을 허용하므로(`RUN_WINDOW_MIN_BY_MARKET`)
닿을 수 있는 자리였다.

KST 자정은 15:00 UTC 라 국내(08:27 KST)·미국(21:30 KST) 배치 어느 쪽에서도 멀다.
**경계에서 가장 먼 달력**을 고른 것이기도 하다.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from batch.core import calendar as cal

뿌리 = Path(__file__).resolve().parent.parent


class _가짜시계:
    """국내 배치가 실제로 도는 순간. 23:27 UTC = 다음 날 08:27 KST."""

    순간 = datetime(2026, 9, 22, 23, 27, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206
        return cls.순간.astimezone(tz) if tz is not None else cls.순간


class Test사용자의_오늘:
    def test_국내_배치_시각에는_UTC_와_하루_다르다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cal, "datetime", _가짜시계)

        assert cal.user_today() == date(2026, 9, 23), "08:27 KST 인데 오늘이 아니다"
        assert _가짜시계.now(UTC).date() == date(2026, 9, 22), "UTC 로 잡으면 하루 전이다"

    def test_시장_현지_날짜와_다른_뜻이다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`local_today('US')` 는 뉴욕 날짜다. 미국 보유분의 보유 기간을 그것으로 세면 안 된다."""
        monkeypatch.setattr(cal, "datetime", _가짜시계)

        assert cal.local_today("KR") == date(2026, 9, 23)
        assert cal.local_today("US") == date(2026, 9, 22)  # 19:27 ET, 아직 전날
        assert cal.user_today() == cal.local_today("KR")

    def test_사용자_시간대가_한국이다(self) -> None:
        # CLAUDE.md: 사용자는 개인 투자자 1명. 조용시간·리포트 시각·장중 묶음이 전부 KST 다
        assert cal.USER_TIMEZONE == "Asia/Seoul"
        assert cal.user_today() == datetime.now(ZoneInfo("Asia/Seoul")).date()


class Test배치가_그_달력을_쓴다:
    """**부르는 곳이 없으면 함수를 하나 더 만든 것일 뿐이다** (25.123 에서 배운 것)."""

    def _mem(self, monkeypatch: pytest.MonkeyPatch, job):  # noqa: ANN001, ANN202
        from batch.core import db
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        monkeypatch.setattr(job, "TursoClient", lambda: mem)
        db.apply_migrations(mem)  # type: ignore[arg-type]
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')"
        )
        return mem

    def test_매도_플래그가_KST_날짜로_찍힌다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from batch.jobs import sell_flags as job

        mem = self._mem(monkeypatch, job)
        mem.conn.execute(
            "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw,"
            " first_buy_date, horizon, price_date, close, market_value, updated_at)"
            " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-01-02', 'long', '2026-09-22', 70, 700, 't')"
        )
        monkeypatch.setattr(cal, "datetime", _가짜시계)

        assert job.run() == 0

        날짜 = {r[0] for r in mem.conn.execute("SELECT as_of_date FROM sell_flags").fetchall()}
        assert 날짜 == {"2026-09-23"}, f"UTC 날짜(09-22)로 찍혔다: {날짜}"

    def test_보유_기간이_하루_짧지_않다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """근거표에 그대로 찍히는 숫자다. 기간초과 문턱을 하루 늦게 넘는다."""
        from batch.services import sell_flags as sf

        h = sf.HoldingInput(
            stock_id=1, name="에이", currency="KRW", horizon="short", cost=1000.0,
            market_value=1000.0, first_buy_date="2026-01-02", price_date="2026-09-22",
        )  # fmt: skip
        monkeypatch.setattr(cal, "datetime", _가짜시계)

        근거 = [
            c
            for f in sf.evaluate(h, cal.user_today())
            for c in f.criteria
            if "보유" in c.get("label", "")
        ]
        assert 근거, "기간 근거를 못 찾았다 — 이름이 바뀌었나"
        기대 = (date(2026, 9, 23) - date(2026, 1, 2)).days
        assert f"{기대}일" in 근거[0]["display"], 근거[0]
        assert 근거[0]["as_of"] == "2026-09-23"

    def test_보유_평가가_KST_날짜로_찍힌다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from batch.jobs import portfolio as job

        mem = self._mem(monkeypatch, job)
        monkeypatch.setattr(cal, "datetime", _가짜시계)

        assert job.run() == 0

        찍힌날 = mem.conn.execute(
            "SELECT trade_date FROM batch_runs WHERE job_name = 'portfolio'"
        ).fetchone()[0]
        assert 찍힌날 == "2026-09-23", f"UTC 날짜로 찍혔다: {찍힌날}"


def test_사용자를_향한_날짜에_UTC_를_쓰지_않는다() -> None:
    """**그물이다.** 다음에 "오늘" 이 필요할 때 `datetime.now(UTC).date()` 를 다시 적게 된다.

    글자가 아니라 **호출 노드**를 센다(`ast`) — 주석이나 설명문에 적어 둔 것은 안 센다.
    여기 목록은 **사용자에게 보이는 날짜를 만드는 자리**만이다. 수집·점수처럼 거래일을
    받아 쓰는 작업은 상관없다(그쪽은 `as_of=trade_date` 를 넘겨받는다).
    """
    보는곳 = {
        "batch/jobs/portfolio.py": "보유 평가의 as_of. 근거표와 화면에 그대로 뜬다",
        "batch/jobs/sell_flags.py": "매도 플래그의 as_of 와 보유 기간",
    }
    걸린것 = []
    for 이름 in 보는곳:
        나무 = ast.parse((뿌리 / 이름).read_text(encoding="utf-8"))
        for node in ast.walk(나무):
            # datetime.now(UTC).date() 한 덩어리를 찾는다
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "date"
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Attribute)
                and node.func.value.func.attr == "now"
            ):
                걸린것.append(f"{이름}:{node.lineno}")
    assert not 걸린것, (
        "사용자를 향한 날짜를 UTC 로 잡는다. `cal.user_today()` 를 쓴다"
        f" (docs/infra.md 25.125): {걸린것}"
    )
    assert all((뿌리 / 이름).exists() for 이름 in 보는곳), "목록의 파일이 사라졌다"
