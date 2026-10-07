/**
 * 추천 질의를 **실제 스키마에 돌려 본다.**
 *
 * 다른 테스트는 SQL 문자열의 생김새만 본다. 그것으로는 열 이름이 틀렸거나
 * 조인이 어긋난 것을 잡지 못한다. 그런 실수는 화면을 열었을 때 500 으로
 * 드러나고, 배포한 뒤에야 알게 된다.
 *
 * 여기서는 migrations/ 를 그대로 적용한 SQLite 에 표본 행을 넣고 질의를
 * 실행한다. 네트워크도 자격 증명도 필요 없다.
 *
 * node:sqlite 는 Node 22 부터 들어 있는 기본 모듈이다. 새 의존성을 더하지
 * 않으려고 이것을 쓴다(비용 규칙과 별개로, 의존성은 적을수록 덜 깨진다).
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import {
  LAST_CALC_DATE,
  OUTCOME_STATS,
  PREVIOUS_AS_OF,
  SIGNALS_ON,
  signalsOnArgs,
  buildQuery,
  recommendQuerySchema,
} from "@/lib/recommend";

const MIGRATIONS = join(process.cwd(), "..", "migrations");

let db: DatabaseSync;

function applyMigrations(target: DatabaseSync): string[] {
  const files = readdirSync(MIGRATIONS)
    .filter((f) => f.endsWith(".sql"))
    .sort();
  for (const file of files) {
    target.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  return files;
}

/** 배치가 실제로 넣는 모양에 맞춘 표본 한 종목. */
function seed(target: DatabaseSync) {
  target.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 'krx_openapi', '2026-09-16');

    INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)
    VALUES (1, '2026-09-15', 248500, 'KRW', 'krx_openapi', '2026-09-16');

    INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores,
                        sentiment_weight_used, weights_json, rank_in_market,
                        calc_version, created_at)
    VALUES (1, '2026-09-16', 78.4, '{"value":83,"quality":71}', 0, '{}', 3, 1, '2026-09-16');

    INSERT INTO signals (stock_id, as_of_date, horizon, signal_type,
                         buy_zone_low, buy_zone_high, currency, tranche_plan,
                         target_price, stop_price, suggested_weight_pct,
                         suggested_amount, size_reduction, sector_cap_applied,
                         sector_cap_note, rationale_text, rationale_data,
                         calc_version, created_at)
    VALUES (1, '2026-09-16', 'mid', '실적 모멘텀',
            236075, 248500, 'KRW',
            '[{"step":1,"ratio":0.4,"price":248500,"amount":400000}]',
            302859, 205978, 10.0, 1000000, 1.0, 0,
            '섹터 상한 미적용 (업종 데이터 없음)',
            '매출 18.2%. 영업이익 24.1%',
            '{"growth":81,"criteria":[{"label":"성장 점수","display":"81점","threshold":"≥ 70점","source":"scores (팩터 점수)","as_of":"2026-09-16","passed":true}]}',
            1, '2026-09-16');
  `);
}

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  applyMigrations(db);
  seed(db);
});

describe("마이그레이션", () => {
  it("전부 순서대로 적용된다", () => {
    const fresh = new DatabaseSync(":memory:");
    const files = applyMigrations(fresh);

    // 번호 순서가 곧 적용 순서다. 0001 이 만든 표를 0004 가 고친다
    expect(files.length).toBeGreaterThanOrEqual(8);
    expect(files[0]).toMatch(/^0001_/);
  });

  it("추천에 필요한 표가 만들어진다", () => {
    const names = db
      .prepare("SELECT name FROM sqlite_master WHERE type = 'table'")
      .all()
      .map((r) => r.name as string);

    for (const table of ["stocks", "prices", "scores", "factors", "signals"]) {
      expect(names).toContain(table);
    }
  });
});

describe("추천 질의를 실제로 실행한다", () => {
  it("가격 표를 통째로 훑지 않는다 — 종목마다 인덱스로 찾는다 (docs/infra.md 25.586)", () => {
    const { sql, args } = buildQuery(recommendQuerySchema.parse({}), "2026-09-16");
    const plan = (db.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args) as Array<{ detail: string }>).map((r) => r.detail);
    expect(plan.filter((d) => /SCAN (prices|p2?\b)/.test(d)), plan.join("\n")).toEqual([]);
  });

  it("기본 조건으로 행이 나온다", () => {
    const { sql, args } = buildQuery(
      recommendQuerySchema.parse({}),
      "2026-09-16",
    );
    const rows = db.prepare(sql).all(...args);

    expect(rows).toHaveLength(1);
  });

  it("화면이 읽는 열이 전부 들어 있다", () => {
    // 열 이름이 하나라도 틀리면 화면에서 값이 조용히 비어 보인다
    const { sql, args } = buildQuery(
      recommendQuerySchema.parse({}),
      "2026-09-16",
    );
    const row = db.prepare(sql).all(...args)[0] as Record<string, unknown>;

    for (const key of [
      "stock_id",
      "ticker",
      "name",
      "market",
      "currency",
      "horizon",
      "signal_type",
      "buy_zone_low",
      "buy_zone_high",
      "target_price",
      "stop_price",
      "suggested_weight_pct",
      "suggested_amount",
      "size_reduction",
      "sector_cap_applied",
      "sector_cap_note",
      "rationale_text",
      "tranche_plan",
      "as_of_date",
      "total_score",
      "factor_scores",
      "rank_in_market",
      "close",
      "price_date",
      "rationale_data",
    ]) {
      expect(row).toHaveProperty(key);
    }
  });

  it("값이 제대로 실려 온다", () => {
    const { sql, args } = buildQuery(
      recommendQuerySchema.parse({}),
      "2026-09-16",
    );
    const row = db.prepare(sql).all(...args)[0] as Record<string, unknown>;

    expect(row.name).toBe("삼성전자");
    expect(row.close).toBe(248500);
    expect(row.total_score).toBe(78.4);
    expect(row.rationale_text).toContain("매출 18.2%");
    // 업종이 없으므로 섹터 상한 미적용이 그대로 실려야 한다
    expect(row.sector_cap_applied).toBe(0);
  });

  it("기간으로 거를 수 있다", () => {
    const hit = buildQuery(
      recommendQuerySchema.parse({ horizon: "mid" }),
      "2026-09-16",
    );
    expect(db.prepare(hit.sql).all(...hit.args)).toHaveLength(1);

    const miss = buildQuery(
      recommendQuerySchema.parse({ horizon: "short" }),
      "2026-09-16",
    );
    expect(db.prepare(miss.sql).all(...miss.args)).toHaveLength(0);
  });

  it("시장으로 거를 수 있다", () => {
    const miss = buildQuery(
      recommendQuerySchema.parse({ market: "KOSDAQ" }),
      "2026-09-16",
    );
    expect(db.prepare(miss.sql).all(...miss.args)).toHaveLength(0);
  });

  it("NULLS LAST 정렬이 이 SQLite 에서 동작한다", () => {
    // SQLite 3.30 부터 지원한다. 낮은 버전이면 여기서 구문 오류가 난다
    expect(() =>
      db.prepare("SELECT 1 ORDER BY 1 NULLS LAST").all(),
    ).not.toThrow();
  });

  it("점수가 없는 종목도 빠지지 않는다", () => {
    // scores 는 LEFT JOIN 이다. 점수가 없다고 신호가 사라지면 안 된다
    const local = new DatabaseSync(":memory:");
    applyMigrations(local);
    local.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
      VALUES (2, '000660', 'KOSPI', 'KR', '점수없음', 'KRW', 'active', 'krx_openapi', '2026-09-16');

      INSERT INTO signals (stock_id, as_of_date, horizon, signal_type,
                           buy_zone_low, buy_zone_high, currency, tranche_plan,
                           size_reduction, sector_cap_applied,
                           rationale_text, rationale_data, calc_version, created_at)
      VALUES (2, '2026-09-16', 'short', '기술적 추세', 100, 110, 'KRW', '[]',
              1.0, 0, '', '{}', 1, '2026-09-16');
    `);

    const { sql, args } = buildQuery(
      recommendQuerySchema.parse({}),
      "2026-09-16",
    );
    const rows = local.prepare(sql).all(...args);

    expect(rows).toHaveLength(1);
    expect((rows[0] as Record<string, unknown>).total_score).toBeNull();
  });
});

