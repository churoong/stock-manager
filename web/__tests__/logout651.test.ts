/** 로그아웃 폼이 JSON 글자 화면에 멈췄다 (docs/infra.md 25.651, 감사). */

import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { POST } from "@/app/api/auth/logout/route";

describe("로그아웃", () => {
  it("쿠키를 지우고 로그인 화면으로 303", async () => {
    const res = await POST(new NextRequest("https://example.com/api/auth/logout", { method: "POST" }));
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("https://example.com/login");
    expect(res.headers.get("set-cookie") ?? "").toMatch(/Max-Age=0/i);
  });
});
