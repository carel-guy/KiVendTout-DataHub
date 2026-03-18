# Databricks notebook source
from datetime import datetime, timezone

from pyspark.sql import functions as F
from pyspark.sql import types as T

# COMMAND ----------
DEFAULT_CATALOG = "kvt_project"
DEFAULT_BRONZE_SCHEMA = "bronze"
DEFAULT_KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
DEFAULT_KAFKA_TOPIC = "clickstream.events"
DEFAULT_STARTING_OFFSETS = "latest"
DEFAULT_TRIGGER_MODE = "available_now"
DEFAULT_PROCESSING_TIME = "30 seconds"
DEFAULT_CHECKPOINT_ROOT = "/Volumes/kvt_project/bronze/source_systems/_checkpoints"
DEFAULT_TARGET_TABLE = "bronze_clickstream_events_stream_raw"


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
KAFKA_TOPIC = get_widget("kafka_topic", DEFAULT_KAFKA_TOPIC)
STARTING_OFFSETS = get_widget("starting_offsets", DEFAULT_STARTING_OFFSETS)
TRIGGER_MODE = get_widget("trigger_mode", DEFAULT_TRIGGER_MODE).lower()
PROCESSING_TIME = get_widget("processing_time", DEFAULT_PROCESSING_TIME)
CHECKPOINT_ROOT = get_widget("checkpoint_root", DEFAULT_CHECKPOINT_ROOT)
TARGET_TABLE = get_widget("target_table", DEFAULT_TARGET_TABLE)
RUN_ID = get_widget(
    "run_id",
    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
)

BRONZE_NAMESPACE = f"{CATALOG}.{BRONZE_SCHEMA}"
FULL_TABLE_NAME = f"{BRONZE_NAMESPACE}.{TARGET_TABLE}"
CHECKPOINT_LOCATION = f"{CHECKPOINT_ROOT}/{TARGET_TABLE}"

print(
    {
        "catalog": CATALOG,
        "bronze_schema": BRONZE_SCHEMA,
        "kafka_bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
        "kafka_topic": KAFKA_TOPIC,
        "starting_offsets": STARTING_OFFSETS,
        "trigger_mode": TRIGGER_MODE,
        "processing_time": PROCESSING_TIME,
        "checkpoint_location": CHECKPOINT_LOCATION,
        "target_table": FULL_TABLE_NAME,
        "run_id": RUN_ID,
    }
)

# COMMAND ----------
if TRIGGER_MODE not in {"available_now", "processing_time"}:
    raise ValueError("trigger_mode must be 'available_now' or 'processing_time'")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {BRONZE_NAMESPACE}")

EVENT_SCHEMA = T.StructType(
    [
        T.StructField("timestamp", T.LongType(), True),
        T.StructField("visitorid", T.StringType(), True),
        T.StructField("event", T.StringType(), True),
        T.StructField("itemid", T.StringType(), True),
        T.StructField("transactionid", T.StringType(), True),
    ]
)

# COMMAND ----------
raw_kafka_df = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
    .option("subscribe", KAFKA_TOPIC)
    .option("startingOffsets", STARTING_OFFSETS)
    .load()
)

bronze_events_stream_df = (
    raw_kafka_df.selectExpr(
        "CAST(key AS STRING) AS kafka_key",
        "CAST(value AS STRING) AS kafka_value",
        "topic AS kafka_topic",
        "partition AS kafka_partition",
        "offset AS kafka_offset",
        "timestamp AS kafka_timestamp",
    )
    .withColumn("parsed_json", F.from_json(F.col("kafka_value"), EVENT_SCHEMA))
    .select(
        F.col("parsed_json.timestamp").cast("string").alias("timestamp"),
        F.col("parsed_json.visitorid").cast("string").alias("visitorid"),
        F.col("parsed_json.event").cast("string").alias("event"),
        F.col("parsed_json.itemid").cast("string").alias("itemid"),
        F.col("parsed_json.transactionid").cast("string").alias("transactionid"),
        F.lit(None).cast("string").alias("source_file_path"),
        F.lit(None).cast("string").alias("source_file_name"),
        F.lit(None).cast("string").alias("source_relative_path"),
        F.lit("clickstream").alias("source_system"),
        F.lit("events_stream").alias("dataset_name"),
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
writer = (
    bronze_events_stream_df.writeStream.outputMode("append")
    .format("delta")
    .option("checkpointLocation", CHECKPOINT_LOCATION)
    .queryName(TARGET_TABLE)
)

if TRIGGER_MODE == "available_now":
    writer = writer.trigger(availableNow=True)
else:
    writer = writer.trigger(processingTime=PROCESSING_TIME)

query = writer.toTable(FULL_TABLE_NAME)

print(f"Started query {query.id} -> {FULL_TABLE_NAME}")
query.awaitTermination()
