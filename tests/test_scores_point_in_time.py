"""점수 적재가 **그때 알 수 있던 것만** 쓰는가 (docs/infra.md 25.97).

`docs/infra.md` 25.92 가 배당 하나를 고쳤다. 같은 축을 다시 훑었더니 **셋이 더** 있었다 —
유니버스(시가총액!)·재무(발표일)·성과지표. 셋 다 `scores.run(as_of=…)` 이 받은 기준일을
**받아서 안 쓰는** 쪽이었다.

가정이 아니다. 두 입구가 있다.

* `jobs/daily.py` 가 `scores.run(market, as_of=전_거래일)` 을 부른다 → **매일 하루치**가 샌다
* `.github/workflows/scores.yml` 이 `--as-of` 를 입력으로 받는다 → 과거를 메울 때 **통째로** 샌다

여기서 지키는 것은 하나다. **기준일 뒤에 생긴 행은 점수에 들어가면 안 된다.**
"""

from __future__ import annotations

import pytest

from batch.jobs import scores as job
from batch.services import metrics as mt
from tests.test_scores_job import _sqlite_client

기준일 = "2026-06-30"
#: 기준일 **뒤**에 생긴 것. 이것이 새면 look-ahead 다
나중 = "2026-09-20"


def _종목(client, stock_id: int = 1, country: str = "KR") -> None:
    client.conn.execute(
        "INSERT OR IGNORE INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (?, ?, 'KOSPI', ?, '가나전자', 'KRW', 'active', 't', 't')",
        [stock_id, f"{stock_id:06d}", country],
    )


class Test유니버스:
    """`universe_members.market_cap` 은 **밸류 팩터 넷의 분모**다. 여기가 새면 값이 통째로 틀린다."""

    @staticmethod
    def _붙이기(client) -> None:
        _종목(client)
        for 날, 시총 in ((기준일, 1_000.0), (나중, 9_999.0)):
            client.conn.execute(
                "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, market_cap, created_at)"
                " VALUES (?, 1, 1, 'KRW', ?, 't')",
                [날, 시총],
            )

    def test_기준일의_시가총액을_쓴다(self) -> None:
        client = _sqlite_client()
        self._붙이기(client)

        행 = job.load_universe(client, "KR", 기준일)

        assert [r["market_cap"] for r in 행] == [1_000.0], (
            "9월 시가총액으로 6월의 싼 정도를 재고 있다 — 밸류 팩터 넷이 전부 틀린다"
        )

    def test_안_걸면_미래_값이_들어온다(self) -> None:
        """**고치기 전 상태를 고정해 둔다.** 이 대조가 없으면 위 테스트의 뜻이 약하다."""
        client = _sqlite_client()
        self._붙이기(client)

        assert [r["market_cap"] for r in job.load_universe(client, "KR", 나중)] == [9_999.0]

    def test_편입_여부도_시점을_따른다(self) -> None:
        """나중에 빠진 종목이 과거 점수에서도 사라지면 **생존편향**이다."""
        client = _sqlite_client()
        _종목(client, 3)  # 1·2 는 _sqlite_client 가 이미 심었고 2 는 미국이다
        client.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, 3, 1, 'KRW', 't')",
            [기준일],
        )
        client.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, 3, 0, 'KRW', 't')",
            [나중],
        )

        assert len(job.load_universe(client, "KR", 기준일)) == 1
        assert job.load_universe(client, "KR", 나중) == []


class Test재무:
    """사업연도로만 고르면 **2025 사업보고서를 2026-01-01 에 이미 아는 것**이 된다."""

    @staticmethod
    def _붙이기(client) -> None:
        _종목(client)
        for 연도, 발표일, 순이익 in (
            (2024, "2025-03-10", 100.0),
            (2025, "2026-03-10", 200.0),   # 기준일(6/30) 전에 나왔다
            (2026, "2026-08-14", 300.0),   # 기준일 **뒤**에 나왔다
        ):
            client.conn.execute(
                "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated,"
                " report_date, receipt_no, currency, unit, net_income, source, fetched_at)"
                " VALUES (1, ?, ?, 'A', 1, ?, ?, 'KRW', '원', ?, 't', 't')",
                [연도, job.ANNUAL_REPORT_CODE, 발표일, f"r{연도}", 순이익],
            )

    def test_발표일이_지난_것만_읽는다(self) -> None:
        client = _sqlite_client()
        self._붙이기(client)

        연도들 = sorted(job.load_financials(client, "KR", 기준일)[1])

        assert 연도들 == [2024, 2025], "2026 사업보고서는 8월에 나왔다 — 6월에는 알 수 없다"

    def test_시간이_지나면_보인다(self) -> None:
        client = _sqlite_client()
        self._붙이기(client)

        assert sorted(job.load_financials(client, "KR", "2026-08-31")[1]) == [2024, 2025, 2026]

    def test_정정_공시는_같은_행을_덮는다(self) -> None:
        """**알고 쓰는 한계다** (docs/infra.md 25.97).

        `financials` 의 유니크 키는 `(stock_id, fiscal_year, report_code, consolidated)` 라
        정정이 오면 **같은 행을 덮는다** — 원래 발표일이 남지 않는다. 그래서 3월에 내고
        5월에 정정한 사업보고서는 `report_date` 가 5월이 되고, **4월 기준 점수에서는
        아예 빠진다.**

        그 방향이 맞다. 빠지는 것은 결측이고, 안 빠지면 **정정된 숫자를 정정 전에 아는**
        look-ahead 다. `financial_snapshots`(시점 스냅샷)가 이 일을 제대로 하고,
        백테스트는 그쪽을 본다.
        """
        client = _sqlite_client()
        _종목(client)
        client.conn.execute(
            "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated,"
            " report_date, receipt_no, currency, unit, net_income, source, fetched_at)"
            " VALUES (1, 2025, ?, 'A', 1, '2026-05-20', 'r2', 'KRW', '원', 250.0, 't', 't')",
            [job.ANNUAL_REPORT_CODE],
        )

        assert job.load_financials(client, "KR", 기준일)[1][2025]["net_income"] == 250.0
        assert job.load_financials(client, "KR", "2026-04-01") == {}, (
            "정정 뒤 발표일로는 4월에 알 수 없다 — 빠지는 것이 맞다"
        )

    def test_유니크_키가_정말_그_모양이다(self) -> None:
        """위 테스트의 전제다. 스키마가 바뀌면 여기가 먼저 깨져야 한다."""
        from pathlib import Path

        본문 = (Path(__file__).resolve().parent.parent / "migrations" / "0005_financials.sql").read_text(
            encoding="utf-8"
        )

        assert "UNIQUE (stock_id, fiscal_year, report_code, consolidated)" in 본문


