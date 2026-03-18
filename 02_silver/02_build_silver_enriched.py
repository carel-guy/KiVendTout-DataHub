# Databricks notebook source
from datetime import datetime, timezone

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

# COMMAND ----------
DEFAULT_CATALOG = "kvt_project"
DEFAULT_SILVER_SCHEMA = "silver"
DEFAULT_WRITE_MODE = "overwrite"
DEFAULT_REFERENCE_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_widget(name: str, default_value: str) -> str:
    try:
        dbutils.widgets.text(name, default_value)
        value = dbutils.widgets.get(name)
        return value or default_value
    except Exception:
        return default_value


CATALOG = get_widget("catalog", DEFAULT_CATALOG)
SILVER_SCHEMA = get_widget("silver_schema", DEFAULT_SILVER_SCHEMA)
WRITE_MODE = get_widget("write_mode", DEFAULT_WRITE_MODE)
REFERENCE_DATE = get_widget("reference_date", DEFAULT_REFERENCE_DATE)

SILVER_NAMESPACE = f"{CATALOG}.{SILVER_SCHEMA}"
REFERENCE_DATE_COL = F.to_date(F.lit(REFERENCE_DATE))

print(
    {
        "catalog": CATALOG,
        "silver_schema": SILVER_SCHEMA,
        "write_mode": WRITE_MODE,
        "reference_date": REFERENCE_DATE,
    }
)

# COMMAND ----------
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SILVER_NAMESPACE}")


def table_exists(namespace: str, table_name: str) -> bool:
    return len(spark.sql(f"SHOW TABLES IN {namespace} LIKE '{table_name}'").take(1)) > 0


def read_table(namespace: str, table_name: str) -> DataFrame:
    if not table_exists(namespace, table_name):
        raise FileNotFoundError(f"Missing table: {namespace}.{table_name}")
    return spark.table(f"{namespace}.{table_name}")


def write_table(df: DataFrame, table_name: str) -> None:
    (
        df.write.format("delta")
        .mode(WRITE_MODE)
        .option("overwriteSchema", "true")
        .saveAsTable(f"{SILVER_NAMESPACE}.{table_name}")
    )
    print(f"Wrote {SILVER_NAMESPACE}.{table_name}")

# COMMAND ----------
silver_events_clean = read_table(SILVER_NAMESPACE, "silver_events_clean")
silver_customers_clean = read_table(SILVER_NAMESPACE, "silver_customers_clean")
silver_orders_clean = read_table(SILVER_NAMESPACE, "silver_orders_clean")
silver_payments_clean = read_table(SILVER_NAMESPACE, "silver_payments_clean")
silver_products_clean = read_table(SILVER_NAMESPACE, "silver_products_clean")
silver_logistics_clean = read_table(SILVER_NAMESPACE, "silver_logistics_clean")
silver_reviews_clean = read_table(SILVER_NAMESPACE, "silver_reviews_clean")
silver_fraud_labels_clean = read_table(SILVER_NAMESPACE, "silver_fraud_labels_clean")
silver_item_properties_clean = read_table(SILVER_NAMESPACE, "silver_item_properties_clean")
silver_category_hierarchy_clean = read_table(
    SILVER_NAMESPACE,
    "silver_category_hierarchy_clean",
)

identity_documents_clean_exists = table_exists(
    SILVER_NAMESPACE,
    "silver_identity_documents_clean",
)
identity_ground_truth_clean_exists = table_exists(
    SILVER_NAMESPACE,
    "silver_identity_ground_truth_clean",
)

# COMMAND ----------
latest_item_category = (
    silver_item_properties_clean.filter(F.col("property_name") == "categoryid")
    .withColumn("category_id", F.col("property_value").cast("long"))
    .filter(F.col("category_id").isNotNull())
    .withColumn(
        "row_number",
        F.row_number().over(
            Window.partitionBy("item_id").orderBy(
                F.col("property_timestamp_ms").desc_nulls_last()
            )
        ),
    )
    .filter(F.col("row_number") == 1)
    .select(
        "item_id",
        "category_id",
        F.col("property_timestamp").alias("latest_category_timestamp"),
    )
)

