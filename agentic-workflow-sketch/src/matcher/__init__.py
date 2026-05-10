"""
Invoice-PO Matching Module

Provides rule-based and ML-powered matching of invoices to purchase orders.
"""

from .matcher import MatchResult, InvoicePoMatcher

__all__ = ['MatchResult', 'InvoicePoMatcher']
