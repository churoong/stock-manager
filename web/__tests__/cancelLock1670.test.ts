/** 입금 취소 경로 — 배치 단계 잠금과 넣기 실패의 까닭 (docs/infra.md 25.1666·25.1668·25.1670) */
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  CANCEL_INSERT,
  FLOW_BY_ID,
  FLOW_BY_KEY,
  PAPER_CASH,
  PAPER_INVESTED,
  PAPER_LEDGER_VERSION,
  PAPER_STEP_LOCK_HELD,
} from "../lib/paper";

type Rs = { columns: string[]; rows: unknown[][]; affectedRows: number };
const 빈: Rs = { columns: [], rows: [], affectedRows: 0 };
const 표 = (columns: string[], rows: unknown[][], affectedRows = 0): Rs => ({ columns, rows, affectedRows });

/** 잠금 질의를 몇 번째로 부를 때 잠겨 있나 — 처음 확인·넣기 실패 뒤 다시 확인 */
let 잠김: boolean[] = [];
let 넣은수 = 0;
let 같은키: unknown[][] = [];
const 부름: string[] = [];

const execute = vi.fn(async (sql: string) => {
  부름.push(sql);
  if (sql === PAPER_STEP_LOCK_HELD) return 잠김.shift() ? 표(["x"], [[1]]) : 빈;
  if (sql === PAPER_LEDGER_VERSION) return 표(["n", "s", "f", "o"], [[1, 1000, 0, 0]]);
  if (sql === FLOW_BY_ID) return 표(["amount_krw", "kind", "cancelled"], [[1000, "deposit", 0]]);
  if (sql === PAPER_CASH) return 표(["account_id", "cash_krw"], [["A", 5000]]);
  if (sql === CANCEL_INSERT) return 표([], [], 넣은수);
  if (sql === FLOW_BY_KEY) return 표(["id"], 같은키);
  if (sql === PAPER_INVESTED) return 표(["invested"], [[0]]);
  return 빈; // 미체결 매수·환율 — 없음
});
vi.mock("@/lib/db", async (orig) => ({ ...(await orig<typeof import("@/lib/db")>()), execute }));

async function 취소() {
  const { POST } = await import("@/app/api/paper/deposits/[id]/cancel/route");
  const req = new Request("http://x/api/paper/deposits/7/cancel", {
    method: "POST",
    body: JSON.stringify({ request_key: "abcd-1234" }),
  });
  const res = await POST(req, { params: Promise.resolve({ id: "7" }) });
  return { status: res.status, body: (await res.json()) as { ok?: boolean; already?: boolean; errors?: string[] } };
}

beforeEach(() => {
  잠김 = [];
  넣은수 = 1;
  같은키 = [];
  부름.length = 0;
});

describe("입금 취소 — 배치 잠금", () => {
  it("배치 단계가 도는 중이면 409 이고 넣는 문장을 부르지 않는다", async () => {
    잠김 = [true];
    const r = await 취소();
    expect(r.status).toBe(409);
    expect(r.body.errors?.[0]).toContain("배치가 지금 도는 중");
    expect(부름).not.toContain(CANCEL_INSERT);
  });

  it("잠금이 없으면 넣는다", async () => {
    const r = await 취소();
    expect(r.status).toBe(200);
    expect(r.body).toMatchObject({ ok: true, already: false });
    expect(부름).toContain(CANCEL_INSERT);
  });

  it("넣기가 0행이고 그 사이 배치가 시작됐으면 '도는 중' 으로 말한다", async () => {
    잠김 = [false, true];
    넣은수 = 0;
    const r = await 취소();
    expect(r.status).toBe(409);
    expect(r.body.errors?.[0]).toContain("배치가 지금 도는 중");
  });

  it("넣기가 0행이고 잠금도 없으면 장부가 바뀐 것으로 말한다", async () => {
    잠김 = [false, false];
    넣은수 = 0;
    const r = await 취소();
    expect(r.status).toBe(409);
    expect(r.body.errors?.[0]).toContain("그 사이 다른 입금·취소·체결");
  });

  it("넣기가 0행이어도 같은 요청 키가 이미 있으면 성공(두 번 누름)", async () => {
    넣은수 = 0;
    같은키 = [[9]];
    const r = await 취소();
    expect(r.status).toBe(200);
    expect(r.body).toMatchObject({ ok: true, already: true });
  });
});