silver_events_enriched = (
    silver_events_clean.alias("events")
    .join(latest_item_category.alias("latest_category"), on="item_id", how="left")
    .join(
        silver_category_hierarchy_clean.alias("hierarchy"),
        F.col("latest_category.category_id") == F.col("hierarchy.category_id"),
        how="left",
    )
    .select(
        F.col("events.event_timestamp_ms").alias("event_timestamp_ms"),
        F.col("events.event_timestamp").alias("event_timestamp"),
        F.to_date(F.col("events.event_timestamp")).alias("event_date"),
        F.hour(F.col("events.event_timestamp")).alias("event_hour"),
        F.col("events.visitor_id").alias("visitor_id"),
        F.col("events.event_type").alias("event_type"),
        F.col("events.item_id").alias("item_id"),
        F.col("events.transaction_id").alias("transaction_id"),
        F.col("events.has_transaction_id").alias("has_transaction_id"),
        F.col("latest_category.category_id").alias("latest_category_id"),
        F.col("hierarchy.parent_category_id").alias("parent_category_id"),
        F.col("latest_category.latest_category_timestamp").alias(
            "latest_category_timestamp"
        ),
        (F.col("events.event_type") == "transaction").alias("is_transaction_event"),
        (F.col("events.event_type") == "addtocart").alias("is_add_to_cart_event"),
        (F.col("events.event_type") == "view").alias("is_view_event"),
        F.col("events.source_file_path").alias("source_file_path"),
        F.col("events.source_file_name").alias("source_file_name"),
        F.col("events.source_relative_path").alias("source_relative_path"),
        F.col("events.source_system").alias("source_system"),
        F.col("events.dataset_name").alias("dataset_name"),
        F.col("events.batch_id").alias("batch_id"),
        F.col("events.ingestion_timestamp").alias("ingestion_timestamp"),
    )
)
write_table(silver_events_enriched, "silver_events_enriched")

# COMMAND ----------
payments_agg = silver_payments_clean.groupBy("order_id").agg(
    F.sum("payment_value").alias("total_payment_amount"),
    F.count("*").alias("payment_record_count"),
    F.max("payment_installments").alias("max_payment_installments"),
    F.array_sort(F.collect_set("payment_type")).alias("payment_type_array"),
    F.countDistinct("payment_type").alias("distinct_payment_type_count"),
)

logistics_agg = silver_logistics_clean.groupBy("order_id").agg(
    F.count("*").alias("item_line_count"),
    F.countDistinct("product_id").alias("distinct_product_count"),
    F.countDistinct("seller_id").alias("distinct_seller_count"),
    F.sum("price_amount").alias("order_items_gmv_amount"),
    F.sum("freight_value").alias("order_freight_total_amount"),
    F.min("shipping_limit_timestamp").alias("min_shipping_limit_timestamp"),
    F.max("shipping_limit_timestamp").alias("max_shipping_limit_timestamp"),
)

reviews_agg = silver_reviews_clean.groupBy("order_id").agg(
    F.avg("review_score").alias("average_review_score"),
    F.count("*").alias("review_count"),
    F.max("review_creation_timestamp").alias("last_review_creation_timestamp"),
)

