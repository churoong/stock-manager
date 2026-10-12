/** 우리 종목 집중 표시 (docs/etf.md 11.2·11.5, docs/infra.md 25.967·25.968). 배치가 적은 값을 글로 옮기기만 한다. */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import { TILT_LEADERS_KR_ROWS, buildTiltLeadersQuery } from "@/lib/etf";
import { onePerIndex, parseTilt, tiltLine, tiltSource, type Tilt } from "@/lib/etfTilt";

function tilt(over: Partial<Tilt> = {}): Tilt {
  return {
    avg_score: 61.4, coverage_pct: 88.2, matched_pct: 99.3, total_pct: 100.1, rec_pct: 3.2, top_pct: 42.3, holdings: 30,
    top: [{ stock_id: 1, symbol: "NVDA", name: "Nvidia", weight_pct: 9.5, score: 82 }],
    vs_market: 9.0, benchmark: "VTI", rank: null, rank_size: null, source_symbol: "SOXX", proxy: false,
    accession: "0002071691-26-019760", filed: "2026-08-28", report_date: "2026-06-30", score_as_of: "2026-10-02",
    signals_as_of: "2026-10-02", coverage_min_pct: 70, rank_all: 1, rank_all_size: 520, market_top_pct: 14.1,
    top_share_pct: 10, top_cut_score: 63.2, pool: "wide", ...over,
  };
}

describe("tiltLine", () => {
  it("상위 비중이 먼저 — 시장 대비 배수·전체 순위, 평균은 뒤", () => {
    expect(tiltLine(tilt())).toBe(
      "우리 상위 10% 종목 비중 42% (VTI 14%의 3.0배) · 미국 주식형 520개 중 1위 · 평균 61점 (VTI 대비 +9)",
    );
  });

  it("순위는 무리 안에서라고 적는다 (25.970·25.974)", () => {
    expect(tiltLine(tilt({ proxy: true, rank_all: 2, rank_all_size: 30 }))).toContain("국내 상장 미국 지수 30개 중 2위");
    expect(tiltLine(tilt({ group: "kr_kr", proxy: false, benchmark: "KODEX 200", rank_all: 1, rank_all_size: 12 }))).toContain(
      "국내 상장 국내 지수 12개 중 1위",
    );
  });

  it("KODEX 구성종목이 출처면 그렇게 적는다 (25.974)", () => {
    const t = tilt({ source: "samsungfund_kodex", source_symbol: "069500", proxy: true, report_date: "2026-10-02", group: "kr_kr" });
    expect(tiltSource(t)).toContain("출처 삼성자산운용 KODEX(069500) 구성종목 2026-10-02");
    expect(tiltLine(t)).toContain("같은 지수 KODEX(069500) 보유로");
  });

  it("KIS 상위 30 이 출처면 전 종목이 아니라고 적는다 (25.990)", () => {
    const t = tilt({ source: "kis_openapi", source_symbol: "069500", proxy: true, report_date: "2026-10-07", group: "kr_kr", holdings: 30 });
    expect(tiltSource(t)).toContain("출처 한국투자증권 KIS 069500 구성종목 상위 30개(전 종목 아님) 2026-10-07");
    expect(tiltLine(t)).toContain("같은 지수 KODEX(069500) 보유로");
  });

  it("국내 대리 보유는 어느 ETF 의 보유인지 적는다", () => {
    expect(tiltLine(tilt({ proxy: true, source_symbol: "IVV", rank_all: null, rank_all_size: null }))).toBe(
      "우리 상위 10% 종목 비중 42% (VTI 14%의 3.0배) · 평균 61점 (VTI 대비 +9) · 같은 지수 IVV 보유로",
    );
  });

  it("덮은 비중이 모자라면 지어내지 않고 까닭을 적는다", () => {
    expect(tiltLine(tilt({ avg_score: null, coverage_pct: 31.4, source_symbol: "VXUS" }))).toBe(
      "우리 점수 — 점수 있는 종목이 비중 31%뿐이라 내지 않음 (하한 70%)",
    );
  });

  it("근거 — 공시 번호·기준일·점수 기준일", () => {
    const s = tiltSource(tilt());
    expect(s).toContain("SEC N-PORT SOXX 공시 0002071691-26-019760 (기준 2026-06-30, 공시 2026-08-28)");
    expect(s).toContain("점수 기준일 2026-10-02");
    expect(s).toContain("백테스트 전 참고");
  });

  it("없거나 깨졌으면 null", () => {
    expect(parseTilt(null)).toBeNull();
    expect(parseTilt(JSON.stringify({ tilt: { coverage_pct: 1 } }))).toBeNull();
    expect(parseTilt("{")).toBeNull();
  });
});

