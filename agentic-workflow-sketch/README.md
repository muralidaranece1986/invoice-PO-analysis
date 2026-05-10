# Invoice-PO Matching: Complete B2B Reconciliation Solution

**A production-grade, defensible architecture for automated invoice-to-purchase-order matching with agentic workflow, human-in-the-loop approval gates, and full audit trails.**

## 📋 Contents

This repository delivers **6 comprehensive artifacts** for invoice-PO reconciliation:

- **[D1](docs/D1_ARCHITECTURE_DECK.md)** - 12-slide architecture deck with design rationale, trade-off analysis, and multi-cloud portability
- **[D2](notebooks/01_invoice_po_matching_model.ipynb)** - Production notebook: TF-IDF + logistic regression model with precision/recall evaluation
- **[D3](src/api/main.py)** - FastAPI service with LangGraph agentic workflow (matching → planning → drafting → approval → execution)
- **[D4](docs/D4_RESPONSIBLE_AI_BRIEF.md)** - 2-slide responsible AI brief: bias risks, privacy, audit trails, model ops
- **[D5](README.md)** - This README: setup, assumptions, and execution instructions
- **Rationale** - Embedded throughout: explicit trade-off thinking (why LangGraph over AutoGen, why rules+ML not deep learning, etc.)

---

## 🎯 Quick Start

### Prerequisites
- Python 3.10+ (tested on 3.10.11)
- `pip` or `conda` (or `poetry` if preferred)
- ~2GB disk space
- Internet connection (to download dependencies; can work offline with cached wheels)

### Setup (5 minutes)

```bash
# Clone or download repository
git clone https://github.com/your-org/invoice-po-matching.git
cd invoice-po-matching

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Verify data files are present
ls data/
# Expected output:
#   invoices.csv
#   po_grn.csv
#   labelled_mismatches.csv
```

### Run D2 (Matching Model Notebook)

```bash
# Start Jupyter (or use VS Code, PyCharm, etc.)
jupyter notebook

# Open: notebooks/01_invoice_po_matching_model.ipynb
# Run all cells; outputs include:
#   - Model training: precision, recall, F1-score
#   - Threshold analysis: tuning trade-offs
#   - Model artifacts saved to: src/matcher/model_artifacts.pkl
```

**Expected Output:**
```
Invoices: 1502 rows
POs: 300 rows
Labelled mismatches: 300 rows

TEST SET METRICS
========================================================
Precision: 0.8235
Recall:    0.7658
F1-Score:  0.7938

[Model artifacts saved to src/matcher/model_artifacts.pkl]
```

**Time to run:** ~30 seconds on CPU

### Run D3 (FastAPI Service)

```bash
# Start API server
python -m src.api.main

# Expected output:
# INFO:     Uvicorn running on http://0.0.0.0:8000
# [Matcher] Loaded model from src/matcher/model_artifacts.pkl
# [Matcher] TF-IDF vectorizer loaded; 50 features

# Test endpoint (in another terminal)
curl -X GET http://localhost:8000/health

# Expected response:
# {"status":"healthy","service":"invoice-po-reconciliation","timestamp":"..."}
```

### Test Reconciliation Workflow

```bash
# Start a reconciliation
curl -X POST http://localhost:8000/reconcile \
  -H "Content-Type: application/json" \
  -d '{
    "invoice": {
      "invoice_id": "INV-001",
      "vendor_id": "V001",
      "invoice_date": "2024-05-10",
      "amount": 1500.00,
      "description": "Hardware supplies"
    },
    "po": {
      "po_id": "PO-100",
      "vendor_id": "V001",
      "po_date": "2024-04-15",
      "line_item_amount": 1500.00,
      "description": "Computer equipment"
    }
  }'

# Expected response:
{
  "invoice_id": "INV-001",
  "po_id": "PO-100",
  "status": "executed",
  "planned_action": "auto_match",
  "match_confidence": 0.92,
  "requires_approval": false,
  "draft_email": null,
  "audit_log": [...]
}
```

### Approve a Paused Workflow

If the match is uncertain (requires human approval):

