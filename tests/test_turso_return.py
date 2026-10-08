"""10월 1일 자동 복귀 (docs/infra.md 25.12). 네트워크를 타지 않는다.

auto 모드는 실행할 때마다 D1 의 복귀 표시를 보고 백엔드를 고른다. 여기서 보는 것:
  - 판정표: 복귀 표시가 있으면 Turso, 없으면 D1 (**Turso 가 살아나도 옮기기 전에는 D1**),
    D1 을 못 읽을 때만 Turso 상태로 정한다
  - 복귀 스크립트: 막혀 있으면 아무것도 안 하고, 풀리면 표 → 데이터 → 복귀 표시 순서로 한다
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import turso_return  # noqa: E402

from batch.core import client as backend  # noqa: E402
from batch.core import db  # noqa: E402
from tests.test_portfolio_job import MemClient  # noqa: E402


@pytest.fixture(autouse=True)
def _d1_값이_있는_저장소(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 파일은 D1 임시 운영(D1 값이 있는 저장소)의 복귀를 본다. D1 값이 없으면 auto 라도 Turso 에 머문다 (25.1029)."""
    for k in backend.D1_ENV:
        monkeypatch.setenv(k, "test")

ROOT = Path(__file__).resolve().parent.parent

# 판정표는 여기 있었다가 `tests/test_db_backend_decision.py` 로 옮겼다 (2026-09-21).
# 웹 테스트가 **같은 표를 손으로 베껴** 갖고 있었고, 한쪽을 고치면 다른 쪽은 옛 표를
# 지키며 조용히 통과했다. 이제 둘 다 tests/fixtures/db_backend_decision.json 을 읽는다.
# 여기 남은 것은 판정 **뒤에** 일어나는 일들이다 (docs/infra.md 25.42).


def _auto(monkeypatch: pytest.MonkeyPatch, marker, probe=None) -> list[str]:
    """auto 로 두고 복귀 표시·Turso 살펴보기를 가짜로 바꾼다. 무엇을 불렀는지 돌려준다."""
    monkeypatch.setenv("DB_BACKEND", "auto")
    monkeypatch.setattr(backend, "_resolved", None)
    calls: list[str] = []

    def read():
        calls.append("marker")
        return marker

    def look():
        calls.append("turso")
        if probe is None:
            pytest.fail("복귀 표시를 읽었으면 Turso 를 살펴볼 필요가 없다")
        return probe

    monkeypatch.setattr(backend, "read_return_marker", read)
    monkeypatch.setattr(backend, "_probe_turso", look)
    return calls


def test_auto_는_한_번_정하고_그대로_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _auto(monkeypatch, marker=False)
    assert backend.resolved_backend() == "d1"
    assert backend.resolved_backend() == "d1"
    assert calls == ["marker"]  # 작업 도중에 DB 가 바뀌면 안 된다


def test_복귀_표시가_없으면_Turso_가_살아나도_D1(monkeypatch: pytest.MonkeyPatch) -> None:
    # 10월 1일 리셋 직후 ~ 복귀 워크플로 사이. Turso 를 살펴보지도 않는다
    _auto(monkeypatch, marker=False)
    assert backend.resolved_backend() == "d1"


def test_복귀_표시가_있으면_Turso(monkeypatch: pytest.MonkeyPatch) -> None:
    _auto(monkeypatch, marker=True)
    assert backend.resolved_backend() == "turso"


def test_D1_을_못_읽으면_Turso_상태로_정한다(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _auto(monkeypatch, marker=None, probe=(True, False))
    assert backend.resolved_backend() == "turso"
    assert calls == ["marker", "turso"]


def test_auto_가_아니면_살펴보지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_BACKEND", "d1")
    monkeypatch.setattr(backend, "_probe_turso", lambda: pytest.fail("살펴보면 안 된다"))
    monkeypatch.setattr(backend, "read_return_marker", lambda: pytest.fail("살펴보면 안 된다"))
    assert backend.resolved_backend() == "d1"


def test_복귀_표시를_못_읽으면_모른다(monkeypatch: pytest.MonkeyPatch) -> None:
    import batch.core.d1 as d1

    class Broken:
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("D1 통신 실패")

    monkeypatch.setattr(d1, "D1Client", Broken)
    assert backend.read_return_marker() is None
    assert backend.returned_to_turso() is False  # 복귀 스크립트는 모르면 옮기기를 시도한다


def _dbs():
    """D1(종목 389) 과 Turso(종목 1) — 번호가 다르다."""
    d1, turso = MemClient(), MemClient()
    for mem, sid in ((d1, 389), (turso, 1)):
        db.apply_migrations(mem)  # type: ignore[arg-type]
        mem.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')",
            [sid],
        )
    d1.conn.execute(
        "INSERT INTO watchlist (stock_id, added_at, target_buy_price) VALUES (389, 't', 60000)"
    )
    return d1, turso


