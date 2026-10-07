/**
 * 매매 기록·포트폴리오 조회 테스트. 질의는 실제 마이그레이션을 적용한 SQLite 에 돌린다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  BUY_COST_UNTIL,
  HELD_QUANTITY,
  dividendSizeCheck,
  signedInt,
  SNAPSHOT_AT,
  STOCK_SEARCH,
  TRADES_VERSION,
  TRADE_INSERT,
  fxUsable,
  isFutureDate,
  money,
  parseJson,
  pct,
  requestRecalc,
  signedWon,
  stockSearchArgs,
  tradeInputSchema,
  won,
} from "@/lib/portfolio";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
let db: DatabaseSync;

beforeAll(() => {
  db = new DatabaseSync(":memory:");
  for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(join(MIGRATIONS, file), "utf-8"));
  }
  db.exec(`
    INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, yahoo_symbol)
    VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't', '005930.KS');
    INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at, yahoo_symbol)
    VALUES (2, 'AAPL', 'NASDAQ', 'US', 'Apple Inc.', 'USD', 'active', 't', 't', 'AAPL');
    INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used, weights_json, calc_version, created_at)
    VALUES (1, '2026-09-10', 71.5, '{"value": 80}', 0, '{}', 1, 't'), (1, '2026-09-16', 60.0, '{}', 0, '{}', 1, 't');
    INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency, tranche_plan, size_reduction, sector_cap_applied,
      rationale_text, rationale_data, calc_version, created_at)
    VALUES (1, '2026-09-10', 'long', '밸류에이션 밴드', 1, 2, 'KRW', '[]', 1, 0, 'x', '{}', 1, 't');
  `);
});

afterEach(() => {
  vi.unstubAllEnvs();
});

function insertTrade(values: unknown[]) {
  db.prepare(TRADE_INSERT).run(...(values as never[]));
}

describe("입력 검증", () => {
  it("가격·수량은 0 보다 커야", () => {
    const base = { stock_id: 1, side: "buy", trade_date: "2026-09-10", price: 70000, quantity: 10 };
    expect(tradeInputSchema.safeParse(base).success).toBe(true);
    expect(tradeInputSchema.safeParse({ ...base, price: 0 }).success).toBe(false);
    expect(tradeInputSchema.safeParse({ ...base, quantity: -1 }).success).toBe(false);
    expect(tradeInputSchema.safeParse({ ...base, trade_date: "2026/09/10" }).success).toBe(false);
  });

  it("미래 날짜는 한국 시각 기준", () => {
    const now = new Date("2026-09-17T16:00:00Z"); // 한국 18일 01시
    expect(isFutureDate("2026-09-18", now)).toBe(false);
    expect(isFutureDate("2026-09-19", now)).toBe(true);
  });

  it("환율은 7일 이내만 자동으로", () => {
    expect(fxUsable("2026-09-10", "2026-09-17")).toBe(true);
    expect(fxUsable("2026-09-09", "2026-09-17")).toBe(false);
    expect(fxUsable(null, "2026-09-17")).toBe(false);
  });
});

describe("질의", () => {
  it("종목 찾기: 티커 정확히 맞으면 먼저", () => {
    const rows = db.prepare(STOCK_SEARCH).all(...stockSearchArgs("aapl")) as Array<{ ticker: string }>;
    expect(rows[0].ticker).toBe("AAPL");
    const kr = db.prepare(STOCK_SEARCH).all(...stockSearchArgs("삼성")) as Array<{ ticker: string }>;
    expect(kr.map((r) => r.ticker)).toEqual(["005930"]);
  });

  it("거래한 종목은 폐지돼도 찾는다 — 정리매매 매도를 적을 수 있게 (25.643)", () => {
    const 찾기 = () => (db.prepare(STOCK_SEARCH).all(...stockSearchArgs("삼성")) as Array<{ ticker: string; status: string }>);
    db.prepare("UPDATE stocks SET status = 'delisted' WHERE id = 1").run();
    try {
      expect(찾기()).toEqual([]); // 거래한 적 없으면 예전처럼 안 나온다
      db.prepare(
        "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, created_at, updated_at)" +
          " VALUES (9901, 1, 'buy', '2026-09-01', 70000, 1, 'KRW', 1, 'none', 't', 't')",
      ).run();
      expect(찾기().map((r) => [r.ticker, r.status])).toEqual([["005930", "delisted"]]);
    } finally {
      db.prepare("DELETE FROM trades WHERE id = 9901").run();
      db.prepare("UPDATE stocks SET status = 'active' WHERE id = 1").run();
    }
  });

  it("매수 근거 스냅샷: 그날 이전 가장 최근 점수와 같은 날 신호", () => {
    const row = db.prepare(SNAPSHOT_AT).get("long", 1, "2026-09-12") as Record<string, unknown>;
    expect(row.as_of_date).toBe("2026-09-10");
    expect(row.total_score).toBe(71.5);
    expect(row.signal_type).toBe("밸류에이션 밴드");
  });

  it("매수 근거는 체결일 전 점수 — 체결일 당일 점수는 다음 날 아침에야 나온다 (25.678)", () => {
    const 당일 = db.prepare(SNAPSHOT_AT).get("long", 1, "2026-09-16") as Record<string, unknown>;
    expect(당일.as_of_date).toBe("2026-09-10");
    const 다음날 = db.prepare(SNAPSHOT_AT).get("long", 1, "2026-09-17") as Record<string, unknown>;
    expect(다음날.as_of_date).toBe("2026-09-16");
  });

  it("같은 날 계산 판이 둘이면 새 판의 점수를 얼린다 (25.210)", () => {
    db.exec(`INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used, weights_json, calc_version, created_at)
      VALUES (2, '2026-09-10', 40, '{}', 0, '{}', 1, 't'), (2, '2026-09-10', 65, '{}', 0, '{}', 2, 't')`);
    const row = db.prepare(SNAPSHOT_AT).get("long", 2, "2026-09-12") as Record<string, unknown>;
    expect(row.total_score).toBe(65);
  });

  it("넣는 값 개수가 열 개수와 같고, 보유 수량은 그날까지만 센다", () => {
    const now = "2026-09-17T00:00:00Z";
    insertTrade([1, "buy", "2026-09-10", 70000, 10, "KRW", 1, "none", null, null, "long", null, null, null, null, null, null, now, now]);
    insertTrade([1, "sell", "2026-09-15", 72000, 4, "KRW", 1, "none", null, null, "long", null, null, null, null, null, null, now, now]);
    const held = (at: string) => (db.prepare(HELD_QUANTITY).get(1, at, -1) as { held: number }).held;
    expect(held("2026-09-12")).toBe(10);
    expect(held("2026-09-16")).toBe(6);
  });

  it("원본 지문은 배치(batch/jobs/portfolio.trades_version)와 같은 모양", () => {
    const row = db.prepare(TRADES_VERSION).get() as { version: string };
    expect(row.version).toMatch(/^t2:2026-09-17T00:00:00Z\|d0:$/);
  });
});

describe("재계산 요청", () => {
  it("토큰이 없으면 부르지 않고 이유를 준다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "");
    const fake = vi.fn();
    const r = await requestRecalc(fake as unknown as typeof fetch);
    expect(r.dispatched).toBe(false);
    expect(fake).not.toHaveBeenCalled();
  });

  it("event_type 은 워크플로의 portfolio 와 같다", async () => {
    vi.stubEnv("GH_DISPATCH_TOKEN", "x");
    vi.stubEnv("GH_REPO", "owner/repo");
    const fake = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const r = await requestRecalc(fake as unknown as typeof fetch);
    expect(r.dispatched).toBe(true);
    const [url, init] = fake.mock.calls[0];
    expect(url).toBe("https://api.github.com/repos/owner/repo/dispatches");
    expect(JSON.parse(init.body).event_type).toBe("portfolio");
    const workflow = readFileSync(join(process.cwd(), "..", ".github", "workflows", "portfolio.yml"), "utf-8");
    expect(workflow).toContain("types: [portfolio]");
  });
});

/**
 * 돈과 비율을 글자로 (2026-09-21 추가).
 *
 * **왜 이제서야.** 웹 덮임을 재 보니 `lib/portfolio.ts` 가 가지 60% 도 안 됐고, 빈 곳이
 * 하필 **사용자가 눈으로 읽는 숫자**였다. 들여다보니 둘이 나왔다.
 *
 * 1. `Math.round(-0.4)` 는 자바스크립트에서 `-0` 이고 `(-0).toLocaleString()` 은 `"-0"`
 *    이다. 400원 손실이 반올림으로 사라졌는데 화면에는 **`-0원`** 이 남았다
 * 2. `null`·`undefined` 만 걸렀다. 나눗셈이 어긋나 `NaN` 이 흘러오면 **`NaN원`** 이 떴다.
 *    사람은 그걸 "값이 없다" 로 읽지 않고 "앱이 고장 났다" 로 읽는다
 */
