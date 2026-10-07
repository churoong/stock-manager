import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * **DB 읽기를 `.catch(() => …)` 로 삼키지 않는다** (docs/infra.md 25.163·25.223).
 *
 * `.catch(() => [])` 는 "표가 아직 없다" 를 넘기려고 붙이지만 **아무 실패나 다 삼킨다** — 한도·인증 실패도 빈 목록이 되고
 * 화면은 "없습니다" 라고 적는다. 표가 없을 때만 넘기려면 `ifMissingTable(대신)` 을 쓴다.
 * 25.163 에서 고쳤는데 `app/api/portfolio` 의 매도 플래그가 남아 있었다(25.223) — 손으로 찾으면 남는다.
 */

const 웹 = process.cwd();

/** 인자 없는 catch 를 쓰되 **삼키는 것이 아닌** 자리와 사유 */
const 면제: Record<string, string> = {
  "app/api/alerts/route.ts":
    "알림 **전체·안 읽은 수**는 곁다리다(25.805). 못 읽으면 null 이고 화면이 목록 안에서 센다 — 이 한 줄 때문에 알림 목록이 500 이 되면 더 나쁘다",
  "app/api/cron/intraday/route.ts":
    "묵은 감시 목록 **말 한 줄**을 붙이려는 읽기다. 못 읽어도 알림은 나가야 한다(25.162) — 알림을 막는 쪽이 더 나쁘다",
  "app/api/trades/[id]/route.ts":
    "빈 값이 아니라 **\"확인하지 못했습니다\" 경고**로 바꾼다(25.163) — 삼키지 않고 말한다",
  "app/api/screener/route.ts": "빈 목록이 아니라 null 로 받아 **\"조회가 실패했습니다\"** 라고 적는다(25.163)",
  "lib/tursoWatch.ts": "Turso 복귀 표시(운영 표시, 25.12). 못 읽으면 다시 깨우는 쪽으로 가고, 사용자 자료가 아니다",
  "app/api/cron/health/route.ts": "읽기가 아니라 발송 실패 뒤 잡은 알림을 푸는 쓰기다 — 풀기마저 실패하면 원래 발송 오류를 그대로 남긴다 (25.593)",
};

function 파일들(): string[] {
  const 나온것: string[] = [];
  for (const 밑 of ["app", "lib"]) {
    for (const e of readdirSync(join(웹, 밑), { recursive: true, withFileTypes: true })) {
      if (!e.isFile() || !/\.tsx?$/.test(e.name)) continue;
      const 디렉터리 = e.parentPath ?? (e as { path: string }).path;
      나온것.push(relative(웹, join(디렉터리, e.name)).split(sep).join("/"));
    }
  }
  return 나온것.sort();
}

/** `execute(` 로 시작한 식이 몇 줄 안에 `.catch(() =>` 로 끝나는 자리 */
function 삼키는_자리(글: string): number[] {
  const 줄들 = 글.split("\n");
  const 나온것: number[] = [];
  줄들.forEach((줄, i) => {
    if (!/\.catch\(\(\) =>/.test(줄)) return;
    const 앞 = 줄들.slice(Math.max(0, i - 4), i + 1).join("\n");
    if (/\bexec(ute)?\(/.test(앞)) 나온것.push(i + 1);
  });
  return 나온것;
}

describe("DB 읽기를 삼키지 않는다", () => {
  it("훑기가 실제로 문다 — 미끼", () => {
    expect(삼키는_자리("const x = await execute(Q)\n  .catch(() => []);")).toEqual([2]);
    expect(삼키는_자리("const x = await execute(Q).catch(ifMissingTable([]));")).toEqual([]);
  });

  it("면제 말고는 없다", () => {
    const 걸린것 = 파일들()
      .filter((f) => !면제[f])
      .flatMap((f) => 삼키는_자리(readFileSync(join(웹, f), "utf-8")).map((n) => `${f}:${n}`));
    expect(걸린것, "표가 없을 때만 넘기려면 `.catch(ifMissingTable(대신))` 을 쓴다").toEqual([]);
  });

  it("면제가 낡지 않았다", () => {
    const 낡은것 = Object.keys(면제).filter((f) => 삼키는_자리(readFileSync(join(웹, f), "utf-8")).length === 0);
    expect(낡은것).toEqual([]);
  });
});
