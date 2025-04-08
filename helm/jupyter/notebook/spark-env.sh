#!/usr/bin/env bash

# AWS S3A configurations
SPARK_OPTS+=" --conf spark.hadoop.fs.s3a.access.key=${AWS_ACCESS_KEY_ID}"
SPARK_OPTS+=" --conf spark.hadoop.fs.s3a.secret.key=${AWS_SECRET_ACCESS_KEY}"
SPARK_OPTS+=" --conf spark.hadoop.fs.s3a.endpoint=${AWS_ENDPOINT_URL}"
SPARK_OPTS+=" --conf spark.hadoop.fs.s3a.endpoint.region=${AWS_REGION:-us-east-1}"
SPARK_OPTS+=" --conf spark.hadoop.fs.s3a.path.style.access=true"
SPARK_OPTS+=" --conf spark.hadoop.fs.s3a.aws.credentials.provider=org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider"

# Delta Lake configurations
SPARK_OPTS+=" --conf spark.databricks.delta.schema.autoMerge.enabled=true"
SPARK_OPTS+=" --conf spark.databricks.delta.merge.repartitionBeforeWrite.enabled=true"
SPARK_OPTS+=" --conf spark.sql.extensions=io.delta.sql.DeltaSparkSessionExtension"
SPARK_OPTS+=" --conf spark.sql.catalog.spark_catalog=org.apache.spark.sql.delta.catalog.DeltaCatalog"

# Additional Spark configurations
SPARK_OPTS+=" --conf spark.driver.extraJavaOptions=-Divy.cache.dir=/tmp -Divy.home=/tmp"

if [ -n "${SPARK_EXECUTOR_MEMORY}" ]; then
    SPARK_OPTS+=" --conf spark.executor.memory=${SPARK_EXECUTOR_MEMORY}"
fi

if [ -n "${SPARK_DRIVER_MEMORY}" ]; then
    SPARK_OPTS+=" --conf spark.driver.memory=${SPARK_DRIVER_MEMORY}"
fi

export SPARK_OPTS