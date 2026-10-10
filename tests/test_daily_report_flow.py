"""아침 리포트를 **실제 스키마 위에서 끝까지** 만들어 본다.

여기서 잡으려는 것은 하나다: **열 이름이 틀리면 내일 아침 리포트가 통째로 깨진다.**
단위 테스트는 각 조각을 보지만, 조각을 잇는 SQL(신호·보유·감성·국면·저장)은 실제 표에
돌려 봐야 안다. 네트워크·텔레그램을 타지 않고, 배치가 부르는 함수를 그 순서대로 부른다.

흐름은 batch/jobs/daily.run 의 리포트 부분과 같다:
  load_signal_rows → load_holdings → trend.report_line → report_picks.compose
  → reports.items_from → reports.store_report → mark_sent
"""

from __future__ import annotations

import json

from batch.jobs import daily
from batch.jobs import signals as signals_job
from batch.services import report_picks, reports, trend
from tests.test_report_picks import SqliteClient

AS_OF = "2026-09-16"


def seed(client: SqliteClient) -> None:
    c = client.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, name_ko, sector)"
        " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '삼성전자', '반도체'),"
        "        (2, '000660', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', 'SK하이닉스', '반도체'),"
        "        (9, '035720', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '보유중', '인터넷')"
    )
    for stock_id, close in ((1, 70000.0), (2, 200000.0), (9, 50000.0)):
        c.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (?, ?, ?, 'KRW', 'krx_openapi', 't')",
            [stock_id, AS_OF, close],
        )
    for stock_id, score in ((1, 78.4), (2, 71.0)):
        c.execute(
            "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
            " weights_json, rank_in_market, calc_version, created_at)"
            " VALUES (?, ?, ?, '{\"value\":83,\"quality\":71}', 0, '{}', 1, 1, 't')",
            [stock_id, AS_OF, score],
        )
    for stock_id, amount in ((1, 1_000_000.0), (2, 800_000.0)):
        c.execute(
            "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high,"
            " currency, tranche_plan, target_price, stop_price, suggested_weight_pct, suggested_amount,"
            " size_reduction, sector_cap_applied, sector_cap_note, rationale_text, rationale_data,"
            " calc_version, created_at)"
            " VALUES (?, ?, 'mid', '실적 모멘텀', 68000, 71000, 'KRW',"
            " '[{\"step\":1,\"ratio\":0.4,\"price\":70000,\"amount\":400000}]', 85000, 60000, 10.0, ?,"
            " 1.0, 0, '섹터 상한 미적용 (업종 데이터 없음)', '매출 18.2%', '{\"criteria\": [{\"label\": \"매출 성장\", \"display\": \"18.2%\", \"threshold\": \"> 0\", \"source\": \"dart\", \"value\": 0.182}]}', 1, 't')",
            [stock_id, AS_OF, amount],
        )
    # 보유 한 종목 (평가까지 끝난 상태). 2부의 여력·섹터를 줄인다
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw,"
        " first_buy_date, market_value_krw, updated_at)"
        " VALUES (9, 10, 'KRW', 45000, 1, 450000, 450000, '2026-01-02', 3000000, 't')"
    )
    # 지수는 200일치가 있어야 국면이 나온다 (docs/signals.md 3.5)
    for i in range(200):
        day = f"2026-{3 + i // 31:02d}-{1 + i % 31:02d}"
        c.execute(
            "INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('KOSPI', ?, ?, 'y', 't')",
            [day, 2600.0],
        )
    c.execute(
        "INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
        " VALUES ('KOSPI', ?, 2400, 'y', 't')",
        [AS_OF],
    )


