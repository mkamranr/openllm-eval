import { useEffect, useState } from "react";

/** useState backed by localStorage. Storage failures (private mode, quota) fall back to memory. */
export function usePersistentState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = window.localStorage.getItem(key);
      return raw ? { ...initial, ...(JSON.parse(raw) as T) } : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Not persisted; the in-memory state still works.
    }
  }, [key, value]);
  return [value, setValue] as const;
}
