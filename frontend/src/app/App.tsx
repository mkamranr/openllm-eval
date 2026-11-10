import { Route, Routes } from "react-router-dom";

import { AuthProvider, RequireAuth } from "../auth/AuthContext";
import { Layout } from "../components/layout/Layout";
import { ComparePage } from "../pages/compare/ComparePage";
import { Dashboard } from "../pages/dashboard/Dashboard";
import { DatasetDetailPage } from "../pages/datasets/DatasetDetailPage";
import { DatasetsPage } from "../pages/datasets/DatasetsPage";
import { EndpointsPage } from "../pages/endpoints/EndpointsPage";
import { NewEvaluationWizard } from "../pages/evaluation/NewEvaluationWizard";
import { ChangePasswordPage } from "../pages/auth/ChangePasswordPage";
import { LoginPage } from "../pages/auth/LoginPage";
import { Placeholder } from "../pages/Placeholder";
import { ProfilesPage } from "../pages/profiles/ProfilesPage";
import { ProjectsPage } from "../pages/projects/ProjectsPage";
import { ReportsPage } from "../pages/reports/ReportsPage";
import { RunDetailPage } from "../pages/runs/RunDetailPage";
import { RunsPage } from "../pages/runs/RunsPage";
import { SettingsPage } from "../pages/settings/SettingsPage";

export function App() {
  return (
    <AuthProvider>
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route path="change-password" element={<ChangePasswordPage />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
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
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Placeholder title="Page not found" milestone="no milestone" />} />
      </Route>
    </Routes>
    </AuthProvider>
  );
}
