import type { Asset, CurrentUser, Obligation, PaginatedResponse, ReviewPayload, Risk } from "@/lib/types";

type GetTokenFn = () => Promise<string | null>;

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function apiFetch<T>(
  path: string,
  getToken: GetTokenFn,
  init: RequestInit = {},
): Promise<T> {
  const token = await getToken();
  if (!token) {
    throw new ApiError("Missing auth token", 401);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
    cache: "no-store",
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) {
        detail = payload.detail;
      }
    } catch {
      // keep default detail text
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export async function getCurrentUser(getToken: GetTokenFn): Promise<CurrentUser> {
  return apiFetch<CurrentUser>("/users/me", getToken);
}

export async function getAssets(getToken: GetTokenFn): Promise<PaginatedResponse<Asset>> {
  return apiFetch<PaginatedResponse<Asset>>("/assets", getToken);
}

export async function getObligations(
  getToken: GetTokenFn,
  params: { assetId: string; status?: string; severity?: string; limit?: number; cursor?: string | number },
): Promise<PaginatedResponse<Obligation>> {
  const query = new URLSearchParams({
    asset_id: params.assetId,
    limit: String(params.limit ?? 20),
    cursor: String(params.cursor ?? 0),
  });
  if (params.status) {
    query.set("status", params.status);
  }
  if (params.severity) {
    query.set("severity", params.severity);
  }
  return apiFetch<PaginatedResponse<Obligation>>(`/obligations?${query.toString()}`, getToken);
}

export async function getRisks(
  getToken: GetTokenFn,
  params: { assetId: string; status?: string; severity?: string; limit?: number; cursor?: string | number },
): Promise<PaginatedResponse<Risk>> {
  const query = new URLSearchParams({
    asset_id: params.assetId,
    limit: String(params.limit ?? 20),
    cursor: String(params.cursor ?? 0),
  });
  if (params.status) {
    query.set("status", params.status);
  }
  if (params.severity) {
    query.set("severity", params.severity);
  }
  return apiFetch<PaginatedResponse<Risk>>(`/risks?${query.toString()}`, getToken);
}

export async function reviewObligation(
  getToken: GetTokenFn,
  obligationId: string,
  payload: ReviewPayload,
): Promise<{ obligation: Obligation; review_id: string }> {
  return apiFetch<{ obligation: Obligation; review_id: string }>(
    `/obligations/${obligationId}/review`,
    getToken,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function reviewRisk(
  getToken: GetTokenFn,
  riskId: string,
  payload: ReviewPayload,
): Promise<{ risk: Risk; review_id: string }> {
  return apiFetch<{ risk: Risk; review_id: string }>(
    `/risks/${riskId}/review`,
    getToken,
    { method: "POST", body: JSON.stringify(payload) },
  );
}
