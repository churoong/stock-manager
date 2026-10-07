import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";

/** 미국 탭도 재무로 걸러지므로 그 값을 볼 열과 정렬이 있어야 한다 (docs/infra.md 25.365) */
describe("스크리너 미국 탭의 재무 열·정렬", () => {
  const src = readFileSync("components/ScreenerForm.tsx", "utf8");
  const 미국 = src.slice(
    src.indexOf("if (!kr) {"),
    src.indexOf("...tail,", src.indexOf("if (!kr) {")),
  );

  it("미국 열에 시총·PER·PBR·ROE 가 있다", () => {
    for (const key of [
      'key: "market_cap"',
      'key: "per"',
      'key: "pbr"',
      'key: "roe"',
    ])
      expect(미국).toContain(key);
  });

  it("정렬 목록이 미국에서 재무 정렬을 숨기지 않는다", () => {
    expect(src).not.toContain("!FINANCIAL_SORTS.has(key)");
  });

  it("문서가 '미국은 재무 조건이 없다' 고 말하지 않는다", () => {
    expect(readFileSync("../docs/screener.md", "utf8")).not.toContain(
      "미국은 재무 조건이 없다",
    );
  });
});
