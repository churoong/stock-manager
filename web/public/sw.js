/**
 * 서비스 워커 (docs/pwa.md).
 *
 * 하는 일은 둘뿐이다.
 *   1. 앱 껍데기(아이콘·매니페스트·오프라인 안내)를 캐시한다
 *   2. 네트워크가 끊겼을 때 화면 요청에 안내 페이지를 돌려준다
 *
 * **하지 않는 것: 데이터 캐시.**
 *   - /api/* 는 어떤 것도 저장하지 않는다. 시세·재무는 제3자 제공이 금지돼 있고(KRX),
 *     매매 기록은 사적인 값이다. 폰 저장소에 남기지 않는다
 *   - 로그인한 화면(HTML)도 저장하지 않는다. 캐시된 화면이 로그아웃 뒤에도 보이면
 *     "로그인 없이는 아무것도 반환하지 않는다" 는 원칙이 깨진다
 *
 * 그래서 이 워커는 오프라인에서 "지금은 볼 수 없습니다" 를 보여 줄 뿐, 옛 숫자를
 * 보여 주지 않는다. 옛 숫자를 지금 값으로 착각하는 것이 더 위험하다.
 */

// **껍데기 파일을 바꾸면 이 이름을 올린다** — 껍데기는 캐시 우선이라 이름이 같으면 설치된 앱이 옛 파일을 계속 쓴다
// (docs/infra.md 25.232). `__tests__/swShellVersion.test.ts` 가 파일 해시로 확인한다
const SHELL = "shell-v2";
const SHELL_FILES = ["/offline.html", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // 데이터는 늘 네트워크로. 저장도 폴백도 하지 않는다
  if (url.pathname.startsWith("/api/")) return;

  // 껍데기 파일은 캐시에서
  if (SHELL_FILES.includes(url.pathname)) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
    return;
  }

  // 화면은 네트워크 우선. 끊기면 안내 페이지
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/offline.html")));
  }
});
