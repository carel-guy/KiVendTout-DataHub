# Databricks notebook source
from datetime import datetime, timezone
from functools import reduce

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

# COMMAND ----------
DEFAULT_CATALOG = "kvt_project"
DEFAULT_BRONZE_SCHEMA = "bronze"
DEFAULT_SILVER_SCHEMA = "silver"
DEFAULT_KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
DEFAULT_KAFKA_TOPIC = "fraud.alerts"
DEFAULT_TRIGGER_MODE = "available_now"
DEFAULT_PROCESSING_TIME = "30 seconds"
DEFAULT_CHECKPOINT_ROOT = "/Volumes/kvt_project/bronze/source_systems/_checkpoints"
DEFAULT_PAYMENTS_STREAM_TABLE = "bronze_ecommerce_order_payments_stream_raw"
DEFAULT_ORDERS_STREAM_TABLE = "bronze_ecommerce_orders_stream_raw"
DEFAULT_ALERTS_TABLE = "silver_fraud_alerts_live"
DEFAULT_ALERT_THRESHOLD = "0.70"
DEFAULT_HIGH_VALUE_THRESHOLD = "250.0"
DEFAULT_HIGH_INSTALLMENTS_THRESHOLD = "6"
DEFAULT_HISTORICAL_RISK_THRESHOLD = "0.50"


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
KAFKA_BOOTSTRAP_SERVERS = get_widget(
    "kafka_bootstrap_servers",
    DEFAULT_KAFKA_BOOTSTRAP_SERVERS,
)
KAFKA_TOPIC = get_widget("kafka_topic", DEFAULT_KAFKA_TOPIC)
TRIGGER_MODE = get_widget("trigger_mode", DEFAULT_TRIGGER_MODE).lower()
PROCESSING_TIME = get_widget("processing_time", DEFAULT_PROCESSING_TIME)
CHECKPOINT_ROOT = get_widget("checkpoint_root", DEFAULT_CHECKPOINT_ROOT)
PAYMENTS_STREAM_TABLE = get_widget(
    "payments_stream_table",
    DEFAULT_PAYMENTS_STREAM_TABLE,
)
ORDERS_STREAM_TABLE = get_widget(
    "orders_stream_table",
    DEFAULT_ORDERS_STREAM_TABLE,
)
ALERTS_TABLE = get_widget("alerts_table", DEFAULT_ALERTS_TABLE)
ALERT_THRESHOLD = float(get_widget("alert_threshold", DEFAULT_ALERT_THRESHOLD))
HIGH_VALUE_THRESHOLD = float(
    get_widget("high_value_threshold", DEFAULT_HIGH_VALUE_THRESHOLD)
)
HIGH_INSTALLMENTS_THRESHOLD = int(
    get_widget("high_installments_threshold", DEFAULT_HIGH_INSTALLMENTS_THRESHOLD)
)
HISTORICAL_RISK_THRESHOLD = float(
    get_widget("historical_risk_threshold", DEFAULT_HISTORICAL_RISK_THRESHOLD)
)
RUN_ID = get_widget(
    "run_id",
    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
)

BRONZE_NAMESPACE = f"{CATALOG}.{BRONZE_SCHEMA}"
SILVER_NAMESPACE = f"{CATALOG}.{SILVER_SCHEMA}"
PAYMENTS_STREAM_FULL_NAME = f"{BRONZE_NAMESPACE}.{PAYMENTS_STREAM_TABLE}"
ORDERS_STREAM_FULL_NAME = f"{BRONZE_NAMESPACE}.{ORDERS_STREAM_TABLE}"
ALERTS_FULL_TABLE_NAME = f"{SILVER_NAMESPACE}.{ALERTS_TABLE}"
CHECKPOINT_LOCATION = f"{CHECKPOINT_ROOT}/{ALERTS_TABLE}"

print(
    {
        "catalog": CATALOG,
        "bronze_schema": BRONZE_SCHEMA,
        "silver_schema": SILVER_SCHEMA,
        "payments_stream_table": PAYMENTS_STREAM_FULL_NAME,
        "orders_stream_table": ORDERS_STREAM_FULL_NAME,
        "alerts_table": ALERTS_FULL_TABLE_NAME,
        "kafka_bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
        "kafka_topic": KAFKA_TOPIC,
        "trigger_mode": TRIGGER_MODE,
        "processing_time": PROCESSING_TIME,
        "checkpoint_location": CHECKPOINT_LOCATION,
        "alert_threshold": ALERT_THRESHOLD,
        "high_value_threshold": HIGH_VALUE_THRESHOLD,
        "high_installments_threshold": HIGH_INSTALLMENTS_THRESHOLD,
        "historical_risk_threshold": HISTORICAL_RISK_THRESHOLD,
        "run_id": RUN_ID,
    }
)

