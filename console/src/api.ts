import { useEffect, useState } from "react";

export type RunSummary = {
  id: string;
  path: string;
  label: string;
  source: "evidence" | "runs";
  kind: "discovery" | "replay";
  name: string;
  started: string;
  status: string;
  side_effects: string;
  error_kind: string | null;
  outcome: string | null;
  handoffs: number;
  recoveries: string[];
  model: string | null;
  dry_run: boolean;
};

export type RunEvent = { seq: number; ts: string; type: string; [key: string]: any };

export type Intervention = {
  id: string;
  run_id: string;
  kind: "stuck" | "approval";
  capability: string;
  goal: string;
  step: string | null;
  reason: string;
  expected: string;
  screenshot: string;
  snapshot: string;
  options: string[];
};

export type RunDetail = RunSummary & {
  events: RunEvent[];
  result: any | null;
  files: string[];
  interventions: Intervention[];
};

export type Locator = { kind: string; frame?: string[]; role: string; name?: string; row?: string; nth?: number; text?: string };

export type Capability = {
  capability: string;
  version: string;
  status: "draft" | "approved";
  description: string;
  file: string;
  app: { product: string; versions: string };
  params?: Record<string, { type: string; pattern?: string; sensitivity: string }>;
  outputs?: Record<string, { type: string; sensitivity: string }>;
  targets: Record<string, { description: string; locators: Locator[] }>;
  steps: {
    id: string;
    action: string;
    target: string;
    value?: string;
    output?: string;
    risk: string;
    note?: string;
    outcomes?: { name: string; description?: string; when: string }[];
  }[];
  success: string;
  provenance?: { discovered_by?: string; discovered_at?: string; verified?: boolean; verification?: string[]; notes?: string[] };
};

export async function get<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response.json();
}

export async function post<T>(url: string, body: unknown = {}): Promise<T> {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return response.json();
}

/** Fetch a URL, optionally re-fetching on an interval. */
export function useApi<T>(url: string | null, everyMs?: number): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!url) return;
    let live = true;
    const load = () =>
      get<T>(url)
        .then((d) => live && (setData(d), setError(null)))
        .catch((e) => live && setError(String(e.message)));
    load();
    const timer = everyMs ? setInterval(load, everyMs) : undefined;
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [url, everyMs]);
  return { data, error };
}