class Test성과지표:
    """리스크 팩터 **열 개 중 일곱**이 이 표에서 온다."""

    @staticmethod
    def _붙이기(client) -> None:
        _종목(client)
        for 계산일, 변동성 in ((기준일, 0.20), (나중, 0.55)):
            client.conn.execute(
                "INSERT INTO performance_metrics (stock_id, as_of_date, window, volatility_ann,"
                " data_points, calc_version, created_at) VALUES (1, ?, '3Y', ?, 700, 1, 't')",
                [계산일, 변동성],
            )

    def test_기준일까지_계산된_것만_쓴다(self) -> None:
        client = _sqlite_client()
        self._붙이기(client)

        assert job.load_metrics(client, "KR", 기준일)[1]["volatility_ann"] == pytest.approx(0.20)

    def test_안_걸면_오늘까지의_가격으로_낸_값이_들어온다(self) -> None:
        client = _sqlite_client()
        self._붙이기(client)

        assert job.load_metrics(client, "KR", 나중)[1]["volatility_ann"] == pytest.approx(0.55)

    def test_창_고르기는_그대로다(self) -> None:
        """시점을 걸면서 3Y→1Y 폴백이 망가지지 않았는지 본다."""
        client = _sqlite_client()
        _종목(client)
        client.conn.execute(
            "INSERT INTO performance_metrics (stock_id, as_of_date, window, volatility_ann,"
            " data_points, calc_version, created_at) VALUES (1, ?, '1Y', 0.3, 250, 1, 't')",
            [기준일],
        )

        assert job.load_metrics(client, "KR", 기준일)[1]["window"] == "1Y"


class Test받은_기준일을_안_쓰는_로더가_또_생기지_않게:
    """**그물은 방향이 있다** (docs/infra.md 25.0).

    `as_of` 를 인자로 받아 놓고 질의에 안 거는 것이 이 결함의 모양이다.
    그러면 타입도 테스트도 아무 말을 하지 않는다 — 인자는 있으니까.

    **`batch/jobs` 전체를 본다.** 25.97 에서 `jobs/scores.py` 만 훑었더니
    다음 날 `jobs/signals.load_candidates` 에서 같은 것이 나왔다(25.98) —
    **그물의 범위를 결함의 범위보다 좁게 잡으면 그 밖에서 되풀이된다.**
    """

    #: 기준일을 받지만 **질의에 안 걸어도 되는** 함수와 그 사유
    예외 = {
        # 지금은 비어 있다. **사유 없는 예외는 두지 않는다**
    }

    @staticmethod
    def _쓰나(fn) -> bool:
        """함수 본문이 `as_of` 를 **한 번이라도 쓰는가.**

        처음에는 "질의 인자 목록(`ast.List`)에 실렸나" 로 좁게 봤는데 **거짓 양성이 다섯**
        나왔다 — 행 묶음을 `tuple` 로 만들거나(`stress.store`, `etf.*_row`) 내포 표기 안에서
        쓰는 경우다. 좁히려다 **엉뚱한 것을 물면 사람이 그물을 끈다.**

        그래서 최소한의 바를 둔다. 실제 결함(`signals.load_candidates`)은 `as_of` 를 받고도
        **본문에서 한 번도 안 썼다** — 이 바로도 잡힌다. 쓰기는 쓰되 엉뚱한 데 쓰는 경우는
        이 그물이 못 본다. **그건 알고 두는 한계다.**
        """
        import ast

        return any(isinstance(n, ast.Name) and n.id == "as_of" for n in ast.walk(fn))

    def _새는것(self) -> list[str]:
        import ast
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        나온것: list[str] = []
        for path in sorted((뿌리 / "batch" / "jobs").glob("*.py")):
            나무 = ast.parse(path.read_text(encoding="utf-8"))
            for fn in [n for n in ast.walk(나무) if isinstance(n, ast.FunctionDef)]:
                if "as_of" not in [a.arg for a in fn.args.args] or fn.name in self.예외:
                    continue
                if not self._쓰나(fn):
                    나온것.append(f"{path.name}::{fn.name} (줄 {fn.lineno})")
        return 나온것

    def test_as_of_를_받으면_질의에도_건다(self) -> None:
        assert not self._새는것(), (
            "`as_of` 를 받아 놓고 안 쓰는 함수가 있다. **인자는 있으니 아무도 안 문다** —\n"
            "  과거 기준일로 다시 계산하면 그 자리만 오늘 값을 쓴다:\n  "
            + "\n  ".join(self._새는것())
        )

    def test_예외에_사유가_있다(self) -> None:
        assert all(self.예외.values())

    def test_훑기가_실제로_함수를_찾았다(self) -> None:
        import ast
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        받는것 = [
            fn.name
            for path in (뿌리 / "batch" / "jobs").glob("*.py")
            for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(fn, ast.FunctionDef) and "as_of" in [a.arg for a in fn.args.args]
        ]

        assert len(받는것) >= 20, f"as_of 를 받는 함수를 {len(받는것)}개밖에 못 찾았다"


