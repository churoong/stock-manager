/** 자기 시세 이력의 사실들 (docs/analysis.md 31~34장, docs/infra.md 25.1057) */
import { describe, expect, it } from "vitest";
import { outlookListLines, seasonTone } from "@/lib/analysis";

describe("이력 카드", () => {
  it("계절성 칸 색은 오른 해가 절반 위·아래·같음", () => {
    expect(seasonTone(3, 5)).toBe("up");
    expect(seasonTone(2, 5)).toBe("down");
    expect(seasonTone(2, 4)).toBe("even");
  });
  it("카드가 그리는 줄은 진단 목록에서 뺀다", () => {
    const 줄 = ["낙폭 회복(2021~ 자기 이력): …", "최악의 한 달(…)", "계절성(…)", "52주 신고가 뒤(…)", "현재 주가 위치: …"];
    expect(outlookListLines(줄)).toEqual(["현재 주가 위치: …"]);
  });
});