describe("돈 표시", () => {
  it("천 단위를 끊고 원을 붙인다", () => {
    expect(won(1234567)).toBe("1,234,567원");
  });

  it("소수는 반올림한다", () => {
    expect(won(1234.5)).toBe("1,235원");
    expect(won(1234.4)).toBe("1,234원");
  });

  it("없으면 '-' 다", () => {
    expect(won(null)).toBe("-");
    expect(won(undefined)).toBe("-");
  });

  it("숫자가 아닌 값이 흘러와도 'NaN원' 을 띄우지 않는다", () => {
    expect(won(NaN)).toBe("-");
    expect(won(Infinity)).toBe("-");
    expect(won(-Infinity)).toBe("-");
  });

  it("반올림해서 0 이면 '-0원' 이 아니라 '0원' 이다", () => {
    expect(won(-0.4)).toBe("0원");
    expect(won(-0)).toBe("0원");
  });

  it("부호 표시는 는 것에만 + 를 붙인다", () => {
    expect(signedWon(1500)).toBe("+1,500원");
    expect(signedWon(-1500)).toBe("-1,500원");
    expect(signedWon(0)).toBe("0원");
  });

  it("반올림해서 0 이면 +0원 도 -0원 도 아니다", () => {
    // "+0원" 은 늘었다는 말도 아니고 아니라는 말도 아니다
    expect(signedWon(0.4)).toBe("0원");
    expect(signedWon(-0.4)).toBe("0원");
  });

  it("없거나 숫자가 아니면 '-'", () => {
    expect(signedWon(null)).toBe("-");
    expect(signedWon(NaN)).toBe("-");
  });
});

