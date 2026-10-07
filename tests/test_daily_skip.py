"""건너뛴 일일 배치도 기록을 끝맺는지 (docs/health.md, docs/infra.md 21절).

왜 이 테스트가 있나: 휴장일이거나 너무 늦어 건너뛸 때 시작만 기록하고 끝내지 않으면
`batch_runs.status` 가 'running' 인 채로 영원히 남는다. 그러면 시스템 상태 화면이
"실행 중" 으로 보여 주고, 사람이 "아직 도는 중인가 보다" 로 읽는다. 운영 DB 에서
그런 행을 실제로 발견해 고쳤다 (2026-09-17).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from batch.core import db
from batch.core.turso import ResultSet
from batch.jobs import daily


class MemClient:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")

    def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
        cur = self.conn.execute(sql, args or [])
        cols = [d[0] for d in cur.description or []]
        return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
        return [self.execute(sql, args) for sql, args in statements]

    def close(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MemClient:
    mem = MemClient()
    monkeypatch.setattr(daily, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    return mem


def test_건너뛰면_skipped_로_끝맺는다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.core import calendar as cal

    monkeypatch.setattr(
        daily.cal,
        "decide",
        lambda market, now=None, force=False: cal.RunDecision(
            should_run=False, reason="한국 시장 휴장일", trade_date="2026-09-16", session_date="2026-09-17"
        ),
    )
    assert daily.run("KR") == 0

    row = client.conn.execute(
        "SELECT status, finished_at, step_log FROM batch_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row[0] == "skipped"
    assert row[1] is not None  # 끝난 시각이 찍힌다
    assert "휴장일" in row[2]  # 왜 건너뛰었는지가 남는다
    # 'running' 으로 남은 행이 없다
    assert client.conn.execute("SELECT COUNT(*) FROM batch_runs WHERE status = 'running'").fetchone()[0] == 0
    # 휴장 판정도 달력 기록에 버전·근거와 함께 남는다 (docs/infra.md 25.670, 감사)
    달력 = client.conn.execute("SELECT is_open, source, note FROM market_calendar WHERE date = '2026-09-17'").fetchone()
    assert 달력 is not None and 달력[0] == 0 and "exchange_calendars" in 달력[1] and "라이브러리" in 달력[1]
    assert "calendar" in row[2]


def test_개장일_기록에_마감_시각과_근거가_있다(client: MemClient) -> None:
    """마감 시각이 NULL 로 박혀 반일장이 빠졌다 (25.670, 감사). 미국 2026-11-27 은 13:00 ET 마감."""
    from datetime import date

    from batch.core import calendar as cal

    cal.record_decision(client, "US", date(2026, 11, 27), is_open=True)
    마감, 출처 = client.conn.execute("SELECT close_utc, source FROM market_calendar WHERE date = '2026-11-27'").fetchone()
    assert 마감.startswith("2026-11-27T18:00") and "라이브러리" in 출처
    assert "수동 휴장일" in cal.holiday_basis("KR", date(2026, 6, 3))


def _should_run(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.core import calendar as cal

    monkeypatch.setattr(
        daily.cal,
        "decide",
        lambda market, now=None, force=False: cal.RunDecision(
            should_run=True, reason="거래일", trade_date="2026-09-17", session_date="2026-09-18"
        ),
    )


def test_D1_에서는_미국을_돌리지_않고_사유를_남긴다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # 2026-09-18: D1 에는 미국 마스터가 없어 평일마다 "미국 종목 마스터가 비어 있습니다" 로 실패할 뻔했다
    _should_run(monkeypatch)
    monkeypatch.setattr(daily.backend, "resolved_backend", lambda: "d1")
    monkeypatch.setattr(daily, "collect_prices", lambda *a, **k: pytest.fail("수집하면 안 된다"))
    assert daily.run("US") == 0

    status, step_log = client.conn.execute(
        "SELECT status, step_log FROM batch_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert status == "skipped"
    assert "25.11" in step_log


def test_D1_이어도_국내는_돈다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _should_run(monkeypatch)
    monkeypatch.setattr(daily.backend, "resolved_backend", lambda: "d1")

    class Collected(Exception):
        pass

    def collect(*_args, **_kwargs):
        raise Collected

    monkeypatch.setattr(daily, "collect_prices", collect)
    monkeypatch.setattr(daily, "_notify_failure", lambda *a, **k: None)
    # 수집까지 갔으면 쉬지 않은 것이다 (가짜 수집이 실패로 끝낸다)
    assert daily.run("KR") == 1


def test_미국을_쉬는데_기록이_한도로_막혀도_한도_알림을_보내지_않는다(
    client: MemClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D1 하루 쓰기 한도가 찬 날(2026-09-18 이 그랬다). 쉬는 이유는 한도가 아니다
    from batch.core.turso import TursoError

    _should_run(monkeypatch)
    monkeypatch.setattr(daily.backend, "resolved_backend", lambda: "d1")

    def full(*_args, **_kwargs):
        raise TursoError("D1 HTTP 400: exceeded D1's free tier daily row write limit")

    monkeypatch.setattr(daily.db, "start_batch_run", full)
    monkeypatch.setattr(daily.telegram, "send", lambda *_: pytest.fail("알림을 보내면 안 된다"))
    assert daily.run("US") == 0


def test_미국을_쉴_때는_마이그레이션도_보지_않는다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # 쓰기 한도가 찬 날에는 마이그레이션 확인(CREATE TABLE IF NOT EXISTS)부터 막힐 수 있다
    _should_run(monkeypatch)
    monkeypatch.setattr(daily.backend, "resolved_backend", lambda: "d1")
    monkeypatch.setattr(daily.db, "apply_migrations", lambda *_: pytest.fail("쉬는 날에는 DB 를 고치지 않는다"))
    assert daily.run("US") == 0


class Test아무것도_안_한_실행은_이슈에_안_올린다:
    """표식 하나가 파이썬과 워크플로 두 곳에 있다 (docs/infra.md 25.84).

    **왜 중요한가.** 클라우드 세션이 읽는 것은 이슈 #1 "운영 출력" 의 **마지막 코멘트**
    하나다(25.17, handoff 4.0 의 1단계). 그런데 `publish_output.py` 는 로그가 비었을 때만
    건너뛰고, 건너뛴 실행도 `실행하지 않음: …` 한 줄을 남기므로 **코멘트가 달린다.**

    미국 일일 배치는 서머타임 때문에 두 시각에 걸려 있어 **평일마다 한쪽은 반드시 건너뛴다.**
    D1 로 운영하는 동안에는 양쪽 다 건너뛴다. 즉 평일마다 빈 코멘트가 **둘**씩 쌓여
    정작 읽어야 할 따라잡기 결과를 밀어낸다 — 10월 1일에 읽을 바로 그것이다.

    글자가 어긋나면 조용히 옛 모양으로 돌아간다(코멘트가 다시 쌓인다). 그래서 묶어 둔다.
    """

    #: 표식을 쓰는 워크플로. **셋 다 예약으로 돌고 셋 다 같은 이슈에 쓴다** —
    #: 그래서 한 곳만 고치면 나머지가 마지막 자리를 계속 덮는다
    워크플로 = ("daily-kr.yml", "daily-us.yml", "turso-return.yml")
    #: 표식을 `run:` 안에서 직접 grep 하는 워크플로 (단계 출력을 쓰지 않는다)
    직접grep = ("d1-catchup.yml",)

    def _글(self, 이름: str) -> str:
        뿌리 = Path(__file__).resolve().parent.parent
        return (뿌리 / ".github" / "workflows" / 이름).read_text(encoding="utf-8")

    def test_표식이_한_곳에_있다(self) -> None:
        """진입점의 공통 약속이다. 배치와 복귀 스크립트가 **같은 글자**를 써야 한다."""
        from batch.core.entry import NOTHING_DONE
        from batch.jobs import daily

        assert NOTHING_DONE == "실행하지 않음: "
        assert daily.NOTHING_DONE is NOTHING_DONE, "daily 가 제 사본을 갖고 있다"

    def test_복귀_스크립트도_같은_것을_쓴다(self) -> None:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import turso_return

        from batch.core.entry import NOTHING_DONE

        assert turso_return.NOTHING_DONE is NOTHING_DONE

    @pytest.mark.parametrize("이름", 워크플로 + 직접grep)
    def test_워크플로가_같은_표식을_본다(self, 이름: str) -> None:
        from batch.core.entry import NOTHING_DONE

        글 = self._글(이름)

        assert f"grep -q '^{NOTHING_DONE}'" in 글, (
            f"{이름} 이 보는 글자가 entry.NOTHING_DONE 과 다르다 — 빈 코멘트가 다시 쌓인다"
        )

    def test_따라잡기도_할_일이_없던_날에는_안_올린다(self) -> None:
        """Turso 로 돌아간 뒤에도 이 잡은 날마다 돈다 — 그때 한 줄짜리 코멘트가 매일 달렸다.

        그리고 이쪽은 **따라잡기보다 15분 뒤에 도는 복귀 워크플로**와 함께 봐야 한다:
        둘 다 안 막으면 09:05 의 복귀 점검 판정이 09:20 의 "아직 막혀 있습니다" 로 덮인다.
        """
        글 = self._글("d1-catchup.yml")

        assert "지금은 $BACKEND 를 쓴다" in 글, "Turso 로 돌아간 날에 표식을 안 남긴다"
        assert '[ "$BACKEND" != "unknown" ]' in 글, "0번이 깨진 날까지 입을 막으면 이유를 못 읽는다"

    def test_두_예약이_같은_이슈의_마지막_자리를_다툰다(self) -> None:
        """따라잡기 00:05 UTC, 복귀 00:20 UTC — **복귀가 15분 뒤**다.

        한쪽만 막으면 나머지가 계속 덮는다. 이 사실을 글자로 못 박아 둔다.
        """
        import yaml

        뿌리 = Path(__file__).resolve().parent.parent / ".github" / "workflows"
        시각 = {}
        for 이름 in ("d1-catchup.yml", "turso-return.yml"):
            data = yaml.safe_load((뿌리 / 이름).read_text(encoding="utf-8"))
            크론 = (data.get("on") or data.get(True))["schedule"][0]["cron"].split()
            시각[이름] = int(크론[1]) * 60 + int(크론[0])

        assert 시각["turso-return.yml"] > 시각["d1-catchup.yml"], (
            "복귀가 따라잡기보다 먼저 돌게 바뀌었다 — 이 항목의 전제가 달라졌으니 다시 따져라"
        )
        for 이름 in ("d1-catchup.yml", "turso-return.yml"):
            assert "실행하지 않음: " in (뿌리 / 이름).read_text(encoding="utf-8"), f"{이름} 이 안 막혀 있다"

    @pytest.mark.parametrize("이름", 워크플로)
    def test_내보내기가_그_결과에_걸려_있다(self, 이름: str) -> None:
        """단계 id 를 **워크플로에서 읽어** 잇는다. 글자를 박아 두면 id 를 바꿀 때 조용히 갈라진다."""
        import yaml

        단계들 = next(iter(yaml.safe_load(self._글(이름))["jobs"].values()))["steps"]
        정하는단계 = [s for s in 단계들 if "did_work=" in str(s.get("run", ""))]
        내보내기 = [s for s in 단계들 if "publish_output.py" in str(s.get("run", ""))]

        assert len(정하는단계) == 1, f"{이름} 에서 did_work 를 정하는 단계가 {len(정하는단계)}개다"
        assert 정하는단계[0].get("id"), "단계에 id 가 없으면 출력이 안 보인다"
        assert len(내보내기) == 1

        가리킴 = f"steps.{정하는단계[0]['id']}.outputs.did_work != 'false'"
        assert 가리킴 in str(내보내기[0].get("if", "")), f"{이름}: 내보내기가 `{가리킴}` 를 안 본다"

    @pytest.mark.parametrize("이름", 워크플로)
    def test_실패한_날에는_그대로_올린다(self, 이름: str) -> None:
        """**멈춘 이유를 읽으려고 두는 장치다**(25.21). 배치가 죽으면 표식 줄이 아예 안 생기고,
        `always()` 와 `!= 'false'` 가 둘 다 참이라 올라간다. 그 성질을 글자로 못 박아 둔다."""
        조건 = self._글(이름).split("기록을 이슈로 내보내기", 1)[1].split("run:", 1)[0]

        assert "always()" in 조건
        assert "did_work == 'true'" not in 조건, "미설정(실패)일 때 올라가지 않는다"

    def test_한도로_건너뛴_것은_다른_글자다(self) -> None:
        """`건너뜀: `(DB 한도)는 **알려야 할 운영 사건**이라 올린다. 둘을 같은 글자로 쓰면 안 된다."""
        from batch.jobs import daily

        assert not daily.NOTHING_DONE.startswith("건너뜀")
        본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
        assert 'print(f"건너뜀: {reason}")' in 본문, "한도 경로가 사라졌거나 글자가 바뀌었다"


def test_수집_뒤_단계가_깨져도_실패로_닫고_알린다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/infra.md 25.231 — 추천 단계의 예외가 `main()` 으로 새어 기록이 `running` 으로 남고 알림이 없었다."""
    _should_run(monkeypatch)
    monkeypatch.setattr(daily.backend, "resolved_backend", lambda: "turso")
    monkeypatch.setattr(daily, "collect_prices", lambda *a, **k: ([], [], {}))
    monkeypatch.setattr(daily.db, "db_size_note", lambda *_: None)

    def 깨진다(*_a, **_k):
        raise RuntimeError("추천 단계 고장")

    monkeypatch.setattr(daily, "refresh_recommendations", 깨진다)
    알림: list[str] = []
    monkeypatch.setattr(daily, "_notify_failure", lambda _c, _m, _n, 사유, dry_run: 알림.append(사유))

    with pytest.raises(RuntimeError):
        daily.run("KR")

    status, error_text = client.conn.execute(
        "SELECT status, error_text FROM batch_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert status == "failed"
    assert "추천 단계 고장" in error_text
    assert 알림 == ["추천 단계 고장"]


def test_main_은_깨진_실행에_1_을_돌려준다(monkeypatch: pytest.MonkeyPatch) -> None:
    def 깨진다(*_a, **_k):
        raise RuntimeError("고장")

    monkeypatch.setattr(daily, "run", 깨진다)
    monkeypatch.setattr("sys.argv", ["daily", "--market", "KR"])
    assert daily.main() == 1


def test_발송이_실패하면_실패로_닫고_알린다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """partial 로 닫으면 성공으로 세어 재시도·무응답 감시가 모두 조용했다 (docs/infra.md 25.370)."""
    run_id = daily.db.start_batch_run(client, job_name="daily_kr", market="KR", trade_date="2026-09-25")  # type: ignore[arg-type]
    알림: list[str] = []
    monkeypatch.setattr(daily, "_notify_failure", lambda _c, _m, _n, 사유, dry_run: 알림.append(사유))
    assert daily._send_failed(client, run_id, {}, "KR", "daily_kr", RuntimeError("chat not found"), False) == 1  # type: ignore[arg-type]
    status, error_text = client.conn.execute("SELECT status, error_text FROM batch_runs WHERE id = ?", [run_id]).fetchone()
    assert status == "failed" and "chat not found" in error_text
    assert 알림 and "발송 실패" in 알림[0]
    assert not daily.db.has_successful_run(client, "daily_kr", "2026-09-25")  # type: ignore[arg-type]


def test_발송_실패_처리가_배선돼_있다() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
    assert "return _send_failed(client, run_id, step_log, market, name, exc, dry_run)" in src


def test_뒤_조각만_실패하면_실패로_닫지_않는다() -> None:
    """실패로 닫으면 다음 예약이 전체를 다시 보내 앞 조각이 두 번 간다 (docs/infra.md 25.414)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
    부분 = src[src.index("except telegram.TelegramPartialError as exc:"):]
    부분 = 부분[: 부분.index("except Exception as exc:")]
    assert "reports.mark_sent(client, report_id, exc.sent_ids)" in 부분
    assert "reports.mark_partial_send(client, report_id, warnings)" in 부분  # 25.419
    # 웹의 발송 표시(`lib/reports.PARTIAL_SEND_MARK`)가 이 글자로 일부 발송을 알아본다 (25.427)
    웹 = (Path(__file__).resolve().parents[1] / "web" / "lib" / "reports.ts").read_text(encoding="utf-8")
    표지 = re.search(r'PARTIAL_SEND_MARK = "([^"]+)"', 웹)
    assert 표지 is not None and 표지.group(1) in 부분
    assert "_send_failed" not in 부분
    assert src.index("except telegram.TelegramPartialError") < src.index(
        "return _send_failed(client, run_id, step_log, market, name, exc, dry_run)"
    )


def test_DB_한도로_끝나면_연_기록을_닫는다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """guard 에 닿지 않는 경로라 바깥·안쪽 기록이 running 으로 남아 수동 실행 단추가 막혔다 (docs/infra.md 25.447)."""
    import sys

    def run(market: str, force: bool = False, dry_run: bool = False) -> int:
        daily.db.start_batch_run(client, job_name="daily_kr", market="KR", trade_date="2026-09-25")  # type: ignore[arg-type]
        daily.db.start_batch_run(client, job_name="scores", market="KR", trade_date="2026-09-25")  # type: ignore[arg-type]
        raise RuntimeError("한도")

    monkeypatch.setattr(daily, "run", run)
    monkeypatch.setattr(daily.db, "quota_reason", lambda exc: "읽기 한도")
    monkeypatch.setattr(daily, "_notify_quota", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["daily", "--market", "KR"])
    assert daily.main() == 0
    상태 = [r[0] for r in client.conn.execute("SELECT status FROM batch_runs ORDER BY id").fetchall()]
    assert 상태 == ["skipped", "skipped"]
    assert daily.db.open_run_depth() == 0


def test_따라잡기와_복귀는_같은_줄에_선다() -> None:
    """따로 서면 복귀가 따라잡기 도중에 DB 를 바꾼다 (docs/infra.md 25.463, 스케줄 감사 #6)."""
    import yaml

    뿌리 = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    그룹 = {
        이름: yaml.safe_load((뿌리 / 이름).read_text(encoding="utf-8"))["concurrency"]
        for 이름 in ("d1-catchup.yml", "turso-return.yml")
    }
    assert 그룹["d1-catchup.yml"]["group"] == 그룹["turso-return.yml"]["group"]
    assert all(g["cancel-in-progress"] is False for g in 그룹.values()), "도는 쪽을 끊으면 옮기다 만 데이터가 남는다"


def test_보냈는지_모르면_실패로_닫지_않고_웹도_모름으로_적는다() -> None:
    """실패로 닫으면 예비 트리거가 전체를 다시 보내 두 번 받는다 (docs/infra.md 25.493, 텔레그램 감사)."""
    from pathlib import Path

    뿌리 = Path(__file__).resolve().parents[1]
    src = (뿌리 / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
    부분 = src[src.index("except telegram.TelegramUncertainError as exc:"):]
    부분 = 부분[: 부분.index("except Exception as exc:")]
    assert "_send_failed" not in 부분 and "reports.mark_partial_send(client, report_id, warnings)" in 부분
    웹 = (뿌리 / "web" / "lib" / "reports.ts").read_text(encoding="utf-8")
    표지 = re.search(r'UNKNOWN_SEND_MARK = "([^"]+)"', 웹)
    assert 표지 is not None and 표지.group(1) in 부분
    assert src.index("except telegram.TelegramUncertainError") < src.index(
        "return _send_failed(client, run_id, step_log, market, name, exc, dry_run)"
    )


def test_보낸_뒤_기록을_한도로_못_닫아도_리포트_안_나감이라_하지_않는다() -> None:
    """리포트를 받은 직후 "오늘 리포트는 나가지 않습니다" 가 왔다 (docs/infra.md 25.567, 감사)."""
    import inspect

    src = inspect.getsource(daily.run)
    끝 = src[src.index('status = "partial" if warnings else "success"\n        try:'):]
    닫기 = 끝.index("db.finish_batch_run(client, run_id, status=status, step_log=step_log)")
    # 25.600: 한도가 아닌 오류도 올리지 않는다(`raise` 가 없어야 한다)
    뒤 = 끝[닫기:닫기 + 1200]
    assert "return 0" in 뒤 and "raise" not in 뒤[: 뒤.index("return 0")]


def test_보낸_리포트가_있으면_예약_재실행은_다시_보내지_않는다() -> None:
    """발송 뒤 기록을 못 닫으면 예비 예약이 리포트를 한 번 더 보냈다 (docs/infra.md 25.600, 감사)."""
    import inspect

    src = inspect.getsource(daily.run)
    검사 = src.index("if not force and reports.sent_exists(client, market, decision.trade_date):")
    assert 검사 < src.index("run_id = db.start_batch_run(")


def test_이미_보낸_리포트는_새_것을_보낸_뒤에_덮는다() -> None:
    """--force 재실행이 발송 전에 아침 행을 지워, 재발송이 실패하면 받은 본문이 이력에서 사라졌다 (docs/infra.md 25.567)."""
    import inspect

    from batch.services import reports
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    assert reports.sent_exists(c, "KR", "2026-09-25") is False  # type: ignore[arg-type]
    c.conn.execute(
        "INSERT INTO daily_reports (market, trade_date, status, generated_at, summary_text, warnings_json, sent_at)"
        " VALUES ('KR', '2026-09-25', 'success', 't', '아침', '[]', '2026-09-25T23:30:00Z')"
    )
    assert reports.sent_exists(c, "KR", "2026-09-25") is True  # type: ignore[arg-type]
    src = inspect.getsource(daily.run)
    보냄 = src.index("message_ids = telegram.send(message)")
    assert src.index("if not 보낸_것_있음:\n            report_id = _저장()") < 보냄
    assert "if 보낸_것_있음:\n                report_id = _저장()" in src[보냄:보냄 + 400]



def test_모름_재실행은_새_리포트로_덮고_한도_알림은_단정하지_않는다() -> None:
    """재실행 중 발송 모름이면 웹이 옛 본문을 가리켰고, 한도 알림은 이미 받은 리포트와 반대 말을 했다 (25.568, 교차검증)."""
    import inspect

    src = inspect.getsource(daily.run)
    모름 = src[src.index("except telegram.TelegramUncertainError as exc:"):]
    # 새 본문의 조각이 확실히 갔을 때만 덮고, 발송 시각을 적는다 (25.570 — 한 조각 "모름" 이 아침 행을 지웠다)
    assert "if 보낸_것_있음 and 확실히_간:\n                report_id = _저장()" in 모름[:1500]
    assert "reports.mark_sent(client, report_id, 확실히_간)" in 모름[:1800]
    # 덮지 않았으면 알림에도 붙인다 (25.574, 교차검증 — 경고는 실행 기록에만 남았다)
    assert "formatter.NOT_SAVED_MARK}(웹은 먼저 보낸 리포트)" in 모름
    보낸것: list[str] = []
    원래 = daily.telegram.send
    daily.telegram.send = lambda text, *a, **k: 보낸것.append(text) or []  # type: ignore[assignment]
    try:
        daily._notify_quota("KR", "쓰기 한도", dry_run=False)
    finally:
        daily.telegram.send = 원래  # type: ignore[assignment]
    assert "나가지 않습니다" not in 보낸것[0] and "이미 받았다면" in 보낸것[0]


def test_백테스트_요청은_앞_실행을_취소하지_않는다() -> None:
    """국내를 요청하고 곧 미국을 요청하면 국내가 조용히 취소됐다 (docs/infra.md 25.581, 감사)."""
    import yaml

    뿌리 = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    그룹 = yaml.safe_load((뿌리 / "backtest.yml").read_text(encoding="utf-8"))["concurrency"]
    assert 그룹["cancel-in-progress"] is False


def test_모름_경로에서_뒤_조각이_실패하면_일부_발송_표지를_붙인다() -> None:
    """끝 조각이 안 갔는데 발송 시각이 찍혀 다음 날 매도 플래그 NEW 가 빠졌다 (docs/infra.md 25.600, 감사)."""
    import inspect

    src = inspect.getsource(daily.run)
    assert 'warnings.append(f"끝 조각까지 확실히 가지 않았습니다 — 리포트 일부 {PARTIAL_SEND_MARK}")' in src
    assert daily.PARTIAL_SEND_MARK == "조각만 보냈습니다"



class _창DB:
    """`find_drift` 의 창 질의(날짜 범위 + 종목 IN)에 답하는 가짜."""

    def __init__(self, rows: dict[tuple[int, str], tuple[float, float]]) -> None:
        self.rows = rows

    def execute(self, sql: str, params: list) -> object:
        from types import SimpleNamespace

        처음, 끝, *ids = params
        return SimpleNamespace(rows=[
            (sid, d, *v) for (sid, d), v in self.rows.items() if sid in ids and 처음 <= d < 끝
        ])


def test_창_맨_앞_날짜가_DB_에_없어도_겹치는_날로_분할을_잡는다() -> None:
    """맨 앞 하나만 보고 없으면 건너뛰어, 복귀 백필이 메운 종목의 분할을 놓쳤다 (docs/infra.md 25.601, 감사)."""
    from batch.sources.yfinance_src import DailyBar

    # DB: 9/12 분할 전 100. 받은 창: 9/11(DB 에 없음)·9/12·9/15 모두 분할 반영 50
    db = _창DB({(1, "2026-09-12"): (100.0, 100.0)})
    창 = [DailyBar("X", d, close=50.0, adj_close=50.0) for d in ("2026-09-11", "2026-09-12", "2026-09-15")]
    assert daily.find_drift(db, 창, "2026-10-02", {"X": 1})  # type: ignore[arg-type]


def test_복귀_백필은_어긋남_감지를_켠다() -> None:
    """짧은 구간만 메우는 복귀 백필이 감지를 꺼 절벽이 영구히 남았다 (docs/infra.md 25.601, 감사)."""
    from pathlib import Path

    wf = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "turso-return.yml").read_text(encoding="utf-8")
    assert "backfill_us --lookback" in wf and "--detect-drift" in wf


def test_발송_뒤_기록_닫기가_실패하면_새_연결로_한_번_더_닫는다() -> None:
    """두 쓰기가 같은 장애로 함께 실패하면 sent_at 도 비어 예비 예약이 다시 보냈다 (docs/infra.md 25.602, 교차검증)."""
    import inspect

    src = inspect.getsource(daily.run)
    assert 'if 사유 == "오류" and _새연결로_닫기(run_id, status, step_log):' in src


def test_건너뛸_실행은_DB_오류로_실패_알림을_내지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """마이그레이션 확인이 휴장 판정보다 먼저라 Turso 일시 오류가 "배치 실패" 알림이 됐다 (docs/infra.md 25.753, 리포트 감사)."""
    from batch.core import calendar as cal

    class 깨진DB(MemClient):
        def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
            raise RuntimeError("Turso 일시 오류")

    monkeypatch.setattr(daily, "TursoClient", lambda: 깨진DB())
    monkeypatch.setattr(
        daily.cal,
        "decide",
        lambda market, now=None, force=False: cal.RunDecision(
            should_run=False, reason="한국 시장 휴장일", trade_date="2026-09-16", session_date="2026-09-17"
        ),
    )
    알림: list[str] = []
    monkeypatch.setattr(daily, "_notify_failure", lambda *a, **k: 알림.append("실패"))
    assert daily.run("KR") == 0
    assert 알림 == []


def test_건너뜀_기록이_도중에_끊기면_열린_행을_닫는다() -> None:
    """시작 행만 들어간 채 끊기면 'running' 으로 6시간 남았다 (docs/infra.md 25.755, 교차검증)."""
    import inspect

    src = inspect.getsource(daily.run)
    # 건너뜀은 실패가 아니라 skipped 로, 이번에 연 행만 닫는다 (25.757, 교차검증)
    assert 'db.fail_runs_opened_after(깊이, f"건너뜀 기록 도중 실패: {exc}", "skipped")' in src


def test_추천을_못_읽어도_리포트는_매도_플래그와_함께_나간다(
    client: MemClient, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """docs/infra.md 25.818 — 신호 조인 한 번의 실패가 손절 플래그까지 그날 리포트를 통째로 막았다."""
    _should_run(monkeypatch)
    monkeypatch.setattr(daily.backend, "resolved_backend", lambda: "turso")
    monkeypatch.setattr(daily, "collect_prices", lambda *a, **k: ([], [], {}))
    monkeypatch.setattr(daily.db, "db_size_note", lambda *_: None)
    monkeypatch.setattr(daily, "refresh_recommendations", lambda *a, **k: [])
    monkeypatch.setattr(daily, "refresh_portfolio", lambda *a, **k: [])

    def 깨진다(*_a, **_k):
        raise RuntimeError("D1 시간 초과")

    monkeypatch.setattr(daily, "load_signal_rows", 깨진다)
    플래그 = [{"stock_id": 1, "name": "삼성전자", "level": "red", "reason_code": "stop_loss", "rationale_text": "손절선 아래",
              "first_seen_date": "2026-09-30", "as_of_date": "2026-09-30", "is_new": True}]  # fmt: skip
    monkeypatch.setattr(daily, "_sell_flags_for_report", lambda *a, **k: 플래그)
    monkeypatch.setattr(daily, "_notify_failure", lambda *a, **k: pytest.fail("실패 알림이 아니라 리포트가 나가야 한다"))

    assert daily.run("KR", dry_run=True) == 0
    글 = capsys.readouterr().out
    assert "추천을 읽지 못해" in 글 and "D1 시간 초과" in 글
    assert "매도 플래그 (자동 매도 없음" in 글 and "[적]" in 글
    assert "오늘 추천할 종목이 없습니다" not in 글  # 못 읽은 것을 "없다" 로 적지 않는다



def test_추천_읽기의_한도_오류는_리포트로_덮지_않는다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """한도는 "건너뜀 한 번 알림" 경로로 간다 — 수집 단계·바깥 except 와 같다 (docs/infra.md 25.822, 교차검증)."""
    _should_run(monkeypatch)
    monkeypatch.setattr(daily.backend, "resolved_backend", lambda: "turso")
    monkeypatch.setattr(daily, "collect_prices", lambda *a, **k: ([], [], {}))
    monkeypatch.setattr(daily.db, "db_size_note", lambda *_: None)
    monkeypatch.setattr(daily, "refresh_recommendations", lambda *a, **k: [])
    monkeypatch.setattr(daily, "refresh_portfolio", lambda *a, **k: [])
    monkeypatch.setattr(daily.db, "quota_reason", lambda exc: "읽기 한도" if "LIMIT" in str(exc) else None)

    def 한도(*_a, **_k):
        raise RuntimeError("SQL_READ_LIMIT")

    monkeypatch.setattr(daily, "load_signal_rows", 한도)
    monkeypatch.setattr(daily, "load_holdings", lambda *a, **k: pytest.fail("한도 날 보유를 읽으면 안 된다"))
    with pytest.raises(RuntimeError, match="SQL_READ_LIMIT"):
        daily.run("KR", dry_run=True)
    # 한도는 `main()` 이 건너뜀으로 닫는다 — 여기서는 열린 기록을 직접 비운다(tests/test_stale_runs.py 와 같이)
    db._열린_실행.clear()


def test_추천을_못_읽은_날은_2부_재료를_읽지_않는다(client: MemClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _should_run(monkeypatch)
    monkeypatch.setattr(daily.backend, "resolved_backend", lambda: "turso")
    monkeypatch.setattr(daily, "collect_prices", lambda *a, **k: ([], [], {}))
    monkeypatch.setattr(daily.db, "db_size_note", lambda *_: None)
    monkeypatch.setattr(daily, "refresh_recommendations", lambda *a, **k: [])
    monkeypatch.setattr(daily, "refresh_portfolio", lambda *a, **k: [])

    def 깨진다(*_a, **_k):
        raise RuntimeError("D1 시간 초과")

    monkeypatch.setattr(daily, "load_signal_rows", 깨진다)
    monkeypatch.setattr(daily, "load_holdings", lambda *a, **k: pytest.fail("2부가 없는 날 보유를 읽으면 안 된다"))
    monkeypatch.setattr(daily, "_other_report_reserved", lambda *a, **k: pytest.fail("2부가 없는 날 예약을 읽으면 안 된다"))
    assert daily.run("KR", dry_run=True) == 0


def test_이미_성공한_날의_예비_실행은_할_일_없음_표식을_찍는다(
    client: MemClient, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """표식이 없어 워크플로가 이슈 #1 에 코멘트를 달고 시세 사본을 맞췄다 — 평일마다 09:35 예비 실행이 (25.913, 감사)."""
    from batch.core import calendar as cal

    monkeypatch.setattr(
        daily.cal,
        "decide",
        lambda market, now=None, force=False: cal.RunDecision(
            should_run=True, reason="예약", trade_date="2026-10-02", session_date="2026-10-05"
        ),
    )
    monkeypatch.setattr(daily.db, "has_successful_run", lambda *a, **k: True)
    assert daily.run("KR") == 0
    assert any(줄.startswith(daily.NOTHING_DONE) for 줄 in capsys.readouterr().out.splitlines())
    monkeypatch.setattr(daily.db, "has_successful_run", lambda *a, **k: False)
    monkeypatch.setattr(daily.reports, "sent_exists", lambda *a, **k: True)
    assert daily.run("KR") == 0
    assert any(줄.startswith(daily.NOTHING_DONE) for 줄 in capsys.readouterr().out.splitlines())


def test_실패_알림의_다음_예정이_같은_날_예비_실행을_말한다() -> None:
    """"다음 예정 매 거래일 08:27" 이 "내일" 로 읽혀 오늘 리포트를 포기하게 했다 (25.913, 감사)."""
    from batch.core import calendar as cal

    assert "09:35 KST 예비 실행" in cal.next_scheduled_hint("KR")
    assert "예비 실행" in cal.next_scheduled_hint("US")
