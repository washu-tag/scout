package edu.washu.tag.extractor.hl7log.model;

/**
 * Derive Delta Lake tables activity input.
 *
 * <p>Input for the derivative half of the ingest pipeline (issue #457): derives the
 * curated/latest/dx/mapping tables (and the epic views) from the base report table's
 * committed change data feed.
 *
 * @param reportTableName Name of the base report table to derive from.
 * @param createMapping Whether to derive the report-patient mapping table and the epic views that depend on it.
 *                      Null is treated as true.
 */
public record DeriveDeltaTablesActivityInput(
    String reportTableName,
    Boolean createMapping
) {}
