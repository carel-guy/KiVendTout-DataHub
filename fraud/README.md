# Fraud files

This folder contains lightweight, heuristic fraud labels derived from the existing Olist datasets.

- fraud_labels.csv: One row per order_id with an is_fraud flag, a simple risk_score, and reason codes.

Heuristics (heuristic_v1):
- HIGH_VALUE_P95: total payment value >= 95th percentile.
- MANY_INSTALLMENTS: payment_installments >= 10.
- CANCELED_OR_UNAVAILABLE: order_status in {canceled, unavailable}.
- LOW_REVIEW: review_score <= 2.
- HIGH_VALUE_CC: credit_card and total payment value >= 90th percentile.

The labels are synthetic and intended for prototyping pipelines and dashboards.
