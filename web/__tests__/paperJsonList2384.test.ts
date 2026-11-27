// 평가 행 JSON 목록 칸 — 깨진 한 줄이 가상모의투자 화면 전체를 500 으로 만들지 않는다 (docs/infra.md 25.2384)
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseJsonList, recordErrorText } from "@/lib/paper";

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
    // 한 줄 전체를 본다 — `[^)]*` 는 첫 `)` 에서 멈춰 옛 꼴(`…find((d) => …)?.unknown_json`)을 못 잡았다(25.2385)
    expect(src).not.toMatch(/JSON\.parse\([^;\n]*positions_json/);
    expect(src).not.toMatch(/JSON\.parse\([^;\n]*unknown_json/);
    expect(src).toContain("unknown: 모름읽기[a.id]?.list");
    expect(src).toContain("record_error: recordErrorText(");
    expect(src).toContain("positions_broken: Boolean(보유읽기[a.id]?.broken)");
  });
  it("객체가 아닌 원소는 걸러 내고 깨짐 (25.2385)", () => {
    expect(parseJsonList('[{"stock_id":1}, null, 3]')).toEqual({ list: [{ stock_id: 1 }], broken: true });
    expect(parseJsonList("[]")).toEqual({ list: [], broken: false });
  });
  it("문구는 보유·모름을 따로, 멀쩡하면 null (25.2385)", () => {
    expect(recordErrorText(false, false)).toBeNull();
    expect(recordErrorText(true, false)).toContain("보유가 없는 것이 아닙니다");
    expect(recordErrorText(false, true)).toContain("원가로 평가한 보유 목록");
    expect(recordErrorText(true, true)).toContain("보유 기록·원가 평가 목록");
  });
  it("화면 — 보유 탭이 깨짐이면 '보유 없음' 대신 그 문구, 탭 수는 '?', 요약에도 빨간 줄 (25.2385)", () => {
    const v = readFileSync(join(process.cwd(), "components", "PaperView.tsx"), "utf8");
    const 보유 = v.slice(v.indexOf("function HoldingsPane"));
    const 깨짐 = 보유.indexOf("if (a.positions_broken) return");
    expect(깨짐).toBeGreaterThanOrEqual(0); // 못 찾음(-1)도 "앞" 으로 통과하지 않게
    expect(깨짐).toBeLessThan(보유.indexOf("보유 종목이 없습니다"));
    expect(v).toContain('a.positions_broken ? "?" : a.positions.length');
    expect(v).toContain("{a.record_error ? <p");
  });
});