class Test창_폴백이_실제로_도나:
    """**폴백이 필요한 바로 그 종목들에서만 폴백이 안 돌았다** (docs/infra.md 25.97).

    `docs/factors.md` 3.5: "창은 3Y 를 기본으로 하고, **3Y 가 없으면 1Y 로 내려간다**."

    그런데 `jobs/metrics.py` 는 표본이 모자라도 **행을 쓴다** — 개수만 담고 값은 NULL 이다.
    그래서 상장 1~3년 종목은 빈 3Y 행이 값이 든 1Y 행을 이겼고, 리스크 지표 열 개 중
    일곱이 비어 절반 규칙(5개)에 걸려 **종합 점수가 아예 안 나왔다.**
    """

    @staticmethod
    def _붙이기(client, *, 삼년치: dict | None, 일년치: dict | None) -> None:
        _종목(client)
        for 창, 값 in (("3Y", 삼년치), ("1Y", 일년치)):
            if 값 is None:
                continue
            client.conn.execute(
                "INSERT INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd,"
                " volatility_ann, data_points, calc_version, created_at)"
                " VALUES (1, ?, ?, ?, ?, ?, ?, 1, 't')",
                [기준일, 창, 값.get("cagr"), 값.get("mdd"), 값.get("vol"), 값["n"]],
            )

    def test_빈_3Y_보다_값이_있는_1Y_를_고른다(self) -> None:
        """상장 2년차 종목이다. 3Y 행은 표본 부족으로 값이 전부 비었다."""
        client = _sqlite_client()
        self._붙이기(
            client,
            삼년치={"n": 480},                                   # 하한 미달 → 값 없음
            일년치={"n": 250, "cagr": 0.12, "mdd": -0.3, "vol": 0.25},
        )

        고른것 = job.load_metrics(client, "KR", 기준일)[1]

        assert 고른것["window"] == "1Y", "빈 3Y 가 값이 든 1Y 를 이기고 있다"
        assert 고른것["cagr"] == pytest.approx(0.12)

    def test_둘_다_값이_있으면_3Y_다(self) -> None:
        """문서가 약속한 기본값은 그대로여야 한다."""
        client = _sqlite_client()
        self._붙이기(
            client,
            삼년치={"n": 700, "cagr": 0.10, "mdd": -0.4, "vol": 0.3},
            일년치={"n": 250, "cagr": 0.12, "mdd": -0.3, "vol": 0.25},
        )

        assert job.load_metrics(client, "KR", 기준일)[1]["window"] == "3Y"

    def test_둘_다_비면_그대로_3Y_다(self) -> None:
        """고를 것이 없다. 어느 쪽이든 리스크 팩터는 비고, **거짓 값을 지어내지 않는다**."""
        client = _sqlite_client()
        self._붙이기(client, 삼년치={"n": 100}, 일년치={"n": 50})

        고른것 = job.load_metrics(client, "KR", 기준일)[1]
        assert 고른것["window"] == "3Y"
        assert 고른것["cagr"] is None

    def test_3Y_행이_아예_없으면_1Y_다(self) -> None:
        """이 경우는 **전에도 됐다.** 고치면서 깨뜨리지 않았는지 본다."""
        client = _sqlite_client()
        self._붙이기(client, 삼년치=None, 일년치={"n": 250, "cagr": 0.12, "mdd": -0.3, "vol": 0.25})

        assert job.load_metrics(client, "KR", 기준일)[1]["window"] == "1Y"

    def test_값이_있다는_판정이_적재_쪽과_같다(self) -> None:
        """**한 규칙이 두 곳에 있다** (docs/infra.md 25.0).

        `jobs/metrics.py` 가 표본 부족을 세는 판정과 같은 글자여야 한다.
        갈라지면 "부족" 이라고 세어 놓고 점수에서는 그 행을 쓰게 된다.
        """
        from pathlib import Path

        본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "metrics.py").read_text(
            encoding="utf-8"
        )

        assert "result.cagr is None and result.mdd is None" in 본문, (
            "metrics 쪽 표본 부족 판정이 바뀌었다 — scores._창_우선순위 도 같이 보라"
        )

    def test_표본_부족이어도_행을_쓴다는_전제(self) -> None:
        """이 전제가 깨지면 위 테스트들의 뜻이 사라진다(행이 없으면 원래 폴백이 됐다)."""
        from pathlib import Path

        본문 = (Path(__file__).resolve().parent.parent / "batch" / "services" / "metrics.py").read_text(
            encoding="utf-8"
        )

        assert "표본 부족. 개수만 남기고 값은 채우지 않는다" in 본문


