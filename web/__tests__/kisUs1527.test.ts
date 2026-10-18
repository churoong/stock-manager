/**
 * KIS모의투자 미국 몫 (docs/paper.md 15.7, docs/infra.md 25.1527) — 계좌 전체 총액은 국내 총평가 + KIS 해외 총자산 원화,
 * 미국 칸 현금은 해외 현금·해외 총자산(모의는 달러 예수금이 늘 0). 값은 배치가 내고 화면은 글만 만든다.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { KIS_TOTAL_BASIS, usCashText, wholeBreakdownText } from "@/lib/kisPaper";

describe("KIS모의투자 미국 몫 글 (25.1527)", () => {
  it("미국 현금 — 해외 현금·총자산이 있으면 그것, 예전 줄이면 달러 예수금", () => {
    expect(usCashText({ cash: 0, cash_krw: 2_400_000, assets_krw: 3_000_000 })).toBe("해외 현금 2,400,000원 · 해외 총자산 3,000,000원 (KIS 원화 환산)");
    expect(usCashText({ cash: 100 })).toBe("달러 예수금 $100.00");
  });

  it("계좌 전체 나눔 — 기준 2 는 국내 + 해외 총자산, 예전 식은 미국 몫이 빠졌다고 밝힌다", () => {
    const k = { kr_total: 1_000_000, value_usd: 0, cash_usd: 0, usd_krw: 1000, total_krw: 4_000_000, assets_krw: 3_000_000, basis: 2 };
    expect(wholeBreakdownText(k, "2026-10-20")).toBe("국내 1,000,000원 + 해외 총자산 3,000,000원 · 2026-10-20 국내 배치 기준");
    expect(wholeBreakdownText({ ...k, basis: undefined }, "2026-10-16")).toContain("미국 몫이 빠졌습니다");
  });

  it("화면의 기준 번호는 배치 TOTAL_BASIS 와 같다", () => {
    const py = readFileSync(join(process.cwd(), "..", "batch", "jobs", "broker_mirror.py"), "utf8");
    expect(Number(/^TOTAL_BASIS = ([0-9]+)/m.exec(py)?.[1])).toBe(KIS_TOTAL_BASIS);
  });
});
