/**
 * 위성 ETF 조회 테스트. 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import {
  buildSatelliteExcludedQuery,
  buildSatellitePassedQuery,
  groupSatellites,
  parseWarnings,
  type SatelliteRow,
} from "@/lib/etfSatellite";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

function pick(id: number, asOf: string, group: string, sub: string, passed: number, rank: number | null, version = 1) {
  db.prepare(
    `INSERT INTO etf_satellite_picks (etf_id, as_of_date, sat_group, sub_group, passed, excluded_reason, score,
       rank_in_group, group_size, rationale_text, rationale_data, calc_version, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 2, '문장', '{"criteria":[],"warnings":["위성은 핵심을 대신하지 않습니다"]}', ?, 't')`,
  ).run(id, asOf, group, sub, passed, passed ? null : "운용 10년 미만", passed ? 80 : null, rank, version);
}

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  const etfs: Array<[number, string, string, number]> = [
    [1, "XLK", "US", 1.2e11],
    [2, "VGT", "US", 1.7e11],
    [3, "XLC", "US", 2.4e10],
    [4, "139260", "KR", 2.6e12],
  ];
  for (const [id, symbol, country, assets] of etfs) {
    db.prepare(
      `INSERT INTO etfs (id, symbol, country, name, source, fetched_at) VALUES (?, ?, ?, ?, 't', 't')`,
    ).run(id, symbol, country, symbol);
    const asOf = country === "US" ? "2026-09-17" : "2026-09-16";
    db.prepare(
      `INSERT INTO etf_profiles (etf_id, as_of_date, total_assets, currency, source, fetched_at)
       VALUES (?, ?, ?, 'USD', 't', 't')`,
    ).run(id, asOf, assets);
  }
  pick(1, "2026-09-17", "업종", "정보기술", 1, 1);
  pick(2, "2026-09-17", "업종", "정보기술", 1, 2);
  pick(3, "2026-09-17", "업종", "커뮤니케이션", 0, null);
  pick(4, "2026-09-16", "업종", "코스피 200 정보기술", 1, 1);
});

describe("위성 질의 (실제 스키마)", () => {
  it("나라별로 최신 판정의 통과분만 읽는다", () => {
    const us = buildSatellitePassedQuery("US");
    const rows = db.prepare(us.sql).all(...us.args) as unknown as SatelliteRow[];
    expect(rows.map((r) => r.symbol)).toEqual(["XLK", "VGT"]);

    const kr = buildSatellitePassedQuery("KR");
    expect((db.prepare(kr.sql).all(...kr.args) as unknown as SatelliteRow[]).map((r) => r.symbol)).toEqual(["139260"]);
  });

  it("뺀 것에 사유가 실려 온다", () => {
    const q = buildSatelliteExcludedQuery("US");
    const rows = db.prepare(q.sql).all(...q.args) as unknown as SatelliteRow[];
    expect(rows[0].symbol).toBe("XLC");
    expect(rows[0].excluded_reason).toContain("10년");
  });
});

describe("묶기", () => {
  it("묶음 순서를 지키고 하위 묶음 안 순위로 자른다", () => {
    const q = buildSatellitePassedQuery("US");
    const passed = db.prepare(q.sql).all(...q.args) as unknown as SatelliteRow[];
    const x = buildSatelliteExcludedQuery("US");
    const excluded = db.prepare(x.sql).all(...x.args) as unknown as SatelliteRow[];
    const groups = groupSatellites(passed, excluded);
    expect(groups.map((g) => g.group)).toEqual(["배당", "업종", "테마"]);
    const sector = groups[1];
    expect(sector.total).toBe(2);
    expect(sector.subGroups[0].rows.map((r) => r.symbol)).toEqual(["XLK", "VGT"]);
    expect(sector.excluded.map((r) => r.symbol)).toEqual(["XLC"]);
  });

  it("경고 문구를 푼다", () => {
    expect(parseWarnings('{"warnings":["a","b"]}')).toEqual(["a", "b"]);
    expect(parseWarnings("깨짐")).toEqual([]);
  });
});