class Test신호도_시점을_본다:
    """`jobs/signals` 에 같은 결함이 **하나 더** 있었다 (docs/infra.md 25.98).

    `load_candidates(client, country, as_of)` 는 기준일을 인자로 받고 질의에 **한 번도
    안 걸었다.** 6월 기준 신호가 9월 점수로 판정됐다는 뜻이다.

    25.97 에서 `jobs/scores.py` 만 훑은 것이 이 결함을 놓친 이유다 —
    **그물의 범위를 결함의 범위보다 좁게 잡으면 그 밖에서 되풀이된다.**
    """

    @staticmethod
    def _붙이기(client) -> None:
        from batch.jobs import signals as sg_job

        _종목(client)
        for 날 in (기준일, 나중):
            client.conn.execute(
                "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                " VALUES (?, 1, 1, 'KRW', 't')",
                [날],
            )
        for 날, 총점 in ((기준일, 60.0), (나중, 95.0)):
            client.conn.execute(
                "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores,"
                " sentiment_weight_used, weights_json, calc_version, created_at)"
                " VALUES (1, ?, ?, '{\"value\": 50}', 0, '{}', 1, 't')",
                [날, 총점],
            )
        return sg_job

    def test_기준일까지의_점수로_판정한다(self) -> None:
        client = _sqlite_client()
        sg_job = self._붙이기(client)

        행 = sg_job.load_candidates(client, "KR", 기준일)

        assert [r["total_score"] for r in 행] == [60.0], (
            "6월 신호를 9월 점수로 판정하고 있다"
        )
        assert 행[0]["score_date"] == 기준일

    def test_시간이_지나면_새_점수를_본다(self) -> None:
        client = _sqlite_client()
        sg_job = self._붙이기(client)

        assert [r["total_score"] for r in sg_job.load_candidates(client, "KR", 나중)] == [95.0]

    def test_점수보다_앞선_기준일이면_점수가_비어_있다(self) -> None:
        """**LEFT JOIN 이라 종목은 남는다.** 판정 입력이 없으면 신호를 안 낼 뿐이다."""
        client = _sqlite_client()
        sg_job = self._붙이기(client)

        행 = sg_job.load_candidates(client, "KR", "2026-01-01")

        assert 행 == [], "유니버스 스냅샷도 그때는 없었다"

    def test_성과지표도_기준일을_건다(self) -> None:
        client = _sqlite_client()
        from batch.jobs import signals as sg_job

        _종목(client)
        for 계산일, 변동성 in ((기준일, 0.20), (나중, 0.55)):
            client.conn.execute(
                "INSERT INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd,"
                " volatility_ann, data_points, calc_version, created_at)"
                " VALUES (1, ?, '3Y', 0.1, -0.3, ?, 700, 1, 't')",
                [계산일, 변동성],
            )

        assert sg_job.load_metrics(client, "KR", 기준일)[1]["volatility_ann"] == pytest.approx(0.20)
        assert sg_job.load_metrics(client, "KR", 나중)[1]["volatility_ann"] == pytest.approx(0.55)

    def test_창_고르기를_한_곳에서_한다(self) -> None:
        """**한 규칙이 두 곳에 있으면 한 곳만 고쳐진다** (docs/infra.md 25.0).

        실제로 그랬다 — 25.97 이 `jobs/scores` 의 폴백을 고쳤을 때
        `jobs/signals` 의 같은 코드는 그대로 남아 있었다.
        """
        import inspect

        from batch.jobs import scores as sc_job
        from batch.jobs import signals as sg_job

        for 모듈 in (sc_job, sg_job):
            원본 = inspect.getsource(모듈.load_metrics)
            assert "mt.pick_window(" in 원본, f"{모듈.__name__} 이 창 고르기를 따로 하고 있다"
            assert "order.index" not in 원본 and "RISK_WINDOWS.index" not in 원본

    def test_읽을_창_목록도_한_곳에서_온다(self) -> None:
        """**규칙이 반만 합쳐져 있었다** (2026-09-23, docs/infra.md 25.176).

        위 검사는 "고르는 규칙" 이 한 곳인지만 본다. 그런데 규칙은 둘이다 —
        **어떤 창을 읽을까**와 **읽은 것 중 무엇을 고를까.** 앞엣것이
        `jobs/signals` 에 `window IN ('3Y', '1Y')` 로 박혀 남아 있었다.

        박힌 채로 `RISK_WINDOWS` 를 고치면 두 가지가 생긴다.
        `("3Y", "1Y", "5Y")` 로 늘리면 점수는 5Y 를 쓰고 신호는 안 써서 **같은 종목의
        리스크가 두 화면에서 다르다.** `("1Y",)` 로 줄이면 신호는 여전히 3Y 행을 읽어
        오고 `pick_window` 가 `ValueError` 로 **배치를 통째로 죽인다.**
        """
        from batch.jobs import scores as sc_job
        from batch.jobs import signals as sg_job

        for 모듈 in (sc_job, sg_job):
            # **설명글을 먼저 걷어낸다.** 안 걷으면 이 고장을 설명한 docstring 자체가
            # 걸려 그물이 제 설명을 문다 (25.175 의 웹 그물에서 같은 일을 겪었다)
            원본 = _코드만(모듈.load_metrics)
            assert "RISK_WINDOWS" in 원본, (
                f"{모듈.__name__}.load_metrics 가 읽을 창 목록을 따로 갖고 있다."
                " `metrics.RISK_WINDOWS` 로 질의를 만들어라"
            )
            for 창 in ("'3Y'", "'1Y'", "'5Y'", '"3Y"', '"1Y"', '"5Y"'):
                assert 창 not in 원본, (
                    f"{모듈.__name__}.load_metrics 에 창 이름 {창} 이 글자로 박혀 있다."
                    " 정의처는 `metrics.RISK_WINDOWS` 하나다 (docs/factors.md 3.5)"
                )


def _코드만(함수: object) -> str:
    """함수 소스에서 **설명글(docstring)을 뺀** 코드만. 주석도 사라진다."""
    import ast
    import inspect
    import textwrap

    나무 = ast.parse(textwrap.dedent(inspect.getsource(함수))).body[0]
    if ast.get_docstring(나무) is not None:
        나무.body = 나무.body[1:]
    return ast.unparse(나무)