silver_orders_enriched = (
    silver_orders_clean.alias("orders")
    .join(silver_customers_clean.alias("customers"), on="customer_id", how="left")
    .join(payments_agg.alias("payments"), on="order_id", how="left")
    .join(logistics_agg.alias("logistics"), on="order_id", how="left")
    .join(reviews_agg.alias("reviews"), on="order_id", how="left")
    .join(silver_fraud_labels_clean.alias("fraud"), on="order_id", how="left")
    .select(
        F.col("orders.order_id").alias("order_id"),
        F.col("orders.customer_id").alias("customer_id"),
        F.col("customers.customer_unique_id").alias("customer_unique_id"),
        F.col("customers.customer_city").alias("customer_city"),
        F.col("customers.customer_state").alias("customer_state"),
        F.col("orders.order_status").alias("order_status"),
        F.col("orders.order_purchase_timestamp").alias("order_purchase_timestamp"),
        F.col("orders.order_approved_at").alias("order_approved_at"),
        F.col("orders.order_delivered_carrier_date").alias(
            "order_delivered_carrier_date"
        ),
        F.col("orders.order_delivered_customer_date").alias(
            "order_delivered_customer_date"
        ),
        F.col("orders.order_estimated_delivery_date").alias(
            "order_estimated_delivery_date"
        ),
        F.col("payments.total_payment_amount").alias("total_payment_amount"),
        F.col("payments.payment_record_count").alias("payment_record_count"),
        F.col("payments.max_payment_installments").alias("max_payment_installments"),
        F.col("payments.payment_type_array").alias("payment_type_array"),
        F.col("payments.distinct_payment_type_count").alias(
            "distinct_payment_type_count"
        ),
        F.col("logistics.item_line_count").alias("item_line_count"),
        F.col("logistics.distinct_product_count").alias("distinct_product_count"),
        F.col("logistics.distinct_seller_count").alias("distinct_seller_count"),
        F.col("logistics.order_items_gmv_amount").alias("order_items_gmv_amount"),
        F.col("logistics.order_freight_total_amount").alias(
            "order_freight_total_amount"
        ),
        F.col("reviews.average_review_score").alias("average_review_score"),
        F.col("reviews.review_count").alias("review_count"),
        F.col("fraud.is_fraud").alias("is_fraud"),
        F.col("fraud.risk_score").alias("fraud_risk_score"),
        F.col("fraud.fraud_reason").alias("fraud_reason"),
        F.col("fraud.fraud_reason_array").alias("fraud_reason_array"),
        F.col("fraud.label_source").alias("fraud_label_source"),
        (F.col("orders.order_status") == "delivered").alias("is_delivered"),
        F.col("orders.order_status").isin("canceled", "unavailable").alias(
            "is_lost_or_canceled"
        ),
        F.when(
            F.col("orders.order_delivered_customer_date").isNotNull(),
            F.datediff(
                F.col("orders.order_delivered_customer_date"),
                F.col("orders.order_purchase_timestamp"),
            ),
        ).alias("delivery_cycle_days"),
        F.when(
            F.col("orders.order_approved_at").isNotNull(),
            (
                F.col("orders.order_approved_at").cast("long")
                - F.col("orders.order_purchase_timestamp").cast("long")
            )
            / 3600.0,
        ).alias("approval_delay_hours"),
        F.col("orders.source_file_path").alias("source_file_path"),
        F.col("orders.source_file_name").alias("source_file_name"),
        F.col("orders.source_relative_path").alias("source_relative_path"),
        F.col("orders.source_system").alias("source_system"),
        F.col("orders.dataset_name").alias("dataset_name"),
        F.col("orders.batch_id").alias("batch_id"),
        F.col("orders.ingestion_timestamp").alias("ingestion_timestamp"),
    )
)
write_table(silver_orders_enriched, "silver_orders_enriched")

# COMMAND ----------
silver_payments_enriched = (
    silver_payments_clean.alias("payments")
    .join(silver_orders_clean.alias("orders"), on="order_id", how="left")
    .join(silver_customers_clean.alias("customers"), on="customer_id", how="left")
    .join(silver_fraud_labels_clean.alias("fraud"), on="order_id", how="left")
    .select(
        F.col("payments.order_id").alias("order_id"),
        F.col("payments.payment_sequential").alias("payment_sequential"),
        F.col("payments.payment_type").alias("payment_type"),
        F.col("payments.payment_installments").alias("payment_installments"),
        F.col("payments.payment_value").alias("payment_value"),
        F.col("orders.customer_id").alias("customer_id"),
        F.col("customers.customer_unique_id").alias("customer_unique_id"),
        F.col("customers.customer_city").alias("customer_city"),
        F.col("customers.customer_state").alias("customer_state"),
        F.col("orders.order_status").alias("order_status"),
        F.col("orders.order_purchase_timestamp").alias("order_purchase_timestamp"),
        F.col("fraud.is_fraud").alias("is_fraud"),
        F.col("fraud.risk_score").alias("fraud_risk_score"),
        F.col("fraud.fraud_reason").alias("fraud_reason"),
        F.col("fraud.label_source").alias("fraud_label_source"),
        F.col("payments.source_file_path").alias("source_file_path"),
        F.col("payments.source_file_name").alias("source_file_name"),
        F.col("payments.source_relative_path").alias("source_relative_path"),
        F.col("payments.source_system").alias("source_system"),
        F.col("payments.dataset_name").alias("dataset_name"),
        F.col("payments.batch_id").alias("batch_id"),
        F.col("payments.ingestion_timestamp").alias("ingestion_timestamp"),
    )
)
write_table(silver_payments_enriched, "silver_payments_enriched")

