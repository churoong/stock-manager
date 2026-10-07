"""시점 조회 테스트.

이 규칙이 틀리면 백테스트가 미래를 보고, 성적이 실제보다 좋게 나온다.
조용히 틀리는 종류의 오류라 경계를 촘촘히 막는다.

네트워크를 타지 않는다. 가짜 클라이언트로 SQL 결과를 흉내 낸다.
"""

from __future__ import annotations

import json

import pytest

from batch.services import pit

# 삼성전자의 실제 접수일로 상황을 만든다.
#   2024 사업보고서  2025-03-11 접수
#   2025 사업보고서  2026-03-10 접수
SNAPSHOTS = [
    {
        "as_of_date": "2025-03-11",
        "fiscal_year": 2024,
        "report_code": "11011",
        "consolidated": 1,
        "receipt_no": "20250311001085",
        "payload": json.dumps({"revenue": 300870903000000, "net_income": 34451351000000}),
    },
    {
        "as_of_date": "2026-03-10",
        "fiscal_year": 2025,
        "report_code": "11011",
        "consolidated": 1,
        "receipt_no": "20260310002820",
        "payload": json.dumps({"revenue": 333605938000000, "net_income": 45206805000000}),
    },
]


class FakeResultSet:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def dicts(self) -> list[dict]:
        return self._rows

    def scalar(self):
        if not self._rows:
            return None
        return next(iter(self._rows[0].values()))


class FakeClient:
    """as_of_date <= 기준일 조건을 실제 SQL 처럼 흉내 낸다."""

    def __init__(self, snapshots: list[dict]):
        self.snapshots = snapshots
        self.queries: list[tuple[str, list]] = []

    def execute(self, sql: str, args: list | None = None):
        args = args or []
        self.queries.append((sql, args))

        if "MAX(as_of_date)" in sql:
            dates = [s["as_of_date"] for s in self.snapshots]
            return FakeResultSet([{"m": max(dates)}] if dates else [{"m": None}])

        if "FROM financial_snapshots" in sql and "as_of_date <= ?" in sql:
            _stock_id, report_code, as_of = args[0], args[1], args[2]
            matched = [
                s for s in self.snapshots if s["report_code"] == report_code and s["as_of_date"] <= as_of
            ]
            matched.sort(key=lambda s: (s["fiscal_year"], s["as_of_date"]), reverse=True)
            return FakeResultSet(matched)

        return FakeResultSet([])


class Test미래차단:
    def test_공개되기_전에는_보이지_않는다(self) -> None:
        client = FakeClient(SNAPSHOTS)

        # 2025 사업보고서는 2026-03-10 에 접수됐다. 하루 전에는 몰랐다
        result = pit.financials_as_of(client, 1, "2026-03-09")

        assert result is not None
        assert result.fiscal_year == 2024
        assert result.values["revenue"] == 300870903000000

    def test_공개된_당일부터_보인다(self) -> None:
        client = FakeClient(SNAPSHOTS)
        result = pit.financials_as_of(client, 1, "2026-03-10")

        assert result is not None
        assert result.fiscal_year == 2025
        assert result.values["revenue"] == 333605938000000

    def test_한참_뒤에는_최신을_준다(self) -> None:
        client = FakeClient(SNAPSHOTS)
        result = pit.financials_as_of(client, 1, "2026-09-16")

        assert result is not None
        assert result.fiscal_year == 2025

    def test_아무것도_공개되기_전이면_없다(self) -> None:
        client = FakeClient(SNAPSHOTS)
        assert pit.financials_as_of(client, 1, "2020-01-01") is None

    def test_기준일_조건이_질의에_들어간다(self) -> None:
        client = FakeClient(SNAPSHOTS)
        pit.financials_as_of(client, 1, "2026-03-09")

        sql, args = client.queries[0]
        assert "as_of_date <= ?" in sql
        assert "2026-03-09" in args


class Test연결과별도:
    def test_연결이_없으면_별도로_물러난다(self) -> None:
        separate = [{**SNAPSHOTS[1], "consolidated": 0}]
        client = FakeClient(separate)

        result = pit.financials_as_of(client, 1, "2026-09-16", consolidated=True)

        assert result is not None
        assert result.consolidated is False

    def test_연결이_있으면_연결을_쓴다(self) -> None:
        both = [
            {**SNAPSHOTS[1], "consolidated": 0, "receipt_no": "별도"},
            SNAPSHOTS[1],
        ]
        client = FakeClient(both)

        result = pit.financials_as_of(client, 1, "2026-09-16", consolidated=True)

        assert result is not None
        assert result.consolidated is True

    def test_별도를_명시하면_연결로_넘어가지_않는다(self) -> None:
        client = FakeClient(SNAPSHOTS)  # 연결만 있다
        assert pit.financials_as_of(client, 1, "2026-09-16", consolidated=False) is None


