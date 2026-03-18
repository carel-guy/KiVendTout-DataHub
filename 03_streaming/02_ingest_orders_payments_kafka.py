# Databricks notebook source
from datetime import datetime, timezone

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql import types as T

# COMMAND ----------
DEFAULT_CATALOG = "kvt_project"
DEFAULT_BRONZE_SCHEMA = "bronze"
DEFAULT_KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
DEFAULT_ORDERS_TOPIC = "ecommerce.orders"
DEFAULT_PAYMENTS_TOPIC = "ecommerce.payments"
DEFAULT_STARTING_OFFSETS = "latest"
DEFAULT_TRIGGER_MODE = "available_now"
DEFAULT_PROCESSING_TIME = "30 seconds"
DEFAULT_CHECKPOINT_ROOT = "/Volumes/kvt_project/bronze/source_systems/_checkpoints"
DEFAULT_ORDERS_TARGET_TABLE = "bronze_ecommerce_orders_stream_raw"
DEFAULT_PAYMENTS_TARGET_TABLE = "bronze_ecommerce_order_payments_stream_raw"


def get_widget(name: str, default_value: str) -> str:
    try:
        dbutils.widgets.text(name, default_value)
        value = dbutils.widgets.get(name)
        return value or default_value
    except Exception:
        return default_value


CATALOG = get_widget("catalog", DEFAULT_CATALOG)
BRONZE_SCHEMA = get_widget("bronze_schema", DEFAULT_BRONZE_SCHEMA)
KAFKA_BOOTSTRAP_SERVERS = get_widget(
    "kafka_bootstrap_servers",
    DEFAULT_KAFKA_BOOTSTRAP_SERVERS,
)
ORDERS_TOPIC = get_widget("orders_topic", DEFAULT_ORDERS_TOPIC)
PAYMENTS_TOPIC = get_widget("payments_topic", DEFAULT_PAYMENTS_TOPIC)
STARTING_OFFSETS = get_widget("starting_offsets", DEFAULT_STARTING_OFFSETS)
TRIGGER_MODE = get_widget("trigger_mode", DEFAULT_TRIGGER_MODE).lower()
PROCESSING_TIME = get_widget("processing_time", DEFAULT_PROCESSING_TIME)
CHECKPOINT_ROOT = get_widget("checkpoint_root", DEFAULT_CHECKPOINT_ROOT)
ORDERS_TARGET_TABLE = get_widget("orders_target_table", DEFAULT_ORDERS_TARGET_TABLE)
PAYMENTS_TARGET_TABLE = get_widget(
    "payments_target_table",
    DEFAULT_PAYMENTS_TARGET_TABLE,
)
RUN_ID = get_widget(
    "run_id",
    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
)

BRONZE_NAMESPACE = f"{CATALOG}.{BRONZE_SCHEMA}"
ORDERS_FULL_TABLE_NAME = f"{BRONZE_NAMESPACE}.{ORDERS_TARGET_TABLE}"
PAYMENTS_FULL_TABLE_NAME = f"{BRONZE_NAMESPACE}.{PAYMENTS_TARGET_TABLE}"
ORDERS_CHECKPOINT_LOCATION = f"{CHECKPOINT_ROOT}/{ORDERS_TARGET_TABLE}"
PAYMENTS_CHECKPOINT_LOCATION = f"{CHECKPOINT_ROOT}/{PAYMENTS_TARGET_TABLE}"

print(
    {
        "catalog": CATALOG,
        "bronze_schema": BRONZE_SCHEMA,
        "kafka_bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
        "orders_topic": ORDERS_TOPIC,
        "payments_topic": PAYMENTS_TOPIC,
        "starting_offsets": STARTING_OFFSETS,
        "trigger_mode": TRIGGER_MODE,
        "processing_time": PROCESSING_TIME,
        "orders_target_table": ORDERS_FULL_TABLE_NAME,
        "payments_target_table": PAYMENTS_FULL_TABLE_NAME,
        "orders_checkpoint": ORDERS_CHECKPOINT_LOCATION,
        "payments_checkpoint": PAYMENTS_CHECKPOINT_LOCATION,
        "run_id": RUN_ID,
    }
)

# COMMAND ----------
if TRIGGER_MODE not in {"available_now", "processing_time"}:
    raise ValueError("trigger_mode must be 'available_now' or 'processing_time'")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {BRONZE_NAMESPACE}")

ORDERS_SCHEMA = T.StructType(
    [
        T.StructField("order_id", T.StringType(), True),
        T.StructField("customer_id", T.StringType(), True),
        T.StructField("order_status", T.StringType(), True),
        T.StructField("order_purchase_timestamp", T.StringType(), True),
        T.StructField("order_approved_at", T.StringType(), True),
        T.StructField("order_delivered_carrier_date", T.StringType(), True),
        T.StructField("order_delivered_customer_date", T.StringType(), True),
        T.StructField("order_estimated_delivery_date", T.StringType(), True),
    ]
)

PAYMENTS_SCHEMA = T.StructType(
    [
        T.StructField("order_id", T.StringType(), True),
        T.StructField("payment_sequential", T.StringType(), True),
        T.StructField("payment_type", T.StringType(), True),
        T.StructField("payment_installments", T.StringType(), True),
        T.StructField("payment_value", T.StringType(), True),
    ]
)