class Test복귀:
    def test_auto_가_아니면_하지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_BACKEND", "d1")
        assert turso_return.run().moved is False

    def test_아직_막혀_있으면_하지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_BACKEND", "auto")
        monkeypatch.setattr(backend, "returned_to_turso", lambda: False)
        monkeypatch.setattr(backend, "_probe_turso", lambda: (False, True))
        assert turso_return.run(lambda: pytest.fail("열면 안 된다"), lambda: pytest.fail("열면 안 된다")).moved is False

    def test_이미_돌아갔고_메우기도_끝났으면_하지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_BACKEND", "auto")
        monkeypatch.setattr(backend, "returned_to_turso", lambda: True)
        _, turso = _dbs()
        turso_return.mark_filled(lambda: turso)
        assert turso_return.run(lambda: pytest.fail("D1 을 열면 안 된다"), lambda: turso).moved is False

    def test_복귀_표시만_있고_메우기가_안_끝났으면_뒤_단계만_다시(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """뒤 단계가 실패하면 다음 실행이 "이미 돌아갔습니다" 로 끝나 다시 메울 길이 없었다 (docs/infra.md 25.469)."""
        monkeypatch.setenv("DB_BACKEND", "auto")
        monkeypatch.setattr(backend, "returned_to_turso", lambda: True)
        monkeypatch.setattr(turso_return, "notify", lambda m: pytest.fail("다시 옮긴 것처럼 알리면 안 된다"))
        _, turso = _dbs()
        turso.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, '2026-10-02', 252500, 'KRW', 't', 't')"
        )
        monkeypatch.setattr(backend, "_probe_turso", lambda: (True, False))
        outcome = turso_return.run(lambda: pytest.fail("D1 을 열면 안 된다 — 옮기기는 이미 했다"), lambda: turso)
        assert outcome.moved is True and outcome.backfill_from == "2026-10-03"
        assert turso.conn.execute("SELECT COUNT(*) FROM watchlist").fetchone() == (0,)  # 옮기기를 다시 하지 않았다

    def test_다시_돌_때는_처음_잰_구간을_쓴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """그 사이 일일 배치가 어제 시세를 넣으면 "마지막 날" 로는 가운데 구멍이 안 보인다 (25.473, 교차검증)."""
        monkeypatch.setenv("DB_BACKEND", "auto")
        monkeypatch.setattr(backend, "returned_to_turso", lambda: True)
        monkeypatch.setattr(backend, "_probe_turso", lambda: (True, False))
        _, turso = _dbs()
        계획 = {"computed_on": "2026-10-01", "retries": 0, "start": "2026-09-18", "us_days": 20, "index_days": 0,
                "fx_days": 15, "disc_days": 14}  # fmt: skip
        turso_return._save_plan(turso, 계획)
        turso.conn.execute(  # 일일 배치가 어제(10/4) 시세를 넣었다
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, '2026-10-04', 252500, 'KRW', 't', 't')"
        )

        class 오늘:
            @staticmethod
            def now(tz: object = None) -> datetime:
                return datetime(2026, 10, 5, tzinfo=UTC)

        monkeypatch.setattr(turso_return, "datetime", 오늘)
        outcome = turso_return.run(lambda: pytest.fail("열면 안 된다"), lambda: turso)
        assert outcome.backfill_from == "2026-09-18"  # 처음 잰 시작일 그대로
        assert (outcome.us_days, outcome.index_days, outcome.fx_days) == (24, 0, 19)  # 지난 4일만큼 늘림

    def test_복귀_뒤_Turso_가_다시_막히면_조용히_넘긴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_BACKEND", "auto")
        monkeypatch.setattr(backend, "returned_to_turso", lambda: True)
        monkeypatch.setattr(backend, "_probe_turso", lambda: (False, True))
        assert turso_return.run(lambda: pytest.fail("열면 안 된다"), lambda: pytest.fail("열면 안 된다")).moved is False

    def test_다시_돌기는_상한에서_멈추고_한_번만_알린다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_BACKEND", "auto")
        monkeypatch.setattr(backend, "returned_to_turso", lambda: True)
        monkeypatch.setattr(backend, "_probe_turso", lambda: (True, False))
        보낸것: list[str] = []
        monkeypatch.setattr(turso_return, "notify", lambda m: 보낸것.append(m))
        _, turso = _dbs()
        turso_return._save_plan(turso, {"computed_on": "2026-10-01", "retries": 0, "start": "2026-09-18",
                                        "us_days": 0, "index_days": 0, "fx_days": 0, "disc_days": 0})  # fmt: skip
        결과 = [turso_return.run(lambda: None, lambda: turso).moved for _ in range(turso_return.MAX_FILL_RETRIES + 3)]
        assert 결과 == [True] * turso_return.MAX_FILL_RETRIES + [False] * 3
        assert len(보낸것) == 1

    def test_워크플로는_모두_성공했을_때만_끝_표시를_남긴다(self) -> None:
        import yaml

        path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "turso-return.yml"
        단계들 = next(iter(yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].values()))["steps"]
        끝 = [s for s in 단계들 if "--mark-filled" in str(s.get("run", ""))]
        assert len(끝) == 1 and "success()" in str(끝[0].get("if", ""))
        이름들 = [s.get("name", "") for s in 단계들]
        assert 이름들.index(끝[0]["name"]) > max(i for i, n in enumerate(이름들) if n.startswith("4."))

    def test_풀리면_옮기고_복귀_표시를_남긴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_BACKEND", "auto")
        monkeypatch.setattr(backend, "returned_to_turso", lambda: False)
        monkeypatch.setattr(backend, "_probe_turso", lambda: (True, False))
        sent: list[str] = []
        monkeypatch.setattr(turso_return, "notify", lambda message: sent.append(message))
        d1, turso = _dbs()

        # Turso 의 마지막 국내 시세가 9/17 이면 빠진 날은 9/18 부터다 (날짜를 박지 않는다)
        turso.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, '2026-09-17', 252500, 'KRW', 't', 't')"
        )
        outcome = turso_return.run(lambda: d1, lambda: turso)
        assert outcome.moved is True
        assert outcome.backfill_from == "2026-09-18"
        # 관심 종목이 종목코드로 번호를 다시 맞춰 옮겨졌다 (389 → 1)
        assert turso.conn.execute("SELECT stock_id, target_buy_price FROM watchlist").fetchall() == [(1, 60000)]
        # 복귀 표시는 두 곳에. D1 쪽이 판정에 쓰인다(Turso 가 다시 막혀도 읽힌다)
        for mem in (d1, turso):
            key = mem.conn.execute("SELECT key FROM settings WHERE key = ?", [backend.RETURN_MARKER]).fetchone()
            assert key is not None
        assert len(sent) == 1 and "Turso" in sent[0]


