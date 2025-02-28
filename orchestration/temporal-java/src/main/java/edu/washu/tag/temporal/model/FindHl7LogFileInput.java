package edu.washu.tag.temporal.model;

import java.util.List;

public record FindHl7LogFileInput(List<String> logPaths, String date, String logsRootPath) { }
