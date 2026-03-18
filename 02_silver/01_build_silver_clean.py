# Databricks notebook source
from datetime import datetime, timezone
from functools import reduce

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

# COMMAND ----------
DEFAULT_CATALOG = "kvt_project"
DEFAULT_BRONZE_SCHEMA = "bronze"
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
BRONZE_SCHEMA = get_widget("bronze_schema", DEFAULT_BRONZE_SCHEMA)
SILVER_SCHEMA = get_widget("silver_schema", DEFAULT_SILVER_SCHEMA)
WRITE_MODE = get_widget("write_mode", DEFAULT_WRITE_MODE)
REFERENCE_DATE = get_widget("reference_date", DEFAULT_REFERENCE_DATE)

BRONZE_NAMESPACE = f"{CATALOG}.{BRONZE_SCHEMA}"
SILVER_NAMESPACE = f"{CATALOG}.{SILVER_SCHEMA}"
REFERENCE_DATE_COL = F.to_date(F.lit(REFERENCE_DATE))

print(
    {
        "catalog": CATALOG,
        "bronze_schema": BRONZE_SCHEMA,
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


def null_if_blank(column_name: str) -> F.Column:
    value = F.trim(F.col(column_name))
    return F.when(value == "", F.lit(None)).otherwise(value)


def normalize_zip_prefix(column_name: str) -> F.Column:
    digits = F.regexp_extract(F.trim(F.col(column_name).cast("string")), r"(\d+)", 1)
    return F.when(digits == "", F.lit(None)).otherwise(F.lpad(digits, 5, "0"))


def normalize_city(column_name: str) -> F.Column:
    return F.when(
        null_if_blank(column_name).isNull(),
        F.lit(None),
    ).otherwise(F.initcap(F.lower(null_if_blank(column_name))))


def normalize_state(column_name: str) -> F.Column:
    return F.when(
        null_if_blank(column_name).isNull(),
        F.lit(None),
    ).otherwise(F.upper(null_if_blank(column_name)))


def cast_timestamp(column_name: str) -> F.Column:
    return F.to_timestamp(null_if_blank(column_name))


def cast_millis_to_timestamp(column_name: str) -> F.Column:
    millis = F.col(column_name).cast("double") / F.lit(1000.0)
    return F.to_timestamp(F.from_unixtime(millis))


def bool_from_zero_one(column_name: str) -> F.Column:
    value = F.col(column_name).cast("int")
    return (
        F.when(value == 1, F.lit(True))
        .when(value == 0, F.lit(False))
        .otherwise(F.lit(None).cast("boolean"))
    )


def ensure_document_key(df: DataFrame) -> DataFrame:
    if "document_key" in df.columns:
        return df

    return df.withColumn(
        "document_key",
        F.concat_ws(
            "|",
            F.col("document_collection"),
            F.coalesce(F.col("document_group"), F.lit("_base")),
            F.col("document_stem"),
        ),
    )


FIELD_SCHEMA = T.StructType(
    [
        T.StructField("value", T.StringType(), True),
        T.StructField("quad", T.ArrayType(T.ArrayType(T.IntegerType())), True),
    ]
)

IDENTITY_ANNOTATION_SCHEMA = T.StructType(
    [
        T.StructField("schema_version", T.StringType(), True),
        T.StructField("document_type", T.StringType(), True),
        T.StructField("record_id", T.StringType(), True),
        T.StructField("document_quad", T.ArrayType(T.ArrayType(T.IntegerType())), True),
        T.StructField(
            "fields",
            T.StructType(
                [
                    T.StructField("nom", FIELD_SCHEMA, True),
                    T.StructField("prenom", FIELD_SCHEMA, True),
                    T.StructField("nationalite", FIELD_SCHEMA, True),
                    T.StructField("date_naissance", FIELD_SCHEMA, True),
                    T.StructField("sexe", FIELD_SCHEMA, True),
                ]
            ),
            True,
        ),
    ]
)

# COMMAND ----------
customers_raw = read_table(BRONZE_NAMESPACE, "bronze_ecommerce_customers_raw")
orders_raw = read_table(BRONZE_NAMESPACE, "bronze_ecommerce_orders_raw")
order_items_raw = read_table(BRONZE_NAMESPACE, "bronze_ecommerce_order_items_raw")
payments_raw = read_table(BRONZE_NAMESPACE, "bronze_ecommerce_order_payments_raw")
reviews_raw = read_table(BRONZE_NAMESPACE, "bronze_ecommerce_order_reviews_raw")
products_raw = read_table(BRONZE_NAMESPACE, "bronze_ecommerce_products_raw")
sellers_raw = read_table(BRONZE_NAMESPACE, "bronze_ecommerce_sellers_raw")
geolocations_raw = read_table(BRONZE_NAMESPACE, "bronze_ecommerce_geolocations_raw")
product_category_translations_raw = read_table(
    BRONZE_NAMESPACE,
    "bronze_ecommerce_product_category_translations_raw",
)
events_raw = read_table(BRONZE_NAMESPACE, "bronze_clickstream_events_raw")
item_properties_raw = read_table(
    BRONZE_NAMESPACE,
    "bronze_clickstream_item_property_history_raw",
)
category_hierarchy_raw = read_table(
    BRONZE_NAMESPACE,
    "bronze_clickstream_category_hierarchy_raw",
)
fraud_labels_raw = read_table(BRONZE_NAMESPACE, "bronze_fraud_order_labels_raw")

# COMMAND ----------
silver_customers_clean = (
    customers_raw.select(
        F.trim(F.col("customer_id")).alias("customer_id"),
        F.trim(F.col("customer_unique_id")).alias("customer_unique_id"),
        normalize_zip_prefix("customer_zip_code_prefix").alias("customer_zip_code_prefix"),
        normalize_city("customer_city").alias("customer_city"),
        normalize_state("customer_state").alias("customer_state"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .dropDuplicates(["customer_id"])
)
write_table(silver_customers_clean, "silver_customers_clean")

# COMMAND ----------
silver_orders_clean = (
    orders_raw.select(
        F.trim(F.col("order_id")).alias("order_id"),
        F.trim(F.col("customer_id")).alias("customer_id"),
        F.lower(F.trim(F.col("order_status"))).alias("order_status"),
        cast_timestamp("order_purchase_timestamp").alias("order_purchase_timestamp"),
        cast_timestamp("order_approved_at").alias("order_approved_at"),
        cast_timestamp("order_delivered_carrier_date").alias("order_delivered_carrier_date"),
        cast_timestamp("order_delivered_customer_date").alias("order_delivered_customer_date"),
        cast_timestamp("order_estimated_delivery_date").alias("order_estimated_delivery_date"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .dropDuplicates(["order_id"])
)
write_table(silver_orders_clean, "silver_orders_clean")

# COMMAND ----------
silver_order_items_clean = (
    order_items_raw.select(
        F.trim(F.col("order_id")).alias("order_id"),
        F.col("order_item_id").cast("int").alias("order_item_id"),
        F.trim(F.col("product_id")).alias("product_id"),
        F.trim(F.col("seller_id")).alias("seller_id"),
        cast_timestamp("shipping_limit_date").alias("shipping_limit_timestamp"),
        F.col("price").cast("double").alias("price_amount"),
        F.col("freight_value").cast("double").alias("freight_value"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .dropDuplicates(
        [
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_timestamp",
            "price_amount",
            "freight_value",
        ]
    )
)
write_table(silver_order_items_clean, "silver_order_items_clean")

# COMMAND ----------
silver_payments_clean = (
    payments_raw.select(
        F.trim(F.col("order_id")).alias("order_id"),
        F.col("payment_sequential").cast("int").alias("payment_sequential"),
        F.lower(F.trim(F.col("payment_type"))).alias("payment_type"),
        F.col("payment_installments").cast("int").alias("payment_installments"),
        F.col("payment_value").cast("double").alias("payment_value"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .dropDuplicates(
        [
            "order_id",
            "payment_sequential",
            "payment_type",
            "payment_installments",
            "payment_value",
        ]
    )
)
write_table(silver_payments_clean, "silver_payments_clean")

# COMMAND ----------
silver_reviews_clean = (
    reviews_raw.select(
        F.trim(F.col("review_id")).alias("review_id"),
        F.trim(F.col("order_id")).alias("order_id"),
        F.col("review_score").cast("int").alias("review_score"),
        null_if_blank("review_comment_title").alias("review_comment_title"),
        null_if_blank("review_comment_message").alias("review_comment_message"),
        cast_timestamp("review_creation_date").alias("review_creation_timestamp"),
        cast_timestamp("review_answer_timestamp").alias("review_answer_timestamp"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .dropDuplicates(["review_id", "order_id"])
)
write_table(silver_reviews_clean, "silver_reviews_clean")

# COMMAND ----------
silver_product_category_translations_clean = (
    product_category_translations_raw.select(
        F.trim(F.col("product_category_name")).alias("product_category_name"),
        F.trim(F.col("product_category_name_english")).alias(
            "product_category_name_english"
        ),
    )
    .dropDuplicates(["product_category_name"])
)
write_table(
    silver_product_category_translations_clean,
    "silver_product_category_translations_clean",
)

# COMMAND ----------
silver_products_clean = (
    products_raw.alias("products")
    .join(
        silver_product_category_translations_clean.alias("translations"),
        on="product_category_name",
        how="left",
    )
    .select(
        F.trim(F.col("products.product_id")).alias("product_id"),
        F.trim(F.col("product_category_name")).alias("product_category_name"),
        F.trim(F.col("product_category_name_english")).alias(
            "product_category_name_english"
        ),
        F.col("product_name_lenght").cast("int").alias("product_name_length"),
        F.col("product_description_lenght").cast("int").alias(
            "product_description_length"
        ),
        F.col("product_photos_qty").cast("int").alias("product_photos_qty"),
        F.col("product_weight_g").cast("double").alias("product_weight_g"),
        F.col("product_length_cm").cast("double").alias("product_length_cm"),
        F.col("product_height_cm").cast("double").alias("product_height_cm"),
        F.col("product_width_cm").cast("double").alias("product_width_cm"),
        F.col("products.source_file_path").alias("source_file_path"),
        F.col("products.source_file_name").alias("source_file_name"),
        F.col("products.source_relative_path").alias("source_relative_path"),
        F.col("products.source_system").alias("source_system"),
        F.col("products.dataset_name").alias("dataset_name"),
        F.col("products.batch_id").alias("batch_id"),
        F.col("products.ingestion_timestamp").alias("ingestion_timestamp"),
    )
    .dropDuplicates(["product_id"])
)
write_table(silver_products_clean, "silver_products_clean")

# COMMAND ----------
silver_sellers_clean = (
    sellers_raw.select(
        F.trim(F.col("seller_id")).alias("seller_id"),
        normalize_zip_prefix("seller_zip_code_prefix").alias("seller_zip_code_prefix"),
        normalize_city("seller_city").alias("seller_city"),
        normalize_state("seller_state").alias("seller_state"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .dropDuplicates(["seller_id"])
)
write_table(silver_sellers_clean, "silver_sellers_clean")

# COMMAND ----------
silver_geolocation_reference_clean = (
    geolocations_raw.select(
        normalize_zip_prefix("geolocation_zip_code_prefix").alias("zip_code_prefix"),
        F.col("geolocation_lat").cast("double").alias("geolocation_lat"),
        F.col("geolocation_lng").cast("double").alias("geolocation_lng"),
        normalize_city("geolocation_city").alias("geolocation_city"),
        normalize_state("geolocation_state").alias("geolocation_state"),
    )
    .filter(F.col("zip_code_prefix").isNotNull())
    .groupBy("zip_code_prefix")
    .agg(
        F.avg("geolocation_lat").alias("avg_latitude"),
        F.avg("geolocation_lng").alias("avg_longitude"),
        F.first("geolocation_city", ignorenulls=True).alias("reference_city"),
        F.first("geolocation_state", ignorenulls=True).alias("reference_state"),
        F.count("*").alias("source_geolocation_row_count"),
    )
)
write_table(silver_geolocation_reference_clean, "silver_geolocation_reference_clean")

# COMMAND ----------
silver_logistics_clean = (
    silver_order_items_clean.alias("items")
    .join(silver_sellers_clean.alias("sellers"), on="seller_id", how="left")
    .join(
        silver_geolocation_reference_clean.alias("geo"),
        F.col("sellers.seller_zip_code_prefix") == F.col("geo.zip_code_prefix"),
        how="left",
    )
    .select(
        F.col("items.order_id").alias("order_id"),
        F.col("items.order_item_id").alias("order_item_id"),
        F.col("items.product_id").alias("product_id"),
        F.col("items.seller_id").alias("seller_id"),
        F.col("items.shipping_limit_timestamp").alias("shipping_limit_timestamp"),
        F.col("items.price_amount").alias("price_amount"),
        F.col("items.freight_value").alias("freight_value"),
        F.col("sellers.seller_zip_code_prefix").alias("seller_zip_code_prefix"),
        F.col("sellers.seller_city").alias("seller_city"),
        F.col("sellers.seller_state").alias("seller_state"),
        F.col("geo.avg_latitude").alias("seller_avg_latitude"),
        F.col("geo.avg_longitude").alias("seller_avg_longitude"),
        F.col("geo.reference_city").alias("seller_reference_city"),
        F.col("geo.reference_state").alias("seller_reference_state"),
        F.col("geo.source_geolocation_row_count").alias(
            "seller_geolocation_row_count"
        ),
        F.col("items.source_file_path").alias("source_file_path"),
        F.col("items.source_file_name").alias("source_file_name"),
        F.col("items.source_relative_path").alias("source_relative_path"),
        F.col("items.source_system").alias("source_system"),
        F.col("items.dataset_name").alias("dataset_name"),
        F.col("items.batch_id").alias("batch_id"),
        F.col("items.ingestion_timestamp").alias("ingestion_timestamp"),
    )
)
write_table(silver_logistics_clean, "silver_logistics_clean")

# COMMAND ----------
silver_events_clean = (
    events_raw.select(
        F.col("timestamp").cast("bigint").alias("event_timestamp_ms"),
        cast_millis_to_timestamp("timestamp").alias("event_timestamp"),
        F.trim(F.col("visitorid")).alias("visitor_id"),
        F.lower(F.trim(F.col("event"))).alias("event_type"),
        F.trim(F.col("itemid")).alias("item_id"),
        null_if_blank("transactionid").alias("transaction_id"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .withColumn("has_transaction_id", F.col("transaction_id").isNotNull())
    .dropDuplicates(
        [
            "event_timestamp_ms",
            "visitor_id",
            "event_type",
            "item_id",
            "transaction_id",
        ]
    )
)
write_table(silver_events_clean, "silver_events_clean")

# COMMAND ----------
silver_item_properties_clean = (
    item_properties_raw.select(
        F.col("timestamp").cast("bigint").alias("property_timestamp_ms"),
        cast_millis_to_timestamp("timestamp").alias("property_timestamp"),
        F.trim(F.col("itemid")).alias("item_id"),
        F.lower(F.trim(F.col("property"))).alias("property_name"),
        null_if_blank("value").alias("property_value"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .dropDuplicates(
        [
            "property_timestamp_ms",
            "item_id",
            "property_name",
            "property_value",
        ]
    )
)
write_table(silver_item_properties_clean, "silver_item_properties_clean")

# COMMAND ----------
silver_category_hierarchy_clean = (
    category_hierarchy_raw.select(
        F.col("categoryid").cast("long").alias("category_id"),
        F.col("parentid").cast("long").alias("parent_category_id"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .dropDuplicates(["category_id", "parent_category_id"])
)
write_table(silver_category_hierarchy_clean, "silver_category_hierarchy_clean")

# COMMAND ----------
silver_fraud_labels_clean = (
    fraud_labels_raw.select(
        F.trim(F.col("order_id")).alias("order_id"),
        bool_from_zero_one("is_fraud").alias("is_fraud"),
        F.col("risk_score").cast("double").alias("risk_score"),
        null_if_blank("fraud_reason").alias("fraud_reason"),
        F.trim(F.col("label_source")).alias("label_source"),
        "source_file_path",
        "source_file_name",
        "source_relative_path",
        "source_system",
        "dataset_name",
        "batch_id",
        "ingestion_timestamp",
    )
    .withColumn(
        "fraud_reason_array",
        F.when(
            F.col("fraud_reason").isNull(),
            F.array().cast("array<string>"),
        ).otherwise(F.split(F.col("fraud_reason"), ";")),
    )
    .dropDuplicates(["order_id"])
)
write_table(silver_fraud_labels_clean, "silver_fraud_labels_clean")

# COMMAND ----------
identity_documents_unified_exists = table_exists(BRONZE_NAMESPACE, "bronze_id_documents")
identity_files_split_exists = table_exists(
    BRONZE_NAMESPACE,
    "bronze_identity_document_files_raw",
)
identity_annotations_split_exists = table_exists(
    BRONZE_NAMESPACE,
    "bronze_identity_document_annotations_raw",
)

identity_files_raw = None
identity_annotations_raw = None

if identity_documents_unified_exists:
    identity_unified = ensure_document_key(read_table(BRONZE_NAMESPACE, "bronze_id_documents"))
    identity_files_raw = identity_unified.filter(F.col("asset_type") == "document_image")
    identity_annotations_raw = identity_unified.filter(
        F.col("asset_type") == "annotation_json"
    )
elif identity_files_split_exists and identity_annotations_split_exists:
    identity_files_raw = ensure_document_key(
        read_table(BRONZE_NAMESPACE, "bronze_identity_document_files_raw").withColumn(
            "asset_type", F.lit("document_image")
        )
    )
    identity_annotations_raw = ensure_document_key(
        read_table(BRONZE_NAMESPACE, "bronze_identity_document_annotations_raw").withColumn(
            "asset_type", F.lit("annotation_json")
        )
    )
else:
    print("No identity Bronze table found; KYC Silver tables will be skipped.")

# COMMAND ----------
if identity_files_raw is not None:
    silver_identity_documents_clean = (
        identity_files_raw.select(
            F.col("source_system").alias("source_system"),
            F.col("document_collection").alias("document_collection"),
            F.col("document_group").alias("document_group"),
            F.col("document_stem").alias("document_stem"),
            F.col("document_key").alias("document_key"),
            F.col("file_extension").alias("file_extension"),
            F.col("source_file_path").alias("source_file_path"),
            F.col("source_file_name").alias("source_file_name"),
            F.col("source_relative_path").alias("source_relative_path"),
            F.col("source_file_modification_time").alias(
                "source_file_modification_time"
            ),
            F.col("source_file_size_bytes").alias("source_file_size_bytes"),
            F.col("asset_type").alias("asset_type"),
            F.col("batch_id").alias("batch_id"),
            F.col("ingestion_timestamp").alias("ingestion_timestamp"),
        )
        .dropDuplicates(["document_key", "source_file_path"])
    )
    write_table(silver_identity_documents_clean, "silver_identity_documents_clean")

# COMMAND ----------
if identity_annotations_raw is not None:
    silver_identity_ground_truth_clean = (
        ensure_document_key(identity_annotations_raw)
        .withColumn(
            "parsed_annotation",
            F.from_json(F.col("annotation_json"), IDENTITY_ANNOTATION_SCHEMA),
        )
        .select(
            F.col("source_system").alias("source_system"),
            F.col("document_collection").alias("document_collection"),
            F.col("document_group").alias("document_group"),
            F.col("document_stem").alias("document_stem"),
            F.col("document_key").alias("document_key"),
            F.col("source_file_path").alias("annotation_source_file_path"),
            F.col("source_file_name").alias("annotation_source_file_name"),
            F.col("source_relative_path").alias("annotation_source_relative_path"),
            F.col("source_file_modification_time").alias(
                "annotation_source_file_modification_time"
            ),
            F.col("source_file_size_bytes").alias("annotation_source_file_size_bytes"),
            F.col("annotation_format").alias("annotation_format"),
            F.col("annotation_json").alias("annotation_json"),
            F.col("parsed_annotation.schema_version").alias("annotation_schema_version"),
            F.col("parsed_annotation.document_type").alias("annotation_document_type"),
            F.col("parsed_annotation.record_id").alias("annotation_record_id"),
            F.col("parsed_annotation.document_quad").alias("document_quad"),
            F.col("parsed_annotation.fields.nom.value").alias("expected_nom"),
            F.col("parsed_annotation.fields.nom.quad").alias("expected_nom_quad"),
            F.col("parsed_annotation.fields.prenom.value").alias("expected_prenom"),
            F.col("parsed_annotation.fields.prenom.quad").alias("expected_prenom_quad"),
            F.col("parsed_annotation.fields.nationalite.value").alias(
                "expected_nationalite"
            ),
            F.col("parsed_annotation.fields.nationalite.quad").alias(
                "expected_nationalite_quad"
            ),
            F.col("parsed_annotation.fields.date_naissance.value").alias(
                "expected_date_naissance_raw"
            ),
            F.to_date(
                F.col("parsed_annotation.fields.date_naissance.value"),
                "dd/MM/yyyy",
            ).alias("expected_birth_date"),
            F.col("parsed_annotation.fields.date_naissance.quad").alias(
                "expected_date_naissance_quad"
            ),
            F.col("parsed_annotation.fields.sexe.value").alias("expected_sexe"),
            F.col("parsed_annotation.fields.sexe.quad").alias("expected_sexe_quad"),
            F.col("batch_id").alias("batch_id"),
            F.col("ingestion_timestamp").alias("ingestion_timestamp"),
        )
        .withColumn(
            "expected_age_years",
            F.floor(F.months_between(REFERENCE_DATE_COL, F.col("expected_birth_date")) / 12),
        )
        .withColumn("expected_is_adult", F.col("expected_age_years") >= 18)
        .dropDuplicates(["document_key"])
    )
    write_table(silver_identity_ground_truth_clean, "silver_identity_ground_truth_clean")

# COMMAND ----------
spark.sql(f"SHOW TABLES IN {SILVER_NAMESPACE}").show(truncate=False)