def test_마지막_시세를_모르면_넉넉히_거슬러_받는다() -> None:
    from datetime import UTC, date, datetime, timedelta

    _d1, turso = _dbs()
    start = date.fromisoformat(turso_return.backfill_start(turso))
    assert start == datetime.now(UTC).date() - timedelta(days=turso_return.FALLBACK_DAYS)


def test_워크플로_출력에_시작일을_남긴다(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    turso_return.set_output(turso_return.Outcome(True, "2026-09-18"))
    적힌것 = out.read_text(encoding="utf-8").splitlines()
    assert 적힌것[:2] == ["moved=true", "backfill_from=2026-09-18"]
    # 미국·지수 구멍도 함께 넘어간다 (25.82). 0 이어도 적는다
    assert 적힌것[2:] == ["us_days=0", "index_days=0", "fx_days=0", "disclosure_days=0"]


class Test워크플로가_읽는_값:
    """`moved=true` 면 `backfill_from` 도 반드시 있다 (docs/infra.md 25.57).

    없으면 뒤 단계가 `backfill_kr --from ""` 를 부르고, 빈 날짜로는 구간을 만들 수 없어
    종료코드 2 로 죽는다. 그 순간 **복귀 표시는 이미 적혀 있다** — 앱은 Turso 를 보는데
    D1 로 운영한 동안의 시세가 없고, 포트폴리오 재계산과 감시 대상도 돌지 않는다.
    2026-09-19 에 따라잡기가 같은 이유로 매번 죽은 적이 있다(25.16).
    """

    def _적힌것(self, tmp_path, outcome) -> dict[str, str]:
        경로 = tmp_path / "gh_output"
        os.environ["GITHUB_OUTPUT"] = str(경로)
        try:
            turso_return.set_output(outcome)
        finally:
            del os.environ["GITHUB_OUTPUT"]
        return dict(
            줄.split("=", 1) for 줄 in 경로.read_text(encoding="utf-8").splitlines() if "=" in 줄
        )

    def test_옮겼으면_시작일이_있다(self, tmp_path) -> None:
        적힌것 = self._적힌것(tmp_path, turso_return.Outcome(True, "2026-09-01"))

        assert 적힌것 == {
            "moved": "true", "backfill_from": "2026-09-01",
            "us_days": "0", "index_days": "0", "fx_days": "0", "disclosure_days": "0",
        }

    def test_시작일을_못_정해도_빈_값을_넘기지_않는다(self, tmp_path) -> None:
        # 지금 코드에서는 닿지 않는 길이다. 그래도 막아 둔다 —
        # 닿지 않는다는 것은 오늘의 사실이지 약속이 아니다
        적힌것 = self._적힌것(tmp_path, turso_return.Outcome(True, None))

        assert 적힌것["moved"] == "true"
        assert date.fromisoformat(적힌것["backfill_from"]) < date.today()

    def test_안_옮겼으면_시작일을_적지_않는다(self, tmp_path) -> None:
        # 뒤 단계가 어차피 안 돈다. 쓸데없는 값을 남기지 않는다
        적힌것 = self._적힌것(tmp_path, turso_return.Outcome(False))

        assert 적힌것 == {"moved": "false"}

    def test_워크플로가_같은_이름으로_읽는다(self) -> None:
        본문 = (ROOT / ".github" / "workflows" / "turso-return.yml").read_text(encoding="utf-8")

        assert "steps.ret.outputs.moved" in 본문
        assert "steps.ret.outputs.backfill_from" in 본문


class Test복귀_알림이_거짓말하지_않는다:
    """**옮기지 못한 것이 있으면 성공이라고 말하지 않는다** (2026-09-21, docs/infra.md 25.81).

    2026-09-21 까지 이 알림은 늘 `✅ … 매매·관심종목·설정을 옮겼습니다` 였다.
    그런데 `move_user_data` 는 번호를 못 맞춘 종목의 행을 **버린다.** 버렸다는 사실은
    Actions 로그에만 남고 부르는 쪽은 보지도 않았다. 매매는 사람이 넣은 것이라
    다른 곳에 없다(CLAUDE.md) — **가장 조용히 잃을 수 있는 데이터에 가장 밝은 메시지**가 붙어 있었다.
    """

    def _결과(self, **over):
        import move_user_data as mover

        return mover.옮긴결과(**{"counts": {"trades": 1}, **over})

    def test_온전하면_초록이다(self) -> None:
        글 = turso_return.복귀_문구(self._결과())

        assert 글.startswith("✅")
        assert "옮겼습니다" in 글

    def test_마스터를_함께_옮겼으면_그것도_말한다(self) -> None:
        글 = turso_return.복귀_문구(self._결과(carried=2))

        assert 글.startswith("✅")
        assert "대상에 없던 종목 2개는 마스터째 옮겼습니다" in 글

    def test_버린_것이_있으면_초록이_아니다(self) -> None:
        글 = turso_return.복귀_문구(self._결과(unmapped=["999999·KOSDAQ"]))

        assert not 글.startswith("✅"), "잃고서 성공이라고 말한다"
        assert "옮기지 못한" in 글

    def test_무엇을_잃었는지_이름을_댄다(self) -> None:
        글 = turso_return.복귀_문구(self._결과(unmapped=["999999·KOSDAQ", "888888·KOSPI"]))

        assert "999999·KOSDAQ" in 글 and "888888·KOSPI" in 글

    def test_되찾는_길을_알려_준다(self) -> None:
        """**원본은 D1 에 그대로 있다.** 그 한 문장이 없으면 사람은 잃었다고 믿는다."""
        글 = turso_return.복귀_문구(self._결과(unmapped=["999999·KOSDAQ"]))

        assert "D1 에 그대로 있습니다" in 글
        assert "다시 돌리면" in 글


def test_복귀가_실제로_잃으면_알림도_그렇게_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """가짜 문구가 아니라 **진짜 복귀 경로**를 끝까지 돌려 본다.

    D1 에만 있는 종목에 **뉴스만** 달아 둔다 — 사람이 쓴 것이 아니라 마스터를 안 옮기고,
    그래서 `unmapped` 가 남는 유일한 경우다.
    """
    monkeypatch.setenv("DB_BACKEND", "auto")
    monkeypatch.setattr(backend, "returned_to_turso", lambda: False)
    monkeypatch.setattr(backend, "_probe_turso", lambda: (True, False))
    sent: list[str] = []
    monkeypatch.setattr(turso_return, "notify", lambda message: sent.append(message))
    d1, turso = _dbs()
    d1.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (900, '999999', 'KOSDAQ', 'KR', 'KRW', 'active', 't', 't')"
    )
    d1.conn.execute(
        "INSERT INTO news (stock_id, title, url, published_at, lang, source, fetched_at)"
        " VALUES (900, '기사', 'https://y/9', '2026-09-20T00:00:00Z', 'ko', 'yna_rss', 't')"
    )

    assert turso_return.run(lambda: d1, lambda: turso).moved is True

    assert len(sent) == 1
    assert not sent[0].startswith("✅"), "잃고서 초록으로 알렸다"
    assert "999999·KOSDAQ" in sent[0]


