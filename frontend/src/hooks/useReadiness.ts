import { api } from "../services/api";
import { useAsync } from "./useAsync";

export function useReadiness() {
  return useAsync(() => api.readiness(), []);
}
