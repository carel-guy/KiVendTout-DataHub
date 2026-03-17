# Databricks notebook source
from datetime import datetime, timezone
from functools import reduce

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

# COMMAND ----------
DEFAULT_CATALOG = "kvt_project"
DEFAULT_BRONZE_SCHEMA = "bronze"
DEFAULT_SOURCE_ROOT = "/Volumes/kvt_project/bronze/source_systems"
DEFAULT_CARD_IDENTITY_COLLECTION = "01_french_id"
DEFAULT_INCLUDE_FAKE_IDENTITY = "true"
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
CARD_IDENTITY_COLLECTION = get_widget_value(
    "card_identity_collection",
    DEFAULT_CARD_IDENTITY_COLLECTION,
)
INCLUDE_FAKE_IDENTITY = (
    get_widget_value("include_fake_identity", DEFAULT_INCLUDE_FAKE_IDENTITY).lower()
    == "true"
)
WRITE_MODE = get_widget_value("write_mode", DEFAULT_WRITE_MODE)
BATCH_ID = get_widget_value(
    "batch_id",
    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
)
BRONZE_NAMESPACE = f"{CATALOG}.{BRONZE_SCHEMA}"
CARD_IDENTITY_DOCUMENT_COLLECTION = f"card_identity_{CARD_IDENTITY_COLLECTION}"

FAKE_IDENTITY_ROOT = f"{SOURCE_ROOT}/Fake_identity"
CARD_IDENTITY_IMAGE_ROOT = (
    f"{SOURCE_ROOT}/card_identity/{CARD_IDENTITY_COLLECTION}/images"
)
CARD_IDENTITY_ANNOTATION_ROOT = (
    f"{SOURCE_ROOT}/card_identity/{CARD_IDENTITY_COLLECTION}/ground_truth"
)

print(
    {
        "catalog": CATALOG,
        "bronze_schema": BRONZE_SCHEMA,
        "source_root": SOURCE_ROOT,
        "card_identity_collection": CARD_IDENTITY_COLLECTION,
        "include_fake_identity": INCLUDE_FAKE_IDENTITY,
        "write_mode": WRITE_MODE,
        "batch_id": BATCH_ID,
        "fake_identity_root": FAKE_IDENTITY_ROOT,
        "card_identity_image_root": CARD_IDENTITY_IMAGE_ROOT,
        "card_identity_annotation_root": CARD_IDENTITY_ANNOTATION_ROOT,
    }
)

# COMMAND ----------
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {BRONZE_NAMESPACE}")

# COMMAND ----------
def path_exists(path: str) -> bool:
    try:
        dbutils.fs.ls(path)
        return True
    except Exception:
        return False


def add_common_file_metadata(
    df: DataFrame,
    source_system: str,
    document_collection: str,
) -> DataFrame:
    with_names = (
        df.withColumnRenamed("path", "source_file_path")
        .withColumnRenamed("modificationTime", "source_file_modification_time")
        .withColumnRenamed("length", "source_file_size_bytes")
    )

    parent_folder = F.regexp_extract("source_file_path", r"/([^/]+)/[^/]+$", 1)

    return (
        with_names.withColumn(
            "source_file_name",
            F.regexp_extract("source_file_path", r"([^/]+)$", 1),
        )
        .withColumn(
            "source_relative_path",
            F.regexp_replace("source_file_path", f"^{SOURCE_ROOT}/", ""),
        )
        .withColumn(
            "file_extension",
            F.lower(F.regexp_extract("source_file_path", r"\.([^.]+)$", 1)),
        )
        .withColumn(
            "document_stem",
            F.regexp_extract("source_file_path", r"([^/]+)\.[^.]+$", 1),
        )
        .withColumn(
            "document_group",
            F.when(parent_folder.isin("images", "ground_truth", "Fake_identity"), F.lit(None))
            .otherwise(parent_folder),
        )
        .withColumn("source_system", F.lit(source_system))
        .withColumn("document_collection", F.lit(document_collection))
        .withColumn("batch_id", F.lit(BATCH_ID))
        .withColumn("ingestion_timestamp", F.current_timestamp())
    )


