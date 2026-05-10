# Responsible AI Brief: Invoice-PO Matching Solution (D4)

## Slide 1: Bias & Fairness Risks

### Identified Bias Vectors

#### 1. **Vendor Size Bias** (High Risk)
**Issue:** Training data (AcmeMini) likely skewed toward large vendors
- 300 labelled mismatches may over-represent top 10 vendors
- Small vendors underrepresented → model undertrained for them

**Manifestation:**
- Model may have lower precision/recall for small vendors
- Invoice from small vendor with typos in description → likely flagged (false positive)
- System appears to discriminate against smaller suppliers

**Mitigation:**
- **Stratified retraining**: Collect ≥50 labelled examples per vendor tier (top 10, mid, long tail)
- **Monitoring**: Dashboard showing precision/recall breakdown by vendor_id
- **Threshold calibration**: Allow per-vendor decision thresholds if bias confirmed
- **Quarterly review**: Retrain on balanced dataset; audit for vendor-wise performance delta

#### 2. **Amount Variance Under-Detection** (Medium Risk)
**Issue:** Model trained on historical mismatches; may miss systematic under-billing or over-billing

**Manifestation:**
- New vendor consistently undercharges by 20% → not caught (amount_similarity rule=0.8, within tolerance)
- If fraud-like pattern: Lost revenue
- Business fairness question: Do we accept systematic discounts?

**Mitigation:**
- **Anomaly detection**: Flag invoices with amount_diff > 2σ from vendor baseline
- **Manual review rules**: Large invoices (>$50k) always require human approval
- **Vendor history**: Track historical discount patterns; alert if deviates
- **Transparency**: Log all accepted mismatches; analyze post-hoc for patterns

---

## Slide 2: Data Privacy, Audit & Model Ops

### Data Privacy & Compliance

#### 1. **PII in Invoices**
**Data Collected:**
- Vendor names, invoice amounts, dates, line-item descriptions
- Sensitive? Potentially (financial data + vendor relationships are confidential)

**Current Handling:**
- ✓ CSV files stored locally (not uploaded to cloud LLMs)
- ✓ Email drafts use aggregated reason, not full invoice text
- ✓ Model artifacts don't memorize PII (TF-IDF is bag-of-words, not embedding)
- ✗ Audit logs contain invoice_id + vendor_id (linkable to PII)

**Compliance Risk:**
- GDPR: Vendor data may qualify as personal data if vendor = person
- SOX: Financial audit trail required; logs must be retained 7 years
- Procurement confidentiality: Vendor agreements often prohibit sharing pricing

**Guardrails:**
- **Data minimization**: Audit logs use invoice_id only; vendor_id stored separately
- **Access control**: Approval API requires analyst credentials; log all approvals
- **Retention policy**: Auto-delete audit logs after 7 years (configurable)
- **Encryption at rest**: CSVs encrypted if deployed to cloud (TLS for API)

#### 2. **Model Explainability & Audit Trail**
**Requirement:** System must answer: "Why was this invoice rejected?"

**Design:**
- **Rule scores** logged for every match (vendor, date, amount, description overlap)
- **ML coefficient** contribution computed (logistic regression: β_i * x_i per feature)
- **Audit log** immutable; stored in database + append-only S3 bucket
- **Human approval** logged with analyst ID + timestamp

**Example Audit Log:**
```json
{
  "invoice_id": "INV-12345",
  "po_id": "PO-987",
  "timestamp": "2024-05-10T14:32:00Z",
  "node": "match_invoice_to_po",
  "action": "scoring",
  "result": {
    "vendor_match": 1.0,
    "date_proximity": 0.95,
    "amount_similarity": 0.65,
    "desc_overlap": 0.4,
    "ml_score": 0.58,
    "decision": "flag_for_review",
    "reason": "Amount mismatch (15% variance); description overlap low"
  }
}
```

**Audit Capability:**
- Query all decisions for vendor X in quarter Q
- Correlate rejections with subsequent vendor disputes
- Detect model drift (e.g., precision drops from 0.85 to 0.72)
- Prove fairness: "This vendor has identical F1 score as competitors"

#### 3. **Model Monitoring & Ops**

**Monitoring KPIs:**
1. **Accuracy Drift**: Track precision/recall on new labelled data (monthly)
   - Alert if precision drops >5% or recall drops >10%
2. **Distribution Shift**: Monitor prediction confidence distribution
   - Alert if mean confidence drops (model uncertainty increasing)
3. **Fairness Metrics**: Precision/recall per vendor tier
   - Alert if small vendors have >10% lower F1 than large vendors
4. **Human Approval Rate**: % of flagged invoices approved by analysts
   - If >95% approved: model over-conservative (raise threshold)
   - If <80% approved: model over-aggressive (lower threshold)
5. **Latency**: API response time for /reconcile endpoint
   - Alert if p95 latency >5 seconds (indicates model regression or data bloat)

**Retraining Schedule:**
- **Trigger**: New labelled data available (quarterly or >100 new examples)
- **Process**:
  1. Collect recent invoice-PO pairs + human labels
  2. Stratify by vendor tier; ensure balanced dataset
  3. Retrain model on new data (takes <1 hour)
  4. Evaluate on hold-out test set
  5. A/B test: Compare new vs. old model on production traffic (10% sample)
  6. If F1 improved or equivalent: deploy; otherwise, revert

**Model Versioning:**
- Git-track model artifacts (TF-IDF vocabulary, LR coefficients)
- Tag each model version: `model_v1.2_2024-05-10.pkl`
- Maintain rollback capability (previous 3 versions)

#### 4. **Escalation & Human Override**
**Design Principle:** Model is advisor, not dictator

**Scenarios:**
- Analyst overrides model decision → Logged with reason (e.g., "vendor has known data quality issues")
- System learns from overrides → Included in next retraining cycle
- Recurring overrides → Flag for manual rule creation (e.g., "exclude vendor X from amount_similarity rule")

**Example:** Vendor "Acme Corp" chronically submits invoices 30 days late. Model flags many as mismatches. Analyst overrides 50+ times. System recommends: "Add 30-day buffer for vendor X in date_proximity rule."

---

## Recommended Actions

### Immediate (Before Deployment)
- [ ] Document all bias assumptions in model card (vendor distribution, mismatch types)
- [ ] Set up monitoring dashboard for KPIs
- [ ] Implement access control + audit logging for API
- [ ] Encrypt CSV files at rest

### Short-term (Weeks 1-4)
- [ ] Collect analyst overrides; analyze for patterns
- [ ] Stratify training data by vendor size; retrain if imbalanced
- [ ] Run fairness audit: compare F1 scores by vendor tier
- [ ] A/B test threshold tuning (precision vs. recall)

### Ongoing (Quarterly)
- [ ] Retrain model on new labelled data
- [ ] Audit 50 random human approvals + rejections
- [ ] Review PII handling; ensure compliance with latest GDPR/SOX
- [ ] Analyze false positives; adjust rules if systematic bias found

---

## Conclusion

This solution prioritizes **explainability over accuracy** and **human oversight over full automation**. Every decision is auditable; every risk is monitored. As the system scales, invest in:
1. Fairness monitoring (per-vendor metrics)
2. Privacy-preserving techniques (data minimization, pseudonymization)
3. Continuous model evaluation (retraining + A/B testing)
4. Human-in-the-loop design (approval gates, override logging)
