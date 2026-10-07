import { describe, expect, it } from "vitest";
import { luckText } from "@/lib/backtest";

/** 우연일 확률 칸 (docs/backtest.md 9장, 25.997) — 배치가 metrics_json.luck 에 싣는다 */
describe("우연일 확률", () => {
  it("값이 있으면 % 와 근거", () => {
    const t = luckText({ luck: { luck_pct: 12.3, n_trials: 8, months: 60, sr_monthly: 0.21, sr_star: 0.15, psr: 0.97 } });
    expect(t.cell).toBe("12%");
    expect(t.title).toContain("시험한 전략 8개, 60개월");
    expect(t.title).toContain("PSR(문턱 0) 97%");
  });

  it("짧으면 판정 안 함, 옛 실행이면 -", () => {
    expect(luckText({ luck: { months: 10, verdict: "판정 안 함(월 10개 < 24)" } })).toEqual({ cell: "판정 안 함", title: "판정 안 함(월 10개 < 24)" });
    expect(luckText({ cagr: 0.1 }).cell).toBe("-");
  });
});