def union_all(dataframes: list[DataFrame]) -> DataFrame:
    if len(dataframes) == 1:
        return dataframes[0]
    return reduce(lambda left, right: left.unionByName(right), dataframes)

# COMMAND ----------
if not path_exists(CARD_IDENTITY_IMAGE_ROOT):
    raise FileNotFoundError(
        f"Card identity image root not found: {CARD_IDENTITY_IMAGE_ROOT}"
    )

if not path_exists(CARD_IDENTITY_ANNOTATION_ROOT):
    raise FileNotFoundError(
        f"Card identity annotation root not found: {CARD_IDENTITY_ANNOTATION_ROOT}"
    )

card_identity_tif_df = (
    spark.read.format("binaryFile")
    .option("recursiveFileLookup", "true")
    .option("pathGlobFilter", "*.tif")
    .load(CARD_IDENTITY_IMAGE_ROOT)
)

file_dataframes = [
    add_common_file_metadata(
        card_identity_tif_df.select("path", "modificationTime", "length"),
        source_system="card_identity",
        document_collection=CARD_IDENTITY_DOCUMENT_COLLECTION,
    )
]

if INCLUDE_FAKE_IDENTITY and path_exists(FAKE_IDENTITY_ROOT):
    fake_identity_png_df = (
        spark.read.format("binaryFile")
        .option("recursiveFileLookup", "true")
        .option("pathGlobFilter", "*.png")
        .load(FAKE_IDENTITY_ROOT)
    )

    fake_identity_jpg_df = (
        spark.read.format("binaryFile")
        .option("recursiveFileLookup", "true")
        .option("pathGlobFilter", "*.jpg")
        .load(FAKE_IDENTITY_ROOT)
    )

    file_dataframes = [
        add_common_file_metadata(
            fake_identity_png_df.select("path", "modificationTime", "length"),
            source_system="fake_identity",
            document_collection="fake_identity",
        ),
        add_common_file_metadata(
            fake_identity_jpg_df.select("path", "modificationTime", "length"),
            source_system="fake_identity",
            document_collection="fake_identity",
        ),
        *file_dataframes,
    ]

identity_document_files_df = union_all(file_dataframes).withColumn(
    "asset_type",
    F.lit("document_image"),
)

# COMMAND ----------
card_identity_annotations_binary_df = (
    spark.read.format("binaryFile")
    .option("recursiveFileLookup", "true")
    .option("pathGlobFilter", "*.json")
    .load(CARD_IDENTITY_ANNOTATION_ROOT)
)

identity_document_annotations_df = (
    add_common_file_metadata(
        card_identity_annotations_binary_df.select(
            "path",
            "modificationTime",
            "length",
            "content",
        ),
        source_system="card_identity",
        document_collection=CARD_IDENTITY_DOCUMENT_COLLECTION,
    )
    .withColumn("annotation_json", F.decode("content", "UTF-8"))
    .drop("content")
    .withColumn("annotation_format", F.lit("json"))
)

# COMMAND ----------
(
    identity_document_files_df.write.format("delta")
    .mode(WRITE_MODE)
    .option("overwriteSchema", "true")
    .saveAsTable(f"{BRONZE_NAMESPACE}.bronze_identity_document_files_raw")
)

(
    identity_document_annotations_df.write.format("delta")
    .mode(WRITE_MODE)
    .option("overwriteSchema", "true")
    .saveAsTable(f"{BRONZE_NAMESPACE}.bronze_identity_document_annotations_raw")
)

print(
    {
        "bronze_identity_document_files_raw": identity_document_files_df.count(),
        "bronze_identity_document_annotations_raw": identity_document_annotations_df.count(),
    }
)

# COMMAND ----------
identity_document_files_df.orderBy("source_file_path").show(20, truncate=False)
identity_document_annotations_df.orderBy("source_file_path").show(20, truncate=False)
