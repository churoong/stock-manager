"use client";

import { useEffect, useState } from "react";

/**
 * DB 가 막혔을 때 화면 맨 위에 띄우는 띠 (docs/infra.md 24절).
 *
 * 2026-09-18: 월 한도로 계정이 막히자 화면에 **메뉴만 보이고 데이터가 전부 비었다.**
 * 각 화면은 "데이터 없음" 이라고만 적어, 데이터가 없는 것인지 앱이 고장 난 것인지 알 수 없었다.
 * 이유는 한 곳에서, 눈에 띄게 말한다.
 *
 * 스스로는 아무 표도 읽지 않는다(`SELECT 1`). 막혔는지 확인하려고 읽기 예산을 쓰지 않는다.
 */
export default function DbBanner() {
  const [reason, setReason] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/db-health")
      .then((r) => r.json())
      .then((body) => {
        if (alive && body && body.ok === false) setReason(String(body.reason ?? "데이터베이스에 연결하지 못했습니다"));
      })
      .catch(() => {
        // 이 요청 자체가 실패하면(오프라인 등) 띠를 띄우지 않는다. 화면마다 나오는 오류로 충분하다
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!reason) return null;
  return (
    <div className="border-b border-red-300 bg-red-50 px-4 py-2 text-xs leading-relaxed text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
      <b>데이터를 불러올 수 없습니다.</b> {reason}
    </div>
  );
}
