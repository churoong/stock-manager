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
    def test_세율_숫자는_인용하지_않는다(self) -> None:
        """사유는 월 1회 잡이 써서 세율을 인용하면 한 달 묵는다 — 지금 세율은 세후 적립 카드가 보인다 (25.1301)."""
        a = ea.accounts_for("KR", "해외 주식", satellite=False, taxes={"kr_dividend_pct": 15.4})
        assert "15.4" not in a["taxable"]["reason"] and "세후 적립 카드" in a["taxable"]["reason"]
        assert ea.accounts_for("KR", "해외 주식", satellite=False)["taxable"]["reason"] == a["taxable"]["reason"]

    def test_IRP_위험자산_안내(self) -> None:
        assert f"{ea.IRP_RISK_LIMIT_PCT}% 이하" in ea.accounts_for("KR", "해외 주식", satellite=False)["irp"]["note"]
        assert "안전자산" in ea.accounts_for("KR", "채권", satellite=False)["irp"]["note"]

    def test_위성은_가능_여부만(self) -> None:
        a = ea.accounts_for("KR", "테마", satellite=True)
        assert all(a[k]["priority"] is None for k in ea.ACCOUNTS)
        assert a["pension"]["eligible"] and "위성" in a["pension"]["reason"]
        assert "위험자산" in a["irp"]["note"]
        assert ea.accounts_for("US", "업종", satellite=True)["pension"]["eligible"] is False


def test_제도_금액은_상수에서() -> None:
    """250만원·2,000만원을 글자로 박지 않는다 (25.1301 — 정의처 `portfolio.US_CGT_DEDUCTION_KRW`·`tax_sim`)."""
    from batch.services import tax_sim as ts

    us = ea._reason("US", "미국 주식", "taxable", {})
    kr = ea._reason("KR", "해외 주식", "taxable", {})
    assert f"{ts.US_GAIN_DEDUCTION_KRW // 10_000}만원 기본공제" in us
    assert f"{ts.FIN_INCOME_COMPREHENSIVE_KRW // 10_000:,}만원 초과" in kr
    # 범위 밖·숫자 아닌 세율도 더는 인용하지 않는다 (25.1130 이 막던 것)
    assert "60" not in ea._reason("KR", "해외 주식", "taxable", {"kr_dividend_pct": 60})


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


def test_설정_화면의_공제_글도_상수와_같다() -> None:
    """웹은 파이썬 상수를 못 읽는다 — 설정 화면의 "기본공제 N만원" 이 정의처와 어긋나지 않게 대조한다 (25.1301)."""
    from pathlib import Path

    from batch.services.portfolio import US_CGT_DEDUCTION_KRW

    글 = (Path(__file__).resolve().parents[1] / "web/components/SettingsForm.tsx").read_text(encoding="utf-8")
    assert f"기본공제 {US_CGT_DEDUCTION_KRW // 10_000}만원" in 글
