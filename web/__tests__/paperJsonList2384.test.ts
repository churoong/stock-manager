// 평가 행 JSON 목록 칸 — 깨진 한 줄이 가상모의투자 화면 전체를 500 으로 만들지 않는다 (docs/infra.md 25.2384)
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseJsonList } from "@/lib/paper";

describe("parseJsonList", () => {
  it("목록은 그대로, 빈 값은 빈 목록(깨짐 아님)", () => {
    expect(parseJsonList('[{"stock_id":1}]')).toEqual({ list: [{ stock_id: 1 }], broken: false });
    expect(parseJsonList(null)).toEqual({ list: [], broken: false });
    expect(parseJsonList("")).toEqual({ list: [], broken: false });
  });
  it("깨진 글·NaN·목록 아님은 빈 목록 + 깨짐", () => {
    expect(parseJsonList("x")).toEqual({ list: [], broken: true });
    expect(parseJsonList('[{"value_krw": NaN}]')).toEqual({ list: [], broken: true }); // 파이썬 json.dumps 기본(allow_nan)
    expect(parseJsonList('{"a":1}')).toEqual({ list: [], broken: true });
  });
  it("라우트가 날 JSON.parse 대신 이것을 쓴다 — 보유·모름 둘 다", () => {
    const src = readFileSync(join(process.cwd(), "app", "api", "paper", "route.ts"), "utf8");
    expect(src).toContain("parseJsonList(d.positions_json)");
    expect(src).toContain("parseJsonList(d.unknown_json)");
    expect(src).not.toMatch(/JSON\.parse\(d\.positions_json/);
    expect(src).not.toMatch(/JSON\.parse\([^)]*unknown_json/);
    expect(src).toContain("record_error");
  });
});