```bash
# API returns status="requires_approval"; requires_approval=true
# Human analyst reviews draft_email and calls:

curl -X POST http://localhost:8000/reconcile/INV-001/approve \
  -H "Content-Type: application/json" \
  -d '{
    "approved": true,
    "notes": "Verified with vendor; amount difference due to freight"
  }'

# Response:
{
  "invoice_id": "INV-001",
  "status": "executed",
  "action_taken": "flag_for_review"
}
```

---

## 📁 Project Structure

```
invoice-po-matching/
├── README.md                          # This file (D5)
├── requirements.txt                   # Python dependencies
├── data/
│   ├── invoices.csv                   # 1502 invoices (columns: invoice_id, vendor_id, invoice_date, amount, description)
│   ├── po_grn.csv                     # 300 POs (columns: po_id, vendor_id, po_date, line_item_amount, description)
│   └── labelled_mismatches.csv        # 300 labelled mismatch pairs (columns: invoice_id, po_id)
│
├── notebooks/
│   └── 01_invoice_po_matching_model.ipynb  # D2: Notebook with model training, evaluation, inference
│
├── src/
│   ├── matcher/
│   │   ├── __init__.py
│   │   ├── matcher.py                 # Core matching logic (rules + ML scoring)
│   │   ├── model_artifacts.pkl        # Pre-trained TF-IDF + LogReg (generated by D2)
│   │   └── model_metrics.json         # Model performance (precision, recall, F1)
│   │
│   ├── workflow/
│   │   └── reconciliation_agent.py    # LangGraph workflow (D3 core logic)
│   │
│   └── api/
│       └── main.py                    # FastAPI service (D3 endpoints)
│
├── docs/
│   ├── D1_ARCHITECTURE_DECK.md        # 12-slide architecture + design rationale
│   └── D4_RESPONSIBLE_AI_BRIEF.md     # Bias, privacy, audit, model ops
│
└── docker/
    └── Dockerfile                      # Container image for production deployment
```

---

## 🔄 End-to-End Workflow Example

### Scenario: Reconcile 10 Invoices (Batch)

```python
# Python script to batch reconcile
import pandas as pd
from src.matcher import InvoicePoMatcher

# Load data
invoices = pd.read_csv('data/invoices.csv')
pos = pd.read_csv('data/po_grn.csv')

# Initialize matcher
matcher = InvoicePoMatcher(model_path='src/matcher/model_artifacts.pkl')

# Batch reconcile first 10 invoices
results = matcher.batch_match(invoices.head(10), pos, return_all_candidates=False)

# Print results
print(results[['invoice_id', 'po_id', 'matched', 'confidence', 'reason']])
#   invoice_id  po_id  matched  confidence                               reason
# 0  INV-001    PO-45     True        0.92  Match (ML): vendor=1.0, date_prox=0.95...
# 1  INV-002    PO-50     False       0.34  Match (Rules): vendor=1.0, date_prox=0.5...
# ...
```

### Scenario: Handle a Disputed Invoice

1. **System detects mismatch** (confidence=0.45, below threshold)
2. **LangGraph workflow**:
   - Node 1: Scores invoice-PO pair → confidence=0.45
   - Node 2: Plans action → action=REQUIRES_VENDOR_CONTACT
   - Node 3: Drafts email → "Dear Acme Corp, Your invoice INV-999 does not match PO-50..."
   - Node 4: Approval gate → **PAUSES, waits for human**
   - Node 5: (If approved) Sends email and logs action
3. **Human analyst**:
   - Calls `/reconcile/INV-999` to view draft email + audit log
   - Investigates: Checks vendor communication system, finds shipment delay
   - Calls `/reconcile/INV-999/approve` with `{"approved": true, "notes": "Shipment delayed; vendor aware"}`
4. **System executes**: Email is sent, workflow logs approval + action

---

## ⚙️ Configuration

### Matching Thresholds

Edit `src/matcher/matcher.py`, class `InvoicePoMatcher.__init__()`:

```python
self.config = {
    'date_window_days': 180,           # Invoices older than 6 months = mismatch
    'amount_tolerance_pct': 0.15,      # Amount can differ by ±15%
    'ml_threshold': 0.50,              # ML confidence threshold for match
    'vendor_match_required': True,     # Vendor ID must match exactly
}
```

