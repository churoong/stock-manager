/** 실제 수익률 경로·움직임 분해·매물대 화면 (docs/analysis.md 43~45장, docs/infra.md 25.1064) */
import { describe, expect, it } from "vitest";
import { tailCell } from "@/components/PriceLadder";
import { outlookListLines, type LadderItem } from "@/lib/analysis";

const 칸 = (x: Partial<LadderItem>): LadderItem => ({ label: "a", price: 1, kind: "high", source: "s", dist: 0.1, ...x });

describe("실제 꼬리", () => {
  it("실제 경로 값과 괄호 안 정규 가정", () => {
    expect(tailCell(칸({ touch_boot: { "12": 0.12 }, touch_norm0: { "12": 0.2 } }))).toBe("12% (20%)");
    expect(tailCell(칸({ touch_boot: { "12": null } }))).toBe("-");
    expect(tailCell(칸({}))).toBe("-");
  });
  it("표·카드가 그리는 줄은 목록에서 뺀다", () => {
    expect(outlookListLines(["움직임 분해(지난 20거래일, …)", "실제 수익률로 그린 1년 범위(…)", "기타"])).toEqual(["기타"]);
  });
});