# COMMAND ----------
if TRIGGER_MODE not in {"available_now", "processing_time"}:
    raise ValueError("trigger_mode must be 'available_now' or 'processing_time'")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {SILVER_NAMESPACE}")


def table_exists(namespace: str, table_name: str) -> bool:
    return len(spark.sql(f"SHOW TABLES IN {namespace} LIKE '{table_name}'").take(1)) > 0


def read_table(namespace: str, table_name: str) -> DataFrame:
    if not table_exists(namespace, table_name):
        raise FileNotFoundError(f"Missing table: {namespace}.{table_name}")
    return spark.table(f"{namespace}.{table_name}")


def union_all(dataframes: list[DataFrame]) -> DataFrame:
    if len(dataframes) == 1:
        return dataframes[0]
    return reduce(lambda left, right: left.unionByName(right, allowMissingColumns=True), dataframes)


if not table_exists(BRONZE_NAMESPACE, PAYMENTS_STREAM_TABLE):
    raise FileNotFoundError(f"Missing table: {PAYMENTS_STREAM_FULL_NAME}")


def latest_orders_snapshot() -> DataFrame | None:
    order_frames = []

    if table_exists(SILVER_NAMESPACE, "silver_orders_clean"):
        order_frames.append(
            read_table(SILVER_NAMESPACE, "silver_orders_clean").select(
                F.col("order_id").alias("order_id"),
                F.col("customer_id").alias("customer_id"),
                F.col("order_status").alias("order_status"),
                F.col("order_purchase_timestamp").alias("order_purchase_timestamp"),
                F.col("ingestion_timestamp").alias("ingestion_timestamp"),
            )
        )

    if table_exists(BRONZE_NAMESPACE, ORDERS_STREAM_TABLE):
        order_frames.append(
            read_table(BRONZE_NAMESPACE, ORDERS_STREAM_TABLE).select(
                F.col("order_id").alias("order_id"),
                F.col("customer_id").alias("customer_id"),
                F.lower(F.trim(F.col("order_status"))).alias("order_status"),
                F.to_timestamp(F.col("order_purchase_timestamp")).alias(
                    "order_purchase_timestamp"
                ),
                F.col("ingestion_timestamp").alias("ingestion_timestamp"),
            )
        )

    if not order_frames:
        return None

    orders_union = union_all(order_frames)
    order_window = Window.partitionBy("order_id").orderBy(
        F.col("ingestion_timestamp").desc_nulls_last(),
        F.col("order_purchase_timestamp").desc_nulls_last(),
    )

    return (
        orders_union.withColumn("row_number", F.row_number().over(order_window))
        .filter(F.col("row_number") == 1)
        .drop("row_number")
    )


