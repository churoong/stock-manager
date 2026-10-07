"""끝맺지 못한 `batch_runs` 기록을 닫는다 (docs/infra.md 25.18).

**있었던 일.** 2026-09-19 D1 따라잡기에서 `financials` 가 D1 하루 쓰기 한도에 걸렸다.
`guard()` 가 한도 오류를 "건너뜀"(종료코드 0)으로 바꾸므로 워크플로 단계는 `success` 로 찍혔는데,
이미 열어 둔 `batch_runs` 행은 8시간 뒤까지 `running` 인 채로 남아 있었다. 운영 이력이 "지금도
돌고 있다" 고 거짓말을 한 것이다.

**두 겹으로 닫는다.** 한도에 걸린 그 순간에는 닫는 UPDATE 조차 쓰기라 막힐 수 있다.
1. `guard()` 가 곧바로 닫아 본다 (`close_current_run`) — 되면 가장 깔끔하다
2. 막혀서 못 닫았으면 **다음 실행이 대신 닫는다** (`reap_stale_runs`) — 다음 날에는 예산이 있다

`ran_within()` 은 `success`/`partial` 만 세므로 열린 행이 건너뛰기를 일으키지는 않는다.
이건 기록이 사실을 말하게 하려는 것이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from batch.core import db
from batch.core.entry import guard
from batch.core.turso import TursoError


class 가짜클라이언트:
    """batch_runs 만 흉내 낸다. 질의를 기록해 무엇을 했는지 본다."""

    def __init__(self, 열린기록: list[tuple[int, str]] | None = None, 쓰기막힘: bool = False) -> None:
        self.열린기록 = 열린기록 or []  # (id, started_at)
        self.쓰기막힘 = 쓰기막힘
        self.닫은것: list[tuple[int, str, str | None]] = []
        self.다음_id = 100

    def execute(self, sql: str, params: list[Any] | None = None) -> Any:
        params = params or []
        if sql.startswith("SELECT id FROM batch_runs"):
            cutoff = params[1]
            return _결과([(i,) for i, started in self.열린기록 if started < cutoff])
        if sql.startswith("UPDATE batch_runs"):
            if self.쓰기막힘:
                raise TursoError("D1 HTTP 400: exceeded D1's free tier daily row write limit")
            self.닫은것.append((params[4], params[1], params[3]))
            return _결과([])
        if sql.startswith("INSERT INTO batch_runs"):
            if self.쓰기막힘:
                raise TursoError("D1 HTTP 400: exceeded D1's free tier daily row write limit")
            # 실행마다 번호가 달라야 한다. 같은 번호를 주면 겹쳐 여는 경우를 볼 수 없다
            self.다음_id += 1
            return _결과([])
        if sql.startswith("SELECT MAX(id)"):
            return _결과([(self.다음_id,)])
        return _결과([])


class _결과:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows
        self.columns: list[str] = []

    def scalar(self) -> Any:
        return self.rows[0][0] if self.rows else None

    def dicts(self) -> list[dict]:
        return []


@pytest.fixture(autouse=True)
def _현재실행_초기화() -> Any:
    """모듈 전역이라 테스트 사이에 새어 나가지 않게 한다."""
    db._열린_실행.clear()
    yield
    db._열린_실행.clear()


def _시각(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


class Test오래된_기록_정리:
    def test_오래_열려_있으면_닫는다(self) -> None:
        client = 가짜클라이언트([(7, _시각(9))])
        assert db.reap_stale_runs(client, "financials") == 1
        run_id, status, note = client.닫은것[0]
        assert (run_id, status) == (7, "skipped")
        assert "끝맺지 못한" in (note or "")

    def test_방금_시작한_것은_건드리지_않는다(self) -> None:
        """지금 돌고 있는 실행을 죽은 것으로 보면 안 된다."""
        client = 가짜클라이언트([(7, _시각(1))])
        assert db.reap_stale_runs(client, "financials") == 0
        assert client.닫은것 == []

    def test_여러_개도_닫는다(self) -> None:
        client = 가짜클라이언트([(7, _시각(9)), (8, _시각(30))])
        assert db.reap_stale_runs(client, "financials") == 2

    def test_정리가_실패해도_본_작업을_막지_않는다(self) -> None:
        """한도에 걸린 날에는 이 UPDATE 도 막힌다. 그때 예외가 올라가면 배치가 죽는다."""
        client = 가짜클라이언트([(7, _시각(9))], 쓰기막힘=True)
        assert db.reap_stale_runs(client, "financials") == 0

    def test_새_실행을_시작할_때_정리한다(self) -> None:
        client = 가짜클라이언트([(7, _시각(9))])
        db.start_batch_run(client, job_name="financials", market="KR", trade_date=None, trigger="manual")
        assert [c[0] for c in client.닫은것] == [7]


class Test내_실행_닫기:
    def test_시작하면_기억하고_끝내면_잊는다(self) -> None:
        client = 가짜클라이언트()
        run_id = db.start_batch_run(client, job_name="f", market=None, trade_date=None, trigger="manual")
        assert db._열린_실행 == [(client, run_id)]
        db.finish_batch_run(client, run_id, status="success")
        assert db._열린_실행 == []

    def test_열어_둔_것이_없으면_아무것도_하지_않는다(self) -> None:
        """예외가 안 나는 것만으로는 모자라다.

        "아무것도 하지 않는다" 를 **쓰기가 한 번도 안 갔다**로 확인한다. 조건이 빠져
        `UPDATE batch_runs SET status = 'skipped'` 가 WHERE 없이 나가면, 예외는 안 나는데
        **지난 모든 실행 기록이 건너뜀으로 바뀐다.** 예외만 보면 그것을 통과시킨다.
        """
        db._열린_실행.clear()
        client = 가짜클라이언트()

        db.close_current_run("skipped")

        assert client.닫은것 == []

    def test_두_번_닫아도_한_번만_쓴다(self) -> None:
        """`guard()` 가 닫고 나서 정상 경로가 또 닫으려 할 수 있다.

        두 번째가 같은 기록을 다시 UPDATE 하면 쓰기를 한 번 더 쓴다 — 하필 **한도에 걸려
        멈추는 길**에서 부르는 함수다(25.6). 한 번 닫았으면 잊어야 한다.
        """
        client = 가짜클라이언트()
        db._열린_실행.append((client, 7))

        db.close_current_run("skipped")
        db.close_current_run("skipped")

        assert len(client.닫은것) == 1
        assert db._열린_실행 == []

    def test_닫기가_막혀도_조용히_넘어간다(self) -> None:
        """한도에 걸린 직후라 이 UPDATE 도 실패할 수 있다. 다음 실행이 대신 닫는다."""
        client = 가짜클라이언트(쓰기막힘=True)
        db._열린_실행.append((client, 7))
        db.close_current_run("skipped", "한도")
        assert db._열린_실행 == []


class Test한도로_멈출_때:
    def test_guard_가_열린_기록을_닫는다(self, capsys: pytest.CaptureFixture[str]) -> None:
        """2026-09-19 financials 가 `running` 으로 남은 그 상황이다."""
        client = 가짜클라이언트()
        db._열린_실행.append((client, 7))

        def main() -> int:
            raise TursoError("D1 HTTP 400: exceeded D1's free tier daily row write limit")

        assert guard(main) == 0
        run_id, status, note = client.닫은것[0]
        assert (run_id, status) == (7, "skipped")
        assert "한도" in (note or "")
        assert "건너뜀" in capsys.readouterr().out

    def test_한도가_아닌_오류는_건너뜀이_아니라_실패로_닫는다(self) -> None:
        """**2026-09-22 에 고쳤다** (docs/infra.md 25.110).

        전에는 아무것도 안 닫고 올려 보냈다. "진짜 실패는 실패로 올라가야 한다" 는 맞지만,
        그것이 **기록을 안 남길 이유는 아니다.** 안 닫으면 행이 `running` 으로 남고 여섯
        시간 뒤 `reap_stale_runs()` 가 `skipped`("끝맺지 못한 기록")로 닫는다 — 실패 사유가
        DB 어디에도 안 남고, 화면은 "건너뜀" 이라고 말한다. **건너뜀과 실패는 다른 사실이다.**
        """
        client = 가짜클라이언트()
        db._열린_실행.append((client, 7))

        def main() -> int:
            raise TursoError("D1 HTTP 401: 인증 실패")

        with pytest.raises(TursoError):
            guard(main)

        run_id, status, note = client.닫은것[0]
        assert (run_id, status) == (7, "failed")
        assert "401" in (note or ""), "사유가 안 남으면 Actions 로그를 열어야 한다 (25.27)"
        assert db._열린_실행 == []

    def test_겹쳐_연_실행을_전부_닫는다(self) -> None:
        """일일 배치는 제 실행을 연 뒤 안에서 점수·신호를 부른다.

        한 칸만 기억하면 안쪽이 시작할 때 **바깥 번호가 지워진다.** 예전 모양이 그랬다.
        """
        client = 가짜클라이언트()
        바깥 = db.start_batch_run(client, job_name="daily_kr", market="KR", trade_date=None, trigger="manual")
        안쪽 = db.start_batch_run(client, job_name="scores", market="KR", trade_date=None, trigger="manual")
        assert [번호 for _c, 번호 in db._열린_실행] == [바깥, 안쪽]

        def main() -> int:
            raise RuntimeError("점수 계산이 깨졌다")

        with pytest.raises(RuntimeError):
            guard(main)

        # 안쪽부터 닫는다. 둘 다 실패로 남아야 무슨 일이 있었는지 알 수 있다
        assert [(i, st) for i, st, _ in client.닫은것] == [(안쪽, "failed"), (바깥, "failed")]

    def test_안쪽이_정상으로_끝나면_바깥은_남는다(self) -> None:
        """끝난 것을 다시 닫으면 안 되고, 안 끝난 것을 잊어도 안 된다."""
        client = 가짜클라이언트()
        바깥 = db.start_batch_run(client, job_name="daily_kr", market="KR", trade_date=None, trigger="manual")
        안쪽 = db.start_batch_run(client, job_name="scores", market="KR", trade_date=None, trigger="manual")
        db.finish_batch_run(client, 안쪽, status="success")

        assert [번호 for _c, 번호 in db._열린_실행] == [바깥]
        db.fail_open_runs("나중에 깨졌다")
        assert [(i, st) for i, st, _ in client.닫은것 if st == "failed"] == [(바깥, "failed")]


def test_실행_기록을_여는_작업은_모두_guard_를_거친다() -> None:
    """**중앙에서 한 번 고쳤으니, 그 중앙을 안 지나는 작업이 없어야 한다** (25.110).

    `batch_runs` 를 여는 작업이 `guard()` 를 안 거치면 죽을 때 아무도 안 닫는다.
    """
    from pathlib import Path

    뿌리 = Path(__file__).resolve().parent.parent
    새는것 = []
    본_작업 = 0
    for path in sorted((뿌리 / "batch" / "jobs").glob("*.py")):
        본문 = path.read_text(encoding="utf-8")
        if "start_batch_run" not in 본문:
            continue
        본_작업 += 1
        if "guard(main" not in 본문:
            새는것.append(path.name)

    assert 본_작업 >= 20, f"실행 기록을 여는 작업을 {본_작업}개밖에 못 찾았다 — 표기가 바뀌었나"
    assert not 새는것, f"guard() 를 안 거치는 작업이 있다. 죽으면 running 으로 남는다: {새는것}"