class Test아침리포트:
    def _client(self) -> SqliteClient:
        client = SqliteClient()
        seed(client)
        return client

    def test_제외된_종목의_신호는_리포트에_싣지_않는다(self) -> None:
        """웹 카드와 같은 상태 조건 (docs/infra.md 25.802, 추천 감사 #5)."""
        client = self._client()
        client.conn.execute("UPDATE stocks SET status = 'excluded' WHERE ticker = '000660'")
        rows, _ = daily.load_signal_rows(client, "KR")  # type: ignore[arg-type]
        assert {r.ticker for r in rows} == {"005930"}

    def test_신호와_보유를_읽어_두_부를_만들고_저장한다(self) -> None:
        client = self._client()

        rows, as_of = daily.load_signal_rows(client, "KR")  # type: ignore[arg-type]
        assert as_of == AS_OF
        assert {r.ticker for r in rows} == {"005930", "000660"}
        assert rows[0].close is not None and rows[0].total_score is not None
        # 종가의 날짜도 함께 읽는다 — 1부가 "현재가" 대신 "몇 일 종가" 로 적는다 (docs/infra.md 25.348)
        assert rows[0].close_date is not None and rows[0].close_date <= AS_OF

        holdings = daily.load_holdings(client, "KRW", None)  # type: ignore[arg-type]
        assert [(h.ticker, h.value, h.sector) for h in holdings] == [("035720", 3_000_000.0, "인터넷")]

        settings = signals_job.load_settings(client, "KRW", AS_OF)  # type: ignore[arg-type]
        trend_settings = trend.load_settings(client)  # type: ignore[arg-type]
        regimes = trend.regimes_for_country(client, "KR", AS_OF)  # type: ignore[arg-type]
        assert regimes["KOSPI"].state == "bear"  # 2400 < 200일선
        regime_line = trend.report_line(regimes, trend_settings)

        composed = report_picks.compose(
            rows, 10_000_000, as_of, "KRW", None, settings["max_weight_per_sector"], [], regime_line, holdings,
            settings["max_weight_per_stock"],
        )
        text = composed.text
        assert "1부 개별 종목" in text and "2부 포트폴리오" in text
        assert "시장 국면" in text.splitlines()[1]
        assert "현재 보유 평가 3,000,000원 (1종목)" in text
        # 보유 300만 + 배분 180만 → 남은 520만
        assert "남은 여력 5,200,000원" in text
        assert "정합성 경고" not in text

        report_id = reports.store_report(
            client, market="KR", trade_date=AS_OF, status="success", message=text, warnings=[],  # type: ignore[arg-type]
            items=reports.items_from(composed, [], [], regime_line), batch_run_id=None,
        )
        reports.mark_sent(client, report_id, [11])  # type: ignore[arg-type]
        stored = client.conn.execute(
            "SELECT summary_text, sent_at, telegram_message_id FROM daily_reports WHERE id = ?", [report_id]
        ).fetchone()
        assert stored[0] == text and stored[1] and stored[2] == "11"

        sections = client.conn.execute(
            "SELECT section, COUNT(*) FROM report_items WHERE report_id = ? GROUP BY section ORDER BY section",
            [report_id],
        ).fetchall()
        assert dict(sections) == {"buy_signal": 2, "notice": 1, "recommend": 2}
        # 1부 추천마다 그날 신호의 근거표와 계산일이 실린다 — 웹 리포트 화면이 펼친다 (docs/infra.md 25.349)
        rec = json.loads(
            client.conn.execute(
                "SELECT payload_json FROM report_items WHERE report_id = ? AND section = 'recommend' LIMIT 1", [report_id]
            ).fetchone()[0]
        )
        assert rec["criteria"] == [{"label": "매출 성장", "display": "18.2%", "threshold": "> 0", "source": "dart", "value": 0.182}]
        assert rec["as_of_date"] == AS_OF
        payload = json.loads(
            client.conn.execute(
                "SELECT payload_json FROM report_items WHERE report_id = ? AND section = 'notice'", [report_id]
            ).fetchone()[0]
        )
        assert payload["kind"] == "regime"

    def test_보유가_섹터_상한을_채우면_2부에서_빠지고_사유가_남는다(self) -> None:
        client = self._client()
        # 반도체를 이미 280만(28%) 들고 있다고 두면 신호 두 건(각 10%)이 상한 30% 를 넘는다
        client.conn.execute("UPDATE stocks SET sector = '반도체' WHERE id = 9")
        rows, as_of = daily.load_signal_rows(client, "KR")  # type: ignore[arg-type]
        holdings = daily.load_holdings(client, "KRW", None)  # type: ignore[arg-type]
        composed = report_picks.compose(rows, 10_000_000, as_of, "KRW", None, 30.0, [], None, holdings, 10.0)
        assert composed.portfolio is not None
        assert len(composed.portfolio.allocations) == 0
        assert {e.reason for e in composed.portfolio.excluded} == {"섹터 상한 도달"}
        assert "배분에서 빠진 종목" in composed.text

    def test_리포트_거래일보다_뒤의_신호는_읽지_않는다(self) -> None:
        """docs/infra.md 25.234 — 주말 날짜로 쌓인 신호가 월요일 리포트의 '가장 새 추천' 이 됐다."""
        client = self._client()
        뒤 = "2099-01-04"
        # 있는 신호를 뒤 날짜로 한 벌 더 복사한다 (열 순서를 몰라도 되게 PRAGMA 로 읽는다)
        열 = [r[1] for r in client.conn.execute("PRAGMA table_info(signals)")]
        고른열 = ", ".join("? " if c == "as_of_date" else c for c in 열 if c != "id")
        client.conn.execute(
            f"INSERT INTO signals ({', '.join(c for c in 열 if c != 'id')}) SELECT {고른열} FROM signals",
            [뒤],
        )
        _, 제한없음 = daily.load_signal_rows(client, "KR")  # type: ignore[arg-type]
        assert 제한없음 == 뒤  # 막지 않으면 뒤 날짜가 이긴다 — 이 검사가 실제로 무는지의 확인
        _, as_of = daily.load_signal_rows(client, "KR", upto=AS_OF)  # type: ignore[arg-type]
        assert as_of == AS_OF

    def test_오늘_계산했는데_한_건도_안_걸리면_어제_신호를_싣지_않는다(self) -> None:
        """docs/infra.md 25.337 — `signals` 의 MAX 만 보면 어제 신호가 오늘 추천으로 금액까지 붙어 나갔다."""
        client = self._client()
        오늘 = "2099-01-05"
        client.conn.execute(
            "INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json,"
            " calc_version, created_at) VALUES (1, ?, 'long', 0, 2, '[]', 1, 't')",
            [오늘],
        )
        rows, as_of = daily.load_signal_rows(client, "KR", upto=오늘)  # type: ignore[arg-type]
        assert as_of == 오늘
        assert rows == []

    def test_신호가_없으면_1부만_없다고_적고_저장도_비어_있다(self) -> None:
        client = SqliteClient()  # 아무것도 없는 DB
        rows, as_of = daily.load_signal_rows(client, "KR")  # type: ignore[arg-type]
        assert rows == [] and as_of is None
        composed = report_picks.compose(rows, 0, as_of)
        assert "오늘 추천할 종목이 없습니다" in composed.text
        assert reports.items_from(composed) == []


