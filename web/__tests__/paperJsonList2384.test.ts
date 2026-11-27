// 평가 행 JSON 목록 칸 — 깨진 한 줄이 가상모의투자 화면 전체를 500 으로 만들지 않는다 (docs/infra.md 25.2384)
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { accountRecord, parseJsonList, recordErrorText, recordFields } from "@/lib/paper";

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
    expect(src).toContain("accountRecord(d.positions_json, d.unknown_json)");
    // 한 줄 전체를 본다 — `[^)]*` 는 첫 `)` 에서 멈춰 옛 꼴(`…find((d) => …)?.unknown_json`)을 못 잡았다(25.2385)
    expect(src).not.toMatch(/JSON\.parse\([^;\n]*positions_json/);
    expect(src).not.toMatch(/JSON\.parse\([^;\n]*unknown_json/);
    // 세 칸은 한 번에 펼친다 — 하나씩 옮겨 담는 줄이 남아 있으면 안 된다(25.2387)
    expect(src).toContain("...recordFields(기록들[a.id], 이름),");
    // 계좌 줄을 만드는 블록 안에서 네 칸을 따로 옮겨 담지 않는다(25.2388 — 보유 칸이 빠져 있었다). 따옴표·공백 꼴도
    const 블록 = src.slice(src.indexOf(">(accounts).map((a) => ({"), src.indexOf("}));", src.indexOf(">(accounts).map((a) => ({")));
    expect(블록.length).toBeGreaterThan(0);
    expect(블록).not.toMatch(/\b["']?(positions|unknown|positions_broken|record_error)["']?\s*:/);
    expect(src).toContain("[k, v.positions]");
  });
  it("객체가 아닌 원소는 걸러 내고 깨짐 (25.2385)", () => {
    expect(parseJsonList('[{"stock_id":1}, null, 3]')).toEqual({ list: [{ stock_id: 1 }], broken: true });
    expect(parseJsonList("[]")).toEqual({ list: [], broken: false });
  });
  it("문구는 보유·모름을 따로, 멀쩡하면 null (25.2385)", () => {
    expect(recordErrorText(false, false)).toBeNull();
    expect(recordErrorText(true, false)).toContain("보유가 없는 것이 아닙니다");
    expect(recordErrorText(false, true)).toContain("원가로 평가한 보유 목록");
    // 전체 문구로 — 꼬리만 보면 앞의 "보유가 없는 것이 아닙니다" 가 빠져도 몰랐다(25.2388)
    expect(recordErrorText(true, true)).toBe(
      "보유 기록을 읽지 못했습니다(평가 행의 JSON 이 깨짐) — 보유가 없는 것이 아닙니다 · 원가 평가 목록도 읽지 못했습니다");
    expect(recordErrorText(true, true, 2)).toBe(
      "보유 기록 일부를 읽지 못했습니다(평가 행의 JSON 이 깨짐) — 읽은 2줄만 보입니다 · 원가 평가 목록도 읽지 못했습니다");
    expect(recordErrorText(true, false, 2)).toBe("보유 기록 일부를 읽지 못했습니다(평가 행의 JSON 이 깨짐) — 읽은 2줄만 보입니다");
  });
  it("화면 — 보유 탭이 깨짐이면 '보유 없음' 대신 그 문구, 탭 수는 '?', 요약에도 빨간 줄 (25.2385)", () => {
    const v = readFileSync(join(process.cwd(), "components", "PaperView.tsx"), "utf8");
    const 보유 = v.slice(v.indexOf("function HoldingsPane"));
    const 깨짐 = 보유.indexOf("if (a.positions_broken && !a.positions.length) return");
    expect(깨짐).toBeGreaterThanOrEqual(0); // 못 찾음(-1)도 "앞" 으로 통과하지 않게
    expect(깨짐).toBeLessThan(보유.indexOf("보유 종목이 없습니다"));
    expect(v).toContain('a.positions_broken ? `${a.positions.length || ""}?` : a.positions.length');
    expect(v).toContain("{a.record_error ? <p");
  });
});


describe("accountRecord — 계좌 한 줄을 동작으로 (25.2386)", () => {
  it("보유만 깨짐 · 모름만 깨짐 · 둘 다 · 멀쩡", () => {
    const 보유깨짐 = accountRecord("x", '[{"stock_id":2}]');
    expect(보유깨짐.positions_broken).toBe(true);
    expect(보유깨짐.record_error).toContain("보유가 없는 것이 아닙니다");
    expect(보유깨짐.unknown).toEqual([{ stock_id: 2 }]);
    const 모름깨짐 = accountRecord('[{"stock_id":1}]', "x");
    expect(모름깨짐.positions_broken).toBe(false); // 인자를 뒤바꾸면 여기서 깨진다
    expect(모름깨짐.record_error).toContain("원가로 평가한 보유 목록");
    expect(모름깨짐.positions).toEqual([{ stock_id: 1 }]);
    expect(accountRecord("x", "x").record_error).toContain("원가 평가 목록도 읽지 못했습니다");
    expect(accountRecord('[{"stock_id":1}]', null)).toEqual({
      positions: [{ stock_id: 1 }], unknown: [], positions_broken: false, record_error: null,
    });
  });
  it("부분 깨짐은 읽은 줄을 남기고 깨짐 표시 — 화면은 빨간 줄 + 표", () => {
    const r = accountRecord('[{"stock_id":1}, null]', null);
    expect(r.positions).toEqual([{ stock_id: 1 }]);
    expect(r.positions_broken).toBe(true);
    expect(r.record_error).toBe("보유 기록 일부를 읽지 못했습니다(평가 행의 JSON 이 깨짐) — 읽은 1줄만 보입니다"); // 25.2387
    const v = readFileSync(join(process.cwd(), "components", "PaperView.tsx"), "utf8");
    expect(v).toContain("{a.positions_broken ? <p className=\"mb-1 text-xs text-rose-600\">{a.record_error}</p> : null}");
  });
});


describe("recordFields — 라우트가 펼치는 세 칸 (25.2387)", () => {
  it("평가 행이 있으면 그 값, 없으면 멀쩡한 빈 값", () => {
    expect(recordFields(accountRecord("x", '[{"stock_id":2}]'))).toEqual({
      positions: [], unknown: [{ stock_id: 2 }], positions_broken: true,
      record_error: "보유 기록을 읽지 못했습니다(평가 행의 JSON 이 깨짐) — 보유가 없는 것이 아닙니다",
    });
    expect(recordFields(undefined)).toEqual({ positions: [], unknown: [], positions_broken: false, record_error: null });
  });
  it("보유 칸은 기록의 보유(모름 아님)에 이름을 붙인다 (25.2388)", () => {
    const r = recordFields(accountRecord('[{"stock_id":1,"quantity":3}]', '[{"stock_id":9}]'), { 1: { ticker: "005930", name: "삼성전자" } });
    expect(r.positions).toEqual([{ stock_id: 1, quantity: 3, ticker: "005930", name: "삼성전자" }]);
    expect(r.unknown).toEqual([{ stock_id: 9 }]);
  });
});
