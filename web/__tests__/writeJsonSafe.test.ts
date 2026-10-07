import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { bodyOf } from "@/lib/http";

/**
 * **저장·검색 경로가 응답 본문을 읽다 던지지 않는가** (docs/infra.md 25.281).
 *
 * 매매·배당 저장/삭제, 관심 종목 추가, 종목 검색이 `await r.json()` 을 직접 불렀다. 배포 직후 HTML 오류
 * 응답이나 네트워크 끊김이면 예외로 끝나 `setBusy(false)` 에 닿지 못하고 **"저장 중…" 에 멈췄다.**
 * 이제 `readJson` + `bodyOf` 로만 읽는다.
 */

describe("bodyOf", () => {
  it("본문을 못 읽었으면 사유를 errors 로 돌려준다", () => {
    const j = bodyOf({
      ok: false,
      data: null,
      error: "응답을 읽지 못했습니다 (HTTP 502)",
      status: 502,
    });
    expect(j.errors).toEqual(["응답을 읽지 못했습니다 (HTTP 502)"]);
  });

  it("본문이 있으면 그대로 준다 (실패여도 errors 를 살린다)", () => {
    const j = bodyOf<{ recalc?: unknown }>({
      ok: false,
      data: { errors: ["수량"] },
      error: "수량",
      status: 400,
    });
    expect(j.errors).toEqual(["수량"]);
  });
});

describe("화면 컴포넌트", () => {
  it("응답 본문을 .json() 으로 직접 읽지 않는다 — readJson 을 거친다", () => {
    const dir = join(process.cwd(), "components");
    // 허용: 위 15줄 안에 `try {` 가 있거나 아래 12줄 안에 `.catch(` 가 있는 곳 — 던져도 받아 준다
    const 걸린 = readdirSync(dir)
      .filter((f) => f.endsWith(".tsx"))
      .flatMap((f) => {
        const lines = readFileSync(join(dir, f), "utf-8").split("\n");
        return lines
          .map((line, i) => ({ line, i }))
          .filter(({ line }) => /\.json\(\)/.test(line))
          .filter(({ i }) => {
            const 위 = lines.slice(Math.max(0, i - 15), i).join("\n");
            const 아래 = lines.slice(i, i + 12).join("\n");
            return !/\btry \{/.test(위) && !/\.catch\(/.test(아래);
          })
          .map(({ line, i }) => `${f}:${i + 1} ${line.trim()}`);
      });
    expect(걸린).toEqual([]);
  });

  it("쓰기 요청의 응답을 버리지 않는다 — `await fetch(…)` 만 쓰는 줄이 없다 (docs/infra.md 25.317)", () => {
    const dir = join(process.cwd(), "components");
    const 걸린 = readdirSync(dir)
      .filter((f) => f.endsWith(".tsx"))
      .flatMap((f) =>
        readFileSync(join(dir, f), "utf-8")
          .split("\n")
          .map((line, i) => ({ f, i: i + 1, line }))
          .filter(({ line }) => /^\s*await fetch\(/.test(line)),
      )
      .map(({ f, i, line }) => `${f}:${i} ${line.trim()}`);
    expect(걸린).toEqual([]);
  });
});
