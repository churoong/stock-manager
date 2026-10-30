// 25.2001 (25.2000 교차검증 [참고]) — 거래내역 근거: 옛 스윙 꼴 {name, value, date} 도 근거표 행으로(빈 칸 대신 "참고")
import { describe, expect, it } from "vitest";
import { tradeCriteria } from "@/lib/paper";

describe("tradeCriteria", () => {
  it("근거표 꼴은 그대로, 옛 꼴은 참고 행으로, 모르는 것은 뺀다", () => {
    const 새 = { label: "판정 종가", display: "8,700원 (3거래일째)", threshold: "손절 -3%", source: "prices", as_of: "2026-10-31", passed: true };
    const rows = tradeCriteria([새, { name: "진입가", value: 10000, date: "2026-10-29" }, null, 3, { x: 1 }]);
    expect(rows).toEqual([
      새,
      { label: "진입가", display: "10,000", threshold: "-", source: "가상모의투자 (옛 기록 꼴)", as_of: "2026-10-29", passed: null },
    ]);
    expect(rows.every((r) => r.label && r.display)).toBe(true);
  });

  it("배열이 아니거나 근거표 꼴이 덜 찬 행은 뺀다 — 깨진 기록이 화면을 죽이지 않게(25.2002)", () => {
    expect(tradeCriteria({})).toEqual([]);
    expect(tradeCriteria("x")).toEqual([]);
    expect(tradeCriteria([{ label: "반쪽", display: "1" }])).toEqual([]);
  });
});
