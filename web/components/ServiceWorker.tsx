"use client";

import { useEffect } from "react";

/**
 * 서비스 워커 등록 (docs/pwa.md).
 *
 * 왜 필요한가: 안드로이드 크롬은 fetch 를 다루는 서비스 워커가 있어야 "홈 화면에 추가"
 * 를 앱 설치로 취급한다. iOS 는 없어도 추가되지만 오프라인 안내를 위해 함께 쓴다.
 *
 * **데이터는 캐시하지 않는다.** 아래 sw.js 주석을 보라 — 시세·매매가 폰에 남으면
 * 데이터 이용 조건과 "로그인 없이는 아무것도 보여 주지 않는다" 는 원칙이 흔들린다.
 */
export default function ServiceWorker() {
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    // 등록 실패는 조용히 넘긴다. 서비스 워커가 없어도 화면은 그대로 동작한다
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  }, []);
  return null;
}
