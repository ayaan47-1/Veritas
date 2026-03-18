export type ReviewDecision = "approve" | "reject" | "edit_approve";

export type PaginatedResponse<T> = {
  items: T[];
  next_cursor: string | null;
};

export type Asset = {
  id: string;
  name: string;
  description: string | null;
  created_by: string;
  created_at: string | null;
  updated_at: string | null;
  document_count?: number;
  obligation_count?: number;
  risk_count?: number;
};

export type Obligation = {
  id: string;
  document_id: string;
  obligation_type: string;
  obligation_text: string;
  modality: string;
  due_kind: string;
  due_date: string | null;
  due_rule: string | null;
  severity: "low" | "medium" | "high" | "critical";
  status: "needs_review" | "confirmed" | "rejected";
  system_confidence: number;
  reviewer_confidence: number | null;
  has_external_reference: boolean;
  contradiction_flag: boolean;
  created_at: string | null;
};

export type Risk = {
  id: string;
  document_id: string;
  risk_type: string;
  risk_text: string;
  severity: "low" | "medium" | "high" | "critical";
  status: "needs_review" | "confirmed" | "rejected";
  system_confidence: number;
  reviewer_confidence: number | null;
  has_external_reference: boolean;
  contradiction_flag: boolean;
  created_at: string | null;
};

export type CurrentUser = {
  id: string;
  email: string;
  name: string;
  role: "admin" | "reviewer" | "viewer";
  oidc_provider: string;
  is_active: boolean;
  created_at: string | null;
  last_login_at: string | null;
};

export type DocumentSummary = {
  id: string;
  asset_id: string;
  source_name: string;
  doc_type: string;
  parse_status: string;
  uploaded_by: string;
  uploaded_at: string | null;
  total_pages: number | null;
  scanned_page_count: number;
};

export type ReviewPayload = {
  decision: ReviewDecision;
  reviewer_id: string;
  reviewer_confidence: number;
  reason?: string;
  field_edits?: Record<string, unknown>;
};
