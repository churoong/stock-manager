"""종합 점수에 들어간 센티먼트를 **남기는지** (docs/infra.md 25.66).

2026-09-21 까지 `jobs/scores.py` 가 `scores.sentiment_score` 에 `None` 을 넣으면서
"센티먼트는 Step 7 에서 붙는다" 고 적어 두었다. **붙이는 코드는 어디에도 없었다.**
그런데 같은 자리에서 `total_score(sentiment=…)` 로 **총점에는 이미 섞고 있었다.**

그래서 이렇게 됐다.

- 총점은 센티먼트를 반영하는데 **그 값이 어디에도 남지 않는다** →
  화면이 "항상 분리 표시" 할 수 없다 (CLAUDE.md 스코어링 규칙)
- 매수 시점 스냅샷 `sentiment_at_trade` 가 **늘 NULL** 이다.
  설계서가 "매수 당시 근거를 얼려둔다. 복기의 기준이다" 라고 부른 바로 그 값이다
  (`web/app/api/trades/route.ts` 가 `scores.sentiment_score` 를 그대로 베낀다)

25.65(베타)와 같은 모양이다 — **계산해서 쓰고는 기록하지 않는다.**
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from batch.core import db
from batch.core.turso import ResultSet
from batch.jobs import scores as job
from batch.services import scoring as sc

뿌리 = Path(__file__).resolve().parent.parent

다섯팩터 = {"value": 60.0, "quality": 70.0, "growth": 50.0, "momentum": 80.0, "risk": 40.0}


class Test총점이_쓴_값을_돌려준다:
    def test_섞었으면_그_값을_돌려준다(self) -> None:
        결과 = sc.total_score(다섯팩터, {f: 20.0 for f in 다섯팩터}, sentiment=40.0, sentiment_weight=10.0)

        assert 결과.sentiment_used == 40.0
        assert 결과.sentiment_weight_used == 10.0

    def test_가중치가_0이면_안_섞었으니_None(self) -> None:
        결과 = sc.total_score(다섯팩터, {f: 20.0 for f in 다섯팩터}, sentiment=40.0, sentiment_weight=0.0)

        assert 결과.sentiment_used is None, "안 쓴 값을 '썼다' 고 남기면 근거가 거짓이 된다"
        assert 결과.sentiment_weight_used == 0.0

    def test_값이_없으면_None(self) -> None:
        결과 = sc.total_score(다섯팩터, {f: 20.0 for f in 다섯팩터}, sentiment=None, sentiment_weight=10.0)

        assert 결과.sentiment_used is None

    def test_음수도_그대로_남는다(self) -> None:
        # 센티먼트 축은 -100~+100 이다. 0~100 으로 옮긴 값이 아니라 **원래 값**을 남긴다
        결과 = sc.total_score(다섯팩터, {f: 20.0 for f in 다섯팩터}, sentiment=-30.0, sentiment_weight=10.0)

        assert 결과.sentiment_used == -30.0

    def test_팩터가_모자라_건너뛴_경우도_None(self) -> None:
        모자람 = {"value": 60.0, "quality": None, "growth": None, "momentum": 80.0, "risk": 40.0}

        결과 = sc.total_score(모자람, {f: 20.0 for f in 다섯팩터}, sentiment=40.0, sentiment_weight=10.0)

        assert 결과.total is None
        assert 결과.sentiment_used is None

    def test_섞은_값이_총점을_실제로_움직인다(self) -> None:
        """남기는 값이 **총점에 쓰인 그 값**인지. 안 그러면 남겨도 소용없다."""
        가중 = {f: 20.0 for f in 다섯팩터}
        안섞음 = sc.total_score(다섯팩터, 가중, sentiment=None, sentiment_weight=10.0)
        섞음 = sc.total_score(다섯팩터, 가중, sentiment=100.0, sentiment_weight=10.0)

        assert 섞음.total is not None and 안섞음.total is not None
        assert 섞음.total > 안섞음.total
        assert 섞음.sentiment_used == 100.0


class MemClient:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        cur = self.conn.execute(sql, args or [])
        cols = [d[0] for d in cur.description or []]
        return ResultSet(
            columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid
        )

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        return [self.execute(sql, args) for sql, args in statements]

    def close(self) -> None:
        pass


class Test적재가_그_값을_넣는다:
    """열 목록과 넣는 줄이 어긋나면 엉뚱한 칸에 들어간다 — 실제 스키마에 넣어 본다."""

    @pytest.fixture
    def client(self) -> MemClient:
        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, '005930', 'KOSPI', 'KR', '회사', 'KRW', 'active', 't', 't')"
        )
        return mem

    def 한줄넣기(self, client: MemClient, total: sc.TotalScore) -> sqlite3.Row:
        행 = (
            1, "2026-09-18", total.total,
            json.dumps(다섯팩터, ensure_ascii=False),
            total.sentiment_used, total.sentiment_weight_used,
            json.dumps(total.weights_used, ensure_ascii=False),
            1, 1, total.skip_reason, sc.CALC_VERSION, "t",
        )  # fmt: skip
        job._bulk(client, "scores", job._SCORE_COLS, "stock_id, as_of_date, calc_version", [행])  # type: ignore[arg-type]
        return client.conn.execute(
            "SELECT sentiment_score, sentiment_weight_used, total_score FROM scores"
        ).fetchone()

    def test_섞은_값이_열에_들어간다(self, client: MemClient) -> None:
        total = sc.total_score(다섯팩터, {f: 20.0 for f in 다섯팩터}, sentiment=25.0, sentiment_weight=10.0)

        행 = self.한줄넣기(client, total)

        assert 행[0] == 25.0, "총점에는 섞고 열에는 안 남겼다"
        assert 행[1] == 10.0

    def test_안_섞었으면_비어_있다(self, client: MemClient) -> None:
        total = sc.total_score(다섯팩터, {f: 20.0 for f in 다섯팩터}, sentiment=None, sentiment_weight=10.0)

        행 = self.한줄넣기(client, total)

        assert 행[0] is None


def test_적재가_None_을_박아_넣지_않는다() -> None:
    """`scores.py` 가 다시 상수 None 을 쓰면 위 테스트를 다 통과하고도 운영은 비어 있다."""
    글 = (뿌리 / "batch" / "jobs" / "scores.py").read_text(encoding="utf-8")
    조각 = 글.split("score_rows = [", 1)[1].split("for stock_id, total in totals.items()", 1)[0]

    assert "total.sentiment_used" in 조각
    assert not re.search(r"^\s*None,\s*#", 조각, re.M), "센티먼트 칸에 상수 None 이 돌아왔다"


def test_매수_스냅샷이_이_열을_베낀다() -> None:
    """이 값이 비면 `sentiment_at_trade` 도 빈다 — 설계서가 부른 '복기의 기준' 이다."""
    경로 = (뿌리 / "web" / "app" / "api" / "trades" / "route.ts").read_text(encoding="utf-8")

    assert "sentiment_score" in 경로, "매수 스냅샷이 더 이상 이 열을 안 쓴다면 이 테스트를 고쳐라"
