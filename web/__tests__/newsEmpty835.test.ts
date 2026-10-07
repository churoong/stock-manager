/** 뉴스 칸이 빌 때 왜 비었는지 (docs/infra.md 25.835) */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { type Exec, loadSection } from "@/lib/stockDetail";

function DB() {
  const db = new DatabaseSync(":memory:");
  const 폴더 = join(process.cwd(), "..", "migrations");
  for (const f of readdirSync(폴더).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(폴더, f), "utf-8"));
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at) VALUES
      (1, '000270', 'KOSPI', 'KR', '기아', NULL, 'KRW', 'active', 't', 't'),
      (2, '005930', 'KOSPI', 'KR', '삼성전자', NULL, 'KRW', 'active', 't', 't'),
      (3, '000660', 'KOSPI', 'KR', 'SK하이닉스', NULL, 'KRW', 'active', 't', 't');
    INSERT INTO watchlist (stock_id, added_at, alert_enabled) VALUES (3, 't', 1);
    INSERT INTO stocks (id, ticker, market, country, name_ko, name_en, currency, status, source, fetched_at) VALUES
      (4, '035420', 'KOSPI', 'KR', 'NAVER', NULL, 'KRW', 'active', 't', 't'),
      (5, '051910', 'KOSPI', 'KR', 'LG화학', NULL, 'KRW', 'delisted', 't', 't');
    INSERT INTO news_targets (market, stock_id, reason, built_at) VALUES ('KR', 4, 'top', 't');
    INSERT INTO watchlist (stock_id, added_at, alert_enabled) VALUES (5, 't', 1);
  `);
  const exec: Exec = async (sql, args = []) => db.prepare(sql).all(...(args as Array<string | number>)) as Record<string, unknown>[];
  return exec;
}

describe("뉴스 칸의 빈 사유", () => {
  it("두 글자 이름은 매칭하지 않는다고 말한다 — 칸이 접혀도 사유 줄로 보인다", async () => {
    expect(String((await loadSection(DB(), "news", 1, { country: "KR" })).reason)).toContain("이 종목에 매칭하지 않습니다");
  });
  it("수집 대상이 아니면 그렇다고, 대상이면 최근 기사가 없었다고", async () => {
    const exec = DB();
    expect((await loadSection(exec, "news", 2, { country: "KR" })).reason).toContain("수집 대상(점수 상위·보유·관심)이 아니라");
    expect((await loadSection(exec, "news", 3, { country: "KR" })).reason).toContain("수집 대상인데 최근 기사가 없었습니다");
    // 점수 상위(news_targets)도 대상, 폐지된 관심 종목은 크론이 보지 않는다 (25.838)
    expect((await loadSection(exec, "news", 4, { country: "KR" })).reason).toContain("수집 대상인데");
    expect((await loadSection(exec, "news", 5, { country: "KR" })).reason).toContain("수집 대상(점수 상위·보유·관심)이 아니라");
  });
});

describe("감성 값이 빈 까닭 (25.838)", () => {
  it("기사 수로 가른다 — 기사 12건에 '5건 미만' 이라 하지 않는다", async () => {
    const { sentimentNullReason } = await import("@/lib/stockDetail");
    expect(sentimentNullReason(3)).toBe("없음(기사 5건 미만)");
    expect(sentimentNullReason(12)).toContain("7일 넘게 새 기사 없음");
  });
});
