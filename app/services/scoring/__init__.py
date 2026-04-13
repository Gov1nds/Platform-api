"""
Vendor scoring package.

vendor_scorer.py retains the local scoring implementation for backward
compatibility. In production, scoring delegates to bom-intelligence-engine
via analyzer_service.call_score(). See vendor_scorer.score_vendors_for_project().

Reference: GAP-017, architecture.md Domain 9
"""