class Test환율은_포트폴리오의_재료다:
    """**미국 배치가 쉬면 아무도 환율을 안 받았다** (docs/infra.md 25.137).

    환율을 받는 곳이 `collect_us_prices` 안뿐이었다 — "미국 권장 금액을 달러로 내려면"
    이 이유였다. 그런데 바로 뒤의 `refresh_portfolio` 가 **미국 보유분을 원화로 평가**한다.
    미국 배치는 D1 운영 중 쉰다(25.14). 그 동안 원화 평가액과 환차손익은 몇 주 묵은
    환율로 계산되어 화면에 오늘 값으로 뜬다 — 25.83 이 복귀 경로에서 찾은 것과 같은 모양이
    일일 경로에 하나 더 있었다.
    """

    def test_보유_평가_전에_환율을_받는다(self, monkeypatch) -> None:
        from batch.jobs import daily

        부른순서: list[str] = []
        monkeypatch.setattr(daily, "_collect_fx", lambda: (부른순서.append("환율"), [])[1])

        import batch.jobs.portfolio as pj
        import batch.jobs.sell_flags as sj

        monkeypatch.setattr(pj, "run", lambda: (부른순서.append("평가"), 0)[1])
        monkeypatch.setattr(sj, "run", lambda *a, **k: (부른순서.append("플래그"), 0)[1])

        daily.refresh_portfolio(None)

        assert 부른순서[0] == "환율", f"환율을 평가보다 먼저 안 받는다: {부른순서}"
        assert "평가" in 부른순서

    def test_환율_경고는_리포트로_올라간다(self, monkeypatch) -> None:
        """**로그에만 남기지 않는다.** 환율이 없으면 원화 평가액이 통째로 어긋난다."""
        from batch.jobs import daily

        monkeypatch.setattr(daily, "_collect_fx", lambda: ["환율 수집 실패: 야후 응답 없음"])
        import batch.jobs.portfolio as pj
        import batch.jobs.sell_flags as sj

        monkeypatch.setattr(pj, "run", lambda: 0)
        monkeypatch.setattr(sj, "run", lambda *a, **k: 0)

        경고 = daily.refresh_portfolio(None)

        assert any("환율" in w for w in 경고), 경고

    def test_클라이언트를_못_열어도_배치가_죽지_않는다(self, monkeypatch) -> None:
        """**환율 하나 때문에 일일 배치 전체가 죽으면 안 된다.**

        클라이언트를 만드는 줄을 `try` 밖에 두면 환경변수가 비었을 때 예외가 그대로
        올라간다. 처음 쓸 때 실제로 그랬고 이 검사가 잡았다.
        """
        from batch.jobs import daily

        def 못연다():
            raise RuntimeError("DB 없음")

        monkeypatch.setattr(daily, "TursoClient", 못연다)

        경고 = daily._collect_fx()

        assert 경고 and "환율" in 경고[0]


def test_환율을_부르는_곳이_보유_평가_경로에_있다() -> None:
    """**부르는 곳이 없으면 헬퍼를 하나 더 만든 것일 뿐이다** (25.123 에서 배운 것)."""
    import ast
    from pathlib import Path

    본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "daily.py").read_text("utf-8")
    나무 = ast.parse(본문)
    안에서 = [
        n
        for fn in ast.walk(나무)
        if isinstance(fn, ast.FunctionDef) and fn.name == "refresh_portfolio"
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_collect_fx"
    ]
    assert 안에서, "refresh_portfolio 가 _collect_fx 를 안 부른다 — 묵은 환율로 평가한다"


