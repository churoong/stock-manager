/** 재무·수급 사건과 그 뒤 (docs/analysis.md 40~42장, docs/infra.md 25.1063) */
import { describe, expect, it } from "vitest";
import { REPORT_NAME, outlookListLines } from "@/lib/analysis";

describe("사건 이력 카드", () => {
  it("카드가 그리는 세 줄도 진단 목록에서 뺀다", () => {
    const 줄 = ["실적 발표 반응(연결, 3번): …", "상승의 출처(2021→2024 사업보고서, …)", "공매도 급증 뒤(…)", "증권사 2곳 목표가 …"];
    expect(outlookListLines(줄)).toEqual(["증권사 2곳 목표가 …"]);
  });
  it("보고서 이름", () => {
    expect(REPORT_NAME["11011"]).toBe("사업");
    expect(REPORT_NAME["11012"]).toBe("반기");
  });
});