class Test규칙에_없는_창은_들어오지_못한다:
    """`pick_window` 는 **부르는 쪽 질의를 믿지 않는다** (docs/infra.md 25.176)."""

    def _행(self, window: str, **덮을것: object) -> dict:
        기본 = {
            "stock_id": 1, "window": window, "as_of_date": "2026-09-23",
            "calc_version": 2, "cagr": 0.1, "mdd": -0.2,
        }  # fmt: skip
        return {**기본, **덮을것}

    def test_규칙에_있는_창은_그대로_지난다(self) -> None:
        나온것 = mt.pick_window([self._행("3Y"), self._행("1Y")])

        assert 나온것[1]["window"] == "3Y"

    @pytest.mark.parametrize("창", ["5Y", "10Y", "", "3y"])
    def test_규칙에_없는_창은_막는다(self, 창: str) -> None:
        with pytest.raises(ValueError, match="리스크 판정이 보지 않는 창"):
            mt.pick_window([self._행(창)])

    def test_한_줄뿐이어도_막는다(self) -> None:
        """**여기가 진짜 고장이었다.**

        예전에는 `RISK_WINDOWS.index()` 가 비교하는 순간에만 불렸다. 같은 종목에 행이
        하나뿐이면 비교가 없으니 **5Y 행이 그대로 리스크 판정에 쓰였다.** 같은 잘못이
        행 개수에 따라 터지기도 하고 조용히 지나가기도 했다.
        """
        with pytest.raises(ValueError):
            mt.pick_window([self._행("5Y")])

        with pytest.raises(ValueError):
            mt.pick_window([self._행("1Y"), self._행("5Y")])

    def test_무엇을_해야_하는지_말한다(self) -> None:
        with pytest.raises(ValueError) as 터진것:
            mt.pick_window([self._행("5Y")])

        글 = str(터진것.value)
        assert "5Y" in 글 and "RISK_WINDOWS" in 글
        assert "docs/factors.md" in 글, "어느 문서가 정의처인지 말해야 한다"

    def test_빈_3Y_문제가_신호_쪽에도_고쳐졌다(self) -> None:
        client = _sqlite_client()
        from batch.jobs import signals as sg_job

        _종목(client)
        client.conn.execute(
            "INSERT INTO performance_metrics (stock_id, as_of_date, window, data_points,"
            " calc_version, created_at) VALUES (1, ?, '3Y', 480, 1, 't')",
            [기준일],
        )
        client.conn.execute(
            "INSERT INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd,"
            " volatility_ann, data_points, calc_version, created_at)"
            " VALUES (1, ?, '1Y', 0.12, -0.3, 0.25, 250, 1, 't')",
            [기준일],
        )

        고른것 = sg_job.load_metrics(client, "KR", 기준일)[1]
        assert 고른것["window"] == "1Y"
        assert 고른것["volatility_ann"] == pytest.approx(0.25)

    def test_cagr_를_읽어야_창_고르기가_돈다(self) -> None:
        """`pick_window` 가 `cagr`·`mdd` 로 '값이 들었나' 를 본다.

        질의에서 `cagr` 를 빼면 **모든 행이 빈 것으로 보여** 창 고르기가 뒤집힌다.
        한 열을 안 읽은 것이 계산을 바꾸는 자리라 그물을 걸어 둔다.
        """
        import inspect

        from batch.jobs import signals as sg_job

        assert "m.cagr" in inspect.getsource(sg_job.load_metrics)


