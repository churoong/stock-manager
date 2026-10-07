"""한국거래소 응답 파싱 테스트.

네트워크를 타지 않는다. 아래 원시 행은 2026-09-16 조사에서
실제 서버가 돌려준 응답 형태를 그대로 옮긴 것이다.
모든 값이 문자열이고 결측이 빈 문자열이라는 점이 핵심이다.
"""

from __future__ import annotations

import pytest

from batch.sources.krx import API, KrxDailyRow, _to_float, _to_int

# 실제 응답에서 옮긴 한 행
REAL_ROW = {
    "BAS_DD": "20200414",
    "ISU_CD": "338100",
    "ISU_NM": "NH프라임리츠",
    "MKT_NM": "KOSPI",
    "SECT_TP_NM": "",
    "TDD_CLSPRC": "4715",
    "CMPPREVDD_PRC": "25",
    "FLUC_RT": "0.53",
    "TDD_OPNPRC": "4655",
    "TDD_HGPRC": "4720",
    "TDD_LWPRC": "4655",
    "ACC_TRDVOL": "21363",
    "ACC_TRDVAL": "100332885",
    "MKTCAP": "87981900000",
    "LIST_SHRS": "18660000",
}


def test_문자열_숫자를_변환한다() -> None:
    assert _to_float("4715") == 4715.0
    assert _to_float("0.53") == 0.53
    assert _to_int("21363") == 21363


def test_쉼표가_들어간_숫자도_변환한다() -> None:
    assert _to_float("1,234,567") == 1234567.0
    assert _to_int("1,234,567") == 1234567


def test_결측은_None_이다() -> None:
    # 빈 문자열은 JSON 응답의 결측, 하이픈은 XML 응답의 결측이다
    for missing in ("", "   ", "-", None):
        assert _to_float(missing) is None
        assert _to_int(missing) is None


def test_숫자가_아니면_None_이고_예외를_내지_않는다() -> None:
    # 액면가에 "무액면" 같은 문자열이 들어오는 경우가 있다
    assert _to_float("무액면") is None
    assert _to_int("무액면") is None


def test_실제_응답_행을_파싱한다() -> None:
    row = KrxDailyRow.from_raw(REAL_ROW)

    assert row.bas_dd == "20200414"
    assert row.isu_cd == "338100"
    assert row.isu_nm == "NH프라임리츠"
    assert row.mkt_nm == "KOSPI"
    assert row.close == 4715.0
    assert row.change == 25.0
    assert row.change_pct == 0.53
    assert row.open == 4655.0
    assert row.high == 4720.0
    assert row.low == 4655.0
    assert row.volume == 21363
    assert row.value == 100_332_885
    assert row.market_cap == 87_981_900_000
    assert row.listed_shares == 18_660_000


def test_등락률이_응답값과_계산값이_맞는지_검산() -> None:
    row = KrxDailyRow.from_raw(REAL_ROW)

    # (4715 - 4690) / 4690 * 100 = 0.533...  거래소가 준 0.53 과 일치한다
    prev_close = row.close - row.change
    computed = (row.close - prev_close) / prev_close * 100
    assert computed == pytest.approx(row.change_pct, abs=0.01)


def test_필드가_없어도_무너지지_않는다() -> None:
    row = KrxDailyRow.from_raw({"ISU_CD": "005930"})

    assert row.isu_cd == "005930"
    assert row.close is None
    assert row.volume is None
    assert row.isu_nm == ""


def test_검증된_API만_등록돼_있다() -> None:
    # 추측한 엔드포인트를 넣지 않는다. 실제 호출로 확인한 것만 둔다.
    assert API["kospi_daily"] == ("sto", "stk_bydd_trd")
    assert API["kosdaq_daily"] == ("sto", "ksq_bydd_trd")
    assert API["kospi_master"] == ("sto", "stk_isu_base_info")
    assert API["kosdaq_master"] == ("sto", "ksq_isu_base_info")


def test_지원하지_않는_시장은_거부한다() -> None:
    from batch.sources.krx import fetch_daily

    result = fetch_daily("NASDAQ", "20260915")
    assert not result.ok
    assert "지원하지 않는 시장" in result.error
