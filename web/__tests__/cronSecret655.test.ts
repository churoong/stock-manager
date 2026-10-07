/** 크론 네 경로가 모두 비밀이 없으면 503 이다 — health 만 401 이었다 (docs/infra.md 25.655, 감사). */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("CRON_SECRET 이 없을 때", () => {
  const 뿌리 = join(process.cwd(), "app", "api", "cron");
  const 경로들 = readdirSync(뿌리, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);

  it("경로를 찾았다", () => expect(경로들.length).toBeGreaterThanOrEqual(4));

  it.each(경로들)("%s 는 503 으로 먼저 말한다", (이름) => {
    const 글 = readFileSync(join(뿌리, 이름, "route.ts"), "utf8");
    const 없음 = 글.indexOf("if (!process.env.CRON_SECRET)");
    expect(없음).toBeGreaterThan(-1);
    expect(없음).toBeLessThan(글.indexOf("tokenMatches("));
  });
});