class Test모멘텀_기준점이_한_규칙이다:
    """**같은 실행·같은 종목·같은 창에서 답이 둘이었다** (docs/infra.md 25.100).

    `momentum_12_1` 은 시장 전체 거래일 목록에서 **날짜**로 기준점을 잡았고,
    `momentum_vol_adjusted` 의 12-1 분자는 `load_series` 가 준 **종목 자신의 계열**을
    뒤에서 세어 잡았다. 거래일에 구멍이 있는 종목에서 둘이 갈렸다.
    백테스트는 늘 계열 쪽이라 **셋이 어긋났다.**
    """

    @staticmethod
    def _계열(n: int) -> tuple[list[str], list[float]]:
        """오르되 흔들리는 계열. 흔들림이 없으면 변동성 조정 모멘텀이 None 이다."""
        from datetime import date, timedelta

        끝 = date(2026, 9, 18)
        날짜 = [(끝 - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
        종가 = [100.0 * (1.002**i) + (1.5 if i % 2 else 0.0) for i in range(n)]
        return 날짜, 종가

    def test_12_1_과_변동성_조정의_분자가_같은_창을_본다(self) -> None:
        """**둘의 12-1 분자가 같은 창에서 나온다.**

        `momentum_vol_adjusted = 12-1 수익률 / 연환산 변동성` 이므로,
        같은 창의 변동성을 손으로 내면 **12-1 을 되짚을 수 있다.**
        분자가 다른 창에서 왔다면 이 식이 안 맞는다.
        """
        import math

        from batch.services import scoring as sc_svc

        날짜, 종가 = self._계열(400)
        묶음 = job.build_inputs(
            [{"stock_id": 1, "market": "KOSPI", "sector": None, "market_cap": 1e9}],
            {}, {}, series={1: (날짜, 종가, [])},
        )
        m = 묶음[0].metrics

        창 = sc_svc._formation_window(종가)
        수익률 = [r for r in sc_svc._daily_returns(창) if r is not None]
        평균 = sum(수익률) / len(수익률)
        # 표본 표준편차(n−1) — docs/metrics.md 3장 (docs/infra.md 25.274). 예전 이 줄은 모집단(n)이라 틀린 식을 지켰다
        분산 = sum((r - 평균) ** 2 for r in 수익률) / (len(수익률) - 1)
        연변동성 = math.sqrt(분산) * math.sqrt(sc_svc.TRADING_DAYS_PER_YEAR)

        assert m["momentum_12_1"] is not None and m["momentum_vol_adjusted"] is not None
        assert m["momentum_vol_adjusted"] * 연변동성 == pytest.approx(m["momentum_12_1"])

    def test_적재와_백테스트가_같은_함수를_부른다(self) -> None:
        """**계산식이 하나여도 넣는 점이 둘이면 답이 둘이다** (25.99 와 같은 줄기)."""
        import inspect

        from batch.jobs import backtest as bt_job

        적재 = inspect.getsource(job.build_inputs)
        백테 = inspect.getsource(bt_job.build_pit_inputs)

        assert "sc.momentum_metrics(" in 적재
        assert "sc.momentum_metrics(" in 백테
        assert "momentum_from_points" not in 적재, (
            "적재가 기준점을 따로 고르고 있다 — 그 자리가 갈라짐의 뿌리였다"
        )

    def test_읽을_필요가_없던_질의_둘을_지웠다(self) -> None:
        """`load_series` 가 이미 274일을 읽는다. 다섯 기준점은 그 안에 다 들어 있다."""
        import inspect

        본문 = inspect.getsource(job)

        assert "def momentum_reference_dates(" not in 본문
        assert "def load_reference_closes(" not in 본문
        assert "지운 이유는 두 가지다" in 본문, "왜 지웠는지가 남아 있어야 한다"

    def test_계열_길이가_기준점을_다_덮는다(self) -> None:
        """이 전제가 깨지면 모멘텀이 통째로 빈다. 숫자를 고정해 둔다."""
        from batch.services import scoring as sc_svc

        읽는길이 = max(sc_svc.MOMENTUM_OFFSETS) + 1
        assert 읽는길이 == sc_svc.MOMENTUM_SKIP_DAYS + sc_svc.MOMENTUM_12M_DAYS + 1 == 274

    def test_날짜와_종가의_길이가_어긋나면_막는다(self) -> None:
        """**조용히 어긋나면 잔차 변동성이 엉뚱한 날을 맞춘다.** 터지는 편이 낫다."""
        날짜, 종가 = self._계열(300)

        with pytest.raises(ValueError):
            job.build_inputs(
                [{"stock_id": 1, "market": "KOSPI", "sector": None, "market_cap": 1e9}],
                {}, {}, series={1: (날짜[:-5], 종가, [])},
                benchmark_closes={d: 1000.0 for d in 날짜},
            )


class Test시점을_걸고도_운영_흐름이_돈다:
    """**시점을 조이면 아무것도 안 나올 수 있다.** 그것이 이 변경의 진짜 위험이다.

    `jobs/signals.load_candidates` 가 예전에는 **날짜 상관없이 가장 최근 점수**를 집었다.
    그래서 점수가 언제 것이든 신호가 났다. 25.98 이 `as_of` 이하로 조이면서,
    **점수와 신호의 기준일이 어긋나면 추천이 통째로 0건**이 될 수 있게 됐다.

    `jobs/daily.refresh_recommendations` 는 둘을 **같은 기준일로 잇달아** 부른다
    (`scores.run(as_of=t)` → `signals.run(as_of=t)`). 그 약속이 이 변경을 안전하게 만든다 —
    약속이 깨지면 여기가 먼저 알려 준다.
    """

    def test_daily_가_점수와_신호를_같은_기준일로_부른다(self) -> None:
        import inspect

        from batch.jobs import daily

        원본 = inspect.getsource(daily._scores_and_signals)  # 25.1033: 사본 문맥 안으로 옮겼다

        assert "scores.run(market, as_of=trade_date)" in 원본
        assert "signals.run(market, as_of=trade_date)" in 원본, (
            "신호가 점수와 다른 기준일로 돌면 방금 쓴 점수를 못 찾아 추천이 0건이 된다"
        )

    def test_점수가_실패하면_신호를_안_부른다(self) -> None:
        """**옛 점수로 새 신호를 내지 않는다.** 그 편이 0건보다 나쁘다."""
        import inspect

        from batch.jobs import daily

        원본 = inspect.getsource(daily._scores_and_signals)  # 25.1033: 사본 문맥 안으로 옮겼다
        점수뒤 = 원본.split("scores.run(market", 1)[1]

        assert 점수뒤.index("return warnings") < 점수뒤.index("signals.run"), (
            "점수가 실패해도 신호를 부르고 있다 — 옛 점수로 판정한 신호가 새것처럼 저장된다"
        )

    def test_방금_쓴_점수를_같은_기준일_신호가_찾는다(self) -> None:
        """**왕복을 실제로 돌린다.** 위 둘은 글자를 볼 뿐이다."""
        from batch.jobs import signals as sg_job

        client = _sqlite_client()
        _종목(client)
        client.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, 1, 1, 'KRW', 't')",
            [기준일],
        )
        # scores 가 그 기준일로 쓴 행
        client.conn.execute(
            "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores,"
            " sentiment_weight_used, weights_json, calc_version, created_at)"
            " VALUES (1, ?, 77.0, '{\"value\": 60}', 0, '{}', 1, 't')",
            [기준일],
        )

        행 = sg_job.load_candidates(client, "KR", 기준일)

        assert [r["total_score"] for r in 행] == [77.0], (
            "같은 기준일로 쓴 점수를 신호가 못 찾는다 — 추천이 통째로 0건이 된다"
        )

    def test_따라잡기처럼_기준일을_안_주면_같은_직전_거래일로_돈다(self) -> None:
        """`d1-catchup.yml` 은 `--as-of` 없이 부른다. 그때도 둘이 맞아야 한다.

        기본값은 2026-09-26 부터 '오늘(UTC)' 이 아니라 **직전 거래일**이다 (docs/infra.md 25.234)."""
        import inspect

        from batch.jobs import scores as sc_job
        from batch.jobs import signals as sg_job

        for 모듈 in (sc_job, sg_job):
            원본 = inspect.getsource(모듈.run)
            assert "as_of = as_of or cal.default_as_of(market)" in 원본, (
                f"{모듈.__name__}.run 의 기본 기준일이 직전 거래일이 아니다 — 둘이 어긋나면 0건이다"
            )


