import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * **껍데기 파일을 바꾸면 캐시 이름도 바꿔야 한다** (docs/infra.md 25.232).
 *
 * `public/sw.js` 는 껍데기(`SHELL_FILES`)를 **캐시 우선**으로 준다. 캐시 이름(`SHELL`)이 그대로면 이미 설치된
 * 앱은 새 워커가 깔려도 옛 캐시를 지우지 않고 옛 파일을 계속 보여 준다. 25.232 에서 `offline.html` 에 책임 고지를
 * 넣으면서 이름을 올리지 않았다면 폰에는 고지 없는 옛 화면이 남았을 것이다.
 *
 * 그래서 파일 내용의 해시를 이름과 **짝으로** 적어 둔다. 파일이 바뀌었는데 이름이 같으면 실패한다.
 * 고치는 법: `sw.js` 의 `SHELL` 을 올리고 아래 `기록` 을 새 이름·새 해시로 바꾼다.
 */
const 기록 = { 이름: "shell-v2", 해시: "a6ba3e548882848e" };

const 공개 = join(process.cwd(), "public");
const 워커 = readFileSync(join(공개, "sw.js"), "utf-8");

function 껍데기_파일(): string[] {
  const 맞음 = 워커.match(/const SHELL_FILES = \[([^\]]*)\]/);
  expect(맞음, "sw.js 에서 SHELL_FILES 를 못 찾았다").not.toBeNull();
  return [...맞음![1].matchAll(/"\/([^"]+)"/g)].map((m) => m[1]);
}

function 캐시_이름(): string {
  const 맞음 = 워커.match(/const SHELL = "([^"]+)"/);
  expect(맞음, "sw.js 에서 SHELL 을 못 찾았다").not.toBeNull();
  return 맞음![1];
}

describe("서비스워커 껍데기 캐시 이름", () => {
  it("껍데기 파일 목록을 실제로 읽었다", () => {
    expect(껍데기_파일()).toContain("offline.html");
  });

  it("파일이 바뀌었으면 캐시 이름도 바뀌었다", () => {
    const h = createHash("sha256");
    for (const 파일 of 껍데기_파일()) h.update(readFileSync(join(공개, 파일)));
    const 지금 = h.digest("hex").slice(0, 16);

    if (지금 !== 기록.해시) {
      expect(
        캐시_이름(),
        `껍데기 파일이 바뀌었다(${기록.해시} → ${지금}). sw.js 의 SHELL 을 올리고 이 테스트의 기록을 갱신하라`,
      ).not.toBe(기록.이름);
      return;
    }
    expect(캐시_이름(), "기록의 이름과 sw.js 가 다르다 — 기록을 갱신하라").toBe(
      기록.이름,
    );
  });
});
