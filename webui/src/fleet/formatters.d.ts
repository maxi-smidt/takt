export function timeAgo(value: string | null | undefined): string;
export function bytes(value: number | null | undefined): string;
export function formatStopwatch(milliseconds: number | null | undefined): string;
export function formatDateTime(value: string | null | undefined): string | null;
export function formatDate(value: string | null | undefined): string | null;
export function mirrorStateLabel(state: string): string;
export function translatePortalError(message: string): string;
export function isSessionExpired(failure: { status?: number } | null | undefined): boolean;
export function insecureRemoteHttp(value: string): boolean;