class Test기준일을_아예_안_받는_로더:
    """**그물이 한 방향만 봤다** (2026-09-22, docs/infra.md 25.106).

    위의 `Test받은_기준일을_안_쓰는_로더가_또_생기지_않게` 는 `as_of` 를 **받는** 함수만
    본다. 그러니 **아예 안 받는** 함수는 처음부터 그물 밖이다 — 그리고 그쪽이 더 안 보인다.
    인자가 없으면 "시점을 봐야 하는 자리인가" 라는 질문 자체가 안 떠오른다.

    실제로 넷이 있었다.

    * `signals.load_growth` — 중기 신호(실적 모멘텀)의 성장률
    * `signals._latest_equity` — 장기 신호가 **밴드와 견주는 현재 PBR**
      (밴드 쪽은 시점을 지켰다. 견주는 두 값 중 한쪽만 막으면 막은 뜻이 없다)
    * `scores.load_benchmark_closes` — 잔차 변동성의 시장 수익률
    * `valuation_bands.load_members` — 그 시점 유니버스

    규칙: `run(as_of=…)` 에서 **닿을 수 있는** 함수가 `SELECT` 를 쓴다면 기준일을 받아야 한다.
    """

    #: 기준일을 안 받아도 되는 함수와 **사유**. 사유 없는 예외는 두지 않는다
    예외: dict[str, str] = {
        "scores.py::load_pending_adjust": "미국 수정주가 재수집 **대기열**(adjust_refresh_queue.done_at IS NULL)이다 — 지금 대기 중인 종목은"
        " 어느 기준일로 다시 세든 수정종가 기준이 어긋날 수 있어 옮기지 않는다(docs/factors.md 3.1, 25.954). 시점 차원이 없다",
        "sentiment.py::_aliases": "사람이 관리하는 별칭 표(0043, 25.840)라 시점 차원이 없다. 채점 대상 이름만 고르고 값에 시점을 싣지 않는다 (25.938)",
    }

    @staticmethod
    def _읽나(fn) -> bool:
        """본문에 `SELECT` 로 시작하는 문자열이 있는가. 쓰기만 하는 함수(store)는 빠진다."""
        import ast
        import re

        return any(
            isinstance(n, ast.Constant) and isinstance(n.value, str) and re.match(r"\s*SELECT\b", n.value, re.I)
            for n in ast.walk(fn)
        )

    @staticmethod
    def _닿는것(fns: dict, start: str) -> set[str]:
        """`start` 에서 이름으로 불러 닿을 수 있는 함수 이름들 (같은 파일 안)."""
        import ast

        본것: set[str] = set()
        할것 = [start]
        while 할것:
            이름 = 할것.pop()
            fn = fns.get(이름)
            if fn is None or 이름 in 본것:
                continue
            본것.add(이름)
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in fns:
                    할것.append(n.func.id)
        return 본것 - {start}

    def _훑기(self) -> tuple[list[str], int]:
        import ast
        from pathlib import Path

        뿌리 = Path(__file__).resolve().parent.parent
        새는것: list[str] = []
        본_모듈 = 0
        for path in sorted((뿌리 / "batch" / "jobs").glob("*.py")):
            나무 = ast.parse(path.read_text(encoding="utf-8"))
            fns = {n.name: n for n in ast.walk(나무) if isinstance(n, ast.FunctionDef)}
            run = fns.get("run")
            if run is None:
                continue
            받는인자 = {a.arg for a in run.args.args} | {a.arg for a in run.args.kwonlyargs}
            if not ({"as_of", "trade_date"} & 받는인자):
                continue
            본_모듈 += 1
            for 이름 in sorted(self._닿는것(fns, "run")):
                fn = fns[이름]
                if {"as_of", "trade_date", "cutoff", "since", "today"} & {a.arg for a in fn.args.args}:
                    continue
                if f"{path.name}::{이름}" in self.예외:
                    continue
                if self._읽나(fn):
                    새는것.append(f"{path.name}::{이름} (줄 {fn.lineno})")
        return 새는것, 본_모듈

    def test_기준일_배치에서_읽는_함수는_기준일을_받는다(self) -> None:
        새는것, _ = self._훑기()
        assert not 새는것, (
            "`run(as_of=…)` 에서 닿는데 기준일을 **아예 안 받는** 조회 함수가 있다.\n"
            "  인자가 없으면 아무도 시점을 묻지 않는다 — 과거 기준일로 다시 계산할 때\n"
            "  그 자리만 오늘 값을 쓴다:\n  " + "\n  ".join(새는것)
        )

    def test_예외에_사유가_있다(self) -> None:
        assert all(self.예외.values())

    def test_훑기가_실제로_모듈을_찾았다(self) -> None:
        """**읽어 냈는지 먼저 센다.** 0개를 훑고 통과하면 그물이 아니라 장식이다."""
        _, 본_모듈 = self._훑기()
        assert 본_모듈 >= 4, f"기준일을 받는 run() 이 있는 모듈을 {본_모듈}개밖에 못 찾았다"


