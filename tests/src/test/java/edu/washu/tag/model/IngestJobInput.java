package edu.washu.tag.model;

public class IngestJobInput {

    private String hl7OutputPath = null;
    private String scratchSpaceRootPath = null;
    private String logsRootPath = "/data/hl7";
    private String reportTableName = "reports";

    public String getHl7OutputPath() {
        return hl7OutputPath;
    }

    public IngestJobInput setHl7OutputPath(String hl7OutputPath) {
        this.hl7OutputPath = hl7OutputPath;
        return this;
    }

    public String getScratchSpaceRootPath() {
        return scratchSpaceRootPath;
    }

    public IngestJobInput setScratchSpaceRootPath(String scratchSpaceRootPath) {
        this.scratchSpaceRootPath = scratchSpaceRootPath;
        return this;
    }

    public String getLogsRootPath() {
        return logsRootPath;
    }

    public IngestJobInput setLogsRootPath(String logsRootPath) {
        this.logsRootPath = logsRootPath;
        return this;
    }

    public String getReportTableName() {
        return reportTableName;
    }

    public IngestJobInput setReportTableName(String reportTableName) {
        this.reportTableName = reportTableName;
        return this;
    }
}
