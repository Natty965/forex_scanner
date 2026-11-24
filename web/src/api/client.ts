import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type {
  AlertSettings, AlertSettingsResponse, AlertsResponse, ChartData, DataHealth, MarketEvent, Overview, PairAnalysis,
} from "./types";

// How often the page asks the local API for new data. The API is cheap to call (it only
// reads the database); the worker decides how often market data is actually fetched.
export const REFRESH_MS = 30_000;

// If the newest data the page holds is older than this, it is NOT shown as current
// (e.g. the API stopped, or the computer slept). The page shows "refreshing" instead.
export const PAGE_DATA_MAX_AGE_MS = 3 * REFRESH_MS;

export class ApiError extends Error {}

export async function getJson<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError("The local API is not reachable. Is `python -m forex_scanner run` running?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(`API error ${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: () => getJson<Overview>("/api/overview"),
    refetchInterval: REFRESH_MS,
    retry: 1,
  });
}

export function usePairAnalysis(pid: string, timeframe: string) {
  return useQuery({
    queryKey: ["analysis", pid, timeframe],
    queryFn: () => getJson<PairAnalysis>(`/api/pairs/${pid}/analysis?timeframe=${timeframe}`),
    refetchInterval: REFRESH_MS,
    retry: 1,
  });
}

// Candles change only when a new one closes, so the chart refreshes less often.
export const CHART_REFRESH_MS = 60_000;

export function useChart(pid: string, timeframe: string, limit: number) {
  return useQuery({
    queryKey: ["chart", pid, timeframe, limit],
    queryFn: () => getJson<ChartData>(`/api/pairs/${pid}/chart?timeframe=${timeframe}&limit=${limit}`),
    refetchInterval: CHART_REFRESH_MS,
    retry: 1,
    placeholderData: keepPreviousData, // keep the old chart visible only while loading MORE history
  });
}

/** A change the server refused, with its reasons in plain English. */
export class ApiRejected extends Error {
  constructor(message: string, public problems: string[]) {
    super(message);
  }
}

export async function putJson<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError("The local API is not reachable. Nothing was changed.");
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    if (detail && typeof detail === "object" && "problems" in detail) {
      const d = detail as { message: string; problems: string[] };
      throw new ApiRejected(d.message, d.problems);
    }
    throw new ApiError(`API error ${res.status}: ${String(detail)}`);
  }
  return (await res.json()) as T;
}

export function useAlerts(type: string, delivery: string) {
  const q = new URLSearchParams({ limit: "200" });
  if (type) q.set("type", type);
  if (delivery) q.set("delivery", delivery);
  return useQuery({
    queryKey: ["alerts", type, delivery],
    queryFn: () => getJson<AlertsResponse>(`/api/alerts?${q}`),
    refetchInterval: REFRESH_MS,
    retry: 1,
  });
}

export function useAlertSettings() {
  return useQuery({
    queryKey: ["alert-settings"],
    queryFn: () => getJson<AlertSettingsResponse>("/api/alert-settings"),
    retry: 1,
  });
}

export function useSaveAlertSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (changes: Partial<AlertSettings>) => putJson<AlertSettingsResponse>("/api/alert-settings", changes),
    onSuccess: (data) => {
      qc.setQueryData(["alert-settings"], data);
      qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

export function useSendTestMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/telegram/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(typeof body.detail === "string" ? body.detail : `API error ${res.status}`);
      return body as { ok: boolean; error: string | null; id: number };
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

export function useDataHealth() {
  return useQuery({
    queryKey: ["data-health"],
    queryFn: () => getJson<DataHealth>("/api/data-health"),
    refetchInterval: REFRESH_MS,
    retry: 1,
  });
}

export function usePairEvents(pid: string) {
  return useQuery({
    queryKey: ["events", pid],
    queryFn: () => getJson<{ count: number; events: MarketEvent[] }>(`/api/events?pair=${pid}&limit=50`),
    refetchInterval: REFRESH_MS,
    retry: 1,
  });
}
