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


def test_근거의_세율은_범위를_거친다() -> None:
    """범위 밖·숫자 아닌 세율을 그대로 인용했다 (docs/infra.md 25.1130)."""
    assert "설정 세율 15.4%" in ea._reason("KR", "해외 주식", "taxable", {"kr_dividend_pct": 15.4})
    assert "설정 세율 범위 밖" in ea._reason("KR", "해외 주식", "taxable", {"kr_dividend_pct": 60})
    assert "설정 세율 범위 밖" in ea._reason("US", "미국 주식", "taxable", {"us_capital_gains_pct": True})
    assert "설정 세율 미입력" in ea._reason("US", "미국 주식", "taxable", {})


def test_연금_2순위_채권은_가장_크다고_하지_않는다() -> None:
    """해외 주식(1순위)과 채권(2순위)이 같은 "가장 큽니다" 를 말했다 (docs/infra.md 25.1239, ETF 감사)."""
    from batch.services import etf_accounts as ea

    채권 = ea.accounts_for("KR", "채권", satellite=False)["pension"]
    해외 = ea.accounts_for("KR", "해외 주식", satellite=False)["pension"]
    assert 채권["priority"] == 2 and "가장" not in 채권["reason"] and "해외 주식 다음" in 채권["reason"]
    assert 해외["priority"] == 1 and "가장 큽니다" in 해외["reason"]


def test_미국_상장_2순위의_까닭은_구조와_단점이다() -> None:
    """예전 근거는 미국 상장도 22% 과세임을 빠뜨렸다 — 앞서는 까닭(분리과세·해마다 공제·손익통산)과 단점(환전·상속세)을 함께
    적는다 (docs/infra.md 25.1240, 교차검증 판정: 순서 유지 + 문구)."""
    from batch.services import etf_accounts as ea

    미국 = ea.accounts_for("US", "해외 주식", satellite=False)["taxable"]
    국내해외 = ea.accounts_for("KR", "해외 주식", satellite=False)["taxable"]
    assert 미국["priority"] == 2 and 국내해외["priority"] == 3
    for 말 in ("분리과세", "250만원", "손익통산", "환전", "상속세"):
        assert 말 in 미국["reason"]
    assert "종합과세" in 국내해외["reason"] and "나눠" in 국내해외["reason"]
