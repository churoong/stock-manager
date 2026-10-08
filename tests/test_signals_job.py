"""신호 적재 테스트.

적재를 테스트하지 않아 재무 배치가 운영에서 죽은 적이 있다. 여기서는
값 묶음이 스키마와 맞는지, 비싼 조회를 문턱으로 거르는지, 입력이 없을 때
값을 지어내지 않는지를 본다. 네트워크를 타지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from batch.core import db
from batch.jobs import signals as job
from batch.services import signals as sg

ROOT = Path(__file__).resolve().parent.parent


class FakeClient:
    def __init__(self) -> None:
        self.batched: list[list[tuple[str, list]]] = []

    def batch(self, statements):
        self.batched.append(statements)
        return []


def sample_signal() -> sg.Signal:
    inp = sg.SignalInput(
        stock_id=1,
        ticker="005930",
        name="테스트전자",
        market="KOSPI",
        closes=[100.0 + i for i in range(80)],
        turnovers=[100.0] * 60 + [200.0] * 20,
        factor_scores={"momentum": 75.0},
    )
    signals = sg.evaluate(inp, total_investable=10_000_000)
    assert signals, "표본 신호가 만들어져야 한다"
    return signals[0]


class Test열개수:
    def test_signals_열_개수(self) -> None:
        assert db.column_count(job._COLS) == 19

    def test_값_묶음이_열_개수와_맞는다(self) -> None:
        row = job.to_row(sample_signal(), "2026-09-15", "지금")
        assert len(row) == db.column_count(job._COLS)

    def test_값이_모자라면_무엇을_고칠지_알려준다(self) -> None:
        client = FakeClient()
        with pytest.raises(ValueError) as exc:
            job.store(client, [(1, 2, 3)])
        assert "열" in str(exc.value)

    def test_빈_목록은_넣지_않는다(self) -> None:
        client = FakeClient()
        assert job.store(client, []) == 0
        assert client.batched == []

    def test_여러_행을_한_요청으로_보낸다(self) -> None:
        client = FakeClient()
        row = job.to_row(sample_signal(), "2026-09-15", "지금")
        job.store(client, [row] * 300)
        assert len(client.batched) == 1


class Test저장형태:
    def test_분할_계획을_JSON으로_남긴다(self) -> None:
        row = job.to_row(sample_signal(), "2026-09-15", "지금")
        plan = json.loads(row[7])

        assert [t["step"] for t in plan] == [1, 2, 3]
        assert sum(t["ratio"] for t in plan) == pytest.approx(1.0)

    def test_근거_수치를_함께_남긴다(self) -> None:
        """문장만 남기면 이 숫자가 어디서 왔는지 답할 수 없다."""
        row = job.to_row(sample_signal(), "2026-09-15", "지금")
        data = json.loads(row[16])

        assert "ma20" in data
        assert data["momentum"] == 75.0

    def test_섹터_상한_미적용을_기록한다(self) -> None:
        row = job.to_row(sample_signal(), "2026-09-15", "지금")
        assert row[13] == 0  # sector_cap_applied
        assert row[14] == sg.SECTOR_CAP_UNAVAILABLE


class Test밴드문턱:
    """밴드 조회는 비싸다. 문턱을 넘은 종목에만 한다."""

    def test_퀄리티와_밸류가_모두_넘어야_한다(self) -> None:
        assert job.needs_band({"quality": 70.0, "value": 70.0})

    def test_하나라도_못_넘으면_건너뛴다(self) -> None:
        assert not job.needs_band({"quality": 70.0, "value": 50.0})
        assert not job.needs_band({"quality": 50.0, "value": 70.0})

    def test_점수가_없으면_건너뛴다(self) -> None:
        assert not job.needs_band({})
        assert not job.needs_band({"quality": None, "value": 70.0})


class Test성장률:
    def test_전년_대비(self) -> None:
        assert job._growth(120.0, 100.0) == pytest.approx(0.2)

    def test_전년이_적자면_없다(self) -> None:
        """스코어링과 같은 규칙이어야 한다. 두 곳이 달라지면 근거가 어긋난다."""
        assert job._growth(-10.0, -100.0) is None

    def test_입력이_없으면_없다(self) -> None:
        assert job._growth(None, 100.0) is None


class Test입력조립:
    def test_시세와_점수를_붙인다(self) -> None:
        candidates = [
            {
                "stock_id": 1,
                "ticker": "005930",
                "name": "테스트전자",
                "market": "KOSPI",
                "sector": None,
                "currency": "KRW",
                "listed_shares": 100,
                "scores": {"momentum": 75.0},
            }
        ]
        prices = {1: [(f"2026-09-{i:02d}", 100.0 + i, 500.0) for i in range(1, 11)]}

        inputs = job.build_inputs(candidates, prices, {}, {}, {})

        assert len(inputs) == 1
        assert inputs[0].closes[-1] == pytest.approx(110.0)
        assert inputs[0].factor_scores["momentum"] == 75.0

    def test_현재_PBR은_밴드와_같은_가격으로(self) -> None:
        """밴드는 분할만 반영한 종가, 현재 PBR 은 배당까지 반영한 수정종가였다 (docs/infra.md 25.521, 감사 재현)."""
        from batch.services import signals as sg

        row = {"stock_id": 1, "ticker": "X", "name": "X", "market": "NYSE", "currency": "USD", "listed_shares": 100,
               "scores": {}, "_latest_equity": 10_000.0, "_band_close": 111.18}  # fmt: skip
        prices = {1: [("2026-09-25", 107.84, 1.0)]}  # 수정종가
        band = sg.build_band([1.0 + i / 100 for i in range(300)])
        inputs = job.build_inputs([row], prices, {}, {}, {1: band})
        assert inputs[0].pbr_now == pytest.approx(1.1118)  # 111.18 / (10,000 / 100)

    def test_시세가_없어도_무너지지_않는다(self) -> None:
        candidates = [
            {
                "stock_id": 1,
                "ticker": "AAPL",
                "name": "애플",
                "market": "NASDAQ",
                "sector": None,
                "currency": "USD",
                "listed_shares": None,
                "scores": {},
            }
        ]
        inputs = job.build_inputs(candidates, {}, {}, {}, {})

        assert inputs[0].closes == []
        assert inputs[0].pbr_now is None
        # 입력이 없으면 신호도 없다. 지어내지 않는다
        assert sg.evaluate(inputs[0]) == []

    def test_깨진_점수_JSON은_빈_값으로_둔다(self) -> None:
        assert job._decode_scores("{망가짐") == {}
        assert job._decode_scores(None) == {}
        assert job._decode_scores('{"value": 70}') == {"value": 70}


class Test판정표적재:
    def test_열_개수와_값_묶음(self) -> None:
        inp = sg.SignalInput(stock_id=1, ticker="A", name="A", market="KOSPI", price_date="2026-09-15")
        rows = job.check_rows(inp, "2026-09-15", "now")
        assert len(rows) == 3
        for row in rows:
            assert len(row) == db.column_count(job._CHECK_COLS) == 9  # 25.1038 levels_json
        # 값이 없는 종목은 전부 탈락이고 그 수가 적힌다
        by_h = {r[2]: r for r in rows}
        assert by_h["mid"][3] == 0 and by_h["mid"][4] == 4
        assert json.loads(by_h["mid"][5])[0]["passed"] is False

    def test_넣고_지난_기준일을_지운다(self) -> None:
        client = FakeClient()
        client.execute = lambda *a, **k: None  # record_rows_written 이 부른다
        inp = sg.SignalInput(stock_id=1, ticker="A", name="A", market="KOSPI")
        n = job.store_checks(client, "KR", "2026-09-15", job.check_rows(inp, "2026-09-15", "now"))  # type: ignore[arg-type]
        assert n == 3
        statements = client.batched[0]
        assert statements[0][0].startswith("INSERT INTO signal_checks")
        assert "DELETE FROM signal_checks WHERE as_of_date < ?" in statements[-1][0]
        assert statements[-1][1] == ["2026-09-15", "KR"]


class Test돈에_닿는_설정의_범위:
    """**권장 금액·비중 상한·목표가가 여기서 나온다** (docs/infra.md 25.170).

    `settings.ts` 의 "검증은 웹 한 곳" 전제가 `restore_backup`·`move_user_data` 에서
    깨진다. 읽는 쪽에서 한 번 더 본다.
    """

    @staticmethod
    def _읽기(monkeypatch, 값: dict):
        from batch.jobs import signals as job

        monkeypatch.setattr(job.db, "get_setting", lambda _c, key, default=None, **_k: 값.get(key, default))
        return job.load_settings(None, "KRW")  # type: ignore[arg-type]

    def test_멀쩡하면_조용하다(self, monkeypatch) -> None:
        out = self._읽기(
            monkeypatch,
            {"max_weight_per_stock": 8, "total_investable_amount": 10_000_000, "max_weight_per_sector": 25},
        )
        assert out["max_weight_per_stock"] == 8
        assert out["setting_warnings"] == []

    def test_비중_상한이_범위를_넘으면_기본값으로(self, monkeypatch) -> None:
        out = self._읽기(monkeypatch, {"max_weight_per_stock": 500})

        assert out["max_weight_per_stock"] == 10.0, "500% 면 권장 금액이 총액의 다섯 배가 된다"
        assert any("max_weight_per_stock" in 줄 for 줄 in out["setting_warnings"])

    def test_음수_총액은_0으로(self, monkeypatch) -> None:
        out = self._읽기(monkeypatch, {"total_investable_amount": -1})
        assert out["total_investable"] == 0.0
        assert out["setting_warnings"]

    def test_손절선이_양수면_그_기간을_버린다(self, monkeypatch) -> None:
        """**한 짝이 함께 뜻을 갖는다.** 손절만 되돌리면 손절이 목표 위에 설 수 있다."""
        out = self._읽기(
            monkeypatch,
            {
                "horizon_targets": {
                    "short": {"target_pct": 10, "stop_pct": -7},
                    "mid": {"target_pct": 25, "stop_pct": 15},
                }
            },
        )

        assert out["targets"] == {"short": {"target_pct": 10, "stop_pct": -7}}
        assert any("mid" in 줄 and "stop_pct" in 줄 for 줄 in out["setting_warnings"])

    def test_전부_나쁘면_목표를_통째로_비운다(self, monkeypatch) -> None:
        out = self._읽기(monkeypatch, {"horizon_targets": {"short": {"target_pct": -5, "stop_pct": 5}}})

        assert out["targets"] is None, "None 이면 signals 가 기본값을 쓴다"
        assert len(out["setting_warnings"]) == 2

    def test_배치가_그_경고를_싣는다(self) -> None:
        신호 = (ROOT / "batch" / "jobs" / "signals.py").read_text(encoding="utf-8")
        일일 = (ROOT / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")

        assert "setting_warnings" in 신호, "실행 기록에 없으면 나중에 알 길이 없다"
        assert 'warnings.extend(settings["setting_warnings"])' in 일일, "리포트에 실려야 사람이 본다"


def test_판정표_저장이_실패하면_실행_기록에_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """"판정표 0건" 만 남으면 "판정할 종목이 없었다" 와 구별되지 않는다 (docs/infra.md 25.222)."""
    import json as _json

    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    # 끝까지 가기 위한 최소한 — 후보 하나, 나머지 재료는 비어 있다
    monkeypatch.setattr(job, "load_candidates", lambda *_a: [{"stock_id": 1, "ticker": "A", "scores": {}}])
    monkeypatch.setattr(job, "load_recent_prices", lambda *_a: {})
    monkeypatch.setattr(job, "load_growth", lambda *_a: {})
    monkeypatch.setattr(job, "load_metrics", lambda *_a: {})
    monkeypatch.setattr(job, "build_inputs", lambda *_a: [])

    def 막힘(*_a):
        raise RuntimeError("D1 일일 쓰기 한도 초과")

    monkeypatch.setattr(job, "store_checks", 막힘)

    assert job.run("KR", "2026-09-16") == 0
    기록 = _json.loads(mem.conn.execute(
        "SELECT step_log FROM batch_runs WHERE job_name = ? ORDER BY id DESC LIMIT 1", [job.JOB_NAME]
    ).fetchone()[0])
    assert "한도" in 기록.get("checks_error", ""), 기록


def test_기준일_종가가_없는_종목은_신호를_내지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """거래가 멈춘 종목에 옛 종가로 금액 붙은 신호가 나갔다 (docs/infra.md 25.517, 감사 재현)."""
    import json as _json
    from types import SimpleNamespace

    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "load_candidates", lambda *_a: [{"stock_id": 1, "ticker": "A", "scores": {}}])
    monkeypatch.setattr(job, "load_recent_prices", lambda *_a: {})
    monkeypatch.setattr(job, "load_growth", lambda *_a: {})
    monkeypatch.setattr(job, "load_metrics", lambda *_a: {})
    monkeypatch.setattr(job, "build_inputs", lambda *_a: [
        SimpleNamespace(ticker="A", market="KOSPI", price_date="2026-09-16", score_date="2026-09-16"),
        SimpleNamespace(ticker="B", market="KOSPI", price_date="2026-07-23", score_date="2026-09-16"),
    ])
    본것: list[str] = []
    monkeypatch.setattr(job, "check_rows", lambda inp, *_a: [])
    monkeypatch.setattr(job.sg, "evaluate", lambda inp, **_k: 본것.append(inp.ticker) or [])
    assert job.run("KR", "2026-09-16") == 0
    assert 본것 == ["A"]  # 신호 판정은 기준일 종가가 있는 종목만
    기록 = _json.loads(mem.conn.execute(
        "SELECT step_log FROM batch_runs WHERE job_name = ? ORDER BY id DESC LIMIT 1", [job.JOB_NAME]
    ).fetchone()[0])
    assert 기록["stale_price"] == 1 and 기록["stale_price_tickers"] == ["B"]


def test_기준일_종가가_없는_종목도_판정표에는_까닭을_남긴다() -> None:
    """빼면 웹이 "신호 없음" 만 보여 주고 까닭을 남기지 않았다 (docs/infra.md 25.526, 교차검증)."""
    import json as _json

    from batch.services import signals as sg

    inp = sg.SignalInput(stock_id=1, ticker="B", name="B", market="KOSPI", price_date="2026-07-23")
    행 = job.check_rows(inp, "2026-09-16", "now")
    assert {r[2] for r in 행} == set(sg.HORIZONS) and all(r[3] == 0 for r in 행)
    첫 = _json.loads(행[0][5])[0]
    assert 첫["label"] == "기준일 종가" and "2026-07-23" in 첫["display"] and 첫["passed"] is False


def test_한_종목도_기준일_종가가_없으면_계산하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """판정표가 지워져 "마지막 계산일" 이 과거로 돌아가 묵은 신호가 되살아났다 (docs/infra.md 25.526, 교차검증 재현)."""
    from types import SimpleNamespace

    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "load_candidates", lambda *_a: [{"stock_id": 1, "ticker": "A", "scores": {}}])
    monkeypatch.setattr(job, "load_recent_prices", lambda *_a: {})
    monkeypatch.setattr(job, "load_growth", lambda *_a: {})
    monkeypatch.setattr(job, "load_metrics", lambda *_a: {})
    monkeypatch.setattr(job, "build_inputs", lambda *_a: [SimpleNamespace(ticker="A", market="KOSPI", price_date="2026-09-15")])
    지움: list[str] = []
    monkeypatch.setattr(job, "store_checks", lambda *a: 지움.append("x") or 0)
    assert job.run("KR", "2026-09-16") == 0
    assert 지움 == []
    상태 = mem.conn.execute("SELECT status FROM batch_runs WHERE job_name = ? ORDER BY id DESC LIMIT 1", [job.JOB_NAME])
    assert 상태.fetchone()[0] == "skipped"


def test_절반_넘게_멈추면_계산하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 시장만 수집에 실패한 날 어제 판정표가 지워지고 경고 없이 "추천 없음" 이었다 (docs/infra.md 25.531, 교차검증)."""
    from types import SimpleNamespace

    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "load_candidates", lambda *_a: [{"stock_id": 1, "ticker": "A", "scores": {}}])
    monkeypatch.setattr(job, "load_recent_prices", lambda *_a: {})
    monkeypatch.setattr(job, "load_growth", lambda *_a: {})
    monkeypatch.setattr(job, "load_metrics", lambda *_a: {})
    monkeypatch.setattr(job, "build_inputs", lambda *_a: [
        SimpleNamespace(ticker=t, market="KOSPI", price_date=d)
        for t, d in (("A", "2026-09-16"), ("B", "2026-09-15"), ("C", "2026-09-15"))
    ])
    지움: list[str] = []
    monkeypatch.setattr(job, "store_checks", lambda *a: 지움.append("x") or 0)
    assert job.run("KR", "2026-09-16") == 0 and 지움 == []


