import Footer from "@/components/Footer";
import LabView from "@/components/LabView";
import Nav from "@/components/Nav";
import PageHeader from "@/components/PageHeader";

export const dynamic = "force-dynamic";

/**
 * 연구실 (docs/desk.md 5장, 2026-10-16 웹 개편 4단계) — 전략을 고치려고 보는 자료: 성과 분해 비교·지주사 가중치·종목 여정.
 */
export default function LabPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/lab" />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-4">
        <PageHeader title="연구실" doc="docs/desk.md">
          전략을 고치려고 보는 자료입니다 — 계좌마다 어디서 이기고 지는지(성과 분해), 지주사가 어느 회사를 믿는지(가중치), 한 종목이 어떻게 지나갔는지(종목 여정). 숫자는 배치가 낸 그대로입니다.
        </PageHeader>
        <LabView />
      </main>
      <Footer />
    </div>
  );
}