**Trade-off: Precision vs. Recall**
- **Increase `ml_threshold`** (e.g., 0.60) → Fewer false positives (higher precision), more misses (lower recall)
- **Decrease `ml_threshold`** (e.g., 0.40) → Catch more matches (higher recall), more false positives (lower precision)

**Recommendation:** Start at 0.50; A/B test with analyst feedback.

---

## 🧪 Testing

### Unit Tests (D2 Notebook)

The notebook includes validation:
1. **Data validation**: Check nulls, data types, ranges
2. **Feature engineering**: Verify rule scores are in [0, 1]
3. **Model training**: Confirm convergence, no NaN coefficients
4. **Evaluation**: Precision, recall, confusion matrix

### Integration Tests (D3 API)

```bash
# Run FastAPI tests (requires pytest)
pip install pytest pytest-asyncio
pytest tests/  # (test directory not included; add as needed)
```

### Manual Testing Checklist

- [ ] D2 Notebook runs end-to-end without errors
- [ ] Model metrics (precision, recall) match expected thresholds (>0.75)
- [ ] D3 API starts successfully; `/health` returns 200
- [ ] `/reconcile` endpoint accepts POST with correct schema
- [ ] High-confidence match returns `status=executed`, `requires_approval=false`
- [ ] Low-confidence match returns `status=requires_approval`, `draft_email` populated
- [ ] `/approve` endpoint correctly updates workflow state
- [ ] Audit logs are complete and traceable

---

## 📊 Assumptions & Limitations

### Assumptions Made

1. **Data Quality**
   - Vendor IDs are unique and consistent (no "ACME" vs "Acme Corp")
   - Dates are valid; amounts are positive
   - Descriptions are present (may be empty strings)
   - Training data (labelled mismatches) is representative of production

2. **Business Rules**
   - Vendor ID must match exactly (no fuzzy matching)
   - Invoices are matched to POs within 6 months
   - Amount tolerance is 15% (adjust per business policy)
   - One invoice matches one PO (1:1 matching, not 1:many)

3. **Infrastructure**
   - Single-machine deployment (CSV data in memory)
   - No high-availability requirements (restart OK)
   - API called synchronously (no queueing system)

4. **Human Workflow**
   - Analysts approve/reject within 1 hour (no long-running SLAs)
   - Draft emails are reviewed before sending (no auto-send)
   - Overrides are logged and reviewed (for model improvement)

### Limitations & Future Work

| Limitation | Impact | Mitigation |
|-----------|--------|-----------|
| CSV data → 300 POs max | Scales to ~10k POs on single machine | Migrate to PostgreSQL + index on vendor_id |
| No fuzzy vendor matching | Typos ("ACME" vs "ACME INC") cause mismatches | Add Levenshtein matching; vendor master data |
| 15% amount tolerance | May miss fraud or systematic discounts | Add anomaly detection; per-vendor baselines |
| Single CPU inference | ~50 invoices/sec; slow for 10k batch | Parallelize across cores; containerize |
| Mock LLM client | Email drafts are dummy text | Integrate OpenAI/Anthropic; add prompt engineering |
| In-memory workflow state | Lost on API restart | Add persistence (Redis, PostgreSQL) |
| No real-time monitoring | Drift goes undetected | Implement Prometheus metrics + dashboards |

---

## 🚀 Deployment

### Local Development

```bash
python -m src.api.main
```

### Docker

```bash
# Build image
docker build -f docker/Dockerfile -t invoice-po-matcher:latest .

# Run container
docker run -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/src/matcher:/app/src/matcher \
  invoice-po-matcher:latest

# Test
curl http://localhost:8000/health
```

### Cloud Deployment (AWS ECS / GCP Cloud Run / Azure Container Instances)

1. Push image to registry (ECR, GCR, ACR)
2. Update `/data` mount to cloud storage (S3, GCS, Azure Blob)
3. Configure API key for access control
4. Enable logging to CloudWatch / Stackdriver / Application Insights

