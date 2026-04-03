package edu.washu.tag.model;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

@JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
public class ReportPatientMappingEntry {

    private String scoutPatientId;
    private String primaryReportIdentifier;
    private String mpi;
    private String epicMrn;
    private boolean consistent;

    public String getScoutPatientId() {
        return scoutPatientId;
    }

    public void setScoutPatientId(String scoutPatientId) {
        this.scoutPatientId = scoutPatientId;
    }

    public String getPrimaryReportIdentifier() {
        return primaryReportIdentifier;
    }

    public void setPrimaryReportIdentifier(String primaryReportIdentifier) {
        this.primaryReportIdentifier = primaryReportIdentifier;
    }

    public String getMpi() {
        return mpi;
    }

    public void setMpi(String mpi) {
        this.mpi = mpi;
    }

    public String getEpicMrn() {
        return epicMrn;
    }

    public void setEpicMrn(String epicMrn) {
        this.epicMrn = epicMrn;
    }

    public boolean isConsistent() {
        return consistent;
    }

    public void setConsistent(boolean consistent) {
        this.consistent = consistent;
    }

}