describe("통화", () => {
  it("원화는 정수로", () => {
    expect(money(1234.6, "KRW")).toBe("1,235원");
  });

  it("달러는 소수 두 자리로", () => {
    expect(money(1234.5, "USD")).toBe("$1,234.50");
    expect(money(0.1, "USD")).toBe("$0.10");
  });

  it("없거나 숫자가 아니면 '-'", () => {
    expect(money(null, "USD")).toBe("-");
    expect(money(NaN, "USD")).toBe("-");
  });
});

describe("비율", () => {
  it("비율을 퍼센트로 바꾼다", () => {
    expect(pct(0.1234)).toBe("+12.3%");
    expect(pct(-0.1234)).toBe("-12.3%");
    expect(pct(0)).toBe("0.0%");
  });

  it("자릿수를 정할 수 있다", () => {
    expect(pct(0.1234, 2)).toBe("+12.34%");
    expect(pct(0.1234, 0)).toBe("+12%");
  });

  it("반올림해서 0 이면 부호를 붙이지 않는다", () => {
    // +0.0% 와 -0.0% 는 둘 다 "0.0%" 다
    expect(pct(0.00001)).toBe("0.0%");
    expect(pct(-0.00001)).toBe("0.0%");
  });

  it("없거나 숫자가 아니면 '-'", () => {
    expect(pct(null)).toBe("-");
    expect(pct(NaN)).toBe("-");
  });
});

describe("저장된 JSON 풀기", () => {
  it("문자열을 푼다", () => {
    expect(parseJson('{"a":1}', null)).toEqual({ a: 1 });
  });

  it("깨져 있으면 기본값으로 돌아간다", () => {
    // 화면이 무너지는 것보다 빈 값이 낫다
    expect(parseJson("{깨짐", [])).toEqual([]);
  });

  it("문자열이 아니거나 비어 있으면 기본값", () => {
    expect(parseJson(null, "기본")).toBe("기본");
    expect(parseJson("", "기본")).toBe("기본");
    expect(parseJson(42, "기본")).toBe("기본");
  });
});

describe("매매·배당 폼은 보이는 칸만 보낸다 (docs/infra.md 25.265)", () => {
  it("매수로 바꾸면 매도에서 적은 세금을 싣지 않는다", async () => {
    const { tradeBody } = await import("@/lib/portfolio");
    const 본문 = tradeBody({
      stockId: 1, currency: "KRW", side: "buy", date: "2026-09-25", price: 1000, quantity: 1,
      fx: 1400, fee: null, tax: -1, horizon: "long", memo: "",
    });
    expect(본문.tax).toBeNull();
    expect(본문.fx_rate).toBeNull(); // 원화 종목에는 환율 칸이 없다
  });

  it("매도·달러 종목이면 그대로 싣는다", async () => {
    const { tradeBody, dividendBody } = await import("@/lib/portfolio");
    const 본문 = tradeBody({
      stockId: 1, currency: "USD", side: "sell", date: "2026-09-25", price: 10, quantity: 1,
      fx: 1400, fee: 1, tax: 2, horizon: "long", memo: "m",
    });
    expect([본문.tax, 본문.fx_rate, 본문.memo]).toEqual([2, 1400, "m"]);
    expect(dividendBody({ stockId: 1, currency: "KRW", date: "2026-09-25", gross: 100, tax: null, fx: 1400 }).fx_rate).toBeNull();
  });
});

