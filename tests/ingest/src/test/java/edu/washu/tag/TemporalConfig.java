package edu.washu.tag;

public class TemporalConfig {

    // Internal frontend: skips the public frontend's JWT authz, like the workers.
    private String temporalUrl = "temporal-internal-frontend.scout-extractor.svc:7236";

    public String getTemporalUrl() {
        return temporalUrl;
    }

    public TemporalConfig setTemporalUrl(String temporalUrl) {
        this.temporalUrl = temporalUrl;
        return this;
    }

}
