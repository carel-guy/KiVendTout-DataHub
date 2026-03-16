# Databricks notebook source
from datetime import datetime, timezone
from functools import reduce

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

# COMMAND ----------
DEFAULT_CATALOG = "kvt_project"
DEFAULT_BRONZE_SCHEMA = "bronze"
DEFAULT_SOURCE_ROOT = "/Volumes/kvt_project/bronze/source_systems"
DEFAULT_WRITE_MODE = "overwrite"


def get_widget_value(name: str, default_value: str) -> str:
    try:
        dbutils.widgets.text(name, default_value)
        value = dbutils.widgets.get(name)
        return value or default_value
    except Exception:
        return default_value


CATALOG = get_widget_value("catalog", DEFAULT_CATALOG)
BRONZE_SCHEMA = get_widget_value("bronze_schema", DEFAULT_BRONZE_SCHEMA)
SOURCE_ROOT = get_widget_value("source_root", DEFAULT_SOURCE_ROOT)
WRITE_MODE = get_widget_value("write_mode", DEFAULT_WRITE_MODE)
BATCH_ID = get_widget_value(
    "batch_id",
    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
)
BRONZE_NAMESPACE = f"{CATALOG}.{BRONZE_SCHEMA}"

print(
    {
        "catalog": CATALOG,
        "bronze_schema": BRONZE_SCHEMA,
        "source_root": SOURCE_ROOT,
        "write_mode": WRITE_MODE,
        "batch_id": BATCH_ID,
    }
)

# COMMAND ----------
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {BRONZE_NAMESPACE}")

# COMMAND ----------
DATASETS = [
    {
        "target_table": "bronze_ecommerce_customers_raw",
        "source_system": "ecommerce",
        "dataset_name": "customers",
        "source_paths": [
            f"{SOURCE_ROOT}/e-commerce/olist_customers_dataset.csv",
        ],
    },
    {
        "target_table": "bronze_ecommerce_orders_raw",
        "source_system": "ecommerce",
        "dataset_name": "orders",
        "source_paths": [
            f"{SOURCE_ROOT}/e-commerce/olist_orders_dataset.csv",
        ],
    },
    {
        "target_table": "bronze_ecommerce_order_items_raw",
        "source_system": "ecommerce",
        "dataset_name": "order_items",
        "source_paths": [
            f"{SOURCE_ROOT}/e-commerce/olist_order_items_dataset.csv",
        ],
    },
    {
        "target_table": "bronze_ecommerce_order_payments_raw",
        "source_system": "ecommerce",
        "dataset_name": "order_payments",
        "source_paths": [
            f"{SOURCE_ROOT}/e-commerce/olist_order_payments_dataset.csv",
        ],
    },
    {
        "target_table": "bronze_ecommerce_order_reviews_raw",
        "source_system": "ecommerce",
        "dataset_name": "order_reviews",
        "source_paths": [
            f"{SOURCE_ROOT}/e-commerce/olist_order_reviews_dataset.csv",
        ],
    },
    {
        "target_table": "bronze_ecommerce_products_raw",
        "source_system": "ecommerce",
        "dataset_name": "products",
        "source_paths": [
            f"{SOURCE_ROOT}/e-commerce/olist_products_dataset.csv",
        ],
    },
    {
        "target_table": "bronze_ecommerce_sellers_raw",
        "source_system": "ecommerce",
        "dataset_name": "sellers",
        "source_paths": [
            f"{SOURCE_ROOT}/e-commerce/olist_sellers_dataset.csv",
        ],
    },
    {
        "target_table": "bronze_ecommerce_geolocations_raw",
        "source_system": "ecommerce",
        "dataset_name": "geolocations",
        "source_paths": [
            f"{SOURCE_ROOT}/e-commerce/olist_geolocation_dataset.csv",
        ],
    },
    {
        "target_table": "bronze_ecommerce_product_category_translations_raw",
        "source_system": "ecommerce",
        "dataset_name": "product_category_translations",
        "source_paths": [
            f"{SOURCE_ROOT}/e-commerce/product_category_name_translation.csv",
        ],
    },
    {
        "target_table": "bronze_clickstream_events_raw",
        "source_system": "clickstream",
        "dataset_name": "events",
        "source_paths": [
            f"{SOURCE_ROOT}/clickStream/events.csv",
        ],
    },
    {
        "target_table": "bronze_clickstream_item_property_history_raw",
        "source_system": "clickstream",
        "dataset_name": "item_property_history",
        "source_paths": [
            f"{SOURCE_ROOT}/clickStream/item_properties_part1.csv",
            f"{SOURCE_ROOT}/clickStream/item_properties_part2.csv",
        ],
    },
    {
        "target_table": "bronze_clickstream_category_hierarchy_raw",
        "source_system": "clickstream",
        "dataset_name": "category_hierarchy",
        "source_paths": [
            f"{SOURCE_ROOT}/clickStream/category_tree.csv",
        ],
    },
    {
        "target_table": "bronze_fraud_order_labels_raw",
        "source_system": "fraud",
        "dataset_name": "order_labels",
        "source_paths": [
            f"{SOURCE_ROOT}/fraud/fraud_labels.csv",
        ],
    },
]

