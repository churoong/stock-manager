"""장기 적립 종목 판정 테스트 (docs/accumulation.md). 합성 5개년 재무로 게이트 경계를 고정한다."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

from batch.core import db
from batch.jobs import accumulation as job
from batch.services import accumulation as acc

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 9, 17)
YEARS = acc.window(2025)


def good(stock_id: int = 1, **kw) -> acc.StockInput:
    """모든 게이트를 통과하는 종목. 테스트마다 한 가지만 어긋나게 한다."""
    inp = acc.StockInput(
        stock_id=stock_id, ticker=f"{stock_id:06d}", name=f"종목{stock_id}", market="KOSPI",
        listed_date="2000-01-04", market_cap=5e12, cap_rank=10,
    )
    for i, y in enumerate(YEARS):
        inp.years[y] = acc.FinYear(
            fiscal_year=y, report_date=f"{y + 1}-03-15", revenue=1_000e8 + i * 50e8,
            operating_income=100e8 + i * 5e8, net_income=80e8, total_equity=500e8,
            total_liabilities=250e8, retained_earnings=300e8 + i * 20e8,
        )
        inp.dividends[y] = acc.DivYear(fiscal_year=y, as_of_date=f"{y + 1}-03-15",
                                       cash_dividend_total=20e8, dps_common=1000, payout_ratio=25.0)
    for key, value in kw.items():
        setattr(inp, key, value)
    return inp


THRESHOLDS = {"roe_median": 0.10, "roe_n": 600, "years": YEARS}


def judge(inp: acc.StockInput) -> acc.Judgement:
    return acc.evaluate(inp, YEARS, THRESHOLDS, AS_OF)


class Test게이트:
    def test_모두_통과(self) -> None:
        j = judge(good())
        assert j.passed, j.excluded_reason
        gates = [row["label"].split()[0] for row in j.criteria if row["passed"] is not None]
        assert gates == ["G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10"]

    def test_G2_상장_10년_미만(self) -> None:
        assert judge(good(listed_date="2020-01-02")).first_failed_gate == "G2"

    def test_G3_한_해라도_없으면(self) -> None:
        inp = good()
        del inp.years[2022]
        assert judge(inp).first_failed_gate == "G3"

    def test_G4_매출_없는_해(self) -> None:
        inp = good()
        inp.years[2023].revenue = None
        assert judge(inp).first_failed_gate == "G4"

    def test_G5_영업적자_한_해(self) -> None:
        inp = good()
        inp.years[2021].operating_income = -1
        assert judge(inp).first_failed_gate == "G5"

    def test_G6_순손실_한_해(self) -> None:
        inp = good()
        inp.years[2024].net_income = -1
        assert judge(inp).first_failed_gate == "G6"

    def test_G7_부채비율_경계(self) -> None:
        inp = good()
        inp.years[2025].total_liabilities = 500e8  # 정확히 100% 는 통과
        assert judge(inp).passed
        inp.years[2025].total_liabilities = 501e8
        assert judge(inp).first_failed_gate == "G7"

    def test_G8_중앙값_미만(self) -> None:
        assert acc.evaluate(good(), YEARS, {**THRESHOLDS, "roe_median": 0.2}, AS_OF).first_failed_gate == "G8"

    def test_G9_400위_밖(self) -> None:
        assert judge(good(cap_rank=401)).first_failed_gate == "G9"

    def test_G10_무배당_한_해(self) -> None:
        inp = good()
        inp.dividends[2022].cash_dividend_total = None
        assert judge(inp).first_failed_gate == "G10"

    def test_G10_은_총액으로_본다_주당값_급감은_상관없다(self) -> None:
        # 액면분할로 주당배당금이 급감해도(삼성전자 2018) 총액이 있으면 통과
        inp = good()
        inp.dividends[2024].dps_common = 42_500
        inp.dividends[2025].dps_common = 850
        assert judge(inp).passed


class Test문턱과_순위:
    def test_중앙값은_연결_5개년_집단에서(self) -> None:
        a, b, c = good(1), good(2), good(3)
        for y in YEARS:
            b.years[y].net_income = 50e8  # ROE 10%
            c.years[y].net_income = 150e8  # ROE 30%
        incomplete = good(4)
        del incomplete.years[2021]
        t = acc.measure_thresholds([a, b, c, incomplete], YEARS)
        assert t["roe_n"] == 3
        assert abs(t["roe_median"] - 0.16) < 1e-9  # a 는 80/500 = 16%

    def test_안정성_순위(self) -> None:
        steady, shaky = good(1), good(2)
        for i, y in enumerate(YEARS):
            shaky.years[y].operating_income = 100e8 if i % 2 else 200e8  # 이익률이 들쭉날쭉
        judgements = [judge(steady), judge(shaky)]
        acc.rank(judgements)
        assert judgements[0].rank == 1 and judgements[1].rank == 2
        assert judgements[0].group_size == 2
        assert "안정성 1위" in acc.rationale_text(judgements[0])

class Test모르는_값을_가장_좋다고_하지_않는다:
    """**영업이익률 표준편차를 못 구한 종목이 1위가 됐다** (docs/infra.md 25.181).

    `margin_std` 는 **낮을수록 좋은** 값이라 0.0 은 "가장 안정적" 이라는 뜻이다.
    예전에는 못 구하면 0.0 으로 채워, 그 축에서 백분위 **100** 을 받았다.

    미국은 G4(매출 유무)를 안 보므로 매출 줄이 없는 해가 있는 회사가
    G1~G10 을 통과한 채로 순위에 들어온다.
    """

    def _매출_하나를_지운(self) -> acc.Judgement:
        inp = good_us(201)
        inp.years[2023].revenue = None
        return judge_us(inp)

    def test_통과는_하되_그_값은_모름이다(self) -> None:
        j = self._매출_하나를_지운()

        assert j.passed, "미국은 G4 를 안 보므로 통과한다 — 여기서 막는 고장이 아니다"
        assert j.metrics["margin_std"] is None, "못 구한 값을 0 으로 채웠다"

    def test_근거표가_모름이라고_말한다(self) -> None:
        j = self._매출_하나를_지운()

        줄 = next(r for r in j.criteria if r["label"] == "영업이익률 5년 표준편차")

        assert 줄["display"] == "모름"
        assert "순위에서 뺐습니다" in 줄["threshold"], "왜 안 썼는지 화면에서 볼 수 있어야 한다"

    def test_모르는_축에서_백분위를_안_받는다(self) -> None:
        모름 = self._매출_하나를_지운()
        성한것 = judge_us(good_us(202))

        acc.rank([모름, 성한것])

        assert "margin_std" not in 모름.component_scores, "모르는 축에 점수를 줬다"
        assert 모름.score is not None, "나머지 세 축으로는 줄 세울 수 있다"

    def test_모르는_값이_남의_순위를_흔들지_않는다(self) -> None:
        """**가짜 0.0 이 분포에 끼면 남의 백분위도 그것을 세고 만들어진다.**"""
        모름 = self._매출_하나를_지운()
        a, b = judge_us(good_us(203)), judge_us(good_us(204))
        b.metrics["margin_std"] = 0.20  # 아주 들쭉날쭉

        acc.rank([모름, a, b])

        assert a.component_scores["margin_std"] == 100.0, (
            "아는 둘 중 나은 쪽이 100 이어야 한다 — 모르는 종목이 분포에 끼면 안 된다"
        )
        assert b.component_scores["margin_std"] == 0.0

    def test_모르는_종목이_1위를_가로채지_않는다(self) -> None:
        모름 = self._매출_하나를_지운()
        성한것 = judge_us(good_us(205))
        성한것.metrics["margin_std"] = 0.0001  # 실제로 가장 안정적이다

        acc.rank([모름, 성한것])

        assert 성한것.rank == 1, "실제로 안정적인 쪽이 1위여야 한다"

    def test_참고_행은_판정에_쓰지_않는다(self) -> None:
        j = judge(good())
        info = [row for row in j.criteria if row["passed"] is None]
        assert {row["label"] for row in info} >= {"배당 (참고)", "5년 최저 ROE"}


class Test저장:
    def test_실제_스키마에_저장(self) -> None:
        j = judge(good())
        acc.rank([j])
        row = job.to_row(j, "2026-09-17", 2025, THRESHOLDS, True, "t")
        assert len(row) == db.column_count(job._COLS)
        conn = sqlite3.connect(":memory:")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (1, '000001', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        conn.execute(f"INSERT INTO stock_accum_picks ({job._COLS}) VALUES ({', '.join(['?'] * len(row))})", row)
        saved = conn.execute("SELECT passed, rank_in_group, long_signal_on, thresholds_json FROM stock_accum_picks").fetchone()
        assert saved[:3] == (1, 1, 1)
        assert json.loads(saved[3])["roe_n"] == 600


# ----------------------------------------------------------------------
# 미국 (docs/accumulation.md 7장)
# ----------------------------------------------------------------------


def good_us(stock_id: int = 101, **kw) -> acc.StockInput:
    inp = good(stock_id, listed_date=None, market="NASDAQ", cap_rank=450)
    inp.filing_years = set(range(2016, 2026))
    for key, value in kw.items():
        setattr(inp, key, value)
    return inp


def judge_us(inp: acc.StockInput) -> acc.Judgement:
    return acc.evaluate(inp, acc.years_for(inp, 2025, acc.US), THRESHOLDS, AS_OF, acc.US)


class Test미국:
    def test_상장일_없이도_10년_공시_이력으로_통과(self) -> None:
        j = judge_us(good_us())
        assert j.passed, j.excluded_reason
        g2 = next(r for r in j.criteria if r["label"].startswith("G2"))
        assert g2["label"] == "G2 공시 이력" and "10/10" in g2["display"]

    def test_G2_공시_한_해_빠지면(self) -> None:
        inp = good_us(filing_years=set(range(2016, 2026)) - {2018})
        assert judge_us(inp).first_failed_gate == "G2"

    def test_G4_는_참고_행_매출_없어도_G4_에서_안_떨어진다(self) -> None:
        inp = good_us()
        inp.years[2023].revenue = None
        j = judge_us(inp)
        assert j.first_failed_gate != "G4"
        g4 = next(r for r in j.criteria if r["label"].startswith("G4"))
        assert g4["passed"] is None

    def test_G5_영업이익_없으면_세전이익으로_대신(self) -> None:
        inp = good_us()
        for y in (2022, 2023):
            inp.years[y].operating_income = None
            inp.years[y].pretax_income = 90e8
        j = judge_us(inp)
        assert j.passed, j.excluded_reason
        g5 = next(r for r in j.criteria if r["label"].startswith("G5"))
        assert "2년은 세전이익으로 대신" in g5["display"]

    def test_G5_대리값도_적자면_탈락(self) -> None:
        inp = good_us()
        inp.years[2024].operating_income = None
        inp.years[2024].pretax_income = -1e8
        assert judge_us(inp).first_failed_gate == "G5"

    def test_국내는_세전이익_대리를_쓰지_않는다(self) -> None:
        inp = good()
        inp.years[2024].operating_income = None
        inp.years[2024].pretax_income = 90e8
        assert judge(inp).first_failed_gate == "G5"

    def test_G9_500위(self) -> None:
        assert judge_us(good_us(cap_rank=500)).passed
        assert judge_us(good_us(cap_rank=501)).first_failed_gate == "G9"

    def test_배당_참고행은_달러(self) -> None:
        inp = good_us()
        inp.dividends[2025].dps_common = 1.02
        info = next(r for r in judge_us(inp).criteria if r["label"] == "배당 (참고)")
        assert "$1.02" in info["display"]

    def test_창은_회사의_최신_회계연도로(self) -> None:
        # 6월 결산 회사가 FY2026 을 이미 냈으면 2022~2026
        inp = good_us(filing_years=set(range(2016, 2027)))
        assert acc.years_for(inp, 2025, acc.US) == [2022, 2023, 2024, 2025, 2026]
        # 공시가 끊겨 최신이 기준 연도보다 오래되면 기준 창(→ G3 탈락)
        stale = good_us(filing_years=set(range(2014, 2024)))
        assert acc.years_for(stale, 2025, acc.US) == YEARS
        # 국내는 늘 기준 창
        assert acc.years_for(good(), 2025, acc.KR) == YEARS


class Test주식종류_중복:
    def test_같은_10K_면_거래대금_큰_하나만(self) -> None:
        a, b, c = good_us(1, ticker="LEN"), good_us(2, ticker="LEN.B"), good_us(3, ticker="FAST")
        kept, dropped = acc.dedupe_share_classes(
            [a, b, c], {1: "0001-26-1", 2: "0001-26-1", 3: "0002-26-9"}, {1: 5e8, 2: 1e7, 3: 3e8}
        )
        assert [i.ticker for i in kept] == ["LEN", "FAST"]
        assert dropped == {2: "LEN"}

    def test_회사_키가_없으면_그대로(self) -> None:
        kept, dropped = acc.dedupe_share_classes([good_us(1), good_us(2)], {}, {})
        assert len(kept) == 2 and dropped == {}


def test_표시_이름_꼬리_떼기() -> None:
    assert acc.display_name("Acme Company - Common Stock") == "Acme Company"
    assert acc.display_name("Snap-On Incorporated Common Stock") == "Snap-On Incorporated"
    assert acc.display_name("삼성전자") == "삼성전자"
    # 2026-10-05 미국 리포트 (25.957)
    assert acc.display_name("Acme Technology Group Holding Ltd - Ordinary Shares") == "Acme Technology Group Holding Ltd"
    assert acc.display_name("RingCentral, Inc. Class A Common Stock") == "RingCentral, Inc."
    assert acc.display_name("Kennametal Inc. Common Stock") == "Kennametal Inc."


# ----------------------------------------------------------------------
# 지난 행 쓸어내기 (2026-09-22, docs/infra.md 25.107)
# ----------------------------------------------------------------------


class Test지난_행_쓸어내기:
    """`sweep_stale` — 이번에 판정 안 한 그 날 행을 지운다.

    예전에는 `NOT IN (?, ?, …)` 하나였다. 판정 종목 전부가 파라미터로 붙어 국내 879·
    미국 1,800 개가 됐고, **D1 은 질의당 100개까지다**. 월 1회 작업이라 D1 전환(9/18)
    뒤로 아직 한 번도 안 돌아 봤다 — 다음 예정이 10-06 이었다.
    """

    @staticmethod
    def _클라이언트(종목수: int):
        import sqlite3
        from typing import Any

        from batch.core import db as core_db
        from batch.core.turso import ResultSet

        class Sqlite:
            def __init__(self) -> None:
                self.conn = sqlite3.connect(":memory:")
                self.보낸것: list[tuple[str, list[Any]]] = []

            def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
                self.보낸것.append((sql, list(args or [])))
                cur = self.conn.execute(sql, args or [])
                cols = [d[0] for d in cur.description or []]
                return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

            def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
                return [self.execute(sql, args) for sql, args in statements]

            def close(self) -> None:
                pass

        client = Sqlite()
        core_db.apply_migrations(client)  # type: ignore[arg-type]
        for i in range(1, 종목수 + 1):
            client.conn.execute(
                "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't')",
                [i, f"{i:06d}"],
            )
        return client

    @staticmethod
    def _행(client, stock_ids, 날: str) -> None:
        for i in stock_ids:
            client.conn.execute(
                "INSERT INTO stock_accum_picks (stock_id, as_of_date, fiscal_year_to, passed,"
                " group_size, long_signal_on, thresholds_json, rationale_text, rationale_data,"
                " calc_version, created_at) VALUES (?, ?, 2025, 0, 1, 0, '{}', 't', '{}', ?, 't')",
                [i, 날, acc.CALC_VERSION],
            )

    def test_남길_것만_남기고_지운다(self) -> None:
        from batch.jobs import accumulation as job

        client = self._클라이언트(250)
        self._행(client, range(1, 251), "2026-09-22")

        지운수 = job.sweep_stale(client, "KR", "2026-09-22", set(range(1, 101)))

        남은것 = {r[0] for r in client.conn.execute("SELECT stock_id FROM stock_accum_picks").fetchall()}
        assert 지운수 == 150
        assert 남은것 == set(range(1, 101))

    def test_어떤_질의도_D1_한도를_안_넘는다(self, monkeypatch) -> None:
        """**이것이 이 절의 본론이다.** 옛 모양은 한 질의에 253개를 실었다.

        `db.in_chunk` 는 백엔드를 보고 답이 달라진다 — Turso 는 수천 개를 받아서 200을 준다.
        그러니 **D1 로 돌 때**를 흉내 내야 이 테스트가 뜻을 가진다. 실제로 지금 운영이 D1 이다.
        """
        from batch.core import client as backend
        from batch.core import d1
        from batch.jobs import accumulation as job

        monkeypatch.setattr(backend, "resolved_backend", lambda: backend.D1)

        client = self._클라이언트(250)
        self._행(client, range(1, 251), "2026-09-22")
        client.보낸것.clear()

        job.sweep_stale(client, "KR", "2026-09-22", set())

        실은수 = [len(args) for _sql, args in client.보낸것]
        assert 실은수, "질의를 하나도 안 보냈다"
        assert max(실은수) <= d1.MAX_PARAMS, f"한 질의가 한도를 넘었다: {실은수}"
        assert sum(1 for s, _ in client.보낸것 if s.strip().upper().startswith("DELETE")) >= 3, (
            "250개를 한 번에 보냈다면 나눈 것이 아니다"
        )

    def test_지울_것이_없으면_아무것도_안_쓴다(self) -> None:
        """대부분의 날이 이쪽이다. D1 하루 쓰기 예산을 아낀다."""
        from batch.jobs import accumulation as job

        client = self._클라이언트(5)
        self._행(client, range(1, 6), "2026-09-22")
        client.보낸것.clear()

        assert job.sweep_stale(client, "KR", "2026-09-22", set(range(1, 6))) == 0
        assert not [s for s, _ in client.보낸것 if s.strip().upper().startswith("DELETE")]

    def test_다른_날_다른_나라_행은_안_건드린다(self) -> None:
        from batch.jobs import accumulation as job

        client = self._클라이언트(5)
        client.conn.execute("UPDATE stocks SET country = 'US' WHERE id = 5")
        self._행(client, [1, 2], "2026-09-22")
        self._행(client, [3], "2026-09-21")
        self._행(client, [5], "2026-09-22")

        job.sweep_stale(client, "KR", "2026-09-22", set())

        남은것 = {
            (r[0], r[1]) for r in client.conn.execute("SELECT stock_id, as_of_date FROM stock_accum_picks").fetchall()
        }
        assert 남은것 == {(3, "2026-09-21"), (5, "2026-09-22")}


def test_G1_G9_근거의_기준일은_유니버스_스냅샷_날짜다() -> None:
    """docs/infra.md 25.261 — 실행일을 적어 유니버스 갱신이 멈춰도 근거표가 '오늘 기준' 이었다."""
    j = judge(good(snapshot_date="2026-09-01"))
    기준일 = {r["label"]: r["as_of"] for r in j.criteria}
    assert 기준일["G1 유니버스 편입"] == "2026-09-01"
    assert 기준일["G9 규모"] == "2026-09-01"
