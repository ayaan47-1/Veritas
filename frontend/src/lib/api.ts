import type {
  Asset,
  CurrentUser,
  DocumentDetail,
  DocumentPage,
  DocumentStatus,
  DocumentSummary,
  User,
  UserAssetAssignment,
  UserNotification,
  ObligationDetail,
  Obligation,
  PaginatedResponse,
  ReviewPayload,
  Risk,
} from "@/lib/types";

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

export async function getAssetDocuments(
  getToken: GetTokenFn,
  params: {
    assetId: string;
    docType?: string;
    parseStatus?: string;
    limit?: number;
    cursor?: string | number;
  },
): Promise<PaginatedResponse<DocumentSummary>> {
  const query = new URLSearchParams({
    limit: String(params.limit ?? 20),
    cursor: String(params.cursor ?? 0),
  });
  if (params.docType) {
    query.set("doc_type", params.docType);
  }
  if (params.parseStatus) {
    query.set("parse_status", params.parseStatus);
  }
  return apiFetch<PaginatedResponse<DocumentSummary>>(
    `/assets/${params.assetId}/documents?${query.toString()}`,
    getToken,
  );
}

export async function getDocument(getToken: GetTokenFn, documentId: string): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/documents/${documentId}`, getToken);
}

export async function getDocumentStatus(getToken: GetTokenFn, documentId: string): Promise<DocumentStatus> {
  return apiFetch<DocumentStatus>(`/documents/${documentId}/status`, getToken);
}

export async function getDocumentPage(
  getToken: GetTokenFn,
  documentId: string,
  pageNumber: number,
): Promise<DocumentPage> {
  return apiFetch<DocumentPage>(`/documents/${documentId}/pages/${pageNumber}`, getToken);
}

export async function getObligations(
  getToken: GetTokenFn,
  params: {
    assetId?: string;
    documentId?: string;
    status?: string;
    severity?: string;
    limit?: number;
    cursor?: string | number;
  },
): Promise<PaginatedResponse<Obligation>> {
  const query = new URLSearchParams();
  if (params.assetId) {
    query.set("asset_id", params.assetId);
  }
  if (params.documentId) {
    query.set("document_id", params.documentId);
  }
  query.set("limit", String(params.limit ?? 20));
  query.set("cursor", String(params.cursor ?? 0));
  if (params.status) {
    query.set("status", params.status);
  }
  if (params.severity) {
    query.set("severity", params.severity);
  }
  return apiFetch<PaginatedResponse<Obligation>>(`/obligations?${query.toString()}`, getToken);
}

export async function getObligation(getToken: GetTokenFn, obligationId: string): Promise<ObligationDetail> {
  return apiFetch<ObligationDetail>(`/obligations/${obligationId}`, getToken);
}

export async function getRisks(
  getToken: GetTokenFn,
  params: {
    assetId?: string;
    documentId?: string;
    status?: string;
    severity?: string;
    limit?: number;
    cursor?: string | number;
  },
): Promise<PaginatedResponse<Risk>> {
  const query = new URLSearchParams();
  if (params.assetId) {
    query.set("asset_id", params.assetId);
  }
  if (params.documentId) {
    query.set("document_id", params.documentId);
  }
  query.set("limit", String(params.limit ?? 20));
  query.set("cursor", String(params.cursor ?? 0));
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

export async function ingestDocument(
  getToken: GetTokenFn,
  payload: { assetId: string; uploadedBy: string; file: File },
): Promise<{ document_id: string }> {
  const token = await getToken();
  if (!token) {
    throw new ApiError("Missing auth token", 401);
  }

  const formData = new FormData();
  formData.set("asset_id", payload.assetId);
  formData.set("uploaded_by", payload.uploadedBy);
  formData.set("file", payload.file);

  const response = await fetch(`${API_BASE}/ingest`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: formData,
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) {
        detail = body.detail;
      }
    } catch {
      // noop
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as { document_id: string };
}

export async function getNotifications(
  getToken: GetTokenFn,
  params: { userId: string; limit?: number; cursor?: string | number },
): Promise<PaginatedResponse<UserNotification>> {
  const query = new URLSearchParams({
    user_id: params.userId,
    limit: String(params.limit ?? 20),
    cursor: String(params.cursor ?? 0),
  });
  return apiFetch<PaginatedResponse<UserNotification>>(`/notifications?${query.toString()}`, getToken);
}

export async function markNotificationRead(
  getToken: GetTokenFn,
  notificationId: string,
  userId: string,
): Promise<UserNotification> {
  const query = new URLSearchParams({ user_id: userId });
  return apiFetch<UserNotification>(`/notifications/${notificationId}/read?${query.toString()}`, getToken, {
    method: "PUT",
  });
}

export async function getUsers(getToken: GetTokenFn): Promise<PaginatedResponse<User>> {
  return apiFetch<PaginatedResponse<User>>("/users", getToken);
}

export async function getUserAssets(getToken: GetTokenFn, userId: string): Promise<UserAssetAssignment[]> {
  return apiFetch<UserAssetAssignment[]>(`/users/${userId}/assets`, getToken);
}

export async function updateUserRole(
  getToken: GetTokenFn,
  userId: string,
  role: User["role"],
): Promise<User> {
  return apiFetch<User>(`/users/${userId}/role`, getToken, {
    method: "PUT",
    body: JSON.stringify({ role }),
  });
}

export async function assignUserAsset(
  getToken: GetTokenFn,
  userId: string,
  assetId: string,
): Promise<UserAssetAssignment> {
  return apiFetch<UserAssetAssignment>(`/users/${userId}/assets`, getToken, {
    method: "POST",
    body: JSON.stringify({ asset_id: assetId }),
  });
}

export async function removeUserAsset(
  getToken: GetTokenFn,
  userId: string,
  assetId: string,
): Promise<{ ok: boolean }> {
  return apiFetch<{ ok: boolean }>(`/users/${userId}/assets/${assetId}`, getToken, {
    method: "DELETE",
  });
}