class Test기업행위를_알아챈다:
    """**국내 수정주가는 예약이 없다** (docs/infra.md 25.138).

    `adjust-kr.yml` 은 `workflow_dispatch` 뿐이고 일일 배치도 부르지 않는다. 사람이 손으로
    돌릴 때만 돈다. 그 사이에 액면분할이 나면 `COALESCE(adj_close, close)` 계열이 그날
    **-90% 로 꺾이고** 모멘텀·변동성·MDD·베타·백테스트가 전부 그 값을 본다.

    매일 다시 내지는 못한다 — 전 종목 수정주가는 하루 읽기 예산의 3분의 2를 쓴다
    (`adjust_kr.ROWS_PER_STOCK = 1,250` × 2,600종목). 그래서 **싼 쪽**을 매일 한다.
    """

    class _Row:
        def __init__(self, isu_cd: str, close: float, change_pct: float | None) -> None:
            self.isu_cd, self.close, self.change_pct = isu_cd, close, change_pct

    @staticmethod
    def _client(직전: str, 종가: dict[int, float], 보유: dict[int, str] | None = None):
        """가짜 DB. **질의마다 다른 답을 준다** — 아무거나 같은 행을 돌려주면

        보유 조회가 종가 행을 받아 "보유 중" 이라고 답한다. 2026-09-23 에 실제로 그랬다.
        """

        class C:
            def execute(self, sql, args=None):
                class RS:
                    def __init__(self, rows):
                        self.rows = rows

                    def scalar(self):
                        return self.rows[0][0] if self.rows else None

                if "MAX(p.date)" in sql:
                    return RS([(직전,)])
                if "FROM positions" in sql:
                    return RS(list((보유 or {}).items()))
                return RS([(sid, v) for sid, v in 종가.items()])

        return C()

    def test_분할을_알아채고_무엇을_하라고_말한다(self) -> None:
        from batch.jobs import daily

        # 1:2 분할. 등락률 0% 인데 종가가 반이 됐다
        말 = daily._detect_kr_actions(
            self._client("2026-09-17", {1: 1000.0}),
            "2026-09-18",
            [self._Row("005930", 500.0, 0.0)],
            {"005930": 1},
        )

        assert len(말) == 1, "보유하지 않은 종목인데 수량 얘기까지 했다"
        assert "005930" in 말[0] and "수정주가" in 말[0]

    def test_걸린_종목만_수정주가를_바로_다시_낸다(self, monkeypatch) -> None:
        """25.1009: 예전에는 사람이 Actions 를 돌릴 때까지 그 종목 수정종가가 꺾인 채 점수에 들어갔다."""
        from batch.jobs import adjust_kr, daily

        받은: list[list[int]] = []
        monkeypatch.setattr(adjust_kr, "adjust_stocks", lambda c, ids: 받은.append(ids) or {"rows_updated": 1234})
        말 = daily._detect_kr_actions(
            self._client("2026-09-17", {1: 1000.0, 2: 2000.0}),
            "2026-09-18",
            [self._Row("005930", 500.0, 0.0), self._Row("000660", 1000.0, 0.0)],
            {"005930": 1, "000660": 2},
        )
        assert 받은 == [[1, 2]]
        assert "바로 다시 냈습니다(바뀐 행 1,234)" in 말[0] and "Actions" not in 말[0]

        # 못 내면 예전처럼 사람에게 말한다
        def 터짐(c, ids):  # noqa: ANN001, ANN202
            raise RuntimeError("읽기 실패")

        monkeypatch.setattr(adjust_kr, "adjust_stocks", 터짐)
        말 = daily._detect_kr_actions(
            self._client("2026-09-17", {1: 1000.0}), "2026-09-18", [self._Row("005930", 500.0, 0.0)], {"005930": 1}
        )
        assert "바로 내지 못했습니다(읽기 실패)" in 말[0] and "Actions" in 말[0]

    def test_걸린_종목이_많으면_바로_내지_않는다(self, monkeypatch) -> None:
        from batch.jobs import adjust_kr, daily

        받은: list = []
        monkeypatch.setattr(adjust_kr, "adjust_stocks", lambda c, ids: 받은.append(ids) or {"rows_updated": 0})
        n = daily.AUTO_ADJUST_MAX_STOCKS + 1
        말 = daily._detect_kr_actions(
            self._client("2026-09-17", {i: 1000.0 for i in range(n)}),
            "2026-09-18",
            [self._Row(f"{i:06d}", 500.0, 0.0) for i in range(n)],
            {f"{i:06d}": i for i in range(n)},
        )
        assert 받은 == [] and "Actions" in 말[0]

    def test_하루_수집_구멍이면_기업행위가_아닐_수_있다고_말한다(self) -> None:
        """간격 2일짜리 구멍은 11일 문턱에 안 걸려 단서 없이 "기업행위" 가 됐다 (docs/infra.md 25.569, 감사 재현)."""
        from batch.jobs import daily

        # 09-18(금) 기준, 직전 거래일 09-17 행이 없고 09-16 종가와 견준다
        말 = daily._detect_kr_actions(
            self._client("2026-09-16", {1: 1000.0}),
            "2026-09-18",
            [self._Row("005930", 1210.0, 10.0)],
            {"005930": 1},
        )
        assert "직전 거래일(2026-09-17)의 시세가 없고" in 말[0]
        # 구멍 뒤에는 수정주가·보유 수량 안내를 붙이지 않는다 (25.572, 교차검증)
        보유 = daily._detect_kr_actions(
            self._client("2026-09-16", {1: 1000.0}, 보유={1: "005930"}),
            "2026-09-18",
            [self._Row("005930", 1210.0, 10.0)],
            {"005930": 1},
        )
        assert "수정주가를 다시" not in 보유[0]
        # 보유 종목 안내는 단서를 달아 남긴다 — 진짜 분할을 영영 놓치지 않게 (25.574, 교차검증)
        assert len(보유) == 2 and "확실하지 않습니다" in 보유[1] and "수량" in 보유[1]

    def test_보유_중이면_수량을_확인하라고_말한다(self) -> None:
        """**분할은 가격만 바꾸지 않는다** (docs/infra.md 25.146).

        1:2 분할이면 종가는 반이 되는데 `trades` 의 수량은 사용자가 적은 값 그대로다
        (CLAUDE.md: 시스템이 고치지 않는다). 그래서 평가액이 **조용히 절반**이 되고,
        그 값이 그대로 매도 플래그에 들어가 **거짓 손절 알림**이 된다.
        """
        from batch.jobs import daily

        말 = daily._detect_kr_actions(
            self._client("2026-09-17", {1: 1000.0}, 보유={1: "005930"}),
            "2026-09-18",
            [self._Row("005930", 500.0, 0.0)],
            {"005930": 1},
        )

        assert len(말) == 2
        assert "수량" in 말[1] and "005930" in 말[1]
        assert "손절" in 말[1], "무엇이 틀어지는지 말해야 사람이 움직인다"

    def test_보유_목록을_읽지_못해도_배치가_죽지_않는다(self) -> None:
        from batch.jobs import daily

        class 깨진:
            #: 표가 없는 DB 의 SQLite 오류 글자. 다른 실패는 따로 경고가 붙는다 (25.219)
            오류 = "no such table: positions"

            def execute(self, sql, args=None):
                class RS:
                    rows = [("2026-09-17",)]

                    def scalar(self):
                        return "2026-09-17"

                if "FROM positions" in sql:
                    raise RuntimeError(self.오류)
                if "MAX(p.date)" in sql:
                    return RS()
                rs = RS()
                rs.rows = [(1, 1000.0)]
                return rs

        말 = daily._detect_kr_actions(
            깨진(), "2026-09-18", [self._Row("005930", 500.0, 0.0)], {"005930": 1}
        )

        assert len(말) == 1, "말해 주는 일이 배치를 죽였다"

        # 표가 없는 것이 아니라 **읽기가 실패**했으면 그 사실도 말한다 (25.219)
        깨진.오류 = "D1 일일 읽기 한도 초과"
        말 = daily._detect_kr_actions(
            깨진(), "2026-09-18", [self._Row("005930", 500.0, 0.0)], {"005930": 1}
        )
        assert len(말) == 2 and "보유를 읽지 못함" in 말[1]

    def test_평범한_날에는_조용하다(self) -> None:
        """거짓 경보를 쌓으면 사람이 경고를 안 읽는다."""
        from batch.jobs import daily

        말 = daily._detect_kr_actions(
            self._client("2026-09-17", {1: 1000.0}),
            "2026-09-18",
            [self._Row("005930", 1012.0, 1.2)],
            {"005930": 1},
        )
        assert 말 == []

    def test_구멍_위면_단정하지_않는다(self) -> None:
        """계수 식은 "앞 행 = 바로 전 거래일" 을 전제한다 (25.132)."""
        from batch.jobs import daily

        말 = daily._detect_kr_actions(
            self._client("2026-08-01", {1: 1000.0}),
            "2026-09-18",
            [self._Row("005930", 500.0, 0.0)],
            {"005930": 1},
        )

        assert 말 and "기업행위가 아닐 수 있습니다" in 말[0]

    def test_직전_거래일이_없으면_아무_말도_안_한다(self) -> None:
        from batch.jobs import daily

        말 = daily._detect_kr_actions(
            self._client(None, {}), "2026-09-18", [self._Row("005930", 500.0, 0.0)], {"005930": 1}
        )
        assert 말 == []


