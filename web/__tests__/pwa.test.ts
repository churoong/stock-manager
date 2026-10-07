/**
 * 홈 화면 설치(PWA) 테스트 (docs/pwa.md, 2026-09-17 사용자 요청 "앱으로도 만들어줘").
 *
 * 여기서 지키는 것은 둘이다.
 *   1. 설치에 필요한 것이 갖춰져 있다 (매니페스트·아이콘·서비스 워커)
 *   2. **데이터를 폰에 남기지 않는다.** 시세는 제3자 제공이 금지돼 있고, 로그인 없이
 *      아무것도 보여 주지 않는다는 원칙이 캐시 때문에 깨지면 안 된다
 */

import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import manifest from "@/app/manifest";

const PUBLIC = join(process.cwd(), "public");

describe("설치 준비", () => {
  it("매니페스트에 설치에 필요한 값이 있다", () => {
    const m = manifest();
    expect(m.name).toBeTruthy();
    expect(m.short_name).toBeTruthy();
    expect(m.display).toBe("standalone");
    expect(m.start_url).toBe("/recommend");
    const sizes = (m.icons ?? []).map((i) => i.sizes);
    expect(sizes).toContain("192x192");
    expect(sizes).toContain("512x512");
    // 안드로이드가 아이콘을 잘라 모양을 맞추므로 여백을 준 그림이 따로 있어야 한다
    expect((m.icons ?? []).some((i) => i.purpose === "maskable")).toBe(true);
  });

  it("아이콘 파일이 실제로 있고 비어 있지 않다", () => {
    for (const name of ["icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"]) {
      const path = join(PUBLIC, name);
      expect(existsSync(path)).toBe(true);
      expect(statSync(path).size).toBeGreaterThan(100);
      // PNG 서명 확인. 이름만 png 인 파일을 넣어 두면 설치가 조용히 실패한다
      expect(readFileSync(path).subarray(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
    }
  });
});

describe("서비스 워커는 데이터를 캐시하지 않는다", () => {
  const sw = readFileSync(join(PUBLIC, "sw.js"), "utf-8");

  it("API 응답은 가로채지 않고 그대로 네트워크로 보낸다", () => {
    expect(sw).toContain('url.pathname.startsWith("/api/")');
    // 캐시에 넣는 것은 껍데기 파일 목록뿐이다
    expect(sw).toContain("SHELL_FILES");
    expect(sw).not.toMatch(/cache\.put\(/);
  });

  it("캐시하는 파일에 화면(HTML)이 없다 — 오프라인 안내만 있다", () => {
    const listed = /const SHELL_FILES = \[(.*?)\]/s.exec(sw)?.[1] ?? "";
    expect(listed).toContain("/offline.html");
    expect(listed).not.toContain("/portfolio");
    expect(listed).not.toContain("/recommend");
  });

  it("오프라인 안내는 옛 숫자를 보여 주지 않는다고 적는다", () => {
    const html = readFileSync(join(PUBLIC, "offline.html"), "utf-8");
    expect(html).toContain("오프라인");
    expect(html).toContain("다시 시도");
  });
});

describe("설치용 파일은 로그인 없이도 받을 수 있다", () => {
  it("proxy 가 매니페스트·아이콘·워커를 통과시킨다", () => {
    const proxy = readFileSync(join(process.cwd(), "proxy.ts"), "utf-8");
    for (const file of ["/manifest.webmanifest", "/sw.js", "/icon-192.png", "/apple-touch-icon.png"]) {
      expect(proxy).toContain(file);
    }
    // 데이터 경로는 여전히 로그인 뒤에 있다
    expect(proxy).toContain('pathname.startsWith("/api/")');
  });
});
