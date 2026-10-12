import { describe, expect, it } from "vitest";
import { otherHorizonNote } from "@/lib/recommend";

/** 같은 종목이 다른 기간에도 있으면 금액을 더하지 말라고 말한다 (docs/infra.md 25.1162, 감사 재현) */
describe("다른 기간 같은 종목", () => {
  const 줄 = [
    { stock_id: 1, horizon: "short" },
    { stock_id: 1, horizon: "mid" },
    { stock_id: 2, horizon: "short" },
  ];
  it("다른 기간이 있으면 그 기간 이름과 함께 한 줄", () => {
    const 글 = otherHorizonNote(줄, 줄[0]);
    expect(글).toContain("중기");
    expect(글).toContain("더하지 마세요");
  });
  it("한 기간뿐이면 없음", () => {
    expect(otherHorizonNote(줄, 줄[2])).toBeNull();
  });
});