it("폼이 그 함수로 본문을 만든다 (25.265)", async () => {
  const { readFileSync } = await import("node:fs");
  const 글 = readFileSync(`${process.cwd()}/components/PortfolioView.tsx`, "utf-8");
  expect(글).toContain("JSON.stringify(tradeBody(");
  expect(글).toContain("JSON.stringify(dividendBody(");
});

it("종목 상세의 보유 금액은 억 단위로 반올림하지 않는다 (25.267)", async () => {
  const { readFileSync } = await import("node:fs");
  const 글 = readFileSync(`${process.cwd()}/components/StockDetail.tsx`, "utf-8");
  const 시작 = 글.indexOf("function PositionBlock");
  const 블록 = 글.slice(시작, 글.indexOf("\nfunction ", 시작 + 10));
  expect(시작).toBeGreaterThan(0);
  expect(블록, "보유 금액에 재무제표용 formatAmount 를 쓰지 않는다").not.toContain("formatAmount(");
  const { money, signedWon } = await import("@/lib/portfolio");
  expect(money(149_000_000, "KRW")).toBe("149,000,000원");
  expect(signedWon(100_000_001)).toBe("+100,000,001원");
});

it("달러는 어느 화면이든 소수 둘째 자리까지 채운다 (25.270)", async () => {
  // $187.5 와 $187.50 이 화면마다 달랐다 — 텔레그램·포트폴리오·종목 상세는 $187.50
  const { readdirSync, readFileSync } = await import("node:fs");
  const { join } = await import("node:path");
  const 걸린: string[] = [];
  for (const d of ["components", "lib"]) {
    for (const f of readdirSync(join(process.cwd(), d))) {
      if (!/\.tsx?$/.test(f)) continue;
      const 글 = readFileSync(join(process.cwd(), d, f), "utf-8");
      for (const m of 글.matchAll(/toLocaleString\("en-US", \{([^}]*)\}\)/g)) {
        if (/maximumFractionDigits: 2/.test(m[1]) && !/minimumFractionDigits: 2/.test(m[1])) 걸린.push(`${d}/${f}`);
      }
    }
  }
  expect(걸린).toEqual([]);
});

it("업종 구성과 상한의 분모를 이름으로 가른다 (25.273)", async () => {
  const { readFileSync } = await import("node:fs");
  const 글 = readFileSync(`${process.cwd()}/components/PortfolioView.tsx`, "utf-8");
  expect(글).toContain("업종 구성(보유 대비)");
  expect(글).toContain("총 투자가능금액의");
});

describe("수수료·세금이 체결금액보다 크면 거절한다 (docs/infra.md 25.399)", () => {
  const 기본 = { stock_id: 1, side: "buy" as const, trade_date: "2026-09-25", price: 100, quantity: 10 };

  it("미국 종목에 원화로 적은 수수료를 잡는다", () => {
    const r = tradeInputSchema.safeParse({ ...기본, fee: 1500 });
    expect(r.success).toBe(false);
    expect(r.error?.issues[0].path).toEqual(["fee"]);
    expect(r.error?.issues[0].message).toContain("종목 통화");
  });

  it("세금도 같다", () => {
    expect(tradeInputSchema.safeParse({ ...기본, side: "sell", tax: 1001 }).success).toBe(false);
  });

  it("체결금액 이하는 받는다", () => {
    expect(tradeInputSchema.safeParse({ ...기본, fee: 1000, tax: 0 }).success).toBe(true);
    expect(tradeInputSchema.safeParse({ ...기본, fee: null }).success).toBe(true);
  });
});

