import {
  parseDeploymentEvent,
  parseDeploymentResponse,
  parseDevices,
  parseJobs,
  parseReleases,
  parseSession,
} from "../../shared/contracts";
import { requestJson, type RequestOptions } from "../../shared/httpClient";

function objectPayload(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("Invalid registry response.");
  return value as Record<string, unknown>;
}

function parseRegistryResponse(url: string, value: unknown): unknown {
  if (url.endsWith("/api/session")) return parseSession(value);
  if (url.endsWith("/api/devices")) return { devices: parseDevices(value) };
  if (url.endsWith("/api/releases")) return parseReleases(value);
  if (url.endsWith("/api/jobs")) return { jobs: parseJobs(value) };
  if (url.includes("/api/deployments")) return parseDeploymentResponse(value);
  return objectPayload(value);
}

export async function request(
  url: string,
  options: RequestOptions = {},
  csrf = "",
): Promise<unknown> {
  return requestJson(url, { ...options, csrf: csrf || options.csrf }, (value) =>
    parseRegistryResponse(url, value),
  );
}

export function openDeploymentEvents(
  deploymentId: string,
  after: number,
  onEvent: (event: ReturnType<typeof parseDeploymentEvent>) => void,
  onError: (error: Error) => void,
): EventSource {
  const source = new EventSource(
    `/api/deployments/${deploymentId}/events?after=${after}`,
  );
  source.onmessage = (message) => {
    try {
      onEvent(parseDeploymentEvent(JSON.parse(message.data) as unknown));
    } catch (error) {
      onError(
        error instanceof Error
          ? error
          : new Error("Deployment log contained invalid data."),
      );
    }
  };
  return source;
}
