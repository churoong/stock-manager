/**
 * KIS모의투자 실행 품질 (docs/paper.md 15.8, docs/infra.md 25.1504). 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { DIVERGE, QUALITY_SQL, qualityLine, qualitySeries, slipSeries, type QualityRow } from "@/lib/kisQuality";

const MIGRATIONS = join(process.cwd(), "..", "migrations");

describe("실행 품질 추이", () => {
  it("시장마다 날짜순, 요약의 칸을 꺼낸다 · 기간 밖·다른 시장은 빼고", () => {
    const db = new DatabaseSync(":memory:");
    for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
    const ins = db.prepare(
      "INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, diff_json, source, fetched_at, summary_json) VALUES (?, ?, 'H', '[]', '{}', 'kis_vts', 't', ?)",
    );
    const 요약 = (n: number, m: number | null, k: number | null) =>
      JSON.stringify({ n, match_pct: m, kept_pct: k, slip_buy: { n, avg: 0.3 }, slip_sell: { n: 0, avg: null }, rejected: 1, unknown: n === 25 ? 2 : 0 });
    ins.run("kis_vts", "2026-10-20", 요약(25, 84, 90));
    ins.run("kis_vts", "2026-10-19", 요약(0, null, null));
    ins.run("kis_vts", "2026-01-01", 요약(9, 50, 50)); // 기간 밖
    ins.run("kis_vts_us", "2026-10-20", 요약(3, 100, 100)); // 다른 시장
    const rows = db.prepare(QUALITY_SQL).all("kis_vts", "2026-06-01") as unknown as QualityRow[];
    expect(rows.map((r) => [r.as_of, r.n, r.match_pct, r.kept_pct, r.slip_buy, r.slip_sell])).toEqual([
      ["2026-10-19", 0, null, null, 0.3, null],
      ["2026-10-20", 25, 84, 90, 0.3, null],
    ]);
    // 그래프 — 끝난 주문이 없는 날은 빼고 % 를 소수로(그래프의 % 꼴)
    const s = qualitySeries(rows);
    expect(s.map((x) => x.label)).toEqual(["체결 일치율", "실현율(가상 체결이 KIS 에서도 찬 비율)"]);
    expect(s[0].points).toEqual([{ date: "2026-10-20", value: 0.84 }]);
    expect(slipSeries(rows).map((x) => x.label)).toEqual(["매수 가격 차이"]);
    expect(qualityLine(rows[1])).toBe(
      "2026-10-20 기준 지난 28일 끝난 주문 25 · 체결 일치 84% · 실현율 90%(가상 체결 가운데 KIS 에서도 찬 비율) · 모름 2건(일치·실현율에서 빠짐)",
    );
    expect(qualityLine(rows[0])).toContain("체결을 견줄 주문 없음 · 거부 1건"); // 25.1617 — 닫힌 주문 건수를 가리지 않는다
    expect(qualityLine(undefined)).toContain("아직");
    expect(qualityLine({ ...rows[1], kept_pct: null })).toContain("실현율 모름");
  });
});

describe("화면 글의 문턱은 배치 상수와 같다 (25.1507, 검토 11)", () => {
  it("broker_mirror.py 의 DIVERGE_*·COMPARE_DAYS", () => {
    const py = readFileSync(join(process.cwd(), "..", "batch", "services", "broker_mirror.py"), "utf8");
    const 값 = (name: string) => Number(new RegExp(`^${name} = ([0-9.]+)`, "m").exec(py)?.[1]);
    expect(값("DIVERGE_MATCH_PCT")).toBe(DIVERGE.MATCH_PCT);
    expect(값("DIVERGE_KEPT_PCT")).toBe(DIVERGE.KEPT_PCT);
    expect(값("DIVERGE_SLIP_PCT")).toBe(DIVERGE.SLIP_PCT);
    expect(값("DIVERGE_MIN_N")).toBe(DIVERGE.MIN_N);
    expect(값("COMPARE_DAYS")).toBe(DIVERGE.COMPARE_DAYS);
  });
});