describe("계산 판을 올려 같은 날을 다시 내도 카드가 한 장 (docs/infra.md 25.378)", () => {
  it("같은 종목·기간·기준일의 옛 판은 읽지 않는다", () => {
    const d = new DatabaseSync(":memory:");
    applyMigrations(d);
    seed(d);
    d.exec(`INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,
      tranche_plan, target_price, stop_price, suggested_weight_pct, suggested_amount, size_reduction, sector_cap_applied,
      rationale_text, rationale_data, calc_version, created_at)
      SELECT stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency, tranche_plan, target_price,
             stop_price, suggested_weight_pct, suggested_amount, size_reduction, sector_cap_applied, rationale_text,
             rationale_data, calc_version + 1, created_at FROM signals`);
    const { sql, args } = buildQuery(
      recommendQuerySchema.parse({}),
      "2026-09-16",
    );
    expect(d.prepare(sql).all(...args)).toHaveLength(1);
    expect(d.prepare(SIGNALS_ON).all(...signalsOnArgs("KR", "2026-09-16"))).toHaveLength(1);
  });

  it("새 판에서 사라진 행은 옛 판으로 살아나지 않는다 (docs/infra.md 25.423)", () => {
    const d = new DatabaseSync(":memory:");
    applyMigrations(d);
    seed(d);
    // 옛 판(1)의 행에 판 2 로 다른 기간 하나를 더한다 — 판 2 에는 옛 행의 기간이 없다
    d.exec(`INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,
      tranche_plan, target_price, stop_price, suggested_weight_pct, suggested_amount, size_reduction, sector_cap_applied,
      rationale_text, rationale_data, calc_version, created_at)
      SELECT stock_id, as_of_date, CASE horizon WHEN 'short' THEN 'long' ELSE 'short' END, signal_type, buy_zone_low,
             buy_zone_high, currency, tranche_plan, target_price, stop_price, suggested_weight_pct, suggested_amount,
             size_reduction, sector_cap_applied, rationale_text, rationale_data, calc_version + 1, created_at FROM signals`);
    const { sql, args } = buildQuery(recommendQuerySchema.parse({}), "2026-09-16");
    expect(d.prepare(sql).all(...args)).toHaveLength(1);
    expect(d.prepare(SIGNALS_ON).all(...signalsOnArgs("KR", "2026-09-16"))).toHaveLength(1);
  });
});

