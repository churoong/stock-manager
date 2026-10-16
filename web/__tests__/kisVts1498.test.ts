/**
 * KIS모의투자 열 때 조회 B (docs/paper.md 15.7, docs/infra.md 25.1498) — 조회만·모의 도메인만·고른 칸만·30초 막기·배치 도는 동안 막기.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { describe, expect, it } from "vitest";
import {
  BATCH_RUNNING_SQL,
  KIS_VTS_BASE,
  LIVE_THROTTLE_SQL,
  VTS_SAVED_ROW_SQL,
  kisErrorCode,
  liveRequests,
  parseKrLive,
  parseUsLive,
  throttleKey,
  vtsAccount,
  vtsConfigured,
  vtsUrlOk,
} from "@/lib/kisVts";

const MIGRATIONS = join(process.cwd(), "..", "migrations");
const ROUTE = readFileSync(join(process.cwd(), "app", "api", "paper", "kis", "live", "route.ts"), "utf8");
const LIB = readFileSync(join(process.cwd(), "lib", "kisVts.ts"), "utf8");

describe("실계좌로 새지 않는가 — 조회만", () => {
  it("모의 도메인·모의 조회 tr_id 만 통과", () => {
    expect(KIS_VTS_BASE).toBe("https://openapivts.koreainvestment.com:29443");
    expect(vtsUrlOk(`${KIS_VTS_BASE}/uapi/x`, "VTTC8434R")).toBe(true);
    expect(vtsUrlOk("https://openapi.koreainvestment.com:9443/uapi/x", "VTTC8434R")).toBe(false); // 실전 도메인
    expect(vtsUrlOk(`${KIS_VTS_BASE}.evil.example/uapi`, "VTTC8434R")).toBe(false); // 앞만 같은 다른 호스트
    expect(vtsUrlOk(`${KIS_VTS_BASE}/uapi/x`, "TTTC8434R")).toBe(false); // 실전 tr_id
    expect(vtsUrlOk(`${KIS_VTS_BASE}/uapi/x`, "VTTC0012U")).toBe(false); // 모의라도 주문(U)은 안 됨
  });
  it("경로는 GET 조회와 배치가 받은 토큰 읽기뿐 — 주문·토큰 발급이 없다", () => {
    for (const m of ["KR", "US"] as const)
      for (const q of liveRequests(m, { cano: "12345678", prdt: "01" }, "2026-10-16")) expect(q.tr).toMatch(/^V[A-Z]{3}\d{4}R$/);
    expect(ROUTE).not.toContain("tokenP");
    expect(ROUTE).not.toMatch(/method:\s*"POST"/);
    expect(ROUTE).not.toContain("INSERT INTO api_tokens");
    expect(LIB + ROUTE).not.toMatch(/order-cash|order-resv|"V[A-Z]{3}\d{4}U"/);
    expect(VTS_SAVED_ROW_SQL).toContain("name = 'kis_vts'");
  });
  it("계좌번호 8·10자리, 키 셋이 있어야 켜진다", () => {
    expect(vtsAccount({ KIS_VTS_ACCOUNT: "5012-3456" })).toEqual({ cano: "50123456", prdt: "01" });
    expect(vtsAccount({ KIS_VTS_ACCOUNT: "5012345601" })).toEqual({ cano: "50123456", prdt: "01" });
    expect(vtsAccount({ KIS_VTS_ACCOUNT: "123" })).toBeNull();
    expect(vtsConfigured({ KIS_VTS_APP_KEY: "k", KIS_VTS_ACCOUNT: "50123456" })).toBe(false);
    expect(vtsConfigured({ KIS_VTS_APP_KEY: "k", KIS_VTS_APP_SECRET: "s", KIS_VTS_ACCOUNT: "50123456" })).toBe(true);
  });
  it("오류는 코드만, 예외는 이름만 — 응답 문장(주소·계좌)이 나가지 않는다", () => {
    expect(kisErrorCode({ rt_cd: "1", msg_cd: "EGW00201", msg1: "초당 거래건수를 초과" })).toBe("EGW00201");
    expect(kisErrorCode({ rt_cd: "0" })).toBeNull();
    expect(kisErrorCode(null)).toBe("JSON 아님");
    expect(ROUTE).toContain("e instanceof Error ? e.name");
    expect(ROUTE).not.toMatch(/e\.message|msg1/);
  });
});

describe("고른 칸", () => {
  it("국내 — 현금은 총평가와 같은 D+2, 오늘 체결", () => {
    const v = parseKrLive(
      {
        rt_cd: "0",
        output1: [{ pdno: "005930", prdt_name: "삼성전자", hldg_qty: "3", pchs_avg_pric: "215000", prpr: "216000", evlu_amt: "648000", evlu_pfls_amt: "3000", evlu_pfls_rt: "0.47", cano: "50123456" }, { pdno: "000660", hldg_qty: "0" }],
        output2: [{ dnca_tot_amt: "500000000", prvs_rcdl_excc_amt: "499354261", scts_evlu_amt: "648000", tot_evlu_amt: "500002261" }],
      },
      { rt_cd: "0", output1: [{ ord_dt: "20261016", ord_tmd: "111800", odno: "20875", sll_buy_dvsn_cd: "02", pdno: "005930", prdt_name: "삼성전자", ord_qty: "1", tot_ccld_qty: "1", avg_prvs: "215000", ord_unpr: "0", rjct_qty: "0", cncl_yn: "N" }] },
      "2026-10-16T02:18:00Z",
    );
    expect(v.holdings).toEqual([{ code: "005930", name: "삼성전자", qty: 3, avg: 215000, price: 216000, value: 648000, pnl: 3000, pnl_pct: 0.47 }]);
    expect(v.account).toEqual({ cash: 499354261, holdings_value: 648000, total: 500002261 });
    expect(v.executions[0]).toMatchObject({ side: "buy", qty: 1, filled: 1, avg_price: 215000, note: null });
    // 원시 응답의 다른 칸(계좌번호 등)은 싣지 않는다
    expect(JSON.stringify(v)).not.toContain("50123456");
  });
  it("미국 — 체결기준현재잔고(외화) 보유·달러 예수금, 거부 사유 0 은 사유 없음", () => {
    const v = parseUsLive(
      {
        rt_cd: "0",
        output1: [{ pdno: "AAPL", prdt_name: "애플", cblc_qty13: "2", avg_unpr3: "200.5", ovrs_now_pric1: "210", frcr_evlu_amt2: "420", evlu_pfls_amt2: "19", evlu_pfls_rt1: "4.74" }],
        output2: [{ crcy_cd: "JPY", frcr_dncl_amt_2: "1" }, { crcy_cd: "USD", frcr_dncl_amt_2: "0" }],
      },
      { rt_cd: "0", output: [{ ord_dt: "20261016", odno: "5501", sll_buy_dvsn_cd: "02", pdno: "AAPL", ft_ord_qty: "2", ft_ccld_qty: "2", ft_ccld_unpr3: "200.5", ft_ord_unpr3: "220.00", rjct_rson: "0" }] },
      "2026-10-16T14:00:00Z",
    );
    expect(v.holdings[0]).toMatchObject({ code: "AAPL", qty: 2, avg: 200.5, price: 210, value: 420, pnl: 19 });
    expect(v.account).toEqual({ cash: 0, holdings_value: 420, total: 420 });
    expect(v.executions[0]).toMatchObject({ side: "buy", filled: 2, avg_price: 200.5, order_price: 220, note: null });
  });
});

describe("막기 질의 (실제 마이그레이션 SQLite)", () => {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((x) => x.endsWith(".sql")).sort()) db.exec(readFileSync(join(MIGRATIONS, f), "utf8"));
  it("30초 — 조건부 upsert 가 처음 1행, 30초 안 0행, 지나면 1행", () => {
    const up = db.prepare(LIVE_THROTTLE_SQL);
    const k = throttleKey("KR");
    expect(Number(up.run(k, '"t1"', "2026-10-16T03:00:00.000Z", "2026-10-16T02:59:30.000Z").changes)).toBe(1);
    expect(Number(up.run(k, '"t2"', "2026-10-16T03:00:10.000Z", "2026-10-16T02:59:40.000Z").changes)).toBe(0);
    expect(Number(up.run(k, '"t3"', "2026-10-16T03:00:31.000Z", "2026-10-16T03:00:01.000Z").changes)).toBe(1);
    // 다른 시장은 따로 센다
    expect(Number(up.run(throttleKey("US"), '"t"', "2026-10-16T03:00:32.000Z", "2026-10-16T03:00:02.000Z").changes)).toBe(1);
  });
  it("일일 배치가 도는 동안(최근 running)만 막는다", () => {
    const run = (job: string, status: string, started: string) =>
      db.prepare("INSERT INTO batch_runs (job_name, market, started_at, status) VALUES (?, 'KR', ?, ?)").run(job, started, status);
    run("daily_kr", "success", "2026-10-16T23:27:00Z");
    run("signals", "running", "2026-10-16T23:28:00Z");
    expect(db.prepare(BATCH_RUNNING_SQL).all("2026-10-16T23:00:00Z")).toHaveLength(0);
    run("daily_us", "running", "2026-10-16T12:27:00Z");
    expect(db.prepare(BATCH_RUNNING_SQL).all("2026-10-16T12:00:00Z")).toHaveLength(1);
    expect(db.prepare(BATCH_RUNNING_SQL).all("2026-10-16T13:30:00Z")).toHaveLength(0); // 오래된 running 은 죽은 실행
  });
});
