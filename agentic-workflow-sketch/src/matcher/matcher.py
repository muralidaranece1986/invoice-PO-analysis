"""
Core matching logic for invoice-PO reconciliation.

Implements:
1. Rule-based matching (vendor, date, amount tolerance)
2. ML-based secondary scoring (TF-IDF + logistic regression)
3. Audit trail for all decisions
"""

import pickle
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd


@dataclass
class MatchResult:
    """Result of a single invoice-PO match attempt."""
    invoice_id: str
    po_id: str
    matched: bool
    confidence: float
    rule_scores: Dict[str, float]
    ml_score: Optional[float]
    reason: str
    timestamp: str
    rule_details: Dict[str, any]


class InvoicePoMatcher:
    """
    Production-grade invoice-PO matcher combining rules + ML.
    
    Design Rationale:
    - Rules are HARD CONSTRAINTS (vendor, date window)
    - ML is SOFT SCORING for edge cases
    - All decisions logged for audit trail
    """
    
    def __init__(self, model_path: Optional[str] = None, config: Optional[Dict] = None):
        """
        Initialize matcher with optional pre-trained model.
        
        Args:
            model_path: Path to pickled model artifacts (TF-IDF, scaler, LR model)
            config: Override default thresholds and tolerances
        """
        self.model_loaded = False
        self.model_artifacts = None
        
        # Default thresholds
        self.config = {
            'date_window_days': 180,  # Invoice must be within 6 months of PO
            'amount_tolerance_pct': 0.15,  # Amount can differ by up to 15%
            'ml_threshold': 0.50,  # ML confidence threshold for match
            'vendor_match_required': True,  # Vendor ID must match exactly
        }
        
        if config:
            self.config.update(config)
        
        # Load pre-trained model if provided
        if model_path:
            try:
                with open(model_path, 'rb') as f:
                    self.model_artifacts = pickle.load(f)
                self.model_loaded = True
                print(f"[Matcher] Loaded model from {model_path}")
            except Exception as e:
                print(f"[Matcher] Warning: Could not load model: {e}")
    
    def match_invoice_to_pos(
        self,
        invoice: Dict,
        pos_list: List[Dict],
        return_top_k: int = 3
    ) -> List[MatchResult]:
        """
        Find best matching POs for a single invoice.
        
        Args:
            invoice: Invoice record (keys: invoice_id, vendor_id, invoice_date, amount, description)
            pos_list: List of PO records to match against
            return_top_k: Return top K matches (ranked by confidence)
        
        Returns:
            List of MatchResult sorted by confidence descending
        """
        results = []
        
        for po in pos_list:
            result = self._score_pair(invoice, po)
            results.append(result)
        
        # Sort by confidence descending
        results.sort(key=lambda x: x.confidence, reverse=True)
        return results[:return_top_k]
    
    def _score_pair(self, invoice: Dict, po: Dict) -> MatchResult:
        """Score a single invoice-PO pair."""
        inv_id = invoice.get('invoice_id', 'unknown')
        po_id = po.get('po_id', 'unknown')
        timestamp = datetime.utcnow().isoformat()
        rule_scores = {}
        rule_details = {}
        ml_score = None
        
        # --- RULE 1: Vendor must match (hard constraint) ---
        inv_vendor = str(invoice.get('vendor_id', '')).strip()
        po_vendor = str(po.get('vendor_id', '')).strip()
        vendor_match = (inv_vendor == po_vendor)
        rule_scores['vendor_match'] = 1.0 if vendor_match else 0.0
        rule_details['vendor_match'] = {'invoice': inv_vendor, 'po': po_vendor, 'match': vendor_match}
        
        if self.config['vendor_match_required'] and not vendor_match:
            return MatchResult(
                invoice_id=inv_id,
                po_id=po_id,
                matched=False,
                confidence=0.0,
                rule_scores=rule_scores,
                ml_score=None,
                reason=f"Vendor mismatch: {inv_vendor} != {po_vendor}",
                timestamp=timestamp,
                rule_details=rule_details
            )
        
        # --- RULE 2: Date proximity ---
        try:
            inv_date = pd.to_datetime(invoice.get('invoice_date'), errors='coerce')
            po_date = pd.to_datetime(po.get('po_date'), errors='coerce')
            
            if pd.isna(inv_date) or pd.isna(po_date):
                date_diff_days = 999
                date_proximity = 0.0
            else:
                date_diff_days = abs((inv_date - po_date).days)
                # Linear decay: perfect (0 days) = 1.0, outside window = 0.0
                max_days = self.config['date_window_days']
                date_proximity = max(0.0, 1.0 - (date_diff_days / max_days))
            
            rule_scores['date_proximity'] = date_proximity
            rule_details['date_proximity'] = {
                'inv_date': str(inv_date),
                'po_date': str(po_date),
                'days_diff': int(date_diff_days),
                'score': float(date_proximity)
            }
        except Exception as e:
            date_proximity = 0.0
            rule_details['date_proximity'] = {'error': str(e)}
        
        # --- RULE 3: Amount similarity ---
        try:
            inv_amt = float(invoice.get('amount', 0))
            po_amt = float(po.get('line_item_amount', 0))
            
            if po_amt > 0:
                pct_diff = abs(inv_amt - po_amt) / po_amt
                tolerance = self.config['amount_tolerance_pct']
                amount_similarity = max(0.0, 1.0 - (pct_diff / tolerance))
            else:
                amount_similarity = 0.0
            
            rule_scores['amount_similarity'] = amount_similarity
            rule_details['amount_similarity'] = {
                'inv_amt': float(inv_amt),
                'po_amt': float(po_amt),
                'pct_diff': float(pct_diff) if po_amt > 0 else None,
                'score': float(amount_similarity)
            }
        except Exception as e:
            amount_similarity = 0.0
            rule_details['amount_similarity'] = {'error': str(e)}
        
        # --- RULE 4: Description overlap ---
        try:
            inv_desc = str(invoice.get('description', '')).lower()
            po_desc = str(po.get('description', '')).lower()
            
            inv_words = set(inv_desc.split())
            po_words = set(po_desc.split())
            
            common = len(inv_words & po_words)
            total = len(inv_words | po_words)
            desc_overlap = common / total if total > 0 else 0.0
            
            rule_scores['desc_overlap'] = desc_overlap
            rule_details['desc_overlap'] = {
                'common_words': int(common),
                'total_words': int(total),
                'score': float(desc_overlap)
            }
        except Exception as e:
            desc_overlap = 0.0
            rule_details['desc_overlap'] = {'error': str(e)}
        
        # --- ML SCORING (if model loaded) ---
        if self.model_loaded and self.model_artifacts:
            try:
                ml_score = self._ml_score(
                    invoice, po,
                    rule_scores, rule_details
                )
            except Exception as e:
                ml_score = None
                rule_details['ml_error'] = str(e)
        
        # --- FINAL DECISION ---
        # Use ML score if available and model is confident; otherwise use rule-based heuristic
        if ml_score is not None:
            final_confidence = ml_score
            matched = ml_score >= self.config['ml_threshold']
            method = 'ML'
        else:
            # Simple rule-based: average of normalized scores
            rule_avg = np.mean([
                rule_scores.get('vendor_match', 0),
                rule_scores.get('date_proximity', 0),
                rule_scores.get('amount_similarity', 0),
                rule_scores.get('desc_overlap', 0)
            ])
            final_confidence = rule_avg
            matched = final_confidence >= 0.65 and vendor_match  # Threshold if no ML
            method = 'Rules'
        
        reason = (
            f"Match ({method}): confidence={final_confidence:.3f}, "
            f"vendor={vendor_match}, date_prox={rule_scores.get('date_proximity', 0):.2f}, "
            f"amt_sim={rule_scores.get('amount_similarity', 0):.2f}"
        )
        
        return MatchResult(
            invoice_id=inv_id,
            po_id=po_id,
            matched=matched,
            confidence=final_confidence,
            rule_scores=rule_scores,
            ml_score=ml_score,
            reason=reason,
            timestamp=timestamp,
            rule_details=rule_details
        )
    
    def _ml_score(
        self,
        invoice: Dict,
        po: Dict,
        rule_scores: Dict[str, float],
        rule_details: Dict[str, any]
    ) -> float:
        """Compute ML-based confidence score."""
        if not self.model_loaded:
            return None
        
        lr_model = self.model_artifacts['lr_model']
        scaler = self.model_artifacts['scaler']
        tfidf = self.model_artifacts['tfidf']
        
        # Extract features (same as notebook)
        rule_feat = np.array([[
            rule_scores.get('vendor_match', 0),
            rule_scores.get('date_proximity', 0),
            rule_scores.get('amount_similarity', 0),
            rule_scores.get('desc_overlap', 0)
        ]])
        
        inv_desc = str(invoice.get('description', '')).lower()
        po_desc = str(po.get('description', '')).lower()
        combined_text = inv_desc + " " + po_desc
        
        tfidf_feat = tfidf.transform([combined_text]).toarray()
        combined = np.hstack([rule_feat, tfidf_feat])
        scaled = scaler.transform(combined)
        
        # Predict
        prob = lr_model.predict_proba(scaled)[0, 1]
        return float(prob)
    
    def batch_match(
        self,
        invoices: pd.DataFrame,
        pos: pd.DataFrame,
        return_all_candidates: bool = False
    ) -> pd.DataFrame:
        """
        Batch match all invoices to POs.
        
        Args:
            invoices: DataFrame with invoice records
            pos: DataFrame with PO records
            return_all_candidates: If False, return only best match per invoice
        
        Returns:
            DataFrame of match results with columns: invoice_id, po_id, matched, confidence, reason
        """
        all_results = []
        
        for _, inv_row in invoices.iterrows():
            invoice_dict = inv_row.to_dict()
            pos_list = pos.to_dict('records')
            
            matches = self.match_invoice_to_pos(invoice_dict, pos_list, return_top_k=10)
            
            if not return_all_candidates:
                matches = matches[:1]
            
            for match in matches:
                all_results.append(asdict(match))
        
        return pd.DataFrame(all_results)