describe("배당 크기를 넣은 원금과 견준다 (docs/infra.md 25.404)", () => {
  it("원금보다 큰 배당은 통화 오타로 거절한다", () => {
    // AAPL 10주 약 $1,000 에 배당 3,500(원화로 착각)
    const r = dividendSizeCheck(3500, 1000, "USD");
    expect(r.error).toContain("종목 통화(USD)");
  });

  it("원금의 20% 를 넘으면 저장하되 경고한다", () => {
    const r = dividendSizeCheck(300, 1000, "USD");
    expect(r.error).toBeUndefined();
    expect(r.warning).toContain("특별배당");
  });

  it("보통 배당·매수 기록이 없으면 말하지 않는다", () => {
    expect(dividendSizeCheck(8.5, 1000, "USD")).toEqual({});
    expect(dividendSizeCheck(3500, 0, "USD")).toEqual({});
  });

  it("원금 질의는 지급일까지의 매수만 더한다", () => {
    const row = db.prepare(BUY_COST_UNTIL).get(999, "2026-09-10") as { cost: number };
    expect(row.cost).toBe(0);
  });

  it("배당 경로가 이 검사를 쓴다", () => {
    const 원본 = readFileSync(join(process.cwd(), "app", "api", "dividends", "route.ts"), "utf-8");
    expect(원본).toContain("dividendSizeCheck(input.gross_amount, 원금, stock.currency)");
    expect(원본).toContain("if (크기.error)");
  });
});

describe("부호 있는 점수는 -0 으로 찍지 않는다 (docs/infra.md 25.432)", () => {
  it("반올림한 뒤 적는다", () => {
    expect(signedInt(-0.3)).toBe("0");
    expect(signedInt(-0.6)).toBe("-1");
    expect(signedInt(42.4)).toBe("+42"); // 리포트와 같이 양수에 + (25.437)
  });

  it("감성을 찍는 화면이 이 함수를 쓴다", () => {
    const 상세 = readFileSync(join(process.cwd(), "components", "StockDetail.tsx"), "utf-8");
    const 포트 = readFileSync(join(process.cwd(), "components", "PortfolioView.tsx"), "utf-8");
    expect(상세).not.toMatch(/sentiment\.toFixed\(0\)/);
    expect(포트).not.toMatch(/sentiment_at_trade\.toFixed\(0\)/);
    expect(포트).not.toContain("${v.toFixed(0)}%`).join");
    // 7일 변화와 스크리너 감성 열도 같다 (docs/infra.md 25.437)
    expect(상세).not.toContain("formatNum(latest.delta_7d, 0)");
    const 스크리너 = readFileSync(join(process.cwd(), "components", "ScreenerForm.tsx"), "utf-8");
    expect(스크리너).not.toContain("num(r.sentiment, 0)");
  });
});

describe("내 계좌 정렬 (docs/infra.md 25.897 → 25.958)", () => {
  it("보유 목록은 손익률(원화 손익 ÷ 원화 원가) 내림차순, 평가 못 한 종목·원가 0 은 맨 아래", async () => {
    const { POSITIONS } = await import("@/lib/portfolio");
    expect(POSITIONS).toContain("ORDER BY CASE WHEN p.cost_krw > 0 THEN p.unrealized_pnl_krw * 1.0 / p.cost_krw END DESC NULLS LAST");
  });

  it("실제 SQLite 에서 금액이 아니라 비율로 줄 선다", async () => {
    const { DatabaseSync } = await import("node:sqlite");
    const { POSITIONS } = await import("@/lib/portfolio");
    const db = new DatabaseSync(":memory:");
    db.exec(`CREATE TABLE stocks (id INTEGER, ticker TEXT, country TEXT, market TEXT, sector TEXT, name_ko TEXT, name_en TEXT);
      CREATE TABLE positions (stock_id INTEGER, cost_krw REAL, unrealized_pnl_krw REAL, market_value_krw REAL);
      INSERT INTO stocks VALUES (1,'A','KR','KOSPI',NULL,'큰돈',NULL),(2,'B','KR','KOSPI',NULL,'높은률',NULL),(3,'C','KR','KOSPI',NULL,'평가못함',NULL),(4,'D','KR','KOSPI',NULL,'손실',NULL);
      INSERT INTO positions VALUES (1, 100000000, 5000000, 105000000), (2, 1000000, 300000, 1300000), (3, 1000000, NULL, NULL), (4, 1000000, -100000, 900000);`);
    const names = (db.prepare(POSITIONS).all() as Array<{ name: string }>).map((r) => r.name);
    // 큰돈은 +5% 로 금액은 1등이지만, 비율로는 +30% 인 높은률 다음이다
    expect(names).toEqual(["높은률", "큰돈", "손실", "평가못함"]);
  });
});
