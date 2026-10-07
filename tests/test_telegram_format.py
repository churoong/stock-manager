"""텔레그램 메시지 분할 테스트.

네트워크를 타지 않는다. API 키 없이 돌아가야 한다.
"""

from __future__ import annotations

from batch.notify.telegram import split_message


def test_짧은_메시지는_그대로_한_통() -> None:
    text = "삼성전자 71,000원 (+1.43%)"
    assert split_message(text) == [text]


def test_한도_이하면_나누지_않는다() -> None:
    text = "가" * 100
    assert len(split_message(text, limit=100)) == 1


def test_한도를_넘으면_줄_단위로_나눈다() -> None:
    # 각 줄 10자, 총 5줄. 한도를 25자로 두면 줄 경계에서 잘려야 한다.
    text = "\n".join("0123456789" for _ in range(5))
    chunks = split_message(text, limit=25)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 25
    # 줄이 중간에서 잘리지 않았는지 확인한다
    for chunk in chunks:
        for line in chunk.split("\n"):
            assert line == "0123456789"


def test_나눠도_내용이_보존된다() -> None:
    text = "\n".join(f"{i}번째 줄입니다" for i in range(50))
    chunks = split_message(text, limit=60)
    assert "\n".join(chunks) == text


def test_한_줄이_한도보다_길면_강제로_쪼갠다() -> None:
    text = "가" * 250
    chunks = split_message(text, limit=100)

    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [100, 100, 50]
    assert "".join(chunks) == text


def test_긴_줄과_짧은_줄이_섞여도_한도를_지킨다() -> None:
    text = "짧은 줄\n" + "나" * 150 + "\n또 짧은 줄"
    chunks = split_message(text, limit=50)
    for chunk in chunks:
        assert len(chunk) <= 50