def test_한_시장이_절반_넘게_멈춰도_계산하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """코스피·코스닥을 합쳐 세면 코스닥 전체 실패가 절반을 넘지 않았다 (docs/infra.md 25.535, 교차검증)."""
    from types import SimpleNamespace

    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "load_candidates", lambda *_a: [{"stock_id": 1, "ticker": "A", "scores": {}}])
    monkeypatch.setattr(job, "load_recent_prices", lambda *_a: {})
    monkeypatch.setattr(job, "load_growth", lambda *_a: {})
    monkeypatch.setattr(job, "load_metrics", lambda *_a: {})
    입력 = [SimpleNamespace(ticker=f"P{i}", market="KOSPI", price_date="2026-09-16") for i in range(60)]
    입력 += [SimpleNamespace(ticker=f"Q{i}", market="KOSDAQ", price_date="2026-09-15") for i in range(40)]
    monkeypatch.setattr(job, "build_inputs", lambda *_a: 입력)
    지움: list[str] = []
    monkeypatch.setattr(job, "store_checks", lambda *a: 지움.append("x") or 0)
    assert job.run("KR", "2026-09-16") == 0 and 지움 == []


def test_작은_시장_한_종목_정지로_나라_전체를_멈추지_않는다() -> None:
    """Cboe BZX(4종목) 한 종목 정지로 미국 신호 전체가 skipped 였다 (docs/infra.md 25.541, 교차검증 재현)."""
    from types import SimpleNamespace

    def 입력(시장: str, n: int, 날: str) -> list:
        return [SimpleNamespace(market=시장, price_date=날) for _ in range(n)]

    기준 = "2026-09-16"
    assert not job.too_stale(입력("NASDAQ", 900, 기준) + 입력("NYSE", 700, 기준) + 입력("BZX", 1, "x"), 기준)
    assert job.too_stale(입력("NASDAQ", 900, 기준) + 입력("NYSE", 700, "x"), 기준)  # 큰 시장 하나 전체
    assert job.too_stale(입력("A", 10, "x") + 입력("B", 9, 기준), 기준)  # 합계 절반 넘음


def test_기준일_점수가_없으면_신호를_계산하지_않는다(monkeypatch) -> None:
    """따라잡기에서 점수가 실패해도 며칠 전 점수로 "오늘" 신호가 나갔다 (docs/infra.md 25.620, 감사)."""
    from types import SimpleNamespace

    from batch.core import db
    from batch.jobs import signals as job
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "load_candidates", lambda *_a: [{"stock_id": 1, "ticker": "A", "scores": {}}])
    monkeypatch.setattr(job, "load_recent_prices", lambda *_a: {})
    monkeypatch.setattr(job, "load_growth", lambda *_a: {})
    monkeypatch.setattr(job, "load_metrics", lambda *_a: {})
    monkeypatch.setattr(job, "build_inputs", lambda *_a: [
        SimpleNamespace(ticker=t, market="KOSPI", price_date="2026-09-16", score_date="2026-09-11") for t in "AB"
    ])
    monkeypatch.setattr(job.sg, "evaluate", lambda *_a, **_k: pytest.fail("묵은 점수로 판정했다"))
    assert job.run("KR", "2026-09-16") == 0
    status = mem.conn.execute("SELECT status FROM batch_runs WHERE job_name = ?", [job.JOB_NAME]).fetchone()[0]
    assert status == "skipped"
