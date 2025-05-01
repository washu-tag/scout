package edu.washu.tag.model;

public class IngestJobInput {

    private String deltaLakePath = "s3://lake/delta";
    private String hl7OutputPath = "s3://lake/hl7";
    private String scratchSpaceRootPath = "s3://lake/scratch";
    private String logsRootPath = "/data/hl7";

    public String getDeltaLakePath() {
        return deltaLakePath;
    }

    public IngestJobInput setDeltaLakePath(String deltaLakePath) {
        this.deltaLakePath = deltaLakePath;
        return this;
    }

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

}
