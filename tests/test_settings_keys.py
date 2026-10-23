"""설정 키를 **문서와 코드가 같게** (docs/infra.md 25.62).

설정의 단일 정의처는 `web/lib/settings.ts` 의 `settingsSchema` 다(그 파일 머리말:
"설정은 웹앱만 쓴다. 배치는 읽기만 한다. 따라서 검증은 여기 한 곳에 둔다"). 그런데
`docs/design.md` 3.5절도 키를 **나열**한다 — 설계를 읽고 무엇을 바꿀 수 있는지 아는 자리다.

2026-09-21 에 재어 보니 둘이 어긋나 있었다.

- 문서에만 있던 것 셋: `telegram_chat_id`(실은 `.env`), `universe_filters`(안 만듦),
  `sentiment_method`(고정이라 뺐음)
- 스키마에만 있던 것 하나: `trend_filter`(2026-09-17 에 넣고 문서를 안 고쳤다)

**왜 이것이 문제인가.** 설계서를 읽은 사람은 "유니버스 필터를 화면에서 바꾼다" 고 믿는다.
없는 기능을 찾느라 시간을 쓰고, 없다는 것을 알면 설계서 전체를 덜 믿게 된다.
반대로 `trend_filter` 처럼 **있는데 안 적힌 것**은 아무도 모르고 기본값으로 방치된다.
"""

from __future__ import annotations

import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent
스키마파일 = 뿌리 / "web" / "lib" / "settings.ts"
설계 = 뿌리 / "docs" / "design.md"


def 스키마키() -> list[str]:
    """`settingsSchema` 의 **맨 윗단** 키. 중첩 키는 더 들여써 있어 걸리지 않는다."""
    글 = 스키마파일.read_text(encoding="utf-8")
    시작 = 글.index("export const settingsSchema")
    몸통 = 글[시작 : 글.index("});", 시작)]
    return re.findall(r"^  ([a-z_]+):", 몸통, re.M)


def 문서키() -> list[str]:
    """3.5절 `키 목록:` 줄들에 백틱으로 적힌 이름."""
    글 = 설계.read_text(encoding="utf-8")
    뒤 = 글.split("키 목록: ", 1)[1]
    # 빈 줄이 나올 때까지가 목록이다 (여러 줄로 접어 써도 된다)
    목록 = 뒤.split("\n\n", 1)[0]
    return re.findall(r"`([a-z_]+)`", 목록)


def test_스키마를_읽어_냈다() -> None:
    """정규식이 빗나가 0개가 나오면 아래 대조가 통째로 헛돈다."""
    키 = 스키마키()

    assert len(키) >= 14, f"맨 윗단 키를 {len(키)}개밖에 못 읽었다 — 들여쓰기가 바뀌었나"
    assert "total_investable_amount" in 키 and "quiet_hours" in 키


def test_문서를_읽어_냈다() -> None:
    키 = 문서키()

    assert len(키) >= 14, f"설계서에서 {len(키)}개밖에 못 읽었다 — 3.5절 모양이 바뀌었나"


def test_설계서와_스키마가_같은_키를_말한다() -> None:
    스키마, 문서 = set(스키마키()), set(문서키())

    assert 문서 - 스키마 == set(), (
        f"설계서에만 있는 키: {sorted(문서 - 스키마)}\n"
        "없는 설정을 적어 두면 읽는 사람이 없는 기능을 찾는다."
        " 만들 생각이면 '설정이 아닌 것' 표로 옮기고 왜인지 적어라"
    )
    assert 스키마 - 문서 == set(), (
        f"스키마에만 있는 키: {sorted(스키마 - 문서)}\n"
        "설정을 더했으면 docs/design.md 3.5절에도 적어라. 안 적힌 설정은 아무도 안 바꾼다"
    )