class Test경계값:
    @pytest.mark.parametrize(
        ("as_of", "expected_year"),
        [
            ("2025-03-10", None),  # 2024 보고서 접수 하루 전
            ("2025-03-11", 2024),  # 접수 당일
            ("2026-03-09", 2024),  # 2025 보고서 접수 하루 전
            ("2026-03-10", 2025),  # 접수 당일
        ],
    )
    def test_접수일_전후로_답이_바뀐다(self, as_of: str, expected_year: int | None) -> None:
        client = FakeClient(SNAPSHOTS)
        result = pit.financials_as_of(client, 1, as_of)

        if expected_year is None:
            assert result is None
        else:
            assert result is not None
            assert result.fiscal_year == expected_year


class Test정정공시:
    def test_같은_회계연도라도_나중에_공개된_것을_쓴다(self) -> None:
        corrected = [
            SNAPSHOTS[1],
            {
                **SNAPSHOTS[1],
                "as_of_date": "2026-05-20",
                "receipt_no": "20260520000001",
                "payload": json.dumps({"revenue": 330000000000000}),
            },
        ]
        client = FakeClient(corrected)

        before = pit.financials_as_of(client, 1, "2026-05-19")
        after = pit.financials_as_of(client, 1, "2026-05-20")

        assert before is not None and before.values["revenue"] == 333605938000000
        assert after is not None and after.values["revenue"] == 330000000000000

    def test_정정_전_시점에는_정정_전_값이_보인다(self) -> None:
        # 정정이 났다고 과거 판단을 소급해 바꾸면 안 된다
        corrected = [
            SNAPSHOTS[1],
            {**SNAPSHOTS[1], "as_of_date": "2026-05-20", "receipt_no": "정정",
             "payload": json.dumps({"revenue": 1})},
        ]
        client = FakeClient(corrected)

        result = pit.financials_as_of(client, 1, "2026-04-01")
        assert result is not None
        assert result.values["revenue"] == 333605938000000


class Test방어선:
    def test_미래_데이터를_쓰려_하면_멈춘다(self) -> None:
        with pytest.raises(ValueError, match="미래 데이터"):
            pit.assert_no_lookahead(as_of="2026-03-09", snapshot_date="2026-03-10")

    def test_같은_날은_허용한다(self) -> None:
        pit.assert_no_lookahead(as_of="2026-03-10", snapshot_date="2026-03-10")

    def test_과거는_허용한다(self) -> None:
        pit.assert_no_lookahead(as_of="2026-09-16", snapshot_date="2025-03-11")


class Test깨진데이터:
    def test_payload_가_깨져도_무너지지_않는다(self) -> None:
        broken = [{**SNAPSHOTS[1], "payload": "이건 JSON 이 아니다"}]
        client = FakeClient(broken)

        result = pit.financials_as_of(client, 1, "2026-09-16")

        assert result is not None
        assert result.values == {}


def test_옛_연도_정정이_최신_연도를_덮지_않는다() -> None:
    """공개일 먼저 정렬해 2026-05 에 정정된 FY2023 이 FY2025 를 덮었다 (docs/infra.md 25.553, 교차검증)."""
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    c.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, '000001', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    for 공개일, 연도, 번호 in (("2026-03-12", 2025, "a"), ("2026-05-20", 2023, "b")):
        c.conn.execute(
            "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
            " consolidated, payload, source, fetched_at) VALUES (1, ?, ?, ?, '11011', 1, '{}', 't', 't')",
            [공개일, 번호, 연도],
        )
    결과 = pit.financials_as_of(c, 1, "2026-06-01")  # type: ignore[arg-type]
    assert 결과 is not None and 결과.fiscal_year == 2025


class Test연간만_백테스트와_같은_기준으로:
    """9회차 검증에서 찾은 결함 (docs/infra.md 25.892)."""

    def test_다음_해_1분기_보고서를_최신_재무로_돌려주지_않는다(self) -> None:
        분기 = {**SNAPSHOTS[1], "fiscal_year": 2026, "report_code": "11013", "as_of_date": "2026-05-15",
                "receipt_no": "1분기", "payload": json.dumps({"revenue": 1})}  # fmt: skip
        result = pit.financials_as_of(FakeClient([*SNAPSHOTS, 분기]), 1, "2026-05-20")
        assert result is not None and result.fiscal_year == 2025 and result.report_code == "11011"

    def test_연결을_끊은_회사는_그때의_기준을_쓴다(self) -> None:
        """가장 늦은 사업연도가 별도뿐이면 별도 — 예전 규칙은 옛 해에 연결이 있으면 옛 연결을 돌려줬다."""
        끊음 = [SNAPSHOTS[0], {**SNAPSHOTS[1], "consolidated": 0, "receipt_no": "별도2025"}]
        result = pit.financials_as_of(FakeClient(끊음), 1, "2026-09-16")
        assert result is not None and result.fiscal_year == 2025 and result.consolidated is False