def test_복귀가_사람이_쓴_종목은_마스터째_옮긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """D1 으로 운영하는 동안 **새로 상장한 종목을 사서 적어 둔** 경우 (docs/infra.md 25.81)."""
    monkeypatch.setenv("DB_BACKEND", "auto")
    monkeypatch.setattr(backend, "returned_to_turso", lambda: False)
    monkeypatch.setattr(backend, "_probe_turso", lambda: (True, False))
    sent: list[str] = []
    monkeypatch.setattr(turso_return, "notify", lambda message: sent.append(message))
    d1, turso = _dbs()
    d1.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (900, '999999', 'KOSDAQ', 'KR', '새내기', 'KRW', 'active', 'krx', '2026-09-25')"
    )
    d1.conn.execute(
        "INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
        " created_at, updated_at) VALUES (900, 'buy', '2026-09-25', 12000, 50, 'KRW', 1, 'none', 't', 't')"
    )

    assert turso_return.run(lambda: d1, lambda: turso).moved is True

    붙은곳 = turso.conn.execute(
        "SELECT s.ticker, s.name_ko, t.quantity FROM trades t JOIN stocks s ON s.id = t.stock_id"
    ).fetchall()
    assert 붙은곳 == [("999999", "새내기", 50)], "새로 상장한 종목에 적은 매매가 사라졌다"
    assert sent[0].startswith("✅")


