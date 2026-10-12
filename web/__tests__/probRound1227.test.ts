// 확률·비율을 반올림으로 0%·100% 로 만들지 않는다 — 가격 사다리·예측 성적표·역사 사실 (docs/infra.md 25.1227)
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { tailCell } from "@/components/PriceLadder";
import type { LadderItem } from "@/lib/analysis";

describe("가격 사다리 꼬리 칸", () => {
  it("99.6% 를 100% 로, 0.3% 를 0% 로 적지 않는다", () => {
    const it_ = { touch_boot: { "12": 0.996 }, touch_norm0: { "12": 0.003 } } as unknown as LadderItem;
    expect(tailCell(it_)).toBe("99% 넘게 (1% 미만)");
  });
  it("참값 100% 는 그대로", () => {
    const it_ = { touch_boot: { "12": 1 } } as unknown as LadderItem;
    expect(tailCell(it_)).toBe("100%");
  });
});

describe("확률 칸이 모두 rateText 를 거친다", () => {
  it("직접 toFixed(0) 로 적는 자리가 없다", () => {
    const 사다리 = readFileSync("components/PriceLadder.tsx", "utf8");
    expect(사다리).toContain("rateText(l.race.p)");
    expect(사다리).toContain("rateText(it.touch[String(m)])");
    expect(readFileSync("components/ForecastTrack.tsx", "utf8")).toContain("ok ? rateText(x)");
    expect(readFileSync("components/HistoryFacts.tsx", "utf8")).not.toMatch(/\.up \* 100\)\.toFixed\(0\)/);
  });
});