describe("우리 종목 집중 목록", () => {
  it("미국 최신 판정에서 배치가 매긴 전체 순위대로 — 핵심 통과를 거르지 않는다", () => {
    const q = buildTiltLeadersQuery(30);
    expect(q.sql).toContain("$.tilt.rank_all') IS NOT NULL");
    expect(q.sql).toContain("ORDER BY json_extract(pk.rationale_data, '$.tilt.rank_all')");
    expect(q.sql).not.toContain("pk.passed = 1");
    expect(q.args).toEqual([30]);
    // 국내 상장은 국내끼리 매긴 순위를 따로 읽는다 (25.970)
    const kr = buildTiltLeadersQuery(30, "KR");
    expect(kr.sql).toContain("e.country = 'KR'");
    expect(kr.sql).not.toContain("e.country = 'US'");
  });

  it("국내는 무리마다 따로 잘라 한 무리가 다른 무리의 자리를 먹지 않는다 (docs/infra.md 25.1201)", () => {
    const db = new DatabaseSync(":memory:");
    const MIG = join(process.cwd(), "..", "migrations");
    for (const f of readdirSync(MIG).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIG, f), "utf-8"));
    // 국내 상장 미국 지수 40상품(순위 1~40), 국내 지수 10상품(순위 1~10)
    let id = 0;
    for (const [group, n] of [["kr_us", 40], ["kr_kr", 10]] as const) {
      for (let r = 1; r <= n; r++) {
        id += 1;
        db.prepare("INSERT INTO etfs (id, symbol, country, name, source, fetched_at) VALUES (?, ?, 'KR', ?, 't', 't')")
          .run(id, `${group}${r}`, `${group}${r}`);
        db.prepare(
          "INSERT INTO etf_picks (etf_id, as_of_date, bucket, passed, rationale_text, rationale_data, calc_version, created_at)" +
            " VALUES (?, '2026-10-01', 'x', 0, 'x', ?, 5, 't')",
        ).run(id, JSON.stringify({ tilt: { rank_all: r, group } }));
      }
    }
    const 한번 = buildTiltLeadersQuery(30, "KR");
    const 섞음 = db.prepare(한번.sql).all(...한번.args) as Array<{ symbol: string }>;
    expect(섞음.filter((r) => r.symbol.startsWith("kr_kr")).length).toBe(10); // 30행 안에 국내 지수는 10개 다 들지만
    expect(섞음.filter((r) => r.symbol.startsWith("kr_us")).length).toBe(20); // 미국 지수는 20위에서 잘렸다
    for (const [group, 기대] of [["kr_us", 40], ["kr_kr", 10]] as const) {
      const q = buildTiltLeadersQuery(TILT_LEADERS_KR_ROWS, "KR", group);
      const rows = db.prepare(q.sql).all(...q.args) as Array<{ symbol: string }>;
      expect(rows.length).toBe(기대);
      expect(rows.every((r) => r.symbol.startsWith(group))).toBe(true);
    }
    const 경로 = readFileSync(join(process.cwd(), "app", "api", "etf", "route.ts"), "utf-8");
    expect(경로).toContain("buildTiltLeadersQuery(TILT_LEADERS_KR_ROWS, \"KR\", group)");
  });

  it("장기 적립 탭이 첫 보기로 그리고, 넓은 지수가 아니면 경고를 단다", () => {
    const src = readFileSync(join(process.cwd(), "components", "EtfList.tsx"), "utf-8");
    expect(src).toContain('{ key: "focus", label: "우리 종목 집중" }');
    expect(src).toContain('t?.pool === "wide"');
    expect(src).toContain("<FocusEtfList country={country} />");
    expect(src).toContain("tiltLine(tilt)");
  });
});

describe("onePerIndex", () => {
  it("국내 상장은 지수마다 첫(가장 큰) 상품만, 나머지는 이름으로 (25.973)", () => {
    const rows = [
      { category: "MVIS US Listed Semiconductor 25 Index", name: "KODEX 미국반도체" },
      { category: "NASDAQ 100", name: "TIGER 미국나스닥100" },
      { category: "NASDAQ 100", name: "KODEX 미국나스닥100" },
      { category: "S&P 500", name: "TIGER 미국S&P500" },
      { category: "NASDAQ 100", name: "ACE 미국나스닥100" },
    ];
    expect(onePerIndex(rows).map((x) => [x.row.name, x.others])).toEqual([
      ["KODEX 미국반도체", []],
      ["TIGER 미국나스닥100", ["KODEX 미국나스닥100", "ACE 미국나스닥100"]],
      ["TIGER 미국S&P500", []],
    ]);
  });
});
