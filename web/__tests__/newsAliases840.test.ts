/** 국내 뉴스 별칭 매칭 (docs/infra.md 25.840, 0043) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { KR_ALIASES, matchStocks, withAliases } from "@/lib/newsKr";

describe("종목 별칭", () => {
  const 대상 = [{ stock_id: 1, name: "NAVER" }, { stock_id: 2, name: "신한지주" }];

  it("기사 표기가 약칭과 달라도 같은 종목으로 붙는다 — 한 번만", () => {
    const 이름들 = withAliases(대상, [{ stock_id: 1, alias: "네이버" }, { stock_id: 2, alias: "신한금융" }, { stock_id: 9, alias: "대상아님" }]);
    expect(matchStocks("네이버, 신한금융과 손잡고 NAVER 페이 확대", 이름들).map((s) => s.stock_id).sort()).toEqual([1, 2]);
    expect(이름들.some((s) => s.stock_id === 9)).toBe(false); // 대상이 아닌 종목의 별칭은 더하지 않는다
  });

  it("별칭도 약칭과 같은 규칙 — 다른 단어의 일부는 아니다", () => {
    const 이름들 = withAliases(대상, [{ stock_id: 1, alias: "네이버" }]);
    expect(matchStocks("네이버웹툰 상장 추진", 이름들)).toEqual([]);
  });

  it("마이그레이션 시드가 종목 코드로 붙고 질의가 읽는다", () => {
    const db = new DatabaseSync(":memory:");
    const 폴더 = join(process.cwd(), "..", "migrations");
    const 파일 = readdirSync(폴더).filter((x) => x.endsWith(".sql")).sort();
    for (const f of 파일.filter((x) => x < "0043")) db.exec(readFileSync(join(폴더, f), "utf-8"));
    db.exec(`INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
      VALUES (1, '035420', 'KOSPI', 'KR', 'NAVER', 'KRW', 'active', 't', 't')`);
    for (const f of 파일.filter((x) => x >= "0043")) db.exec(readFileSync(join(폴더, f), "utf-8"));
    expect(db.prepare(KR_ALIASES).all()).toEqual([{ stock_id: 1, alias: "네이버" }]);
  });

  it("두 글자 별칭은 넣지 않는다(오탐)", () => {
    const 시드 = readFileSync(join(process.cwd(), "..", "migrations", "0043_stock_aliases.sql"), "utf-8");
    for (const [, 별칭] of 시드.matchAll(/\('\d{6}', '([^']+)'\)/g)) expect(별칭.length).toBeGreaterThanOrEqual(3);
  });
});
