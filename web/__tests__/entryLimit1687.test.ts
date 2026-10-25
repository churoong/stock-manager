import { describe, expect, it } from "vitest";
import { entryCellText } from "@/lib/paper";

// docs/infra.md 25.1687 — 지정가 체결은 진입에서 재지 않고 수만(모형상 늘 종가 이상 — B·H 가 늘 "비쌌다")
describe("성과 분해 진입 칸", () => {
  it("지정가만이면 수만 적는다", () => {
    expect(entryCellText({ n: 0, avg: null, limit_n: 3 })).toBe("지정가 체결 3건은 재지 않음(모형상 늘 종가 이상)");
  });
  it("시장가와 섞이면 시장가 값 뒤에 붙인다", () => {
    expect(entryCellText({ n: 2, avg: 0.5, limit_n: 1 })).toBe(
      "+0.50% (n=2, 참고), 지정가 체결 1건은 재지 않음(모형상 늘 종가 이상)",
    );
  });
  it("지정가가 없으면 예전 꼴", () => {
    expect(entryCellText({ n: 2, avg: 0.5 })).toBe("+0.50% (n=2, 참고)");
    expect(entryCellText(undefined)).toBe("표본 없음");
  });
  it("종가로 판정된 시장가 체결도 수만 — 늘 0 이라 평균을 묽혔다(25.1783)", () => {
    expect(entryCellText({ n: 10, avg: 3, close_n: 10 })).toBe(
      "+3.00% (n=10, 참고), 종가로 판정된 시장가 체결 10건은 재지 않음(체결가가 그날 종가라 늘 0)");
    expect(entryCellText({ n: 0, avg: null, limit_n: 2, close_n: 1 })).toBe(
      "지정가 체결 2건, 종가로 판정된 시장가 체결 1건은 재지 않음(지정가는 모형상 늘 종가 이상, 종가 판정은 늘 0)");
  });
});
