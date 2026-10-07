"""KorFinASC 실측 (docs/sentiment.md 1.2, 사용자 결정 2026-09-17). **DB 를 읽지도 쓰지도 않는다.**

amphora/KorFinASC-XLM-RoBERTa (apache-2.0, 약 2.24GB) 를 GitHub Actions 러너(CPU)에서 돌려
  1. 내려받기·불러오기 시간
  2. 문장당 채점 시간 (하루 수집량 1~2천 건이면 몇 분인가)
  3. 판정 방향이 맞는가 (손으로 긍정·부정을 붙인 문장)
을 잰다. 연합뉴스 피드의 "AI 학습 및 활용 금지" 문구 범위가 확인되기 전이라, **실제 기사 대신 직접 쓴 문장**만 넣는다.
속도는 문장 출처와 무관하다.

입력 형식은 모델 카드대로 "문장 </s> 대상". 대상 종목에 대한 감성을 판정한다(한 문장에 두 회사가 나오면 다를 수 있다).

실행 (Actions: korfinasc-bench.yml)
  python -m batch.jobs.korfinasc_bench --count 1000
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time

MODEL = "amphora/KorFinASC-XLM-RoBERTa"

# 손으로 쓴 판정 확인용 문장: (문장, 대상, 기대 방향 +1/−1)
LABELED: list[tuple[str, str, int]] = [
    ("가나전자, 3분기 영업이익 사상 최대…시장 기대치 웃돌아", "가나전자", 1),
    ("가나전자 대규모 수주 계약 체결, 주가 급등", "가나전자", 1),
    ("다라바이오 신약 임상 3상 성공 발표", "다라바이오", 1),
    ("마바화학, 배당 확대와 자사주 매입 결정", "마바화학", 1),
    ("사아자동차 수출 호조로 실적 개선 기대", "사아자동차", 1),
    ("차카건설 해외 플랜트 수주로 신용등급 상향", "차카건설", 1),
    ("가나전자, 적자 전환…영업손실 확대", "가나전자", -1),
    ("다라바이오 임상 중단 소식에 주가 폭락", "다라바이오", -1),
    ("마바화학 공장 화재로 생산 차질 불가피", "마바화학", -1),
    ("사아자동차 대규모 리콜 발표, 소송 우려", "사아자동차", -1),
    ("차카건설 유동성 위기설…회사채 금리 급등", "차카건설", -1),
    ("타파은행, 횡령 사고로 금융당국 제재", "타파은행", -1),
    # 한 문장에 두 회사: 대상에 따라 방향이 달라야 한다
    ("가나전자가 마바화학을 제치고 점유율 1위 차지", "가나전자", 1),
    ("가나전자가 마바화학을 제치고 점유율 1위 차지", "마바화학", -1),
]

TEMPLATES = [
    "{c}, {q}분기 매출 {n}% 증가…영업이익은 소폭 감소",
    "{c} 주가 {n}% 하락 마감, 외국인 순매도 지속",
    "증권가 \"{c} 목표주가 상향\"…업황 회복 기대",
    "{c} 신규 사업 진출 발표, 투자 규모 {n}억원",
    "{c} 최대주주 지분 매각설에 투자자 관심",
]
COMPANIES = ["가나전자", "다라바이오", "마바화학", "사아자동차", "차카건설", "타파은행"]


def synthetic(count: int) -> list[tuple[str, str]]:
    out = []
    for i, (t, c) in enumerate(itertools.cycle(itertools.product(TEMPLATES, COMPANIES))):
        if i >= count:
            break
        out.append((t.format(c=c, q=i % 4 + 1, n=(i * 7) % 30 + 1), c))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="KorFinASC 실측")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(max(1, torch.get_num_threads()))
    t0 = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL)
    model.eval()
    load_sec = time.monotonic() - t0
    labels = model.config.id2label
    print(f"불러오기 {load_sec:.1f}초 (내려받기 포함, 캐시가 있으면 짧다)")
    print(f"라벨 {labels}, 스레드 {torch.get_num_threads()}")

    def classify(pairs: list[tuple[str, str]]) -> list[dict[str, float]]:
        results = []
        for start in range(0, len(pairs), args.batch_size):
            chunk = pairs[start : start + args.batch_size]
            enc = tokenizer([s for s, _ in chunk], [c for _, c in chunk], padding=True, truncation=True,
                            max_length=128, return_tensors="pt")  # fmt: skip
            with torch.no_grad():
                probs = torch.softmax(model(**enc).logits, dim=-1)
            for row in probs.tolist():
                results.append({labels[i]: round(p, 4) for i, p in enumerate(row)})
        return results

    labeled = classify([(s, c) for s, c, _ in LABELED])
    correct = 0
    for (sentence, company, expected), probs in zip(LABELED, labeled, strict=True):
        top = max(probs, key=probs.get)
        direction = 1 if "pos" in top.lower() else (-1 if "neg" in top.lower() else 0)
        correct += direction == expected
        mark = "O" if direction == expected else "X"
        print(f"  {mark} [{company}] {sentence} → {top} {json.dumps(probs, ensure_ascii=False)}")
    print(f"판정 방향 {correct}/{len(LABELED)} 맞음 (중립은 틀림으로 셈)")

    pairs = synthetic(args.count)
    classify(pairs[: args.batch_size])  # 첫 호출 준비 시간은 재지 않는다
    t1 = time.monotonic()
    classify(pairs)
    elapsed = time.monotonic() - t1
    per = elapsed / len(pairs)
    print(f"채점 {len(pairs)}건 {elapsed:.1f}초 → 건당 {per * 1000:.1f}ms")
    print(f"1,000건 {per * 1000:.0f}초, 2,000건 {per * 2000 / 60:.1f}분")
    return 0


if __name__ == "__main__":
    sys.exit(main())