def test_리포트의_과거_성과는_비중_축소와_같은_행을_쓴다() -> None:
    """docs/infra.md 25.268 — 3Y 만 보고 기준일로 자르지 않아, 상장 1~3년 종목은 MDD 가 비고 신호 뒤 지표가 섞였다."""
    client = SqliteClient()
    seed(client)
    c = client.conn
    sid = c.execute("SELECT stock_id FROM signals LIMIT 1").fetchone()[0]
    열 = [r[1] for r in c.execute("PRAGMA table_info(performance_metrics)")]

    def 넣기(창: str, 날: str, mdd: float | None, cagr: float | None) -> None:
        값 = {"stock_id": sid, "as_of_date": 날, "window": 창, "mdd": mdd, "cagr": cagr, "sharpe": 0.5,
             "calc_version": 1, "data_points": 300, "created_at": "t"}  # fmt: skip
        쓸열 = [k for k in 값 if k in 열]
        c.execute(
            f"INSERT INTO performance_metrics ({', '.join(chr(34) + k + chr(34) for k in 쓸열)})"
            f" VALUES ({', '.join('?' * len(쓸열))})",
            [값[k] for k in 쓸열],
        )

    넣기("3Y", AS_OF, None, None)  # 상장 1~3년 — 3Y 는 비었다
    넣기("1Y", AS_OF, -0.48, 0.12)
    넣기("3Y", "2099-01-01", -0.99, -0.5)  # 신호 뒤에 계산된 행 — 보면 안 된다
    rows, _ = daily.load_signal_rows(client, "KR")  # type: ignore[arg-type]
    행 = next(r for r in rows if r.stock_id == sid)
    assert 행.mdd == -0.48 and 행.cagr == 0.12


