package edu.washu.tag.temporal.model;

import java.util.List;

/**
 * Output for the FindHl7LogsActivity.
 *
 * @param logFiles List of log files to process in this workflow.
 * @param continued If we need to continue the ingest workflow, this will be set.
 */
public record FindHl7LogFileOutput(List<String> logFiles, ContinueIngestWorkflow continued) {}
