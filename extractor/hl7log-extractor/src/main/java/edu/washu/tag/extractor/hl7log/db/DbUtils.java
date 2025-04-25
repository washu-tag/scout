package edu.washu.tag.extractor.hl7log.db;

import java.lang.reflect.Field;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public class DbUtils {

    public static final String SUCCEEDED = "succeeded";
    public static final String FAILED = "failed";

    private static final Map<Class<?>, String> INSERT_SQL_CACHE = new ConcurrentHashMap<>();
    private static final Pattern CAMEL_CASE_REGEX = Pattern.compile("([a-z0-9])([A-Z])");
    static final Pattern DATE_PATTERN = Pattern.compile("\\d{8}");
    static final DateTimeFormatter DATE_FORMATTER = DateTimeFormatter.ofPattern("yyyyMMdd");

    /**
     * Get the cached SQL insert statement for a record class
     * @param recordClass The record class
     * @return The SQL insert statement
     */
    public static <T> String getInsertSql(Class<T> recordClass) {
        return INSERT_SQL_CACHE.computeIfAbsent(recordClass, DbUtils::generateInsertSql);
    }

    /**
     * Generate the SQL insert statement for a record class
     * @param recordClass The record class
     * @return The SQL insert statement
     */
    private static <T> String generateInsertSql(Class<T> recordClass) {
        // Get table name from record class name
        String tableName = camelToSnakeCase(recordClass.getSimpleName()) + "s";

        // Get field information
        List<String> columnNames = Arrays.stream(recordClass.getDeclaredFields())
            .map(Field::getName)
            .map(DbUtils::camelToSnakeCase)
            .toList();

        // Create a string of column names
        String columns = String.join(", ", columnNames);
        // Create a string of "?" placeholders
        String placeholders = Stream.generate(() -> "?")
            .limit(columnNames.size())
            .collect(Collectors.joining(", "));

        return String.format("INSERT INTO %s (%s) VALUES (%s)", tableName, columns, placeholders);
    }

    /**
     * Extract values from a record
     * @param record The record
     * @return Array of values
     */
    public static <T> Object[] extractValues(T record) {
        if (!record.getClass().isRecord()) {
            throw new IllegalArgumentException("Provided object is not a record");
        }
        return Arrays.stream(record.getClass().getRecordComponents())
            .map(component -> {
                try {
                    return component.getAccessor().invoke(record);
                } catch (ReflectiveOperationException e) {
                    throw new RuntimeException("Failed to extract value from record component: " + component.getName(), e);
                }
            })
            .toArray(Object[]::new);
    }

    // Utility method to convert camelCase to snake_case
    private static String camelToSnakeCase(String camelCase) {
        return CAMEL_CASE_REGEX.matcher(camelCase).replaceAll("$1_$2").toLowerCase();
    }
}