for dataset in DATASETS:
    print(dataset)

# COMMAND ----------
def strip_bom_from_column_names(df: DataFrame) -> DataFrame:
    renamed_df = df
    for column_name in renamed_df.columns:
        clean_name = column_name.lstrip("\ufeff")
        if clean_name != column_name:
            renamed_df = renamed_df.withColumnRenamed(column_name, clean_name)
    return renamed_df


def read_csv_as_raw_df(
    source_path: str,
    source_system: str,
    dataset_name: str,
) -> DataFrame:
    df = (
        spark.read.format("csv")
        .option("header", "true")
        .option("inferSchema", "false")
        .option("mode", "PERMISSIVE")
        .option("multiLine", "false")
        .option("nullValue", "__KVT_NULL__")
        .load(source_path)
    )

    df = strip_bom_from_column_names(df)

    return (
        df.withColumn("source_file_path", F.input_file_name())
        .withColumn(
            "source_file_name",
            F.regexp_extract(F.input_file_name(), r"([^/]+)$", 1),
        )
        .withColumn(
            "source_relative_path",
            F.regexp_replace(F.input_file_name(), f"^{SOURCE_ROOT}/", ""),
        )
        .withColumn("source_system", F.lit(source_system))
        .withColumn("dataset_name", F.lit(dataset_name))
        .withColumn("batch_id", F.lit(BATCH_ID))
        .withColumn("ingestion_timestamp", F.current_timestamp())
    )


def union_all(dataframes: list[DataFrame]) -> DataFrame:
    if len(dataframes) == 1:
        return dataframes[0]
    return reduce(lambda left, right: left.unionByName(right), dataframes)

# COMMAND ----------
audit_rows = []

for dataset in DATASETS:
    source_frames = [
        read_csv_as_raw_df(
            source_path=source_path,
            source_system=dataset["source_system"],
            dataset_name=dataset["dataset_name"],
        )
        for source_path in dataset["source_paths"]
    ]

    bronze_df = union_all(source_frames)
    full_table_name = f"{BRONZE_NAMESPACE}.{dataset['target_table']}"

    (
        bronze_df.write.format("delta")
        .mode(WRITE_MODE)
        .option("overwriteSchema", "true")
        .saveAsTable(full_table_name)
    )

    row_count = bronze_df.count()
    audit_rows.append(
        (
            dataset["target_table"],
            ", ".join(dataset["source_paths"]),
            row_count,
            WRITE_MODE,
            BATCH_ID,
        )
    )
    print(f"Wrote {row_count} rows to {full_table_name}")

# COMMAND ----------
audit_df = spark.createDataFrame(
    audit_rows,
    [
        "target_table",
        "source_paths",
        "row_count",
        "write_mode",
        "batch_id",
    ],
)

audit_df.orderBy("target_table").show(truncate=False)
