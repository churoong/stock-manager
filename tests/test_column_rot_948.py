"""조용히 썩는 열 감시 (docs/health.md 7장, docs/infra.md 25.948) — 행은 들어오는데 값이 비어 가거나 멈추는 고장."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from batch.jobs import weekly_summary as ws
from batch.services import column_rot as cr

ROOT = Path(__file__).resolve().parent.parent


def cc(n7=700, null7=0, d7=650, n90=9000, null90=0, d90=8500, table="prices", column="close") -> cr.ColumnCounts:
    return cr.ColumnCounts(table, column, n7, null7, d7, n90, null90, d90)


class Test판정:
    def test_빈_값이_기준보다_20퍼센트포인트_넘게_늘면_비어_간다(self) -> None:
        # 미국 거래대금처럼 **원래 절반이 빈** 열은 조용하다. 절반 → 80% 로 늘면 말한다
        assert cr.judge([cc(null7=350, null90=4500, column="value")]) == []
        [f] = cr.judge([cc(null7=560, null90=4500, column="value")])
        assert f.kind == "null" and "prices.value" in f.text and "50% → 80%" in f.text

    def test_같은_값이_되풀이되면_멈췄다(self) -> None:
        # 기준은 비지 않은 행의 89% 가 서로 다른 값인데 최근은 20% — 종가가 굳었다
        [f] = cr.judge([cc(d7=140, d90=8000)])
        assert f.kind == "frozen" and "0.89 → 0.20" in f.text
        assert cr.judge([cc(d7=500, d90=8000)]) == []  # 0.71 — 절반 아래가 아니다

    def test_표본이_적으면_판단하지_않는다(self) -> None:
        # 최근 행이 50 아래 — 종목 몇 개의 우연. 빈 값도 고유값도 판단하지 않는다
        assert cr.judge([cc(n7=40, null7=0, d7=2)]) == []
        assert cr.judge([cc(n7=40, null7=40, d7=0)]) == []
        # 25.976 — 예전 주석 "다 비어도 말하지 않는다(그건 신선도의 일)" 은 틀렸다: 신선도는 행이 들어오는지만 보고
        # 열이 빈지는
        # 보지 않는다. 최근 60행 중 30행 빈 값(기준 1%)은 이제 "비어 갑니다" 다
        [f] = cr.judge([cc(n7=60, null7=30, d7=5, n90=9000)])
        assert f.kind == "null"

    def test_견줄_과거가_없으면_조용하다(self) -> None:
        assert cr.judge([cc(n90=0, null90=0, d90=0, null7=600)]) == []

    def test_최근_행이_없으면_표마다_한_줄(self) -> None:
        out = cr.judge([cc(n7=0, null7=0, d7=0), cc(n7=0, null7=0, d7=0, column="volume")])
        assert len(out) == 1 and out[0].kind == "empty" and "prices: 최근 7일 행 없음" in out[0].text

    def test_질의_결과_한_행에서_열마다_수를_꺼낸다(self) -> None:
        row = {"n7": 10, "n90": 100, "rate_null7": 1, "rate_null90": 2, "rate_d7": 9, "rate_d90": 90}
        [c] = cr.from_row("fx_rates", ("rate",), row)
        assert (c.n7, c.null7, c.distinct7, c.n90, c.null90, c.distinct90) == (10, 1, 9, 100, 2, 90)
        # 빈 결과(표가 비어 있음)는 전부 0 — 터지지 않는다
        assert cr.from_row("fx_rates", ("rate",), {})[0].n7 == 0


class Test질의:
    def test_감시_목록의_표와_열마다_질의가_그_이름으로_센다(self) -> None:
        """질의는 글자 그대로라(SQL 그물 때문) 목록과 어긋날 수 있다 — 열마다 null7·null90·d7·d90 네 별칭이 있어야 한다."""
        for table, date_col, columns in cr.WATCHED:
            sql = cr.QUERIES[table]
            assert f"FROM {table} WHERE {date_col} >= ?" in sql
            assert sql.count("?") == 2
            for c in columns:
                for 꼬리 in ("null7", "null90", "d7", "d90"):
                    assert f"AS {c}_{꼬리}" in sql, (table, c, 꼬리)
        assert set(cr.QUERIES) == {t for t, _, _ in cr.WATCHED}

    def test_감시하는_열이_마이그레이션에_있다(self) -> None:
        sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "migrations").glob("*.sql")))
        for table, date_col, columns in cr.WATCHED:
            m = re.search(rf"CREATE TABLE (?:IF NOT EXISTS )?{table}\s*\((.*?)\n\);", sql, re.S)
            assert m, table
            body = m.group(1)
            for c in (date_col, *columns):
                assert re.search(rf"^\s+{c}\s", body, re.M), (table, c)

    def test_한_표가_실패해도_나머지는_본다(self) -> None:
        class Client:
            def execute(self, sql, params):
                if "FROM prices" in sql:
                    raise RuntimeError("timeout")

                class R:
                    def dicts(self_inner):
                        return [{"n7": 100, "n90": 1000}]

                return R()

        findings, checked, errors = cr.scan(Client(), "2026-09-27", "2026-06-29")
        assert findings == [] and checked == 7 and errors == ["prices: timeout"]  # 11열 가운데 prices 4열을 못 봤다


class Test글:
    def test_이상이_없어도_점검한_줄은_남는다(self) -> None:
        assert cr.render([], 11, 8) == ["데이터 열 점검 (최근 7일 vs 그 전 90일, 11열) — 비어 가거나 멈춘 열 없음"]

    def test_여덟_줄까지만(self) -> None:
        fs = [cr.Finding("t", f"c{i}", "null", f"t.c{i}: 빈 값") for i in range(10)]
        out = cr.render(fs, 12, 8)
        assert len(out) == 10 and out[-1] == "  … 외 2"

    def test_주간_요약이_절로_싣는다(self) -> None:
        지금 = datetime(2026, 10, 4, 22, 41, tzinfo=UTC)
        글 = ws.compose([], {}, 지금, 1, rot_lines=cr.render([cr.Finding("prices", "value", "null", "prices.value: 빈 값 48% → 100%")], 12, 8))
        assert "데이터 열 점검" in 글 and "  prices.value: 빈 값 48% → 100%" in 글
        assert "데이터 열 점검" not in ws.compose([], {}, 지금, 1)  # 안 넘기면 전과 같다


class Test통째로_빈_열:
    """25.976 — 최근 7일 열이 100% 빈 값이면 비지 않은 행이 0개라 "표본 부족" 으로 빠졌다. 60% 는 잡고 100% 는 놓쳤다."""

    def test_통째로_비면_잡는다(self) -> None:
        [f] = cr.judge([cr.ColumnCounts("scores", "x", 500, 500, 0, 9000, 90, 800)])
        assert f.kind == "null" and "100%" in f.text

    def test_최근_행이_적으면_빈_값도_판단하지_않는다(self) -> None:
        assert cr.judge([cr.ColumnCounts("scores", "x", 10, 10, 0, 9000, 90, 800)]) == []

    def test_고유값은_비지_않은_행이_적으면_판단하지_않는다(self) -> None:
        # 빈 값은 늘지 않았고(기준도 높다) 비지 않은 행이 30개 — 고유값 비율로 판단하지 않는다
        assert cr.judge([cr.ColumnCounts("scores", "x", 500, 470, 1, 9000, 8400, 300)]) == []
