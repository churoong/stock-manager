"""뉴스 **이용 조건**을 코드가 지키는가 (docs/infra.md 25.158).

CLAUDE.md 에 절대 규칙으로 둘이 적혀 있다.

> **구글 뉴스 RSS는 사용 금지**(피드 본문에 개인 피드 리더 외 사용 금지 명시)
> 원문은 저장하지 않고 **제목·URL·발행시각·점수만** 저장

2026-09-23 현재 둘 다 지켜지고 있다. **그런데 지키는 장치가 없었다.** 책임 고지는
`tests/test_disclaimer.py` 가 지키고, 한도 이름은 `test_api_usage_names.py` 가 지키는데
이 둘은 사람의 기억에 맡겨져 있었다.

이런 규칙은 **어기는 날 조용하다.** 피드 하나를 더 붙이는 일은 몇 줄이고, 그 몇 줄이
약관 위반이 되는지는 붙이는 사람이 그날 기억해야 한다. 기억은 낡는다.

여기서 막는 것은 셋이다.
  1. 구글 뉴스 호스트가 **코드에** 들어오는 것
  2. 이용 조건을 적어 두지 않은 피드가 붙는 것
  3. 기사 **원문**이 DB 에 들어오는 것
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent

#: 뉴스를 받아 오는 곳. **호스트마다 이용 조건을 확인하고 적은 뒤에만** 들어올 수 있다
#: (CLAUDE.md: "새 외부 서비스를 쓰기 전에 … 이용 조건을 확인해 docs/data-sources.md 에 기록").
#: 사유는 그 문서가 적은 것을 요약한 것이고, 원문 근거는 `docs/sentiment.md` 2·3장에 있다.
피드_출처 = {
    "www.yna.co.kr": "연합뉴스 RSS. 이용 조건이 명시된 유일한 국내 피드 —"
    ' "비상업적 블로그와 개인적인 용도로만 허용"(yna.co.kr/rss/index)',
    "www.nasdaq.com": "나스닥 종목별 RSS. 항목마다 관련 티커가 있어 종목 매칭이 정확하다."
    " 이용 조건 원문은 JS 렌더링이라 못 읽었다 `[확인필요: 사용자 브라우저 확인]`"
    " (docs/sentiment.md 2장)",
}

#: 쓰지 않기로 **정한** 곳. 이유가 곧 판단의 기록이다
금지_출처 = {
    "news.google.com": "피드 본문에 개인 피드 리더 외 사용 금지가 명시돼 있다"
    " (CLAUDE.md 데이터 소스 · docs/data-sources.md 7장)",
}

#: `news` 표에 있어도 되는 칸. **기사 본문을 담는 칸은 없다**
허용_열 = {
    "id", "stock_id", "title", "url", "published_at", "publisher", "lang", "source", "fetched_at",
}  # fmt: skip

#: 본문을 담을 법한 이름들. 하나라도 생기면 규칙이 깨진 것이다
본문_냄새 = ("body", "content", "description", "summary", "full_text", "article_text")

_뉴스모듈 = ("web/lib/news.ts", "web/lib/newsKr.ts")


def _소스들() -> list[Path]:
    나온것 = []
    for 자리 in ("batch", "scripts", "web/lib", "web/app", "web/components"):
        for 길 in sorted((뿌리 / 자리).rglob("*")):
            if 길.is_file() and 길.suffix in (".py", ".ts", ".tsx") and "node_modules" not in 길.parts:
                나온것.append(길)
    return 나온것


def _호스트들(글: str) -> set[str]:
    return {m.group(1) for m in re.finditer(r"https://([A-Za-z0-9.\-]+)/", 글)}


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 전부 공짜로 통과한다."""
    파일 = _소스들()
    assert len(파일) > 100, f"소스를 {len(파일)}개밖에 못 찾았다"
    for 이름 in _뉴스모듈:
        assert (뿌리 / 이름).exists(), f"{이름} 이 사라졌다 — 이 검사의 전제가 바뀌었다"