describe("상태 조건 (docs/infra.md 25.802)", () => {
  it("제외·폐지된 종목의 신호는 카드에도 '새로 N건' 에도 없다", () => {
    const d = new DatabaseSync(":memory:");
    applyMigrations(d);
    seed(d);
    d.exec("UPDATE stocks SET status = 'delisted'");
    const { sql, args } = buildQuery(recommendQuerySchema.parse({}), "2026-09-16");
    expect(d.prepare(sql).all(...args)).toHaveLength(0);
    expect(d.prepare(SIGNALS_ON).all(...signalsOnArgs("KR", "2026-09-16"))).toHaveLength(0);
  });
});

describe("마지막 계산일 (docs/infra.md 25.145)", () => {
  /**
   * **`signals` 의 MAX 는 "마지막으로 계산한 날" 이 아니다.** 그 표에는 걸린 신호만
   * 들어가므로, 한 건도 안 걸린 날에는 어제가 기준일로 남는다. 그러면 화면이
   * **어제 신호를 오늘 추천처럼** 보여 주고, 판정표는 오늘 것만 남아 사라진다.
   *
   * 판정표는 신호가 없어도 전 종목에 남으므로(`jobs/signals`) 둘의 MAX 가 답이다.
   */
  function 새DB() {
    const local = new DatabaseSync(":memory:");
    applyMigrations(local);
    local.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
      VALUES (9, '000009', 'KOSPI', 'KR', '어떤종목', 'KRW', 'active', 't', 't');
    `);
    return local;
  }
  const 날 = (local: DatabaseSync) =>
    (local.prepare(LAST_CALC_DATE).all("KR", "KR")[0] as { d: string | null })
      .d;

  it("신호가 걸린 날에는 그 날이다", () => {
    const local = 새DB();
    local.exec(`INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high,
        currency, tranche_plan, size_reduction, sector_cap_applied, rationale_text, rationale_data, calc_version, created_at)
      VALUES (9, '2026-09-16', 'mid', 't', 1, 2, 'KRW', '[]', 1.0, 0, '', '{}', 1, 't')`);
    expect(날(local)).toBe("2026-09-16");
  });

  it("한 건도 안 걸린 날은 판정표가 말해 준다", () => {
    const local = 새DB();
    local.exec(`INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high,
        currency, tranche_plan, size_reduction, sector_cap_applied, rationale_text, rationale_data, calc_version, created_at)
      VALUES (9, '2026-09-15', 'mid', 't', 1, 2, 'KRW', '[]', 1.0, 0, '', '{}', 1, 't')`);
    local.exec(`INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json, calc_version, created_at)
      VALUES (9, '2026-09-16', 'mid', 0, 1, '[]', 1, 't')`);

    expect(날(local), "어제를 오늘이라고 말한다").toBe("2026-09-16");
  });

  it("그 날의 신호만 골라낸다 — 어제 것이 딸려 오지 않는다", () => {
    const local = 새DB();
    local.exec(`INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high,
        currency, tranche_plan, size_reduction, sector_cap_applied, rationale_text, rationale_data, calc_version, created_at)
      VALUES (9, '2026-09-15', 'mid', 't', 1, 2, 'KRW', '[]', 1.0, 0, '', '{}', 1, 't')`);
    local.exec(`INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json, calc_version, created_at)
      VALUES (9, '2026-09-16', 'mid', 0, 1, '[]', 1, 't')`);

    const { sql, args } = buildQuery(
      recommendQuerySchema.parse({}),
      날(local) ?? "",
    );
    expect(local.prepare(sql).all(...args)).toHaveLength(0);
  });

  it("아무것도 없으면 null — 0 으로도 오늘로도 지어내지 않는다", () => {
    expect(날(새DB())).toBeNull();
  });

  it("나라를 섞지 않는다", () => {
    const local = 새DB();
    local.exec(`
      INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)
      VALUES (10, 'AAPL', 'NASDAQ', 'US', '애플', 'USD', 'active', 't', 't');
      INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json, calc_version, created_at)
      VALUES (10, '2026-09-20', 'mid', 0, 1, '[]', 1, 't');
    `);
    expect(날(local), "미국 계산일이 국내로 새어 들어왔다").toBeNull();
  });
});

describe("마지막 배치 질의를 실제로 실행한다", () => {
  it("batch_runs 의 열 이름이 맞다", () => {
    // route.ts 의 질의와 같은 문장이다. 열 이름이 틀리면 여기서 잡힌다
    const local = new DatabaseSync(":memory:");
    applyMigrations(local);
    local.exec(`
      INSERT INTO batch_runs (job_name, market, started_at, finished_at, status)
      VALUES ('signals', 'KR', '2026-09-16T12:40:00Z', '2026-09-16T12:41:52Z', 'success'),
             ('signals', 'KR', '2026-09-16T13:00:00Z', NULL, 'failed');
    `);
    const row = local
      .prepare(
        "SELECT finished_at, market FROM batch_runs" +
          " WHERE job_name = 'signals' AND status = 'success'" +
          " ORDER BY finished_at DESC LIMIT 1",
      )
      .get() as Record<string, unknown>;

    expect(row.finished_at).toBe("2026-09-16T12:41:52Z");
    expect(row.market).toBe("KR");
  });
});

describe("어제와 비교 질의", () => {
  it("전 기준일과 그날의 신호를 읽는다", () => {
    db.exec(`
      INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency, tranche_plan,
                           size_reduction, rationale_text, rationale_data, calc_version, created_at)
      VALUES (1, '2026-09-15', 'short', '기술적 추세', 1, 2, 'KRW', '[]', 1.0, 'r', '{}', 1, 't')
    `);
    const prev = db
      .prepare(PREVIOUS_AS_OF)
      .get("KR", "2026-09-16", "KR", "2026-09-16") as {
      d: string | null;
    };
    expect(prev.d).toBe("2026-09-15");
    const rows = db.prepare(SIGNALS_ON).all(...signalsOnArgs("KR", "2026-09-15")) as Array<{
      stock_id: number;
      horizon: string;
      name: string;
    }>;
    expect(rows).toEqual([
      { stock_id: 1, horizon: "short", ticker: "005930", name: "삼성전자" },
    ]);
    // 가장 이른 기준일에는 전 기준일이 없다
    expect(
      (
        db
          .prepare(PREVIOUS_AS_OF)
          .get("KR", "2026-09-15", "KR", "2026-09-15") as {
          d: string | null;
        }
      ).d,
    ).toBeNull();
    db.exec("DELETE FROM signals WHERE as_of_date = '2026-09-15'");
  });

  it("신호가 0건이었던 계산일도 앞선 날로 친다 (docs/infra.md 25.384)", () => {
    db.exec(`
      INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency, tranche_plan,
                           size_reduction, rationale_text, rationale_data, calc_version, created_at)
      VALUES (1, '2026-09-14', 'short', '기술적 추세', 1, 2, 'KRW', '[]', 1.0, 'r', '{}', 1, 't');
      INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json, calc_version, created_at)
      VALUES (1, '2026-09-15', 'short', 0, 1, '[]', 1, 't');
    `);
    const prev = db
      .prepare(PREVIOUS_AS_OF)
      .get("KR", "2026-09-16", "KR", "2026-09-16") as { d: string | null };
    expect(prev.d).toBe("2026-09-15");
    db.exec(
      "DELETE FROM signals WHERE as_of_date = '2026-09-14'; DELETE FROM signal_checks WHERE as_of_date = '2026-09-15'",
    );
  });
});

describe("신호 성적표 질의", () => {
  it("실제 스키마에서 나라별로 읽고 기간·창 순이다", () => {
    db.exec(`INSERT INTO signal_outcome_stats (country, horizon, window_days, n, avg_ret, win_rate, computed_at)
      VALUES ('KR', 'mid', 20, 7, 0.02, 0.6, 't'), ('KR', 'short', 60, 9, 0.05, 0.5, 't'), ('US', 'short', 20, 3, NULL, NULL, 't')`);
    const rows = db.prepare(OUTCOME_STATS).all("KR") as Array<{
      horizon: string;
      window_days: number;
    }>;
    expect(rows.map((r) => `${r.horizon}:${r.window_days}`)).toEqual([
      "short:60",
      "mid:20",
    ]);
    db.exec("DELETE FROM signal_outcome_stats");
  });
});

describe("종합 점수는 신호 기준일 이하 (docs/infra.md 25.269)", () => {
  it("신호 뒤에 계산된 점수를 붙이지 않는다", () => {
    const d = new DatabaseSync(":memory:");
    applyMigrations(d);
    seed(d);
    d.exec(`INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used, weights_json,
              rank_in_market, calc_version, created_at)
            VALUES (1, '2026-09-17', 12.3, '{}', 0, '{}', 99, 1, 't')`);
    const { sql, args } = buildQuery(
      recommendQuerySchema.parse({}),
      "2026-09-16",
    );
    const rows = d.prepare(sql).all(...(args as never[])) as Array<{
      total_score: number;
    }>;
    expect(rows[0].total_score).toBe(78.4);
  });
});

describe("판 하위질의는 바깥 행을 가리키지 않는다 (docs/infra.md 25.428)", () => {
  it("상관 하위질의가 아니다 — 신호 행마다 그 나라 종목 표를 다시 훑지 않는다", () => {
    const d = new DatabaseSync(":memory:");
    applyMigrations(d);
    const 계획 = (sql: string, args: Array<string | number>) =>
      (d.prepare(`EXPLAIN QUERY PLAN ${sql}`).all(...args) as Array<{ detail: string }>).map((r) => r.detail).join("\n");
    // 신호 판 하위질의(`signals c`)의 바로 윗줄이 상관이 아니어야 한다. 점수 하위질의는 종목별 색인 조회라 따로다
    const 판줄 = (plan: string) => {
      const 줄 = plan.split("\n");
      const i = 줄.findIndex((l) => l.startsWith("SEARCH c USING INDEX idx_signals_date"));
      expect(i).toBeGreaterThan(0);
      return 줄[i - 1];
    };
    const { sql, args } = buildQuery(recommendQuerySchema.parse({}), "2026-09-16");
    expect(판줄(계획(sql, args))).toMatch(/^SCALAR SUBQUERY/);
    expect(판줄(계획(SIGNALS_ON, signalsOnArgs("KR", "2026-09-16")))).toMatch(/^SCALAR SUBQUERY/);
  });
});
