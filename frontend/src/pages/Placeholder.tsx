import { Hourglass } from "lucide-react";

import { EmptyState, PageHeader } from "../components/ui";

export function Placeholder({ title, milestone }: { title: string; milestone: string }) {
  return (
    <div className="page">
      <PageHeader title={title} />
      <div className="card">
        <EmptyState icon={Hourglass} title="Coming soon">
          Not implemented yet. Planned for {milestone}.
        </EmptyState>
      </div>
    </div>
  );
}
