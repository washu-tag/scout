package edu.washu.tag.temporal;

import java.util.Arrays;

/**
 * Example class.
 */
public class Example {

  private static final String[] strings = new String[] {
      "hello",
      "world"
  };

  /**
   * Example.
   */
  public void classicGoogle() {
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