def test_설정이_아닌_것을_설정처럼_적지_않았다() -> None:
    """뺀 키들이 **왜 뺐는지와 함께** 남아 있어야 한다. 그냥 지우면 다음 사람이 다시 적는다."""
    글 = 설계.read_text(encoding="utf-8")
    # 다음 절(`**…` 굵은 줄이나 `###` 제목)이 나오기 전까지가 이 설명이다
    뒤 = 글.split("**설정이 아닌 것**", 1)[1]
    표 = re.split(r"\n(?:###|\*\*)", 뒤, maxsplit=1)[0]

    for 이름 in ("telegram_chat_id", "universe_filters", "sentiment_method"):
        assert f"`{이름}`" in 표, f"{이름} 을 왜 설정으로 두지 않았는지가 사라졌다"


def test_배치는_설정을_읽기만_한다() -> None:
    """`settings` 에 쓰는 것은 웹과 초기값뿐이다. 배치가 사용자 설정을 덮으면 안 된다.

    (`batch/core/db.py` 의 기본값 넣기는 예외다 — 없을 때 한 번 만든다)
    """
    쓰는곳 = []
    for 길 in sorted((뿌리 / "batch").rglob("*.py")):
        글 = 길.read_text(encoding="utf-8")
        if re.search(r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+settings\b", 글, re.I):
            쓰는곳.append(길.name)

    assert 쓰는곳 == ["db.py"], f"배치가 설정을 쓴다: {쓰는곳}"


# ----------------------------------------------------------------------
# 두 언어의 **기본값**이 같은가 (docs/infra.md 25.185)
# ----------------------------------------------------------------------

#: 배치가 `get_setting(…, 기본값)` 으로 읽는 키 → 그 기본값을 내는 파이썬 식.
#: 화면에 입력칸이 없는 키라도 `PUT /api/settings` 는 `SETTINGS_KEYS` 를 **전부** 쓴다 —
#: 사용자가 아무 설정이나 한 번 저장하면 웹 기본값이 DB 에 박히고, 그때부터 배치는
#: 제 기본값이 아니라 **웹 기본값**으로 돈다. 그러니 둘이 같아야 한다
양쪽_기본값 = {
    "sentiment_target_top_n": "batch/jobs/monitor_targets.py::NEWS_TOP_N",
    "min_order_amount": "batch/jobs/signals.py::DEFAULT_MIN_ORDER",
    # 25.230 — 배치 0 · 웹 10 으로 갈라져 있었는데 이 목록에 없어 못 잡았다
    "sentiment_weight": "batch/jobs/scores.py::DEFAULT_SENTIMENT_WEIGHT",
    "max_weight_per_stock": None,  # 값이 식이 아니라 숫자로 박혀 있다. 아래에서 직접 읽는다
    "max_weight_per_sector": None,
}


def 웹_기본값(키: str) -> float:
    """`DEFAULT_SETTINGS` 의 맨 윗단 숫자 값."""
    글 = 스키마파일.read_text(encoding="utf-8")
    본문 = 글[글.index("DEFAULT_SETTINGS") :]
    m = re.search(rf"^  {키}: ([\d_]+),", 본문, re.M)
    assert m, f"web/lib/settings.ts 의 DEFAULT_SETTINGS 에서 `{키}` 를 못 읽었다"
    return float(m.group(1).replace("_", ""))


def test_기본값을_읽어_냈다() -> None:
    """읽기가 조용히 실패하면 아래가 공짜로 통과한다."""
    assert 웹_기본값("max_weight_per_stock") == 10.0


def test_뉴스_후보_수_기본값이_같다() -> None:
    """**이 값이 갈라져 있었다** (50 vs 200, docs/infra.md 25.185).

    설정 화면에 입력칸이 없어 사용자는 이 값을 본 적도 없는데, 다른 설정을 한 번
    저장하는 순간 50 이 박히고 뉴스 수집 후보가 200 → 50 종목으로 줄었다.
    """
    from batch.jobs import monitor_targets as mt

    assert 웹_기본값("sentiment_target_top_n") == float(mt.NEWS_TOP_N), (
        "화면 기본값과 배치 기본값이 다르다 — 사용자가 아무 설정이나 저장하면"
        " 배치의 기본값이 조용히 덮인다"
    )


def test_최소_주문_기본값이_같다() -> None:
    from batch.jobs import signals as sj

    assert 웹_기본값("min_order_amount") == float(sj.DEFAULT_MIN_ORDER)


def test_비중_상한_기본값이_같다() -> None:
    """배치의 정의처는 `settings_range.DEFAULT_MAX_*` 하나(25.1754) — 웹 기본값과 같고, 배치 어디서도 숫자로 다시 적지 않는다."""
    from batch.core import settings_range as sr
    from batch.services import report_picks as rp

    assert 웹_기본값("max_weight_per_stock") == sr.DEFAULT_MAX_STOCK_PCT == rp.DEFAULT_MAX_STOCK_PCT
    assert 웹_기본값("max_weight_per_sector") == sr.DEFAULT_MAX_SECTOR_PCT == rp.DEFAULT_MAX_SECTOR_PCT
    따로 = [f"{f.relative_to(뿌리)}:{k}" for f in (뿌리 / "batch").rglob("*.py")
            for k, 줄 in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
            if re.search(r'"max_weight_per_(stock|sector)"\)? *(,|or) *\d', 줄)
            or re.search(r'max_(weight_per_(stock|sector)|stock_pct|sector_pct)"?\s*(:\s*float)?\s*[:=]\s*[1-9]', 줄)
            # 0 은 "배분하지 않음"(추천을 못 읽은 날 — `jobs/daily`)이라 뺀다
            or re.search(r'\b(10|30)(\.0)? if \w*상한|상한 is (not )?None else (10|30)\b', 줄)]  # fmt: skip
    # 줄을 넘긴 두 번째 인자(`_담다(get_setting_in_range(…, 기본), 10.0)`)도 — 파일 글 전체에서(25.1755)
    따로 += [str(f.relative_to(뿌리)) for f in (뿌리 / "batch").rglob("*.py")
             if re.search(r'_담다\([^()]*\([^()]*"max_weight_per_\w+"[^()]*\),\s*\d', f.read_text(encoding="utf-8"))]
    assert not 따로, f"비중 상한 기본값을 숫자로 다시 적었다 — settings_range.DEFAULT_MAX_* 를 쓴다: {따로}"


def test_대_보는_목록이_낡지_않았다() -> None:
    """키 이름이 바뀌면 위 검사들이 조용히 엉뚱한 것을 본다."""
    있는키 = set(스키마키())
    없는것 = sorted(set(양쪽_기본값) - 있는키)

    assert not 없는것, f"스키마에 없는 키를 대 보고 있다: {없는것}"


def test_목록의_기본값이_모두_같다() -> None:
    """**목록에 넣기만 해서는 아무것도 안 본다** (docs/infra.md 25.230).

    키마다 따로 검사를 쓰던 방식이라, `sentiment_weight` 를 목록에 넣어도 비교하는 검사가 없으면 그만이었다.
    `파일::이름` 으로 적힌 것은 여기서 전부 댄다.
    """
    import importlib

    어긋남 = []
    for 키, 자리 in 양쪽_기본값.items():
        if 자리 is None:
            continue
        파일, 이름 = 자리.split("::")
        모듈 = importlib.import_module(파일.removesuffix(".py").replace("/", "."))
        if float(getattr(모듈, 이름)) != 웹_기본값(키):
            어긋남.append(f"{키}: 배치 {getattr(모듈, 이름)} · 웹 {웹_기본값(키)}")
    assert not 어긋남, "두 언어의 기본값이 다르다:\n  " + "\n  ".join(어긋남)


def 배치가_숫자_기본값으로_읽는_키() -> set[str]:
    """`get_setting(client, "키", 숫자)`·`get_setting_in_range(…)` 꼴. 기본값이 숫자 상수·이름인 것."""
    import ast

    나온것: set[str] = set()
    for 길 in sorted((뿌리 / "batch").rglob("*.py")):
        if "__pycache__" in str(길):
            continue
        for 마디 in ast.walk(ast.parse(길.read_text(encoding="utf-8"))):
            if not isinstance(마디, ast.Call):
                continue
            이름 = 마디.func.attr if isinstance(마디.func, ast.Attribute) else getattr(마디.func, "id", "")
            if 이름 not in ("get_setting", "get_setting_in_range") or len(마디.args) < 3:
                continue
            키, 기본 = 마디.args[1], 마디.args[2]
            if isinstance(기본, ast.Call) and getattr(기본.func, "id", "") in ("float", "int") and 기본.args:
                기본 = 기본.args[0]  # float(DEFAULT_MIN_ORDER) 같은 꼴
            숫자 = (isinstance(기본, ast.Constant) and isinstance(기본.value, int | float)) or isinstance(
                기본, ast.Name | ast.Attribute
            )
            if isinstance(키, ast.Constant) and isinstance(키.value, str) and 숫자:
                나온것.add(키.value)
    return 나온것


#: 숫자 기본값으로 읽지만 **웹 기본값과 댈 필요가 없는** 키와 사유
대지_않는_키 = {
    "total_investable_amount": "기본값 0 은 '아직 안 정함' 이다. 웹도 비워 두고 사용자가 넣는다",
}


def test_숫자_기본값으로_읽는_키는_모두_대_본다() -> None:
    """**역방향.** 배치가 숫자 기본값으로 읽는 키가 목록에 없으면 두 언어가 갈라져도 모른다 — 25.230 이 그랬다."""
    빠진것 = sorted(배치가_숫자_기본값으로_읽는_키() - set(양쪽_기본값) - set(대지_않는_키))
    assert not 빠진것, f"웹 기본값과 대 보지 않는 키: {빠진것} — `양쪽_기본값` 에 적거나 `대지_않는_키` 에 사유를"


def test_역방향_훑기가_실제로_문다() -> None:
    assert {"sentiment_weight", "min_order_amount"} <= 배치가_숫자_기본값으로_읽는_키()


# ----------------------------------------------------------------------
# **중첩 칸**의 기본값도 같은가 (docs/infra.md 25.1746, 중복 규칙 감사 ⑦)
# ----------------------------------------------------------------------
# 위 대조는 맨 윗단 숫자만 본다. `PUT /api/settings` 는 중첩 칸(기간 목표·손절, 추세 필터, 조용시간, 알림 문턱)도
# 웹 기본값으로 **전부** DB 에 쓰니, 갈라지면 사용자가 아무 설정을 한 번 저장한 날 가상모의투자 손절선·약세장 비중이
# 조용히 바뀐다


def 웹_중첩_기본값(키: str) -> dict:
    """`DEFAULT_SETTINGS` 의 `키: { … }` 를 파이썬 dict 로 — 글자 그대로 옮긴다(키에 따옴표, 끝 쉼표·주석 뺌)."""
    import json

    글 = 스키마파일.read_text(encoding="utf-8")
    본문 = 글[글.index("DEFAULT_SETTINGS") :]
    i = 본문.index(f"\n  {키}: {{") + len(f"\n  {키}: ")
    깊이, j = 0, i
    while True:
        깊이 += {"{": 1, "}": -1}.get(본문[j], 0)
        j += 1
        if 깊이 == 0:
            break
    덩이 = re.sub(r"//[^\n]*", "", 본문[i:j])
    덩이 = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', 덩이)
    덩이 = re.sub(r",(\s*})", r"\1", 덩이)
    return json.loads(re.sub(r"(\d)_(\d)", r"\1\2", 덩이))  # 100_000 꼴 숫자


def test_중첩_기본값_기간_목표_손절() -> None:
    from batch.services import sell_flags, signals

    웹 = 웹_중첩_기본값("horizon_targets")
    assert 웹 == {k: {kk: float(vv) for kk, vv in v.items()} for k, v in signals.DEFAULT_TARGETS.items()}
    assert sell_flags.DEFAULT_TARGETS is signals.DEFAULT_TARGETS  # 정의처 하나(25.1746)


def test_중첩_기본값_추세_필터_조용시간_알림_문턱() -> None:
    from batch.services import after_hours, trend

    assert 웹_중첩_기본값("trend_filter") == {"enabled": trend.DEFAULT_ENABLED, "bear_factor": trend.DEFAULT_BEAR_FACTOR}
    assert 웹_중첩_기본값("quiet_hours") == after_hours.DEFAULT_QUIET
    assert 웹_중첩_기본값("alert_thresholds")["spike_pct"] == after_hours.DEFAULT_SPIKE_PCT
