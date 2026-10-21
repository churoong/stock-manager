"""35회차 곁가지 1 (docs/desk.md 6장, docs/infra.md 25.1624).

지키는 것: 관문 KIS 칸에 **경로 포함 실현율**(모름·거부를 못 찬 것으로 분모에 더한 최악 경계)을 참고로 싣는다 —
판정(`ok`)은 지금 실현율 그대로
"""

from __future__ import annotations

from batch.services import desk


def test_경로_포함_실현율은_참고_칸이다() -> None:
    k = desk.combine_kept([{"both": 18, "internal_only": 2, "unknown": 4, "rejected": 1},
                           {"both": 9, "internal_only": 1, "unknown": 0, "rejected": 0}])  # fmt: skip
    assert k["kept_pct"] == 27 / 30 * 100 and k["n"] == 30
    assert k["path_pct"] == 27 / 35 * 100 and k["path_n"] == 35
    assert desk.combine_kept([])["path_pct"] is None