**See [D1 Slide 9](docs/D1_ARCHITECTURE_DECK.md#slide-9-multi-cloud-portability) for cloud-agnostic design.**

---

## 📖 Reading Guide

### For Decision-Makers
1. Start with [D1 Slide 1-2](docs/D1_ARCHITECTURE_DECK.md#slide-1-problem-statement--business-context) (problem + layers)
2. Review [D1 Slide 12](docs/D1_ARCHITECTURE_DECK.md#slide-12-trade-offs-summary) (trade-offs summary table)
3. Skim [D4](docs/D4_RESPONSIBLE_AI_BRIEF.md) (bias, privacy, audit)

### For ML Engineers
1. Study [D2 Notebook](notebooks/01_invoice_po_matching_model.ipynb) (feature engineering, model training)
2. Review [D1 Slide 5-6](docs/D1_ARCHITECTURE_DECK.md#slide-5-ml-model-selection) (ML design choices)
3. Check [D4 Slide 2](docs/D4_RESPONSIBLE_AI_BRIEF.md#slide-2-data-privacy--audit--model-ops) (model monitoring)

### For Software Engineers
1. Understand [D1 Slide 7-8](docs/D1_ARCHITECTURE_DECK.md#slide-7-workflow-layer-langgraph) (workflow + API)
2. Read [src/workflow/reconciliation_agent.py](src/workflow/reconciliation_agent.py) (LangGraph implementation)
3. Review [src/api/main.py](src/api/main.py) (FastAPI endpoints)

### For Auditors / Compliance
1. Read [D4](docs/D4_RESPONSIBLE_AI_BRIEF.md) (bias, privacy, audit trails)
2. Review [D1 Slide 11](docs/D1_ARCHITECTURE_DECK.md#slide-11-security-privacy--audit) (security + audit)
3. Check audit log format in [src/workflow/reconciliation_agent.py](src/workflow/reconciliation_agent.py#L480-L510)

---

## 🤝 Contributing

To improve this solution:

1. **New labelled data**: Add to `data/labelled_mismatches.csv`; retrain D2
2. **Threshold tuning**: Adjust `config` in `src/matcher/matcher.py`; monitor precision/recall
3. **Bias investigation**: Run fairness audit (precision by vendor_id); add monitoring
4. **Cloud migration**: Implement storage adapter (S3Storage, GCSStorage); update D3 to load from cloud

---

## 📝 License

This solution is provided as-is for educational and commercial use. See LICENSE.

---

## 🆘 Troubleshooting

### Issue: `ModuleNotFoundError: No module named 'langgraph'`

```bash
pip install langgraph==0.0.33
```

### Issue: D2 Notebook model saves but D3 can't load it

```bash
# Ensure model path is correct and readable
ls -la src/matcher/model_artifacts.pkl
# If missing, re-run D2 notebook
```

### Issue: `/reconcile` endpoint returns 500 error

Check API logs:
```bash
# Logs printed to console; look for [Matcher] or [Workflow] lines
# Ensure data/ CSVs are present and readable
```

### Issue: Precision/Recall lower than expected

1. Verify training data balance (use threshold analysis in D2)
2. Check date formats in invoices/pos CSVs (should be YYYY-MM-DD)
3. Inspect rule scores for a sample pair:
   ```python
   from src.matcher import InvoicePoMatcher
   matcher = InvoicePoMatcher()
   result = matcher._score_pair(inv_row, po_row)
   print(result.rule_details)
   ```

---

## 📞 Contact & Support

For questions, issues, or improvements, open a GitHub issue or contact the team.

---

## Appendix: Trade-Off Rationale Quick Reference

| Decision | **Why** | **Trade-off** |
|----------|--------|--------------|
| **Rules + ML** | Transparent, fast, explainable | Slightly lower accuracy than pure ML |
| **LangGraph** | Explicit control, human gates | More boilerplate than AutoGen |
| **CSV storage** | Portable, simple | Can't scale beyond 10k POs |
| **Logistic Regression** | Interpretable, fast | Limited to linear decision boundary |
| **TF-IDF text** | Efficient, sparse | Loses word order (bag-of-words) |
| **Cloud-agnostic** | No vendor lock-in, flexible | Less optimized than cloud-native |
| **Human approval** | Prevents vendor disputes | Slower than fully automated |
| **Local-first data** | Privacy-preserving, portable | Doesn't leverage cloud ML services |

**Central Design Philosophy:** Explainability and auditability > bleeding-edge accuracy

---

**Generated:** May 2024 | **Status:** Production-Ready PoC
