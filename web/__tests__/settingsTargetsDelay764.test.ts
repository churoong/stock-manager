import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { TARGETS_DELAY_NOTE } from "@/lib/settings";

/** 목표·손절 변경이 장중 감시·매도 플래그엔 다음 배치부터라는 것을 말하지 않았다 (docs/infra.md 25.764, 설정 감사) */
describe("목표·손절 반영 시점 안내", () => {
  it("저장 API 가 바뀌었는지 알려 주고 화면이 안내한다", () => {
    const route = readFileSync("app/api/settings/route.ts", "utf8");
    const form = readFileSync("components/SettingsForm.tsx", "utf8");
    expect(route).toContain('전.get("horizon_targets") !== JSON.stringify(result.value["horizon_targets"])');
    expect(route).toContain("targets_changed,");
    expect(form).toContain("body.targets_changed ? [TARGETS_DELAY_NOTE]");
    expect(TARGETS_DELAY_NOTE).toContain("다음 일일 배치");
    expect(TARGETS_DELAY_NOTE).toContain("장중 감시 준비"); // 25.767·25.769 — 시장·시각별로, 즉시 경로(장중 감시만)도 알린다
  });
});

/** 배당 세율이 배치 범위를 넘으면 폼이 채우지 않는다 (docs/infra.md 25.765, 설정 감사) */
describe("배당 세율 범위", () => {
  it("50% 를 넘으면 모름", async () => {
    const { withholdingRate } = await import("@/lib/portfolio");
    expect(withholdingRate("KRW", { kr_dividend_pct: 60 })).toBeNull();
    expect(withholdingRate("KRW", { kr_dividend_pct: 15.4 })).toBe(15.4);
    expect(withholdingRate("USD", { us_dividend_pct: 50 })).toBe(50);
  });
});
