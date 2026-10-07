import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";
import EnvCheck from "@/components/EnvCheck";
import StatusView from "@/components/StatusView";

export const dynamic = "force-dynamic";

/**
 * 시스템 상태 (Step 17, docs/health.md 4장).
 *
 * 무언가 이상할 때만 여는 화면이라 상단 탭에 넣지 않았다. 들어오는 문은 화면마다 붙는
 * 아래쪽 고지의 "시스템 상태" 링크다.
 */
export default function StatusPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/status" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="시스템 상태" doc="docs/health.md">
          배치가 조용히 멈춘 것을 알아채는 화면입니다. 실패는 배치가 스스로 알리지만, 안 도는 것은 배치가
          알릴 수 없어 웹이 1시간마다 확인하고 텔레그램으로 알립니다.
        </PageHeader>
        <StatusView />
        {/* 환경변수 점검 — DB 를 읽지 않아 상태 화면이 실패해도 보인다 (25.842) */}
        <EnvCheck />
      </main>
      <Footer />
    </div>
  );
}
