package edu.washu.tag;

public class TemporalConfig {

    private String temporalUrl = "temporal-frontend.temporal.svc:7233";

    public String getTemporalUrl() {
        return temporalUrl;
    }

    public TemporalConfig setTemporalUrl(String temporalUrl) {
        this.temporalUrl = temporalUrl;
        return this;
    }

}