def publish_alerts(batch_df: DataFrame, micro_batch_id: int) -> None:
    if batch_df.rdd.isEmpty():
        print(f"Micro-batch {micro_batch_id}: no rows to score.")
        return

    orders_lookup_df = latest_orders_snapshot()
    customer360_exists = table_exists(SILVER_NAMESPACE, "silver_customer360_base")

    scored_df = (
        batch_df.select(
            F.col("order_id").alias("order_id"),
            F.col("payment_sequential").cast("int").alias("payment_sequential"),
            F.lower(F.trim(F.col("payment_type"))).alias("payment_type"),
            F.col("payment_installments").cast("int").alias("payment_installments"),
            F.col("payment_value").cast("double").alias("payment_value"),
            F.col("kafka_partition").alias("kafka_partition"),
            F.col("kafka_offset").alias("kafka_offset"),
            F.col("kafka_timestamp").alias("kafka_timestamp"),
            F.col("ingestion_timestamp").alias("ingestion_timestamp"),
        )
        .withColumn("high_value_flag", F.col("payment_value") >= F.lit(HIGH_VALUE_THRESHOLD))
        .withColumn(
            "high_installments_flag",
            F.col("payment_installments") >= F.lit(HIGH_INSTALLMENTS_THRESHOLD),
        )
    )

    if orders_lookup_df is not None:
        scored_df = scored_df.join(orders_lookup_df, on="order_id", how="left")
    else:
        scored_df = (
            scored_df.withColumn("customer_id", F.lit(None).cast("string"))
            .withColumn("order_status", F.lit(None).cast("string"))
            .withColumn("order_purchase_timestamp", F.lit(None).cast("timestamp"))
        )

    if customer360_exists:
        customer360_df = read_table(SILVER_NAMESPACE, "silver_customer360_base").select(
            F.col("customer_id").alias("customer_id"),
            F.col("fraud_order_count").alias("fraud_order_count"),
            F.col("average_fraud_risk_score").alias("average_fraud_risk_score"),
            F.col("max_fraud_risk_score").alias("max_fraud_risk_score"),
        )
        scored_df = scored_df.join(customer360_df, on="customer_id", how="left")
    else:
        scored_df = (
            scored_df.withColumn("fraud_order_count", F.lit(0))
            .withColumn("average_fraud_risk_score", F.lit(0.0))
            .withColumn("max_fraud_risk_score", F.lit(0.0))
        )

    alerts_df = (
        scored_df.withColumn(
            "historical_fraud_flag",
            F.coalesce(F.col("fraud_order_count"), F.lit(0)) > 0,
        )
        .withColumn(
            "historical_risk_flag",
            F.coalesce(F.col("max_fraud_risk_score"), F.lit(0.0))
            >= F.lit(HISTORICAL_RISK_THRESHOLD),
        )
        .withColumn(
            "risk_score",
            F.least(
                F.lit(0.99),
                F.when(F.col("high_value_flag"), F.lit(0.40)).otherwise(F.lit(0.0))
                + F.when(F.col("high_installments_flag"), F.lit(0.20)).otherwise(
                    F.lit(0.0)
                )
                + F.when(F.col("historical_fraud_flag"), F.lit(0.25)).otherwise(
                    F.lit(0.0)
                )
                + F.when(F.col("historical_risk_flag"), F.lit(0.15)).otherwise(
                    F.lit(0.0)
                ),
            ),
        )
        .withColumn(
            "fraud_reason",
            F.concat_ws(
                ";",
                F.when(F.col("high_value_flag"), F.lit("high_payment_value")),
                F.when(F.col("high_installments_flag"), F.lit("high_installments")),
                F.when(F.col("historical_fraud_flag"), F.lit("historical_fraud_customer")),
                F.when(F.col("historical_risk_flag"), F.lit("historical_high_risk")),
            ),
        )
        .withColumn(
            "alert_level",
            F.when(F.col("risk_score") >= 0.85, F.lit("high"))
            .when(F.col("risk_score") >= ALERT_THRESHOLD, F.lit("medium"))
            .otherwise(F.lit("low")),
        )
        .filter(F.col("risk_score") >= F.lit(ALERT_THRESHOLD))
        .withColumn(
            "alert_id",
            F.sha2(
                F.concat_ws(
                    "|",
                    F.col("order_id"),
                    F.col("payment_sequential").cast("string"),
                    F.col("kafka_partition").cast("string"),
                    F.col("kafka_offset").cast("string"),
                ),
                256,
            ),
        )
        .withColumn("alert_timestamp", F.current_timestamp())
        .withColumn("source_system", F.lit("fraud"))
        .withColumn("dataset_name", F.lit("fraud_alerts_live"))
        .withColumn("batch_id", F.lit(RUN_ID))
        .withColumn("stream_micro_batch_id", F.lit(str(micro_batch_id)))
    )

    if alerts_df.rdd.isEmpty():
        print(f"Micro-batch {micro_batch_id}: no alerts emitted.")
        return

    (
        alerts_df.write.format("delta")
        .mode("append")
        .saveAsTable(ALERTS_FULL_TABLE_NAME)
    )

    kafka_df = alerts_df.select(
        F.col("order_id").cast("string").alias("key"),
        F.to_json(
            F.struct(
                "alert_id",
                "order_id",
                "customer_id",
                "order_status",
                "payment_sequential",
                "payment_type",
                "payment_installments",
                "payment_value",
                "risk_score",
                "alert_level",
                "fraud_reason",
                "alert_timestamp",
            )
        ).alias("value"),
    )

    (
        kafka_df.write.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP_SERVERS)
        .option("topic", KAFKA_TOPIC)
        .save()
    )

    print(f"Micro-batch {micro_batch_id}: published {alerts_df.count()} alerts.")


# COMMAND ----------
payments_stream_df = spark.readStream.table(PAYMENTS_STREAM_FULL_NAME)

streaming_input_df = payments_stream_df.select(
    F.col("order_id").alias("order_id"),
    F.col("payment_sequential").alias("payment_sequential"),
    F.col("payment_type").alias("payment_type"),
    F.col("payment_installments").alias("payment_installments"),
    F.col("payment_value").alias("payment_value"),
    F.col("kafka_partition").alias("kafka_partition"),
    F.col("kafka_offset").alias("kafka_offset"),
    F.col("kafka_timestamp").alias("kafka_timestamp"),
    F.col("ingestion_timestamp").alias("ingestion_timestamp"),
)

writer = (
    streaming_input_df.writeStream.outputMode("append")
    .foreachBatch(publish_alerts)
    .option("checkpointLocation", CHECKPOINT_LOCATION)
    .queryName(ALERTS_TABLE)
)

if TRIGGER_MODE == "available_now":
    writer = writer.trigger(availableNow=True)
else:
    writer = writer.trigger(processingTime=PROCESSING_TIME)

query = writer.start()

print(f"Started query {query.id} -> Kafka topic {KAFKA_TOPIC}")
query.awaitTermination()
