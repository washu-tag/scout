package edu.washu.tag.model;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class ReportPatientMappingHistoryEntry extends ReportPatientMappingEntry {

    private String previousScoutPatientId;

    public String getPreviousScoutPatientId() {
        return previousScoutPatientId;
    }

    public void setPreviousScoutPatientId(String previousScoutPatientId) {
        this.previousScoutPatientId = previousScoutPatientId;
    }
    
}