# COMMAND ----------
customer_order_agg = silver_orders_enriched.groupBy("customer_id").agg(
    F.first("customer_unique_id", ignorenulls=True).alias("customer_unique_id"),
    F.first("customer_city", ignorenulls=True).alias("customer_city"),
    F.first("customer_state", ignorenulls=True).alias("customer_state"),
    F.countDistinct("order_id").alias("total_orders"),
    F.sum(F.when(F.col("is_delivered"), F.lit(1)).otherwise(F.lit(0))).alias(
        "delivered_order_count"
    ),
    F.sum(
        F.when(F.col("is_lost_or_canceled"), F.lit(1)).otherwise(F.lit(0))
    ).alias("lost_or_canceled_order_count"),
    F.min("order_purchase_timestamp").alias("first_order_timestamp"),
    F.max("order_purchase_timestamp").alias("last_order_timestamp"),
    F.sum("order_items_gmv_amount").alias("gross_merchandise_value_amount"),
    F.sum("order_freight_total_amount").alias("total_freight_amount"),
    F.sum("total_payment_amount").alias("total_payment_amount"),
    F.avg("average_review_score").alias("average_review_score"),
    F.sum(F.when(F.col("is_fraud"), F.lit(1)).otherwise(F.lit(0))).alias(
        "fraud_order_count"
    ),
    F.avg("fraud_risk_score").alias("average_fraud_risk_score"),
    F.max("fraud_risk_score").alias("max_fraud_risk_score"),
)

silver_customer360_base = (
    silver_customers_clean.alias("customers")
    .join(customer_order_agg.alias("agg"), on="customer_id", how="left")
    .select(
        F.col("customers.customer_id").alias("customer_id"),
        F.coalesce(F.col("agg.customer_unique_id"), F.col("customers.customer_unique_id")).alias(
            "customer_unique_id"
        ),
        F.coalesce(F.col("agg.customer_city"), F.col("customers.customer_city")).alias(
            "customer_city"
        ),
        F.coalesce(
            F.col("agg.customer_state"),
            F.col("customers.customer_state"),
        ).alias("customer_state"),
        F.col("agg.total_orders").alias("total_orders"),
        F.col("agg.delivered_order_count").alias("delivered_order_count"),
        F.col("agg.lost_or_canceled_order_count").alias(
            "lost_or_canceled_order_count"
        ),
        F.col("agg.first_order_timestamp").alias("first_order_timestamp"),
        F.col("agg.last_order_timestamp").alias("last_order_timestamp"),
        F.col("agg.gross_merchandise_value_amount").alias(
            "gross_merchandise_value_amount"
        ),
        F.col("agg.total_freight_amount").alias("total_freight_amount"),
        F.col("agg.total_payment_amount").alias("total_payment_amount"),
        F.col("agg.average_review_score").alias("average_review_score"),
        F.col("agg.fraud_order_count").alias("fraud_order_count"),
        F.col("agg.average_fraud_risk_score").alias("average_fraud_risk_score"),
        F.col("agg.max_fraud_risk_score").alias("max_fraud_risk_score"),
        F.col("customers.source_file_path").alias("source_file_path"),
        F.col("customers.source_file_name").alias("source_file_name"),
        F.col("customers.source_relative_path").alias("source_relative_path"),
        F.col("customers.source_system").alias("source_system"),
        F.col("customers.dataset_name").alias("dataset_name"),
        F.col("customers.batch_id").alias("batch_id"),
        F.col("customers.ingestion_timestamp").alias("ingestion_timestamp"),
    )
)
write_table(silver_customer360_base, "silver_customer360_base")

