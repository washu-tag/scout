package edu.washu.tag.extractor.hl7log.model;

/**
 * Status of the view refresh entity workflow.
 */
public record ViewRefreshStatus(
    boolean isRefreshing,
    boolean hasPendingRefresh,
    int totalRefreshes
) {}