class Test기준일을_안_받던_넷:
    """위 그물이 잡아낸 넷을 **값으로** 확인한다 (docs/infra.md 25.106).

    그물만 두면 다음 사람이 인자만 더하고 질의에는 안 걸 수 있다. 인자는 생겼으니
    그물은 조용하다 — 25.98 에서 본 그대로다. 그래서 넷 다 값으로 한 번씩 더 본다.
    """

    @staticmethod
    def _재무(client, stock_id: int, fiscal_year: int, 접수일: str, **값) -> None:
        client.conn.execute(
            "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated,"
            " report_date, receipt_no, currency, unit, revenue, operating_income, total_equity, source, fetched_at)"
            " VALUES (?, ?, '11011', 'A', 1, ?, ?, 'KRW', '원', ?, ?, ?, 't', 't')",
            [stock_id, fiscal_year, 접수일, f"{접수일}0001",
             값.get("revenue"), 값.get("operating_income"), 값.get("total_equity")],
        )

    def test_성장률은_그때_나온_실적만_쓴다(self) -> None:
        """중기 신호는 실적 모멘텀이다. 아직 안 나온 실적으로 모멘텀을 재면 안 된다."""
        from batch.jobs import signals as sg_job

        client = _sqlite_client()
        _종목(client)
        self._재무(client, 1, 2024, "2025-03-10", revenue=100.0, operating_income=10.0)
        self._재무(client, 1, 2025, 나중, revenue=200.0, operating_income=20.0)  # 기준일 **뒤** 접수

        행 = sg_job.load_growth(client, "KR", 기준일)

        assert 행[1]["fiscal_year"] == 2024, "기준일에는 2025 사업보고서가 아직 안 나왔다"
        assert 행[1]["revenue_growth"] is None, "전년이 없으니 성장률을 지어내면 안 된다"

    def test_기준일_뒤면_성장률이_보인다(self) -> None:
        """대조군. 위 테스트가 '늘 None' 으로 통과하는 것이 아님을 못 박는다."""
        from batch.jobs import signals as sg_job

        client = _sqlite_client()
        _종목(client)
        self._재무(client, 1, 2024, "2025-03-10", revenue=100.0, operating_income=10.0)
        self._재무(client, 1, 2025, "2026-03-10", revenue=200.0, operating_income=20.0)

        행 = sg_job.load_growth(client, "KR", "2026-06-30")

        assert 행[1]["fiscal_year"] == 2025
        assert 행[1]["revenue_growth"] == pytest.approx(1.0)

    def test_묵은_사업보고서로는_성장률을_내지_않는다(self) -> None:
        """사업보고서를 한 해 건너뛴 회사가 재작년 성장률로 중기 신호를 받았다 (docs/infra.md 25.660, 감사)."""
        from batch.jobs import signals as sg_job

        client = _sqlite_client()
        _종목(client)
        self._재무(client, 1, 2023, "2024-03-10", revenue=100.0, operating_income=10.0)
        self._재무(client, 1, 2024, "2025-03-10", revenue=200.0, operating_income=20.0)

        묵음 = sg_job.load_growth(client, "KR", "2026-09-29")[1]  # FY2025 가 안 나왔다 — 접수 1년 반 전
        assert 묵음["revenue_growth"] is None and 묵음["stale_report_date"] == "2025-03-10"
        제때 = sg_job.load_growth(client, "KR", "2026-03-31")[1]  # 1년 3주 — 아직 주기 안
        assert 제때["revenue_growth"] == pytest.approx(1.0)

    def test_정정공시로_접수일이_새로워도_재작년_회계연도면_묵음이다(self) -> None:
        """FY2024 를 2025-12 에 정정하고 FY2025 가 안 나온 회사가 신선해 보였다 (docs/infra.md 25.663, 교차검증)."""
        from batch.jobs import signals as sg_job

        client = _sqlite_client()
        _종목(client)
        self._재무(client, 1, 2023, "2024-03-10", revenue=100.0, operating_income=10.0)
        self._재무(client, 1, 2024, "2025-12-01", revenue=200.0, operating_income=20.0)  # 정정 접수일

        행 = sg_job.load_growth(client, "KR", "2026-09-29")[1]
        assert 행["revenue_growth"] is None and "FY2025" not in 행["stale_reason"] and "2025 회계연도" in 행["stale_reason"]
        assert sg_job.load_growth(client, "KR", "2026-06-30")[1]["revenue_growth"] == pytest.approx(1.0)  # 7월 전

    def test_밴드와_견주는_현재_PBR_도_시점을_지킨다(self) -> None:
        """**견주는 두 값 중 한쪽만 막으면 막은 뜻이 없다.**

        밴드는 `known_equity_at` 으로 시점을 지켰는데 현재 PBR 만 최신 자본을 썼다.
        자본이 커지면 PBR 이 작아지므로, 안 막으면 **실제보다 싸 보여** 장기 신호가 더 켜진다.
        """
        from batch.jobs import signals as sg_job

        client = _sqlite_client()
        _종목(client)
        self._재무(client, 1, 2024, "2025-03-10", total_equity=1_000.0)
        self._재무(client, 1, 2025, 나중, total_equity=9_999.0)

        assert sg_job._latest_equity(client, 1, 기준일) == 1_000.0
        assert sg_job._latest_equity(client, 1, "2026-09-30") == 9_999.0
        # 옛 연도(2024)가 나중에 정정돼도 최신 연도(2025)를 쓴다 (25.549, 감사 재현)
        client.conn.execute(
            "UPDATE financials SET report_date = '2026-09-20', total_equity = 500 WHERE fiscal_year = 2024"
        )
        assert sg_job._latest_equity(client, 1, "2026-09-30") == 9_999.0

    def test_안_걸면_미래_자본이_들어온다(self) -> None:
        """고치기 전 상태를 고정한다."""
        client = _sqlite_client()
        _종목(client)
        self._재무(client, 1, 2024, "2025-03-10", total_equity=1_000.0)
        self._재무(client, 1, 2025, 나중, total_equity=9_999.0)

        샌것 = client.execute(
            "SELECT total_equity FROM financials WHERE stock_id = 1 AND report_code = '11011'"
            " AND consolidated = 1 ORDER BY fiscal_year DESC LIMIT 1",
            [],
        ).scalar()
        assert 샌것 == 9_999.0, "옛 질의는 기준일과 상관없이 최신 자본을 집었다"

    def test_잔차_변동성의_시장_수익률도_기준일까지만(self) -> None:
        """종목 종가는 기준일에 묶여 있는데 지수만 안 묶여 있었다.

        `aligned_returns` 가 날짜로 맞추므로 겹치는 날이 없으면 **아무 값도 안 나온다.**
        틀린 값보다 알아채기 어렵다 — 리스크 팩터의 재료 하나가 조용히 사라진다.
        """
        from batch.jobs import scores as job

        client = _sqlite_client()
        for 날, 종가 in ((기준일, 2_500.0), (나중, 9_999.0)):
            client.conn.execute(
                "INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
                " VALUES ('KOSPI', ?, ?, 't', 't')",
                [날, 종가],
            )

        닫힘 = job.load_benchmark_closes(client, "KR", 500, 기준일)

        assert 닫힘 == {기준일: 2_500.0}, "기준일 뒤의 지수가 들어왔다"
        assert len(job.load_benchmark_closes(client, "KR", 500, "2026-09-30")) == 2

    def test_밴드를_만들_종목도_그때_편입돼_있던_것만(self) -> None:
        from batch.jobs import valuation_bands as vb_job

        client = _sqlite_client()
        _종목(client, 1)
        _종목(client, 3)
        client.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, 1, 1, 'KRW', 't')",
            [기준일],
        )
        client.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, 3, 1, 'KRW', 't')",
            [나중],
        )

        # 스냅샷 **하나**를 고른다 — 기준일 이하에서 가장 늦은 것. 여러 날을 합치지 않는다
        assert [r["stock_id"] for r in vb_job.load_members(client, "KR", 기준일)] == [1]
        assert [r["stock_id"] for r in vb_job.load_members(client, "KR", "2026-09-30")] == [3]
