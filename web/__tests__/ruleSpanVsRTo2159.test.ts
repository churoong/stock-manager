import { describe, expect, it } from "vitest";
import { ruleSpanText, type RuleSpan } from "@/lib/desk";

// 25.2159 — 판 이력 줄도 R 대비가 대조 R 기록이 멈춘 날까지 잰 값이면 끝을 적는다(순위표·관문 칸과 같게, 25.2147)
describe("판 이력 R 대비 끝", () => {
  const 줄 = (vs_r_to?: string | null): RuleSpan => ({
    version: 2, from: "2026-10-16", to: null, orders: 3, pct: 1.2, control_pct: 0.4, vs_r_pp: 0.8, vs_r_to,
  });
  it("끝이 있으면 적는다", () => {
    expect(ruleSpanText(줄("2026-10-30"))).toContain("R 대비 +0.80%p, 2026-10-30까지)");
  });
  it("끝이 없으면 예전 글 그대로", () => {
    expect(ruleSpanText(줄(null))).toContain("R 대비 +0.80%p)");
    expect(ruleSpanText(줄())).not.toContain("까지");
  });
});