def build_kafka_stream(topic_name: str) -> DataFrame:
    return (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
        .option("subscribe", topic_name)
        .option("startingOffsets", STARTING_OFFSETS)
        .load()
    )


def apply_trigger(writer):
    if TRIGGER_MODE == "available_now":
        return writer.trigger(availableNow=True)
    return writer.trigger(processingTime=PROCESSING_TIME)


def start_delta_query(
    df: DataFrame,
    full_table_name: str,
    checkpoint_location: str,
    query_name: str,
):
    writer = (
        df.writeStream.outputMode("append")
        .format("delta")
        .option("checkpointLocation", checkpoint_location)
        .queryName(query_name)
    )
    writer = apply_trigger(writer)
    return writer.toTable(full_table_name)


# COMMAND ----------
orders_stream_df = (
    build_kafka_stream(ORDERS_TOPIC)
    .selectExpr(
        "CAST(key AS STRING) AS kafka_key",
        "CAST(value AS STRING) AS kafka_value",
        "topic AS kafka_topic",
        "partition AS kafka_partition",
        "offset AS kafka_offset",
        "timestamp AS kafka_timestamp",
    )
    .withColumn("parsed_json", F.from_json(F.col("kafka_value"), ORDERS_SCHEMA))
    .select(
        F.col("parsed_json.order_id").cast("string").alias("order_id"),
        F.col("parsed_json.customer_id").cast("string").alias("customer_id"),
        F.col("parsed_json.order_status").cast("string").alias("order_status"),
        F.col("parsed_json.order_purchase_timestamp")
        .cast("string")
        .alias("order_purchase_timestamp"),
        F.col("parsed_json.order_approved_at").cast("string").alias("order_approved_at"),
        F.col("parsed_json.order_delivered_carrier_date")
        .cast("string")
        .alias("order_delivered_carrier_date"),
        F.col("parsed_json.order_delivered_customer_date")
        .cast("string")
        .alias("order_delivered_customer_date"),
        F.col("parsed_json.order_estimated_delivery_date")
        .cast("string")
        .alias("order_estimated_delivery_date"),
        F.lit(None).cast("string").alias("source_file_path"),
        F.lit(None).cast("string").alias("source_file_name"),
        F.lit(None).cast("string").alias("source_relative_path"),
        F.lit("ecommerce").alias("source_system"),
        F.lit("orders_stream").alias("dataset_name"),
        F.lit(RUN_ID).alias("batch_id"),
        F.current_timestamp().alias("ingestion_timestamp"),
        F.col("kafka_key").alias("kafka_key"),
        F.col("kafka_topic").alias("kafka_topic"),
        F.col("kafka_partition").alias("kafka_partition"),
        F.col("kafka_offset").alias("kafka_offset"),
        F.col("kafka_timestamp").alias("kafka_timestamp"),
    )
)

payments_stream_df = (
    build_kafka_stream(PAYMENTS_TOPIC)
    .selectExpr(
        "CAST(key AS STRING) AS kafka_key",
        "CAST(value AS STRING) AS kafka_value",
        "topic AS kafka_topic",
        "partition AS kafka_partition",
        "offset AS kafka_offset",
        "timestamp AS kafka_timestamp",
    )
    .withColumn("parsed_json", F.from_json(F.col("kafka_value"), PAYMENTS_SCHEMA))
    .select(
        F.col("parsed_json.order_id").cast("string").alias("order_id"),
        F.col("parsed_json.payment_sequential").cast("string").alias("payment_sequential"),
        F.col("parsed_json.payment_type").cast("string").alias("payment_type"),
        F.col("parsed_json.payment_installments")
        .cast("string")
        .alias("payment_installments"),
        F.col("parsed_json.payment_value").cast("string").alias("payment_value"),
        F.lit(None).cast("string").alias("source_file_path"),
        F.lit(None).cast("string").alias("source_file_name"),
        F.lit(None).cast("string").alias("source_relative_path"),
        F.lit("ecommerce").alias("source_system"),
        F.lit("order_payments_stream").alias("dataset_name"),
        F.lit(RUN_ID).alias("batch_id"),
        F.current_timestamp().alias("ingestion_timestamp"),
        F.col("kafka_key").alias("kafka_key"),
        F.col("kafka_topic").alias("kafka_topic"),
        F.col("kafka_partition").alias("kafka_partition"),
        F.col("kafka_offset").alias("kafka_offset"),
        F.col("kafka_timestamp").alias("kafka_timestamp"),
    )
)

# COMMAND ----------
orders_query = start_delta_query(
    df=orders_stream_df,
    full_table_name=ORDERS_FULL_TABLE_NAME,
    checkpoint_location=ORDERS_CHECKPOINT_LOCATION,
    query_name=ORDERS_TARGET_TABLE,
)

payments_query = start_delta_query(
    df=payments_stream_df,
    full_table_name=PAYMENTS_FULL_TABLE_NAME,
    checkpoint_location=PAYMENTS_CHECKPOINT_LOCATION,
    query_name=PAYMENTS_TARGET_TABLE,
)

print(f"Started query {orders_query.id} -> {ORDERS_FULL_TABLE_NAME}")
print(f"Started query {payments_query.id} -> {PAYMENTS_FULL_TABLE_NAME}")

orders_query.awaitTermination()
payments_query.awaitTermination()
