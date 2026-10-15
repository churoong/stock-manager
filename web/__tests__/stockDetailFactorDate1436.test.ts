import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { LATEST_FACTORS } from "@/lib/stockDetail";

// 팩터 표는 점수 행과 같은 날 (docs/infra.md 25.1436, 감사) — 점수 쓰기가 실패한 날 두 날짜가 섞였다
describe("팩터 표 날짜", () => {
  function db() {
    const d = new DatabaseSync(":memory:");
    d.exec(`CREATE TABLE scores (stock_id INT, as_of_date TEXT, calc_version INT);
      CREATE TABLE factors (stock_id INT, factor TEXT, raw_json TEXT, zscore REAL, score REAL, peer_group TEXT, peer_size INT,
        missing_fields TEXT, as_of_date TEXT, calc_version INT);
      INSERT INTO factors VALUES (1, 'value', '{}', 0, 40, 'g', 30, '[]', '2026-10-13', 1), (1, 'value', '{}', 0, 70, 'g', 30, '[]', '2026-10-14', 1);`);
    return d;
  }

  it("점수가 D−1 이면 팩터도 D−1", () => {
    const d = db();
    d.exec("INSERT INTO scores VALUES (1, '2026-10-13', 1)");
    expect(d.prepare(LATEST_FACTORS).all(1, 1, 1)).toMatchObject([
      { as_of_date: "2026-10-13", score: 40 },
    ]);
  });

  it("점수 행이 없으면 팩터 최신 날", () => {
    expect(db().prepare(LATEST_FACTORS).all(1, 1, 1)).toMatchObject([
      { as_of_date: "2026-10-14", score: 70 },
    ]);
  });
});
