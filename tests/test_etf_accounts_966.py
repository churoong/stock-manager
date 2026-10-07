"""장기 적립 ETF 의 계좌별 가능 여부와 우선순위 (docs/etf.md 11.1, docs/infra.md 25.966)."""

from __future__ import annotations

from batch.services import etf_accounts as ea


def 순위(country: str, bucket: str) -> dict[str, int | None]:
    return {k: v["priority"] for k, v in ea.accounts_for(country, bucket, satellite=False).items()}


class Test순위:
    def test_국내_상장_해외_주식은_연금_1순위_일반_3순위(self) -> None:
        assert 순위("KR", "해외 주식") == {"pension": 1, "irp": 1, "taxable": 3}

    def test_국내_주식은_일반계좌_1순위(self) -> None:
        # 매매차익 비과세 — 연금에 넣는 이득이 분배금뿐
        assert 순위("KR", "국내 주식") == {"pension": 3, "irp": 3, "taxable": 1}

    def test_채권은_연금_2순위(self) -> None:
        assert 순위("KR", "채권") == {"pension": 2, "irp": 2, "taxable": 3}

    def test_미국_상장은_연금에서_못_산다(self) -> None:
        a = ea.accounts_for("US", "미국 주식", satellite=False)
        assert a["pension"]["eligible"] is False and a["irp"]["eligible"] is False
        assert "국내 상장 ETF 만" in a["pension"]["reason"]
        assert a["taxable"] == {**a["taxable"], "eligible": True, "priority": ea.US_TAXABLE_PRIORITY}
        assert a["irp"]["note"] is None  # 살 수 없는 계좌에는 한도 안내를 달지 않는다


class Test근거:
    def test_세율은_설정값을_인용하고_없으면_미입력(self) -> None:
        a = ea.accounts_for("KR", "해외 주식", satellite=False, taxes={"kr_dividend_pct": 15.4})
        assert "설정 세율 15.4%" in a["taxable"]["reason"]
        b = ea.accounts_for("KR", "해외 주식", satellite=False)
        assert "설정 세율 미입력" in b["taxable"]["reason"]

    def test_IRP_위험자산_안내(self) -> None:
        assert f"{ea.IRP_RISK_LIMIT_PCT}% 이하" in ea.accounts_for("KR", "해외 주식", satellite=False)["irp"]["note"]
        assert "안전자산" in ea.accounts_for("KR", "채권", satellite=False)["irp"]["note"]

    def test_위성은_가능_여부만(self) -> None:
        a = ea.accounts_for("KR", "테마", satellite=True)
        assert all(a[k]["priority"] is None for k in ea.ACCOUNTS)
        assert a["pension"]["eligible"] and "위성" in a["pension"]["reason"]
        assert "위험자산" in a["irp"]["note"]
        assert ea.accounts_for("US", "업종", satellite=True)["pension"]["eligible"] is False
