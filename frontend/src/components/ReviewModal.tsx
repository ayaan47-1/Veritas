"use client";

import { useEffect, useState } from "react";

import type { ReviewDecision } from "@/lib/types";

type ReviewModalProps = {
  open: boolean;
  title: string;
  initialDecision?: ReviewDecision;
  onClose: () => void;
  onSubmit: (payload: {
    decision: ReviewDecision;
    reviewer_confidence: number;
    reason?: string;
    field_edits?: Record<string, unknown>;
  }) => Promise<void>;
};

export default function ReviewModal({
  open,
  title,
  initialDecision = "approve",
  onClose,
  onSubmit,
}: ReviewModalProps) {
  const [decision, setDecision] = useState<ReviewDecision>(initialDecision);
  const [confidence, setConfidence] = useState(75);
  const [reason, setReason] = useState("");
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
  }, [open, initialDecision]);

  if (!open) {
    return null;
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await onSubmit({
        decision,
        reviewer_confidence: confidence,
        reason: reason.trim() || undefined,
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
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/55 p-4 backdrop-blur-sm">
      <div className="w-full max-w-xl rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl">
        <h3 className="text-lg font-semibold text-slate-900">Review Item</h3>
        <p className="mt-2 text-sm text-slate-600">{title}</p>

        <form className="mt-5 space-y-5" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Decision</p>
            <div className="flex flex-wrap gap-2">
              {(["approve", "reject", "edit_approve"] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setDecision(option)}
                  className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${
                    decision === option
                      ? "border-cyan-700 bg-cyan-600 text-white"
                      : "border-slate-300 bg-white text-slate-700"
                  }`}
                >
                  {option}
                </button>
              ))}
            </div>
          </div>

          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Reviewer Confidence: {confidence}
            </span>
            <input
              className="mt-2 w-full accent-cyan-600"
              type="range"
              min={0}
              max={100}
              value={confidence}
              onChange={(event) => setConfidence(Number(event.target.value))}
            />
          </label>

          <label className="block">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Reason (optional)</span>
            <textarea
              className="mt-2 h-24 w-full rounded-xl border border-slate-300 p-3 text-sm text-slate-900 outline-none ring-cyan-600 focus:ring"
              placeholder="Why this decision?"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>

          {error ? <p className="text-sm font-medium text-rose-600">{error}</p> : null}

          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-full border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="rounded-full bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-55"
            >
              {isSubmitting ? "Saving..." : "Submit Review"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
