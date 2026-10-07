/** D1 한도 오류는 사람 말을 앞에 두고 원문을 뒤에 남긴다 (docs/infra.md 25.877) — 추천 화면이 영어 원문만 띄웠다 */
import { expect, it, vi } from "vitest";

it("읽기 한도면 한국어 안내가 앞, 원문은 뒤(한도 판정용)", async () => {
  vi.resetModules();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    success: false, result: [],
    errors: [{ code: 7500, message: "Your account has exceeded D1's free tier daily row read limit. Upgrade to a paid plan or wait until tomorrow (midnight UTC) to continue." }],
  }), { status: 400 })));
  for (const [k, v] of Object.entries({ D1_ACCOUNT_ID: "a", D1_DATABASE_ID: "b", D1_API_TOKEN: "c" })) vi.stubEnv(k, v);
  const { d1Batch } = await import("@/lib/d1");
  const { quotaReason } = await import("@/lib/db");
  const 오류 = await d1Batch([{ sql: "SELECT 1", args: [] }]).catch((e: Error) => e);
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  expect(오류).toBeInstanceOf(Error);
  const 글 = (오류 as Error).message;
  expect(글.startsWith("DB 하루 읽기 한도(500만 행)")).toBe(true);
  expect(quotaReason(글)).toContain("읽기 한도");
});
