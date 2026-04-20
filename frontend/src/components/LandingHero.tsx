"use client";

import { SignInButton, SignUpButton } from "@clerk/nextjs";
import styles from "./LandingHero.module.css";

export default function LandingHero() {
  return (
    <div className={styles.landing}>
      <main className={styles.main}>
        <div className={styles.hero}>
          <div className={styles.eyebrow}>
            <span className={styles.rule} />
            <span>AI operational intelligence layer</span>
            <span className={styles.rule} />
          </div>

          <div className={styles.logo}>
            <div className={styles.wordmark} aria-label="VeritasLayer">
              <span className={styles.veritas}>Veritas</span>
              <span className={styles.layer}>Layer</span>
            </div>
            <div className={styles.anchor} aria-hidden="true">
              <span className={styles.tick} />
              <span className={styles.quote}>
                &ldquo;…contractor <span className={styles.quoteAccent}>shall</span> deliver&nbsp;…&rdquo;
              </span>
              <span className={styles.sep}>·</span>
              <span>p.7</span>
              <span className={styles.sep}>·</span>
              <span>[412:439]</span>
            </div>
          </div>

          <p className={styles.desc}>
            A truth layer for operational documents — every obligation, risk and deadline traced back to an{" "}
            <span className={styles.em}>exact verbatim quote</span>.
          </p>

          <p className={styles.sub}>
            Veritas ingests your PDFs and produces structured, auditable intelligence. No claim without verifiable
            evidence: document, page, quote, and character offsets.
          </p>

          <div className={styles.ctaRow}>
            <SignUpButton mode="modal">
              <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}>
                Get started
                <span className={styles.arrow} aria-hidden="true">
                  →
                </span>
              </button>
            </SignUpButton>
            <SignInButton mode="modal">
              <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}>
                Log in
              </button>
            </SignInButton>
          </div>

          <div className={styles.pillars}>
            <div className={styles.pillar}>
              <div className={styles.pillarNum}>01 / EVIDENCE-FIRST</div>
              <div className={styles.pillarTitle}>Every claim, anchored</div>
              <div className={styles.pillarDesc}>document_id, page, quote, char_start, char_end.</div>
            </div>
            <div className={styles.pillar}>
              <div className={styles.pillarNum}>02 / QUOTE-FIRST</div>
              <div className={styles.pillarTitle}>Extract, then verify</div>
              <div className={styles.pillarDesc}>
                Verbatim quotes are confirmed in page text before any structured field is filled.
              </div>
            </div>
            <div className={styles.pillar}>
              <div className={styles.pillarNum}>03 / PRECISION-GATED</div>
              <div className={styles.pillarTitle}>Confidence thresholds</div>
              <div className={styles.pillarDesc}>≥80 confirmed · 50–79 needs review · &lt;50 rejected.</div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
