export type JsonObject = Record<string, unknown>;

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type RequestOptions = Omit<RequestInit, "body"> & {
  body?: BodyInit | JsonObject | null;
  csrf?: string;
};

function isJsonObject(value: unknown): value is JsonObject {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    !(value instanceof FormData) &&
    !(value instanceof Blob) &&
    !(value instanceof URLSearchParams) &&
    !(value instanceof ArrayBuffer)
  );
}

function errorMessage(value: unknown, status: number): string {
  if (typeof value === "string" && value.trim()) return value;
  if (
    isJsonObject(value) &&
    typeof value.message === "string" &&
    value.message.trim()
  ) {
    return value.message;
  }
  return `HTTP ${status}`;
}

function bodyAndHeaders(options: RequestOptions): {
  body?: BodyInit;
  headers: Headers;
} {
  const headers = new Headers(options.headers);
  const body = options.body;
  if (isJsonObject(body)) {
    headers.set("Content-Type", "application/json");
    return { body: JSON.stringify(body), headers };
  }
  if (typeof body === "string") {
    headers.set("Content-Type", "application/json");
  }
  return { body: body ?? undefined, headers };
}

export async function requestJson<T>(
  url: string,
  options: RequestOptions = {},
  parse: (payload: unknown) => T = (payload) => payload as T,
): Promise<T> {
  const { body, headers } = bodyAndHeaders(options);
  if (options.csrf) headers.set("X-CSRF-Token", options.csrf);
  const response = await fetch(url, { ...options, body, headers });
  const text = await response.text();
  let payload: unknown = text;
  if (text) {
    try {
      payload = JSON.parse(text) as unknown;
    } catch {
      // Existing endpoints return plain-text errors in some failure cases.
    }
  }
  if (!response.ok)
    throw new ApiError(response.status, errorMessage(payload, response.status));
  if (!text) return parse(undefined);
  try {
    return parse(payload);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Invalid server response.";
    throw new ApiError(response.status, message);
  }
}

export function withTimeout(
  signal: AbortSignal | undefined,
  milliseconds: number,
): AbortSignal {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), milliseconds);
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  controller.signal.addEventListener(
    "abort",
    () => {
      window.clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
    },
    { once: true },
  );
  return controller.signal;
}
