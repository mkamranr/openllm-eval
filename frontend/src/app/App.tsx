import { Route, Routes } from "react-router-dom";

import { Layout } from "../components/layout/Layout";
import { ComparePage } from "../pages/compare/ComparePage";
import { Dashboard } from "../pages/dashboard/Dashboard";
import { DatasetDetailPage } from "../pages/datasets/DatasetDetailPage";
import { DatasetsPage } from "../pages/datasets/DatasetsPage";
import { EndpointsPage } from "../pages/endpoints/EndpointsPage";
import { NewEvaluationWizard } from "../pages/evaluation/NewEvaluationWizard";
import { Placeholder } from "../pages/Placeholder";
import { ProfilesPage } from "../pages/profiles/ProfilesPage";
import { ProjectsPage } from "../pages/projects/ProjectsPage";
import { ReportsPage } from "../pages/reports/ReportsPage";
import { RunDetailPage } from "../pages/runs/RunDetailPage";
import { RunsPage } from "../pages/runs/RunsPage";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="endpoints" element={<EndpointsPage />} />
        <Route path="new" element={<NewEvaluationWizard />} />
        <Route path="datasets" element={<DatasetsPage />} />
        <Route path="datasets/:datasetId" element={<DatasetDetailPage />} />
        <Route path="profiles" element={<ProfilesPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="compare" element={<ComparePage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="settings" element={<Placeholder title="Settings" milestone="a later milestone" />} />
        <Route path="*" element={<Placeholder title="Page not found" milestone="no milestone" />} />
      </Route>
    </Routes>
  );
}