def test_거래정지_뒤_재개한_분할도_알아챈다() -> None:
    """docs/infra.md 25.246 — 전날 행이 없는 종목(정지)은 건너뛰어져 분할을 놓쳤다. 실제 스키마로 본다."""
    client = SqliteClient()
    c = client.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
              " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 'active', 't', 't'),"
              "        (2, '000660', 'KOSPI', 'KR', '나', 'KRW', 'active', 't', 't')")  # fmt: skip
    for sid, d, close in [(1, "2026-10-13", 1000.0), (1, "2026-10-14", 1000.0), (2, "2026-10-14", 500.0)]:
        c.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?, ?, ?, 'KRW', 't', 't')",
                  [sid, d, close])  # fmt: skip
    # 1번은 10-14 이후 정지(10-15·16 행 없음), 10-19 에 1:5 분할로 재개. 2번은 평범하게 10-16 에도 거래
    c.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
              " VALUES (2, '2026-10-16', 505.0, 'KRW', 't', 't')")  # fmt: skip

    class _Row:
        def __init__(self, isu_cd: str, close: float, change_pct: float) -> None:
            self.isu_cd, self.close, self.change_pct = isu_cd, close, change_pct

    말 = daily._detect_kr_actions(
        client,  # type: ignore[arg-type]
        "2026-10-19",
        [_Row("005930", 200.0, 0.0), _Row("000660", 510.0, 0.99)],
        {"005930": 1, "000660": 2},
    )
    assert 말 and "005930" in 말[0] and "정지 뒤 재개" in 말[0]
    assert "000660" not in 말[0]


def test_기업행위_확인이_실패해도_국내_수집은_끝난다() -> None:
    """docs/infra.md 25.246 — 시세를 저장한 뒤의 경고 한 줄이 리포트 전체를 막았다."""
    import ast
    from pathlib import Path

    본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "daily.py").read_text("utf-8")
    for n in ast.walk(ast.parse(본문)):
        if isinstance(n, ast.Try) and "_detect_kr_actions" in ast.unparse(n.body[0]):
            return
    raise AssertionError("_detect_kr_actions 부르는 자리가 try 로 감싸여 있지 않다")


def test_국내_시세_수집이_기업행위를_본다() -> None:
    """**부르는 곳이 없으면 헬퍼를 하나 더 만든 것일 뿐이다** (25.123)."""
    import ast
    from pathlib import Path

    본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "daily.py").read_text("utf-8")
    안에서 = [
        n
        for fn in ast.walk(ast.parse(본문))
        if isinstance(fn, ast.FunctionDef) and fn.name == "collect_kr_prices"
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_detect_kr_actions"
    ]
    assert 안에서, "collect_kr_prices 가 _detect_kr_actions 를 안 부른다"


class Test국면_줄은_금액을_줄인_날의_것이다:
    """**국면 줄이 아래 금액을 설명한다** (docs/infra.md 25.197).

    "약세 지수의 신규 매수 비중 ×0.50" 은 2부 금액이 그만큼 줄었다는 말이다. 금액은 신호 작업이
    신호 기준일의 국면으로 줄였다. 신호가 오늘 계산되지 않은 날 국면을 오늘로 다시 내면
    줄과 금액이 어긋난다.
    """

    def _client(self) -> SqliteClient:
        client = SqliteClient()
        seed(client)
        # 다음 거래일에 지수가 200일선 위로 올라섰다 — 오늘 기준이면 강세다
        client.conn.execute(
            "INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
            " VALUES ('KOSPI', '2026-09-17', 2700, 'y', 't')"
        )
        return client

    def test_미끼가_문다__두_날의_국면이_실제로_다르다(self) -> None:
        client = self._client()
        assert trend.regimes_for_country(client, "KR", AS_OF)["KOSPI"].state == "bear"  # type: ignore[arg-type]
        assert trend.regimes_for_country(client, "KR", "2026-09-17")["KOSPI"].state == "bull"  # type: ignore[arg-type]

    def test_신호가_묵었으면_신호_기준일의_국면을_적는다(self) -> None:
        regimes, line = daily.report_regimes(self._client(), "KR", AS_OF, "2026-09-17")  # type: ignore[arg-type]

        assert regimes["KOSPI"].state == "bear"
        assert "×0.50" in line, line

    def test_신호가_없으면_거래일로_낸다(self) -> None:
        regimes, _line = daily.report_regimes(self._client(), "KR", None, "2026-09-17")  # type: ignore[arg-type]

        assert regimes["KOSPI"].state == "bull"


