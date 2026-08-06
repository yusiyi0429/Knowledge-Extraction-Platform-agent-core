import type { JobEvent } from "./types";

export class ApiError extends Error {
  code: string;
  status: number;
  retryable: boolean;
  details: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    super(String(body.message || "请求失败"));
    this.name = "ApiError";
    this.status = status;
    this.code = String(body.code || "REQUEST_FAILED");
    this.retryable = Boolean(body.retryable);
    this.details = (body.details as Record<string, unknown>) || {};
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`/api/v1${path}`, { ...init, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, body as Record<string, unknown>);
  }
  return body as T;
}

export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file, file.name);
  return api<T>(path, { method: "POST", body: form });
}

export function watchJob(
  jobId: string,
  onEvent: (event: JobEvent) => void,
  onFailure: (error: Error) => void,
): () => void {
  const source = new EventSource(`/api/v1/jobs/${jobId}/events`);
  source.addEventListener("progress", (raw) => {
    const event = JSON.parse((raw as MessageEvent<string>).data) as JobEvent;
    onEvent(event);
    if (event.status === "COMPLETED" || event.status === "FAILED") {
      source.close();
    }
  });
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) return;
    source.close();
    onFailure(new Error("进度连接已中断，请刷新任务状态。"));
  };
  return () => source.close();
}

export function jsonBody(value: unknown): string {
  return JSON.stringify(value);
}