def test_구글_뉴스를_코드가_부르지_않는다() -> None:
    """**문서는 이 이름을 적어도 된다.** 안 쓰는 이유를 적는 것이 문서의 일이다.

    여기서 보는 것은 **코드**다. 호스트가 소스에 들어오면 그 순간 약관을 어긴다.
    """
    걸린것 = []
    for 길 in _소스들():
        글 = 길.read_text(encoding="utf-8", errors="ignore")
        for 호스트 in 금지_출처:
            if 호스트 in 글:
                걸린것.append(f"{길.relative_to(뿌리)}: {호스트}")

    assert not 걸린것, (
        f"쓰지 않기로 한 뉴스 출처가 코드에 있다: {걸린것}\n"
        + "\n".join(f"  {k}: {v}" for k, v in 금지_출처.items())
    )


def test_피드_출처가_적어_둔_것뿐이다() -> None:
    """새 피드를 붙이면 여기가 깨진다. 그때 **이용 조건을 확인하고 적게** 된다."""
    본것: dict[str, str] = {}
    for 이름 in _뉴스모듈:
        길 = 뿌리 / 이름
        for 호스트 in _호스트들(길.read_text(encoding="utf-8")):
            본것.setdefault(호스트, 이름)

    새것 = sorted(set(본것) - set(피드_출처))
    assert not 새것, (
        f"이용 조건을 적어 두지 않은 뉴스 출처가 생겼다: {[(h, 본것[h]) for h in 새것]}\n"
        "docs/data-sources.md 에 조건을 적고 `피드_출처` 에 사유와 함께 넣어라"
        " (CLAUDE.md 데이터 소스 규칙)"
    )

    사라짐 = sorted(set(피드_출처) - set(본것))
    assert not 사라짐, f"목록에만 남은 출처: {사라짐} — 안 쓰면 목록에서도 지운다"


def test_출처마다_사유가_있다() -> None:
    for 표 in (피드_출처, 금지_출처):
        짧은것 = [k for k, v in 표.items() if len(v.strip()) < 20]
        assert not 짧은것, f"사유가 너무 짧다: {짧은것}"


def test_기사_원문을_담는_칸이_없다() -> None:
    """CLAUDE.md: "원문은 저장하지 않고 제목·URL·발행시각·점수만 저장"."""
    본문 = (뿌리 / "migrations" / "0021_sentiment.sql").read_text(encoding="utf-8")
    몸통 = 본문.split("CREATE TABLE IF NOT EXISTS news (", 1)[1].split(");", 1)[0]
    열 = {
        m.group(1)
        for 줄 in 몸통.splitlines()
        if (m := re.match(r"\s{4}([a-z_]+)\s", 줄))
    }

    assert 열, "news 표의 열을 못 읽었다"
    새열 = sorted(열 - 허용_열)
    assert not 새열, f"news 표에 새 열이 생겼다: {새열} — 원문을 담는 칸이 아닌지 보라"


def test_다른_표에도_본문_칸을_만들지_않는다() -> None:
    """뉴스 곁에 표를 하나 더 만들어 본문을 담는 길도 막는다."""
    본문 = (뿌리 / "migrations" / "0021_sentiment.sql").read_text(encoding="utf-8")
    걸린것 = [이름 for 이름 in 본문_냄새 if re.search(rf"^\s+{이름}\s", 본문, re.M)]

    assert not 걸린것, f"기사 본문을 담을 법한 열이 있다: {걸린것}"


@pytest.mark.parametrize("이름", _뉴스모듈)
def test_파서가_본문_태그를_안_읽는다(이름: str) -> None:
    """**담을 칸이 없어도 읽어 오면 언젠가 담는다.** 읽는 데서 끊는다.

    `parseRss` 는 제목·링크·시각·작성자·티커만 꺼낸다(설명문에 그렇게 적혀 있다).
    """
    글 = (뿌리 / 이름).read_text(encoding="utf-8")
    걸린것 = [t for t in ("description", "content:encoded") if f'"{t}"' in 글]

    assert not 걸린것, f"{이름} 가 본문 태그를 읽는다: {걸린것}"
