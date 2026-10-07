"""재무를 며칠에 걸쳐 이어 받기 (docs/infra.md 25.24).

**막으려는 것은 교착이다.** D1 하루 쓰기 한도(10만 행)에 잘린 재무가 다음 날 **처음부터 다시**
받으면, 같은 순서로 받다가 같은 자리에서 또 잘린다. 매일 같은 일을 하고 매일 같은 곳에서
멈춘다 — **영원히 끝나지 않는다.** 2026-09-20 에 재무를 따라잡기 맨 앞으로 옮기면서 이 위험이
생겼다(그 전에는 시세가 예산을 다 써서 재무가 아예 시작도 못 했다).

`--resume` 은 이미 들어온 회계연도·종목을 건너뛴다. 하루에 조금씩이라도 **앞으로 나간다.**

**기본값이 아닌 이유**: 정정 공시가 오면 덮어써야 한다(모듈 머리말). 주 1회 평소 실행은 전부
다시 받고, 따라잡기만 이어 받는다.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.jobs import financials as fin
from batch.sources import dart


class 가짜클라이언트:
    """`financials` 에 이미 들어온 종목만 흉내 낸다."""

    def __init__(self, 있는것: set[int] | None = None, 이어진수: int = 0, 터짐: bool = False) -> None:
        self.있는것 = 있는것 or set()
        self.이어진수 = 이어진수
        self.터짐 = 터짐
        self.질의: list[str] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> Any:
        self.질의.append(sql)
        if self.터짐:
            raise RuntimeError("D1 응답 없음")
        if "DISTINCT stock_id FROM financials" in sql:
            return _결과([(i,) for i in sorted(self.있는것)])
        if "COUNT(*) FROM stocks" in sql:
            return _결과([(self.이어진수,)])
        return _결과([])

    def batch(self, statements: list) -> list:
        return []


class _결과:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def scalar(self) -> Any:
        return self.rows[0][0] if self.rows else None


class Test이미_들어온_것_알아내기:
    def test_저장된_종목을_돌려준다(self) -> None:
        assert fin.already_collected(가짜클라이언트({3, 7}), 2025, "11011") == {3, 7}

    def test_못_읽으면_빈_집합이다(self) -> None:
        """모르면 받는 쪽이 안전하다. 빠뜨리는 것보다 다시 받는 편이 낫다."""
        assert fin.already_collected(가짜클라이언트(터짐=True), 2025, "11011") == set()

    def test_고유번호가_이어졌는지도_본다(self) -> None:
        assert fin.linked_count(가짜클라이언트(이어진수=873)) == 873
        assert fin.linked_count(가짜클라이언트(터짐=True)) == 0


class Test이어_받기:
    """`collect_period` 가 이미 있는 종목을 빼고 부르는지."""

    @staticmethod
    def _준비(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        보낸것: list[list[str]] = []

        def 가짜받기(chunk: list[str], year: int, report: str) -> Any:
            보낸것.append(list(chunk))
            return type("R", (), {"ok": True, "data": [], "source": "dart", "limit_state": "ok"})()

        monkeypatch.setattr(dart, "fetch_multi_financials", 가짜받기)
        monkeypatch.setattr(dart, "MAX_CORPS_PER_CALL", 100)
        monkeypatch.setattr(fin.db, "record_api_call", lambda *a, **k: {"state": "ok"})
        monkeypatch.setattr(fin, "CALL_INTERVAL_SECONDS", 0)
        return 보낸것

    def test_이미_있는_종목은_다시_받지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        보낸것 = self._준비(monkeypatch)
        corps = [(f"c{i}", i) for i in range(1, 11)]
        client = 가짜클라이언트(있는것={1, 2, 3, 4, 5})
        fin.collect_period(client, corps, 2025, "11011", resume=True)
        assert sorted(sum(보낸것, [])) == ["c10", "c6", "c7", "c8", "c9"]

    def test_전부_있으면_한_번도_부르지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DART 호출도 D1 쓰기도 0 이어야 한다. 이것이 '이어 받기' 의 값어치다."""
        보낸것 = self._준비(monkeypatch)
        corps = [(f"c{i}", i) for i in range(1, 6)]
        stored, warnings = fin.collect_period(
            가짜클라이언트(있는것={1, 2, 3, 4, 5}), corps, 2025, "11011", resume=True
        )
        assert 보낸것 == []
        assert (stored, warnings) == (0, [])

    def test_resume_가_아니면_전부_다시_받는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """평소 실행은 정정 공시를 반영해야 한다. 기본값이 바뀌면 그게 조용히 깨진다."""
        보낸것 = self._준비(monkeypatch)
        corps = [(f"c{i}", i) for i in range(1, 11)]
        fin.collect_period(가짜클라이언트(있는것={1, 2, 3, 4, 5}), corps, 2025, "11011")
        assert len(sum(보낸것, [])) == 10

    def test_잘려도_다음_날_앞으로_나간다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """교착을 재현한다. 하루에 3종목씩만 들어간다고 볼 때, 이어 받으면 사흘이면 끝난다."""
        corps = [(f"c{i}", i) for i in range(1, 10)]
        들어온것: set[int] = set()
        for _ in range(3):
            보낸것 = self._준비(monkeypatch)
            fin.collect_period(가짜클라이언트(있는것=set(들어온것)), corps, 2025, "11011", resume=True)
            받은 = sum(보낸것, [])
            assert 받은, "이어 받기가 멈춰 섰다 — 교착이다"
            들어온것 |= {int(code[1:]) for code in 받은[:3]}  # 하루에 3종목만 들어갔다고 본다
        assert 들어온것 == {1, 2, 3, 4, 5, 6, 7, 8, 9}


