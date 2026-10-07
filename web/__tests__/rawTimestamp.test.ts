import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * **화면이 UTC 시각 문자열을 그대로 보여 주지 않는가** (docs/infra.md 25.306).
 *
 * DB 의 `*_at` 은 UTC ISO 다. 25.266 에서 네 곳을 `userTimeOf` 로 바꿨는데 네 곳이 더 남아 있었다 —
 * 상태 화면은 한 줄에 "시작 08:27(KST) → 끝 23:33(UTC)" 를 섞어 적었다.
 * 여기서는 `*_at` 을 **잘라 쓰거나(`.slice`) 템플릿에 그대로 넣는** 줄을 잡는다.
 */
describe("화면 컴포넌트", () => {
  it("*_at 을 userTimeOf/userDateOf 없이 표시하지 않는다", () => {
    const dir = join(process.cwd(), "components");
    const 걸린 = readdirSync(dir)
      .filter((f) => f.endsWith(".tsx"))
      .flatMap((f) =>
        readFileSync(join(dir, f), "utf-8")
          .split("\n")
          .map((line, i) => ({ f, i: i + 1, line }))
          .filter(({ line }) =>
            /\b\w+_at\??\.slice\(|\$\{[\w.?]*_at\}/.test(line),
          )
          .filter(({ line }) => !/userTimeOf\(|userDateOf\(/.test(line)),
      )
      .map(({ f, i, line }) => `${f}:${i} ${line.trim()}`);
    expect(걸린).toEqual([]);
  });
});