# COMMAND ----------
if identity_documents_clean_exists and identity_ground_truth_clean_exists:
    silver_identity_documents_clean = read_table(
        SILVER_NAMESPACE,
        "silver_identity_documents_clean",
    )
    silver_identity_ground_truth_clean = read_table(
        SILVER_NAMESPACE,
        "silver_identity_ground_truth_clean",
    )

    silver_identity_ocr_eval_base = (
        silver_identity_documents_clean.alias("documents")
        .filter(
            (F.col("documents.source_system") == "card_identity")
            & (F.col("documents.asset_type") == "document_image")
        )
        .join(
            silver_identity_ground_truth_clean.alias("ground_truth"),
            on="document_key",
            how="inner",
        )
        .select(
            F.col("documents.document_key").alias("document_key"),
            F.col("documents.document_collection").alias("document_collection"),
            F.col("documents.document_group").alias("document_group"),
            F.col("documents.document_stem").alias("document_stem"),
            F.col("documents.source_file_path").alias("image_source_file_path"),
            F.col("documents.source_file_name").alias("image_source_file_name"),
            F.col("documents.source_relative_path").alias("image_source_relative_path"),
            F.col("documents.file_extension").alias("image_file_extension"),
            F.col("documents.source_file_size_bytes").alias("image_file_size_bytes"),
            F.col("ground_truth.annotation_source_file_path").alias(
                "annotation_source_file_path"
            ),
            F.col("ground_truth.annotation_source_file_name").alias(
                "annotation_source_file_name"
            ),
            F.col("ground_truth.annotation_source_relative_path").alias(
                "annotation_source_relative_path"
            ),
            F.col("ground_truth.annotation_schema_version").alias(
                "annotation_schema_version"
            ),
            F.col("ground_truth.annotation_document_type").alias(
                "annotation_document_type"
            ),
            F.col("ground_truth.document_quad").alias("document_quad"),
            F.col("ground_truth.expected_nom").alias("expected_nom"),
            F.col("ground_truth.expected_nom_quad").alias("expected_nom_quad"),
            F.col("ground_truth.expected_prenom").alias("expected_prenom"),
            F.col("ground_truth.expected_prenom_quad").alias("expected_prenom_quad"),
            F.col("ground_truth.expected_nationalite").alias(
                "expected_nationalite"
            ),
            F.col("ground_truth.expected_nationalite_quad").alias(
                "expected_nationalite_quad"
            ),
            F.col("ground_truth.expected_date_naissance_raw").alias(
                "expected_date_naissance_raw"
            ),
            F.col("ground_truth.expected_birth_date").alias("expected_birth_date"),
            F.col("ground_truth.expected_date_naissance_quad").alias(
                "expected_date_naissance_quad"
            ),
            F.col("ground_truth.expected_age_years").alias("expected_age_years"),
            F.col("ground_truth.expected_is_adult").alias("expected_is_adult"),
            F.col("ground_truth.expected_sexe").alias("expected_sexe"),
            F.col("ground_truth.expected_sexe_quad").alias("expected_sexe_quad"),
            F.col("ground_truth.annotation_json").alias("annotation_json"),
        )
        .withColumn(
            "dataset_split",
            F.when(F.pmod(F.crc32(F.col("document_key")), F.lit(100)) < 70, F.lit("train"))
            .when(
                F.pmod(F.crc32(F.col("document_key")), F.lit(100)) < 85,
                F.lit("validation"),
            )
            .otherwise(F.lit("test")),
        )
        .withColumn("reference_date", REFERENCE_DATE_COL)
    )
    write_table(silver_identity_ocr_eval_base, "silver_identity_ocr_eval_base")
else:
    print("Identity clean tables not found; skipped silver_identity_ocr_eval_base.")

# COMMAND ----------
spark.sql(f"SHOW TABLES IN {SILVER_NAMESPACE}").show(truncate=False)