class Test미국과_지수의_구멍:
    """**국내에만 있던 일이다** (2026-09-21, docs/infra.md 25.82).

    2단계는 `backfill_kr` 뿐이었다. 미국 시세와 지수 일봉은 일일 배치의
    `US_LOOKBACK_DAYS=10` · `index_prices.LOOKBACK_DAYS=10` **달력일 고정 창**에
    기대고 있었다. Turso 는 2026-09-17 밤에 잠겼고 복귀는 빨라야 10/1 이다 —
    구멍이 10일보다 크면 그만큼 **영영 빈다.**

    지수가 비면 값이 없는 것으로 끝나지 않는다. `metrics.align` 이 날짜를 맞추므로
    빈 날은 양쪽에서 빠지지만, **구멍을 사이에 둔 하루치 수익률이 2주치가 되어**
    변동성·샤프·MDD 를 부풀린다. **틀린 값이 그럴듯하게 나온다.**
    """

    오늘 = date(2026, 10, 1)

    def test_구멍만큼_거슬러_받는다(self) -> None:
        날수, 말 = turso_return.메울_날수("2026-09-17", self.오늘, "없다")

        assert 날수 == 14 + turso_return.CATCHUP_MARGIN
        assert "2026-09-17 이후 14일이 비었다" in 말

    def test_10일_고정_창으로는_모자란_바로_그_경우(self) -> None:
        """9/17 잠금 → 10/1 복귀. 고정 10일 창은 9/21 까지밖에 못 닿는다."""
        날수, _ = turso_return.메울_날수("2026-09-17", self.오늘, "없다")

        assert 날수 > 10, "일일 배치의 고정 창으로 덮이는 만큼이면 이 단계가 있을 이유가 없다"

    def test_구멍이_없으면_돌지_않는다(self) -> None:
        for 마지막 in ("2026-10-01", "2026-09-30"):
            날수, 말 = turso_return.메울_날수(마지막, self.오늘, "없다")

            assert 날수 == 0, 마지막
            assert "구멍 없음" in 말

    def test_아예_없으면_이_워크플로가_하지_않는다(self) -> None:
        """5년치를 여기서 받으면 안 된다 — backfill-us.yml 은 종목 구간으로 나눠 도는 큰 작업이다."""
        날수, 말 = turso_return.메울_날수(None, self.오늘, "미국 시세가 아예 없다")

        assert 날수 == 0
        assert 말 == "미국 시세가 아예 없다"

    def test_너무_크면_상한까지만_메우고_말한다(self) -> None:
        """**조용히 자르지 않는다.** 남은 구멍을 사람이 알아야 따로 돌린다."""
        날수, 말 = turso_return.메울_날수("2025-01-01", self.오늘, "없다")

        assert 날수 == turso_return.MAX_CATCHUP_DAYS
        assert "다 메우지 못한다" in 말 and "backfill-us.yml" in 말

    def test_경계_하루를_놓치지_않게_여유를_얹는다(self) -> None:
        """딱 맞게 잡으면 시간대·휴장 때문에 경계 하루가 빈다. 겹쳐 받는 것은 공짜다(같은 행)."""
        날수, _ = turso_return.메울_날수("2026-09-29", self.오늘, "없다")

        assert 날수 == 2 + turso_return.CATCHUP_MARGIN

    def test_DB_에서_미국과_지수를_따로_본다(self) -> None:
        _d1, turso = _dbs()
        turso.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (7, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
        )
        turso.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (7, '2026-09-17', 200, 'USD', 't', 't')"
        )
        turso.conn.execute(
            "INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
            " VALUES ('SP500', '2026-09-25', 6000, 't', 't')"
        )

        assert turso_return.us_catchup(turso, self.오늘)[0] == 14 + turso_return.CATCHUP_MARGIN
        assert turso_return.index_catchup(turso, self.오늘)[0] == 6 + turso_return.CATCHUP_MARGIN

    def test_국내_시세는_미국_판정에_섞이지_않는다(self) -> None:
        """25.37·25.75 에서 되풀이된 모양. 국내가 최신이면 미국 구멍이 감춰진다."""
        _d1, turso = _dbs()  # 국내 종목 1번만 있다
        turso.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, '2026-10-01', 70000, 'KRW', 't', 't')"
        )

        assert turso_return.us_catchup(turso, self.오늘) == (0, "미국 시세가 아예 없다 — backfill-us.yml 로 따로 받는다")


