package edu.washu.tag.temporal.db;

import java.lang.reflect.Field;
import java.util.regex.Pattern;

public class DbUtils {

    public static final String SUCCEEDED = "succeeded";
    public static final String FAILED = "failed";
    private static final Pattern CAMEL_CASE_REGEX = Pattern.compile("([a-z])([A-Z])");
    public static String camelToSnakeCase(String camelCase) {
        return CAMEL_CASE_REGEX.matcher(camelCase).replaceAll("$1_$2").toLowerCase();
    }

//    public static String getSqlFields(Object object) {
//        Field[] fields = object.getClass().getDeclaredFields();
//    }
}
