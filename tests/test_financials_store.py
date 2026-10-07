"""재무 적재 테스트.

열 개수와 값 개수가 어긋나 배치가 죽은 적이 있다.
"24 values for 26 columns" 로 실패했고, 원인은 열 개수를 손으로 세어
넘기게 둔 것이었다. 열을 하나 더할 때 같이 고치는 것을 잊으면 또 난다.

여기서는 열 목록과 실제로 만드는 값 묶음이 맞는지 본다.
네트워크를 타지 않는다.
"""

from __future__ import annotations

import json

import pytest

from batch.jobs import financials as fin
from batch.sources import dart


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[list[tuple[str, list]]] = []

    def batch(self, statements):
        self.calls.append(statements)
        return []


def sample_item(consolidated: bool = True) -> dart.CompanyFinancials:
    return dart.CompanyFinancials(
        corp_code="00126380",
        stock_code="005930",
        fiscal_year=2025,
        report_code="11011",
        consolidated=consolidated,
        receipt_no="20260310002820",
        report_date="2026-03-10",
        currency="KRW",
        values={name: 1000 for name in dart.ACCOUNT_MAP},
    )


class Test열개수:
    def test_열_목록에서_개수를_센다(self) -> None:
        assert fin.column_count("a, b, c") == 3
        assert fin.column_count("a,b,  c ,d") == 4

    def test_빈_항목은_세지_않는다(self) -> None:
        assert fin.column_count("a, b, ") == 2

    def test_financials_열_개수(self) -> None:
        # 실제로 26개다. 이 값이 바뀌면 값 묶음도 함께 바뀌어야 한다
        assert fin.column_count(fin._FIN_COLS) == 26

    def test_snapshots_열_개수(self) -> None:
        assert fin.column_count(fin._SNAP_COLS) == 9


class Test값과열이맞는가:
    """실제로 만드는 값 묶음이 열 개수와 맞는지 본다.

    이 테스트가 없어서 배치가 운영에서 죽었다.
    """

    def test_두_표_모두_정상_적재된다(self) -> None:
        client = FakeClient()
        stored = fin._store(client, [sample_item()], {"00126380": 42}, "dart_opendart")

        assert stored == 1
        assert len(client.calls) == 2  # financials, financial_snapshots

    def test_financials_행의_값_개수가_열과_같다(self) -> None:
        client = FakeClient()
        fin._store(client, [sample_item()], {"00126380": 42}, "dart_opendart")

        _sql, args = client.calls[0][0]
        assert len(args) == fin.column_count(fin._FIN_COLS)

    def test_snapshots_행의_값_개수가_열과_같다(self) -> None:
        client = FakeClient()
        fin._store(client, [sample_item()], {"00126380": 42}, "dart_opendart")

        _sql, args = client.calls[1][0]
        assert len(args) == fin.column_count(fin._SNAP_COLS)

    def test_어긋나면_바로_실패한다(self) -> None:
        # 조용히 틀린 값을 넣느니 실패하는 편이 낫다
        client = FakeClient()
        with pytest.raises(ValueError, match="열은"):
            fin._bulk(client, "financials", fin._FIN_COLS, "", [(1, 2, 3)])

    def test_실패_메시지가_무엇을_고쳐야_하는지_알려준다(self) -> None:
        client = FakeClient()
        with pytest.raises(ValueError) as exc:
            fin._bulk(client, "financials", fin._FIN_COLS, "", [(1, 2, 3)])

        assert "3개" in str(exc.value)
        assert "26개" in str(exc.value)


class Test저장내용:
    def test_연결_여부를_정수로_넣는다(self) -> None:
        client = FakeClient()
        fin._store(client, [sample_item(consolidated=True)], {"00126380": 1}, "x")

        _sql, args = client.calls[0][0]
        # 다섯 번째 값이 consolidated 다
        assert args[4] == 1

    def test_별도는_0이다(self) -> None:
        client = FakeClient()
        fin._store(client, [sample_item(consolidated=False)], {"00126380": 1}, "x")

        _sql, args = client.calls[0][0]
        assert args[4] == 0

    def test_스냅샷에_접수일이_들어간다(self) -> None:
        client = FakeClient()
        fin._store(client, [sample_item()], {"00126380": 1}, "x")

        _sql, args = client.calls[1][0]
        # 두 번째 값이 as_of_date 다. 시점 조회의 기준이다
        assert args[1] == "2026-03-10"

    def test_스냅샷_payload_는_JSON_이다(self) -> None:
        client = FakeClient()
        fin._store(client, [sample_item()], {"00126380": 1}, "x")

        _sql, args = client.calls[1][0]
        payload = json.loads(args[6])
        assert payload["revenue"] == 1000

    def test_마스터에_없는_회사는_건너뛴다(self) -> None:
        client = FakeClient()
        stored = fin._store(client, [sample_item()], {}, "x")

        assert stored == 0


class Test덮어쓰기정책:
    def test_financials_는_덮어쓴다(self) -> None:
        # 정정 공시가 오면 최신으로 갱신한다. 화면과 팩터가 이걸 본다
        assert "DO UPDATE SET" in fin._FIN_SQL

    def test_snapshots_는_덮어쓰지_않는다(self) -> None:
        # 과거에 받은 값을 지금 값으로 바꾸면 시점 조회의 뜻이 사라진다
        assert "DO NOTHING" in fin._SNAP_SQL
        assert "DO UPDATE" not in fin._SNAP_SQL


class Test통화와단위:
    """단위는 통화를 따르고 스냅샷에도 통화·단위·기준·기간이 실린다 (docs/infra.md 25.729)."""

    @staticmethod
    def _item(currency: str) -> dart.CompanyFinancials:
        item = sample_item()
        item.currency = currency
        return item

    def test_원화는_단위가_원(self) -> None:
        client = FakeClient()
        fin._store(client, [self._item("KRW")], {"00126380": 1}, "x")
        args = client.calls[0][0][1]
        cols = [c.strip() for c in fin._FIN_COLS.split(",") if c.strip()]
        assert args[cols.index("unit")] == "원"

    def test_달러로_보고하면_단위도_달러(self) -> None:
        client = FakeClient()
        fin._store(client, [self._item("USD")], {"00126380": 1}, "x")
        args = client.calls[0][0][1]
        cols = [c.strip() for c in fin._FIN_COLS.split(",") if c.strip()]
        assert args[cols.index("currency")] == "USD"
        assert args[cols.index("unit")] == "USD"

    def test_스냅샷_payload_에_통화와_단위가_있다(self) -> None:
        client = FakeClient()
        fin._store(client, [self._item("USD")], {"00126380": 1}, "x")
        args = client.calls[1][0][1]
        cols = [c.strip() for c in fin._SNAP_COLS.split(",") if c.strip()]
        payload = json.loads(args[cols.index("payload")])
        assert payload["currency"] == "USD"
        assert payload["unit"] == "USD"
        assert payload["accounting_standard"] == "K-IFRS"
        assert payload["period_type"] == "A"
        assert payload["current_assets"] == 1000

    def test_다시_받으면_통화와_출처도_갱신한다(self) -> None:
        for col in ("currency", "unit", "accounting_standard", "period_type", "source"):
            assert f"{col} = excluded.{col}" in fin._FIN_SQL
