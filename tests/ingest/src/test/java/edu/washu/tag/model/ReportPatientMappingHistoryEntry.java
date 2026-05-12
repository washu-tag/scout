package edu.washu.tag.model;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class ReportPatientMappingHistoryEntry extends ReportPatientMappingEntry {

    private String previousScoutId;

    public String getPreviousScoutId() {
        return previousScoutId;
    }

    public void setPreviousScoutId(String previousScoutId) {
        this.previousScoutId = previousScoutId;
    }
    
}