class Test보유를_못_읽으면_말한다:
    """**아무 실패나 '보유 없음' 이 되어 2부 금액이 커졌다** (docs/infra.md 25.219)."""

    def test_표가_없으면_조용하다(self) -> None:
        from tests.test_report_picks import SqliteClient as C

        client = C()
        client.conn.execute("DROP TABLE positions")
        경고: list[str] = []
        assert daily.load_holdings(client, "KRW", None, 경고) == []  # type: ignore[arg-type]
        assert 경고 == []

    def test_다른_실패는_경고로_남긴다(self) -> None:
        client = SqliteClient()
        seed(client)

        def 막힘(sql, args=None):
            raise RuntimeError("D1 일일 읽기 한도 초과")

        client.execute = 막힘  # type: ignore[method-assign]
        경고: list[str] = []

        assert daily.load_holdings(client, "KRW", None, 경고) == []  # type: ignore[arg-type]
        assert len(경고) == 1 and "보유 없이" in 경고[0] and "한도" in 경고[0]

    def test_경고가_리포트_글보다_먼저_쌓인다(self) -> None:
        """글을 만든 뒤에 경고를 더하면 텔레그램에 안 실린다 — 읽는 순서를 소스로 고정한다."""
        import inspect

        본문 = inspect.getsource(daily.run)
        assert 본문.index("load_holdings(client, currency, fx_rate, warnings)") < 본문.index(
            "message = formatter.daily_report("
        )


class Test매도_플래그와_상관_시세도_말한다:
    """**리포트 경로에서 조용히 빠지던 두 가지** (docs/infra.md 25.220)."""

    @staticmethod
    def _막힌():
        client = SqliteClient()
        seed(client)

        def 막힘(sql, args=None):
            raise RuntimeError("D1 일일 읽기 한도 초과")

        client.execute = 막힘  # type: ignore[method-assign]
        return client

    def test_매도_플래그를_못_읽으면_말한다(self) -> None:
        경고: list[str] = []
        assert daily._sell_flags_for_report(self._막힌(), "KR", 경고) == []  # type: ignore[arg-type]
        assert len(경고) == 1 and "매도 플래그" in 경고[0]

    def test_상관_시세를_못_읽으면_말한다(self) -> None:
        경고: list[str] = []
        assert daily.load_price_series(self._막힌(), [1], AS_OF, 경고) == {}  # type: ignore[arg-type]
        assert len(경고) == 1 and "상관" in 경고[0]

    def test_셋_다_리포트_글보다_먼저_읽는다(self) -> None:
        import inspect

        본문 = inspect.getsource(daily.run)
        글 = 본문.index("message = formatter.daily_report(")
        for 읽기 in ("load_holdings(", "_sell_flags_for_report(", "load_price_series("):
            assert 본문.index(읽기) < 글, f"{읽기} 가 리포트 글 뒤에 있다 — 경고가 안 실린다"


def test_글을_만든_뒤에_생긴_경고도_텔레그램에_싣는다() -> None:
    """추천 이력·자기 채점·보유 점수·기업행위 일정·엇갈림 읽기 실패가 DB·웹에만 남았다 (docs/infra.md 25.1103, 감사).

    그 다섯은 2부·절을 만들려면 글 뒤에 읽어야 한다 — 그래서 늘어난 경고를 끝에 싣고, 그 덧붙임이 마지막 읽기 뒤·
    발송·저장 앞에 있음을 소스로 고정한다."""
    import inspect

    from batch.jobs import daily
    from batch.notify import formatter

    글 = formatter.late_warnings(["기업행위 일정을 읽지 못했습니다: 한도 초과"])
    assert 글.startswith("경고") and "  - 기업행위 일정을 읽지 못했습니다" in 글
    본문 = inspect.getsource(daily.run)
    덧붙임 = 본문.index("formatter.late_warnings(warnings[본문_경고수:])")
    for 읽기 in ("_pick_history(", "_self_grade(", "_holding_scores(", "_corp_event_lines(", "_divergence_lines("):
        assert 본문.index(읽기) < 덧붙임, f"{읽기} 가 경고 덧붙임 뒤에 있다"
    assert 덧붙임 < 본문.index("append_disclaimer(message)") and 덧붙임 < 본문.index("reports.store_report(")
    assert 본문.index("본문_경고수 = len(warnings)") < 본문.index("message = formatter.daily_report(")


