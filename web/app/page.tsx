import { redirect } from "next/navigation";

export default function Home() {
  // 첫 화면은 추천이다. 리포트 화면(/reports)은 이미 있지만 추천을 첫 화면으로 둔다 —
  // 조건을 정하지 않고도 볼 수 있고, 리포트가 없는 날(휴장·배치 정지)에도 비지 않는다 (2026-09-27 주석 정정).
  // 예전 주석은 "리포트 화면은 나중에 만든다" 였는데 화면이 생긴 뒤에도 남아 있었다
  redirect("/recommend");
}
