package edu.washu.tag.extractor.hl7log.db;

import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.RecordComponent;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public class DbUtils {
    public static final DateTimeFormatter DATE_FORMATTER = DateTimeFormatter.ofPattern("yyyyMMdd");

    public enum FileStatusStatus {
        PARSED("parsed"),
        STAGED("staged"),
        INGESTED("ingested"),
        FAILED("failed");

        private final String status;

        FileStatusStatus(String status) {
            this.status = status;
        }

        public String getStatus() {
            return status;
        }
    }

    public enum FileStatusType {
        HL7("HL7"),
        LOG("Log");

        private final String type;

        FileStatusType(String type) {
            this.type = type;
        }

        public String getType() {
            return type;
        }
    }

    private static final Map<Class<?>, String> INSERT_SQL_CACHE = new ConcurrentHashMap<>();
    private static final Pattern CAMEL_CASE_REGEX = Pattern.compile("([a-z0-9])([A-Z])");

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
        String tableName = pluralize(camelToSnakeCase(recordClass.getSimpleName()));

        // Get field information
        List<String> columnNames;
        if (recordClass.isRecord()) {
            // For records, use getRecordComponents() to get only the record fields
            columnNames = Arrays.stream(recordClass.getRecordComponents())
                .map(RecordComponent::getName)
                .map(DbUtils::camelToSnakeCase)
                .toList();
        } else {
            // For non-records, filter out static fields
            columnNames = Arrays.stream(recordClass.getDeclaredFields())
                .filter(field -> !Modifier.isStatic(field.getModifiers()))
                .map(Field::getName)
                .map(DbUtils::camelToSnakeCase)
                .toList();
        }

        // Create a string of column names
        String columns = String.join(", ", columnNames);
        // Create a string of "?" placeholders
        String placeholders = Stream.generate(() -> "?")
            .limit(columnNames.size())
            .collect(Collectors.joining(", "));

        String insertSql = String.format("INSERT INTO %s (%s) VALUES (%s)", tableName, columns, placeholders);

        // Add ON CONFLICT clause if the record class has one
        try {
            Method getUpsertMethod = recordClass.getDeclaredMethod("getUpsertSql");
            insertSql += " " + getUpsertMethod.invoke(null);
        } catch (NoSuchMethodException | InvocationTargetException | IllegalAccessException e) {
            // Ignore if the method is not present
        }

        return insertSql;
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

    private static String pluralize(String singular) {
        return singular + (singular.endsWith("s") ? "e" : "") + "s";
    }
}
