# ADR 002: Research-Only Scope and Mandatory Watermarking for Adversarial Robustness Tooling (M2)

## Status
Accepted (Phase 2)

## Context
Adversarial perturbation techniques against vision-language models (such as CLIP) can be used for two diametrically opposed purposes:
1. **Legitimate AI safety & robustness research**: Evaluating how neural perceptual hashes, watermarks, and feature embeddings behave under distribution shifts and imperceptible perturbations.
2. **Harmful evasion**: Attempting to bypass platform content moderation, copyright detection, or trust and safety filters during livestreams.

LiveGuard is strictly committed to lawful, compliant tooling for creators. We categorically refuse to build evasion tools or facilitate moderation circumvention.

## Decision
The M2 Robustness Tester module enforces non-negotiable security and ethical constraints:
1. **Mandatory Dual Flags**: M2 refuses execution unless BOTH `--research-only` AND `--i-understand-research-use` are explicitly provided at the CLI. Omitting either results in an immediate `UsageError` (exit code 2).
2. **Permanent Visible Watermark**: A visible corner overlay ("PERTURBED") is burned into every frame of the output video. The watermark cannot be disabled by any configuration setting, command-line parameter, or environment override.
3. **Provenance & Metadata Disclosures**: M2 automatically writes XMP metadata `XMP:LiveGuard:robustness_test=true`.
4. **Audit Trail**: Every M2 execution writes an HMAC-chained audit event (`m2_research_run`) recording the input file hash and both acknowledgement hashes.
5. **Quality Guardrails**: PGD perturbation epsilon is bounded ($\le 8/255$) with automatic quality guards halting processing if SSIM drops below 0.95 or PSNR below 40.0 dB.

## Consequences
- **Positive**:
  - Eliminates the risk of M2 being weaponized as a platform evasion tool.
  - Visible watermarking and XMP tagging ensure downstream systems and human viewers clearly identify research artifacts.
  - Hard compliance gates prevent unauthorized execution in production livestreaming pipelines.
- **Negative / Trade-offs**:
  - Output video contains a visible watermark that cannot be removed, rendering it unsuitable for production livestreams (by design).