def test_저장하는_본문에도_고지가_붙어_보낸_글과_같다(monkeypatch) -> None:
    """예전에는 고지 붙이기 전의 글을 저장해 화면 본문과 텔레그램이 한 줄 달랐다 (docs/infra.md 25.325)."""
    import inspect

    from batch import config
    from batch.jobs import daily
    from batch.notify import telegram

    monkeypatch.setattr(config, "TELEGRAM_DISCLAIMER", True)  # 켜면 이렇게 — 기본은 꺼짐(25.878)
    붙임 = telegram.append_disclaimer("본문")
    assert 붙임.endswith(config.DISCLAIMER) and telegram.append_disclaimer(붙임) == 붙임  # 두 번 붙지 않는다
    원본 = inspect.getsource(daily.run)
    assert 원본.index("append_disclaimer(message)") < 원본.index("reports.store_report(")


def test_평가_못_한_보유는_빼되_리포트에_말한다() -> None:
    """환율이 없어 미국 보유가 비면 국내 2부 여력이 부풀었는데 말이 없었다 (docs/infra.md 25.411)."""
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    client = MemClient()
    db.apply_migrations(client)  # type: ignore[arg-type]
    for sid, ticker, value in ((9, "035720", 3_000_000), (10, "AAPL", None)):
        client.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't')",
            [sid, ticker],
        )
        client.conn.execute(
            "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw,"
            " first_buy_date, market_value_krw, updated_at) VALUES (?, 10, 'KRW', 1, 1, 10, 10, '2026-01-02', ?, 't')",
            [sid, value],
        )
    경고: list[str] = []
    holdings = daily.load_holdings(client, "KRW", None, 경고)  # type: ignore[arg-type]
    assert [h.ticker for h in holdings] == ["035720"]
    assert len(경고) == 1 and "AAPL" in 경고[0] and "금액이 실제보다 클 수 있습니다" in 경고[0]


def test_환율이_없어_뺀_보유는_따로_말한다() -> None:
    """달러 리포트에 환율이 없으면 국내 보유까지 "평가하지 못한 보유 … 금액이 실제보다 클 수 있습니다" 로 나열됐다
    (docs/infra.md 25.426)."""
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    client = MemClient()
    db.apply_migrations(client)  # type: ignore[arg-type]
    client.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (9, '035720', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    client.conn.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw,"
        " first_buy_date, market_value_krw, updated_at) VALUES (9, 10, 'KRW', 1, 1, 10, 10, '2026-01-02', 3000000, 't')"
    )
    # 평가액이 빈 보유도 환율이 없는 날에는 "환율 없음" 으로 센다 (docs/infra.md 25.441)
    client.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (10, 'AAPL', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
    )
    client.conn.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw,"
        " first_buy_date, market_value_krw, updated_at) VALUES (10, 1, 'USD', 1, 1, 1, 1, '2026-01-02', NULL, 't')"
    )
    경고: list[str] = []
    assert daily.load_holdings(client, "USD", None, 경고) == []  # type: ignore[arg-type]
    assert 경고 == ["환율이 없어 보유 2종목을 달러 리포트의 여력 계산에 넣지 못했습니다"]


def test_묵은_신호로는_2부에서_금액을_배분하지_않는다() -> None:
    """신호 계산이 며칠 멈춰도 2부가 며칠 전 매수 구간으로 금액을 냈다 (docs/infra.md 25.820)."""
    from batch.jobs import daily
    from batch.services import report_picks as rp

    assert daily.signal_age_sessions("KR", "2026-09-30", "2026-10-01") == 1  # 어제 신호 — 배분한다
    assert daily.signal_age_sessions("KR", "2026-09-25", "2026-10-01") == 4
    assert daily.signal_age_sessions("KR", None, "2026-10-01") is None
    assert daily.STALE_SIGNAL_SESSIONS == 1

    client = SqliteClient()
    seed(client)
    rows, as_of = daily.load_signal_rows(client, "KR")  # type: ignore[arg-type]
    assert rows
    보통 = rp.compose(rows, 10_000_000, as_of)
    묵음 = rp.compose(rows, 10_000_000, as_of, no_allocation_reason="신호가 4거래일 전")
    assert 보통.portfolio is not None and 보통.portfolio.allocations
    assert 묵음.portfolio is None or not 묵음.portfolio.allocations
    assert "⚠️ 신호가 4거래일 전" in 묵음.text and 묵음.picks == 보통.picks  # 1부는 그대로
    # 종목마다 "사유 없음" 정합성 경고를 거짓으로 내지 않는다 — 2부에는 사유 한 줄 (25.822, 교차검증)
    assert "정합성 경고" not in 묵음.text and "배분할 종목이 없습니다" not in 묵음.text


def test_일일_배치가_묵은_신호_사유를_2부에_넘긴다() -> None:
    from pathlib import Path

    본문 = (Path(__file__).resolve().parent.parent / "batch" / "jobs" / "daily.py").read_text(encoding="utf-8")
    assert "no_allocation_reason=배분_안함" in 본문
    assert "묵은_거래일 > STALE_SIGNAL_SESSIONS" in 본문
