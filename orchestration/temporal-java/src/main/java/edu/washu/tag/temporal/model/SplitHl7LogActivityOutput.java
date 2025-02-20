package edu.washu.tag.temporal.model;

import java.util.List;

public record SplitHl7LogActivityOutput(String date, String rootPath, List<String> relativePaths) { }
