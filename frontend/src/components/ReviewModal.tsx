"use client";

import { useEffect, useState } from "react";

import type { ReviewDecision } from "@/lib/types";

type Severity = "low" | "medium" | "high" | "critical";

type ObligationInitialValues = {
  text: string;
  severity: Severity;
};

type RiskInitialValues = {
  text: string;
  severity: Severity;
  risk_type: string;
};

type ReviewModalProps = {
  open: boolean;
  title: string;
  initialDecision?: ReviewDecision;
  itemType: "obligation" | "risk";
  initialValues?: ObligationInitialValues | RiskInitialValues;
  onClose: () => void;
  onSubmit: (payload: {
    decision: ReviewDecision;
    reviewer_confidence: number;
    reason?: string;
    field_edits?: Record<string, unknown>;
  }) => Promise<void>;
};

const SEVERITY_OPTIONS: Severity[] = ["low", "medium", "high", "critical"];

const RISK_TYPE_OPTIONS = [
  "financial",
  "schedule",
  "quality",
  "safety",
  "compliance",
  "contractual",
  "unknown_risk",
];

const inputClass =
  "w-full rounded-xl border border-border bg-bg-subtle px-3 py-2 text-sm text-text-primary outline-none transition-colors focus:border-border-strong";

export default function ReviewModal({
  open,
  title,
  initialDecision = "approve",
  itemType,
  initialValues,
  onClose,
  onSubmit,
}: ReviewModalProps) {
  const [decision, setDecision] = useState<ReviewDecision>(initialDecision);
  const [confidence, setConfidence] = useState(75);
  const [reason, setReason] = useState("");
  const [editText, setEditText] = useState("");
  const [editSeverity, setEditSeverity] = useState<Severity>("medium");
  const [editRiskType, setEditRiskType] = useState("financial");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    setDecision(initialDecision);
    setConfidence(75);
    setReason("");
    setError(null);
    setIsSubmitting(false);
    setEditText(initialValues?.text ?? "");
    setEditSeverity(initialValues?.severity ?? "medium");
    if (itemType === "risk" && initialValues) {
      setEditRiskType((initialValues as RiskInitialValues).risk_type ?? "financial");
    }
  }, [open, initialDecision, initialValues, itemType]);

  if (!open) {
    return null;
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    let field_edits: Record<string, unknown> | undefined;
    if (decision === "edit_approve" && initialValues) {
      field_edits = {};
      const textKey = itemType === "obligation" ? "obligation_text" : "risk_text";
      if (editText !== initialValues.text) {
        field_edits[textKey] = editText;
      }
      if (editSeverity !== initialValues.severity) {
        field_edits["severity"] = editSeverity;
      }
      if (itemType === "risk") {
        const orig = (initialValues as RiskInitialValues).risk_type;
        if (editRiskType !== orig) {
          field_edits["risk_type"] = editRiskType;
        }
      }
      if (Object.keys(field_edits).length === 0) {
        field_edits = undefined;
      }
    }

    try {
      await onSubmit({
        decision,
        reviewer_confidence: confidence,
        reason: reason.trim() || undefined,
        field_edits,
      });
      onClose();
    } catch (submitError) {
      const message = submitError instanceof Error ? submitError.message : "Review submission failed";
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-brand/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-xl rounded-2xl border border-border bg-surface p-7 shadow-2xl">
        <h3 className="font-serif text-xl text-text-primary">Review Item</h3>
        <p className="mt-2 text-sm leading-relaxed text-text-secondary">{title}</p>

        <form className="mt-6 space-y-5" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-widest text-text-tertiary">Decision</p>
            <div className="flex flex-wrap gap-2">
              {(["approve", "reject", "edit_approve"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setDecision(option)}
                  className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${
                    decision === option
                      ? "border-brand bg-brand text-bg"
                      : "border-border text-text-secondary hover:border-border-strong hover:text-text-primary"
                  }`}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          {decision === "edit_approve" && (
            <div className="space-y-3 rounded-xl border border-border bg-bg-subtle p-4">
              <p className="text-xs font-medium uppercase tracking-widest text-text-tertiary">Edit Fields</p>

              <label className="block">
                <span className="mb-1 block text-xs text-text-secondary">
                  {itemType === "obligation" ? "Obligation text" : "Risk text"}
                </span>
                <textarea
                  className={`${inputClass} h-24 resize-y`}
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                />
              </label>

              <label className="block">
                <span className="mb-1 block text-xs text-text-secondary">Severity</span>
                <select
                  className={inputClass}
                  value={editSeverity}
                  onChange={(e) => setEditSeverity(e.target.value as Severity)}
                >
                  {SEVERITY_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </label>

              {itemType === "risk" && (
                <label className="block">
                  <span className="mb-1 block text-xs text-text-secondary">Risk type</span>
                  <select
                    className={inputClass}
                    value={editRiskType}
                    onChange={(e) => setEditRiskType(e.target.value)}
                  >
                    {RISK_TYPE_OPTIONS.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
          )}

          <label className="block">
            <span className="text-xs font-medium uppercase tracking-widest text-text-tertiary">
              Confidence: {confidence}%
            </span>
            <input
              className="mt-2 w-full"
              style={{ accentColor: "var(--accent)" }}
              type="range"
              min={0}
              max={100}
              value={confidence}
              onChange={(event) => setConfidence(Number(event.target.value))}
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium uppercase tracking-widest text-text-tertiary">Reason (optional)</span>
            <textarea
              className="mt-2 h-24 w-full rounded-xl border border-border bg-bg-subtle p-3 text-sm text-text-primary outline-none transition-colors focus:border-border-strong focus:ring-0"
              placeholder="Why this decision?"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>

          {error ? (
            <p className="rounded-lg bg-danger-subtle px-3 py-2 text-sm font-medium text-danger">{error}</p>
          ) : null}

          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-full border border-border px-4 py-2 text-sm text-text-secondary transition-colors hover:border-border-strong hover:text-text-primary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="rounded-full bg-brand px-4 py-2 text-sm font-medium text-bg transition-opacity disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isSubmitting ? "Saving..." : "Submit Review"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