class Test한도:
    """docs/infra.md 25.318 — 한도에서 멈추지 않고, 마지막 호출분을 버리고, 020 을 못 알아봤다."""

    @staticmethod
    def _준비(monkeypatch: pytest.MonkeyPatch, 상태들: list[str], 결과: list[Any]) -> tuple[list, list]:
        보낸것: list[list[str]] = []
        저장: list[Any] = []
        결과_반복 = iter(결과)
        상태_반복 = iter(상태들)

        def 가짜받기(chunk: list[str], year: int, report: str) -> Any:
            보낸것.append(list(chunk))
            return next(결과_반복)

        monkeypatch.setattr(dart, "fetch_multi_financials", 가짜받기)
        monkeypatch.setattr(dart, "MAX_CORPS_PER_CALL", 1)
        monkeypatch.setattr(fin.db, "record_api_call", lambda *a, **k: {"state": next(상태_반복)})
        monkeypatch.setattr(fin, "_store", lambda _c, data, *_a: 저장.append(data) or len(data))
        monkeypatch.setattr(fin, "CALL_INTERVAL_SECONDS", 0)
        return 보낸것, 저장

    @staticmethod
    def _ok(data: list) -> Any:
        return type("R", (), {"ok": True, "data": data, "source": "dart", "limit_state": "ok", "error": None})()

    def test_한도에_닿은_호출의_데이터도_저장하고_멈춘다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        보낸것, 저장 = self._준비(monkeypatch, ["ok", "blocked"], [self._ok([1]), self._ok([2])])
        corps = [(f"c{i}", i) for i in range(1, 6)]
        stored, w = fin.collect_period(가짜클라이언트(), corps, 2025, "11011")
        assert 저장 == [[1], [2]] and stored == 2  # 두 번째(한도에 닿은) 호출분도 저장
        assert len(보낸것) == 2 and fin.LIMIT_REACHED in w

    def test_020_은_한도_표지를_남긴다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        막힘 = type("R", (), {"ok": False, "data": [], "source": "dart", "limit_state": "blocked",
                             "error": "요청 제한을 초과하였습니다"})()  # fmt: skip
        _보낸것, _ = self._준비(monkeypatch, ["ok"], [막힘])
        _stored, w = fin.collect_period(가짜클라이언트(), [("c1", 1), ("c2", 2)], 2025, "11011")
        assert fin.LIMIT_REACHED in w
