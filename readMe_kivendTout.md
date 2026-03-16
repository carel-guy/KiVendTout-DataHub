┌──────────────────────────────────────────────────────────────┐
│                         SOURCES                               │
├──────────────────────────────────────────────────────────────┤
│  e-commerce/*.csv    clickStream/*.csv    fraud/*.csv         │
│  card_identity/*.tif + ground_truth/*.json                    │
└───────────────┬───────────────────────┬──────────────────────┘
                │                       │
                ▼                       ▼
┌──────────────────────────────────────────────────────────────┐
│                 INGESTION BRONZE (Parquet)                    │
│  - raw append-only                                            │
│  - partition by source/date                                   │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│               PREP + EVENT REPLAY / NORMALISATION            │
│  prepare_events.py (clean + normalize + event_id)            │
│  event_replay.py (replay chronologique vers Redpanda)         │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                  EVENT BUS (Redpanda)                         │
│  topics: user_events, payment_events, kyc_events              │
│  + DLQ invalid_events                                         │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│         SPARK STRUCTURED STREAMING (temps réel)               │
│  - schema validation                                          │
│  - sessionization + features                                  │
│  - scoring fraude live                                        │
│  - write: Bronze + DuckDB serving                             │
└───────────────┬───────────────────────┬──────────────────────┘
                │                       │
                ▼                       ▼
┌──────────────────────────────┐   ┌──────────────────────────┐
│ DATA LAKE — BRONZE            │   │ DUCKDB (serving live)    │
│ raw events, raw payments      │   │ fraud_scores_live        │
└───────────────┬──────────────┘   └──────────────┬───────────┘
                │                                 │
                ▼                                 ▼
┌──────────────────────────────────────────────────────────────┐
│              AIRFLOW — BATCH ORCHESTRATION                    │
│  Bronze → Silver: clean, dedup, validate, mappings            │
│  Silver → Gold: BI aggregates, ML datasets                    │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│            DATA LAKE — SILVER / GOLD                          │
│  Silver: mappings (unified_user/session/payment/doc)          │
│  Gold: fraud_scores_daily, KPIs, ML datasets                  │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                 SERVING & CONSOMMATION                        │
│  DuckDB (Parquet Gold)                                        │
│  FastAPI (/fraud_score, /kyc_status, /kpis)                   │
│  BI (Metabase / Power BI)                                     │
└──────────────────────────────────────────────────────────────┘
