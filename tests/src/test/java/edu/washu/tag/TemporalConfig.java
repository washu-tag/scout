package edu.washu.tag;

import edu.washu.tag.model.IngestJobInput;

public class TemporalConfig {

    private IngestJobInput ingestJobInput = new IngestJobInput();
    private String temporalUrl = "temporal-frontend.temporal.svc:7233";

    public IngestJobInput getIngestJobInput() {
        return ingestJobInput;
    }

    public TemporalConfig setIngestJobInput(IngestJobInput ingestJobInput) {
        this.ingestJobInput = ingestJobInput;
        return this;
    }

    public String getTemporalUrl() {
        return temporalUrl;
    }

    public TemporalConfig setTemporalUrl(String temporalUrl) {
        this.temporalUrl = temporalUrl;
        return this;
    }

}
