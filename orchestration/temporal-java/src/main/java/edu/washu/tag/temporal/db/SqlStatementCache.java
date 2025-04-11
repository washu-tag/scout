package edu.washu.tag.temporal.db;

import java.lang.reflect.Field;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public class SqlStatementCache {
    private static final Map<Class<?>, String> INSERT_SQL_CACHE = new ConcurrentHashMap<>();
    private static final Pattern CAMEL_CASE_REGEX = Pattern.compile("([a-z])([A-Z])");
    private static final String UPSERT_SQL_TEMPLATE = "ON CONFLICT (%s) DO %s";

    /**
     * Get the cached SQL insert statement for a record class
     * @param recordClass The record class
     * @return The SQL insert statement
     */
    public static <T> String getInsertSql(Class<T> recordClass) {
        return INSERT_SQL_CACHE.computeIfAbsent(recordClass, SqlStatementCache::generateInsertSql);
    }

    /**
     * Get the SQL upsert statement for a record class
     * @param recordClass The record class
     * @return The SQL upsert statement
     */
    public static <T> String getUpsertSql(Class<T> recordClass) {
        return INSERT_SQL_CACHE.computeIfAbsent(recordClass, SqlStatementCache::generateInsertSql);
    }

    /**
     * Generate the SQL insert statement for a record class
     * @param recordClass The record class
     * @return The SQL insert statement
     */
    private static <T> String generateInsertSql(Class<T> recordClass) {
        // Get table name from record class name
        String tableName = camelToSnakeCase(recordClass.getSimpleName());

        // Get field information
        List<String> columnNames = Arrays.stream(recordClass.getDeclaredFields())
            .map(Field::getName)
            .map(SqlStatementCache::camelToSnakeCase)
            .toList();

        // Create a string of column names
        String columns = String.join(", ", columnNames);
        // Create a string of "?" placeholders
        String placeholders = Stream.generate(() -> "?").limit(columnNames.size()).collect(Collectors.joining(", "));

        return String.format("INSERT INTO %s (%s) VALUES (%s)", tableName, columns, placeholders);
    }

    /**
     * Extract values from a record
     * @param record The record
     * @return Array of values
     */
    public static <T> Object[] extractValues(T record) {
        return Arrays.stream(record.getClass().getDeclaredFields())
            .map(field -> {
                try {
                    return field.get(record);
                } catch (IllegalAccessException e) {
                    throw new RuntimeException(e);
                }
            })
            .toArray(Object[]::new);
    }

    // Utility method to convert camelCase to snake_case
    private static String camelToSnakeCase(String camelCase) {
        return CAMEL_CASE_REGEX.matcher(camelCase).replaceAll("$1_$2").toLowerCase();
    }
}