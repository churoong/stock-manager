import { describe, expect, it } from "vitest";
import { bandSpan } from "@/lib/stockDetail";

/** 밴드 표기는 실제로 덮은 기간 — 배치 `signals.band_span` 과 같은 규칙 (docs/infra.md 25.1368) */
describe("밴드 기간", () => {
  it("창을 채우면 3년, 아니면 표본 ÷ 250", () => {
    expect(bandSpan(750)).toBe("3년");
    expect(bandSpan(720)).toBe("3년");
    expect(bandSpan(412)).toBe("1.6년");
    expect(bandSpan(null)).toBe("0년");
  });
});
