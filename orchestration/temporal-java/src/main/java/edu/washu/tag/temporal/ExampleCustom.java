package edu.washu.tag.temporal;

import java.util.Arrays;

public class ExampleCustom {

    private static final String[] strings = new String[] {
        "hello",
        "world"
    };

    public void custom() {
        final int[] nonsenseInts = Arrays.stream(strings)
            .map(String::toUpperCase)
            .mapToInt(x -> {
                int asInt = 0;
                for (char stringDigit : x.toCharArray()) {
                    asInt += (int) stringDigit;
                }
                return asInt;
            }).toArray();
        System.out.println(nonsenseInts);
    }

}