def test_워크플로_출력에_미국과_지수_날수도_남는다(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """0 이어도 **늘 적는다.** 값이 없으면 `!= '0'` 이 빈 문자열과 비교돼 뜻이 흐려진다 (25.57)."""
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    turso_return.set_output(turso_return.Outcome(True, "2026-09-18", us_days=17, index_days=0, fx_days=5))

    assert out.read_text(encoding="utf-8").splitlines() == [
        "moved=true", "backfill_from=2026-09-18",
        "us_days=17", "index_days=0", "fx_days=5", "disclosure_days=0",
    ]


def test_복귀가_미국과_지수의_구멍을_재어_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """진짜 복귀 경로를 끝까지 돌려 본다."""
    monkeypatch.setenv("DB_BACKEND", "auto")
    monkeypatch.setattr(backend, "returned_to_turso", lambda: False)
    monkeypatch.setattr(backend, "_probe_turso", lambda: (True, False))
    monkeypatch.setattr(turso_return, "notify", lambda message: None)
    d1, turso = _dbs()
    # **오늘을 기준으로 20일 전**에 마지막 미국 시세를 둔다. 날짜를 박으면 이 테스트가
    # 내일부터 다른 것을 재게 된다 — 이 항목이 고치려는 것이 바로 "박아 둔 날짜" 다
    from datetime import UTC, datetime, timedelta

    마지막 = (datetime.now(UTC).date() - timedelta(days=20)).isoformat()
    turso.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (7, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
    )
    turso.conn.execute(
        "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (7, ?, 200, 'USD', 't', 't')",
        [마지막],
    )

    outcome = turso_return.run(lambda: d1, lambda: turso)

    assert outcome.us_days == 20 + turso_return.CATCHUP_MARGIN
    assert outcome.us_days > 10, "미국 구멍을 안 재면 일일 배치의 고정 10일 창에 기대는 셈이다"
    assert outcome.index_days == 0, "지수가 아예 없으면 이 워크플로가 하지 않는다"


class Test환율의_구멍:
    """**가장 늦게 눈치채는 자리** (2026-09-21, docs/infra.md 25.83).

    환율도 `fx.LOOKBACK_DAYS = 10` 고정 창인데, 그것을 부르는 것이 **미국 일일 배치**다 —
    D1 로 운영하는 동안 쉬는 바로 그것이다(25.14). 그리고 복귀 3단계는 `portfolio.yml` 과
    달리 환율 단계 없이 재계산만 부른다.

    비어도 **실패하지 않는 것이 함정이다.** `portfolio.value_series` 의 `_carry` 가 빈 날을
    앞 값으로 이어 붙여, 미국 보유 종목이 **2주 묵은 환율**로 평가되고 화면에는 오늘 날짜가 찍힌다.
    """

    오늘 = date(2026, 10, 1)

    def test_쌍_이름을_여기서_다시_적지_않는다(self) -> None:
        """25.65 가 코드를 'S&P500' 으로 잘못 적어 둔 항목이다. 확인하는 쪽이 틀리면 무의미하다."""
        from batch.services import fx

        _d1, turso = _dbs()
        turso.conn.execute(
            "INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES (?, '2026-09-17', 1380, 't', 't')",
            [fx.PAIR],
        )

        assert turso_return.fx_catchup(turso, self.오늘)[0] == 14 + turso_return.CATCHUP_MARGIN

    def test_다른_쌍은_세지_않는다(self) -> None:
        _d1, turso = _dbs()
        turso.conn.execute(
            "INSERT INTO fx_rates (pair, date, rate, source, fetched_at)"
            " VALUES ('JPYKRW', '2026-09-30', 9.1, 't', 't')"
        )

        assert turso_return.fx_catchup(turso, self.오늘)[0] == 0
        assert "아예 없다" in turso_return.fx_catchup(turso, self.오늘)[1]

    def test_복귀가_환율_구멍도_재어_넘긴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import UTC, datetime, timedelta

        from batch.services import fx

        monkeypatch.setenv("DB_BACKEND", "auto")
        monkeypatch.setattr(backend, "returned_to_turso", lambda: False)
        monkeypatch.setattr(backend, "_probe_turso", lambda: (True, False))
        monkeypatch.setattr(turso_return, "notify", lambda message: None)
        d1, turso = _dbs()
        마지막 = (datetime.now(UTC).date() - timedelta(days=14)).isoformat()
        turso.conn.execute(
            "INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES (?, ?, 1380, 't', 't')",
            [fx.PAIR, 마지막],
        )

        outcome = turso_return.run(lambda: d1, lambda: turso)

        assert outcome.fx_days == 14 + turso_return.CATCHUP_MARGIN
        assert outcome.fx_days > 10, "고정 10일 창으로 덮이는 만큼이면 이 단계가 있을 이유가 없다"


class Test국내_공시의_구멍:
    """`disclosures_kr.DEFAULT_DAYS = 7` — **주석 문구까지 환율과 같다** (docs/infra.md 25.83).

    국내 일일 배치 안에서 도는데 그 배치는 D1 을 보고 있었다. Turso 쪽은 잠긴 날에 멈춰 있다.
    시세·환율과 달리 앞 값을 이어 붙이지는 않는다 — 그냥 **기록에 구멍이 남는다.**
    메우는 값이 공짜다: 회사당 호출 1회이고 날짜 구간은 그 한 호출의 인자다.
    """

    오늘 = date(2026, 10, 1)

    def _공시(self, turso, stock_id: int, 날: str, no: str) -> None:
        turso.conn.execute(
            "INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, source, fetched_at)"
            " VALUES (?, 'c', ?, '제목', ?, 't', 't')",
            [stock_id, no, 날],
        )

    def test_구멍을_잰다(self) -> None:
        _d1, turso = _dbs()  # 1번이 국내 종목이다
        self._공시(turso, 1, "2026-09-17", "A1")

        assert turso_return.disclosure_catchup(turso, self.오늘)[0] == 14 + turso_return.CATCHUP_MARGIN

    def test_미국_공시는_국내_판정에_섞이지_않는다(self) -> None:
        """같은 표에 두 나라가 들어간다. 나라를 안 가르면 25.37·25.75 를 되풀이한다."""
        _d1, turso = _dbs()
        turso.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (9, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
        )
        self._공시(turso, 9, "2026-09-30", "US1")  # 미국 것이 더 최신이다

        날수, 말 = turso_return.disclosure_catchup(turso, self.오늘)

        assert 날수 == 0 and "국내 공시가 아예 없다" in 말, "미국 공시가 국내 구멍을 감췄다"

    def test_복귀가_공시_구멍도_재어_넘긴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import UTC, datetime, timedelta

        monkeypatch.setenv("DB_BACKEND", "auto")
        monkeypatch.setattr(backend, "returned_to_turso", lambda: False)
        monkeypatch.setattr(backend, "_probe_turso", lambda: (True, False))
        monkeypatch.setattr(turso_return, "notify", lambda message: None)
        d1, turso = _dbs()
        self._공시(turso, 1, (datetime.now(UTC).date() - timedelta(days=14)).isoformat(), "A1")

        outcome = turso_return.run(lambda: d1, lambda: turso)

        assert outcome.disclosure_days == 14 + turso_return.CATCHUP_MARGIN
        assert outcome.disclosure_days > 7, "고정 7일 창으로 덮이는 만큼이면 이 단계가 있을 이유가 없다"


def test_다시_잴_때도_상한을_넘지_않는다() -> None:
    """처음에 상한(120)에 걸린 구간이 날이 지나 늘어도 상한 그대로 (docs/infra.md 25.478, 교차검증)."""
    계획 = {"computed_on": "2026-10-01", "start": "2026-06-01", "us_days": turso_return.MAX_CATCHUP_DAYS,
            "index_days": 5, "fx_days": 0, "disc_days": 1}  # fmt: skip
    got = turso_return.replan(계획, date(2026, 10, 11))
    assert got["us_days"] == turso_return.MAX_CATCHUP_DAYS and got["index_days"] == 15 and got["fx_days"] == 0


def test_D1_값이_없으면_복귀는_할_일이_없다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """25.1029 — 새 공개 저장소(D1 시크릿 없음)에서 Turso 가 살아 있자 D1 을 열려다 실패로 끝났다(10-08 06:18 UTC)."""
    for k in backend.D1_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(backend, "backend", lambda: backend.AUTO)
    monkeypatch.setattr(backend, "_probe_turso", lambda: pytest.fail("D1 이 없으면 Turso 를 찔러 볼 까닭도 없다"))
    assert turso_return.run().moved is False
    assert "D1 값이 없어 옮길 것이 없습니다" in capsys.readouterr().out
