export type ReviewDecision = "approve" | "reject" | "edit_approve";

export type PaginatedResponse<T> = {
  items: T[];
  next_cursor: string | null;
  total?: number;
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
  pending_review_count?: number;
};

export type Obligation = {
  id: string;
  document_id: string;
  domain?: string | null;
  document_domain?: string | null;
  obligation_type: string;
  obligation_text: string;
  modality: string;
  responsible_entity_id?: string | null;
  due_kind: string;
  due_date: string | null;
  due_rule: string | null;
  trigger_date?: string | null;
  severity: "low" | "medium" | "high" | "critical";
  status: "needs_review" | "confirmed" | "rejected";
  system_confidence: number;
  reviewer_confidence: number | null;
  llm_severity: "low" | "medium" | "high" | "critical" | null;
  llm_quality_confidence: number | null;
  critic_valid: boolean | null;
  critic_confidence: number | null;
  critic_reasoning: string | null;
  has_external_reference: boolean;
  contradiction_flag: boolean;
  created_at: string | null;
  updated_at?: string | null;
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
  llm_severity: "low" | "medium" | "high" | "critical" | null;
  llm_quality_confidence: number | null;
  critic_valid: boolean | null;
  critic_confidence: number | null;
  critic_reasoning: string | null;
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
  domain: string | null;
  parse_status: string;
  uploaded_by: string;
  uploaded_at: string | null;
  total_pages: number | null;
  scanned_page_count: number;
};

export type DocumentDetail = {
  id: string;
  asset_id: string;
  source_name: string;
  mime_type: string;
  uploaded_at: string | null;
  doc_type: string;
  domain: string | null;
  parse_status: string;
  total_pages: number | null;
  scanned_page_count: number;
};

export type DocumentStatus = {
  document_id: string;
  parse_status: string;
  total_pages: number | null;
  pages_processed: number;
  pages_failed: number;
};

export type BulkIngestSuccess = {
  filename: string;
  document_id: string;
};

export type BulkIngestFailure = {
  filename: string;
  reason: string;
};

export type BulkIngestResponse = {
  succeeded: BulkIngestSuccess[];
  failed: BulkIngestFailure[];
};

export type TextSpan = {
  id: string;
  char_start: number;
  char_end: number;
  bbox_x1: number;
  bbox_y1: number;
  bbox_x2: number;
  bbox_y2: number;
  span_text: string;
};

export type DocumentPage = {
  document_id: string;
  page_number: number;
  raw_text: string;
  normalized_text: string;
  text_source: string;
  processing_status: string;
  processing_error: string | null;
  text_spans: TextSpan[];
};

export type ObligationEvidence = {
  id: string;
  document_id: string;
  page_number: number;
  quote: string;
  raw_char_start: number;
  raw_char_end: number;
  normalized_char_start: number;
  normalized_char_end: number;
  source: string;
};

export type ObligationDetail = Obligation & {
  evidence: ObligationEvidence[];
};

export type RiskEvidence = {
  id: string;
  document_id: string;
  page_number: number;
  quote: string;
  raw_char_start: number;
  raw_char_end: number;
  normalized_char_start: number;
  normalized_char_end: number;
  source: string;
};

export type RiskDetail = Risk & {
  evidence: RiskEvidence[];
};

export type NotificationEvent = {
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string | null;
};

export type UserNotification = {
  id: string;
  user_id: string;
  event_id: string;
  channel: string;
  status: "pending" | "sent" | "failed" | "read";
  sent_at: string | null;
  read_at: string | null;
  event: NotificationEvent | null;
};

export type ConfigResponse = {
  base: Record<string, unknown>;
  overrides: Record<string, unknown>;
  effective: Record<string, unknown>;
};

export type User = CurrentUser;

export type UserAssetAssignment = {
  id: string;
  user_id: string;
  asset_id: string;
};

export type ReviewPayload = {
  decision: ReviewDecision;
  reviewer_id: string;
  reviewer_confidence: number;
  reason?: string;
  field_edits?: Record<string, unknown>;
};
