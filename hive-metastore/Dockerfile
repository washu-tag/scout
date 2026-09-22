# Stock Hive metastore plus the AWS Advanced JDBC Wrapper and AWS SDK v2, for sites
# that opt into RDS IAM database auth (helm/hive-metastore dbAuth: iam). Only jars
# change; the upstream entrypoint and config are untouched.
FROM gradle:8.14.3-jdk21 AS jars

WORKDIR /app
COPY build.gradle settings.gradle ./
RUN gradle --no-daemon --quiet copyJars


FROM starburstdata/hive:3.1.3-e.15

# bin/hive puts auxlib/*.jar on the classpath; drop the stock pgjdbc so the newer one
# the wrapper needs is the only copy.
RUN rm /opt/apache-hive-3.1.3-bin/lib/postgresql-jdbc.jar
COPY --from=jars --chown=hive:root /app/build/jars/ /opt/apache-hive-3.1.3-bin/auxlib/
