# Scout + XNAT DICOM Integration: Architecture Analysis

**Date**: April 2026  
**Status**: Research & POC Planning  
**Audience**: Scout & XNAT teams exploring integration opportunities

## Executive Summary

Scout currently processes HL7 radiology reports (2–20 KB/exam). Adding DICOM image storage increases data volume by 5,000–100,000x (50 MB to 2 GB per exam). This document analyzes five architectural approaches to integrating Scout with XNAT for DICOM ingestion and analytics.

**Recommendation**: For a 2-week POC, build a **Scout-native pipeline** (Option C) as proof-of-concept, designed with **API contract architecture** (Option E) as the target for production. This maximizes POC velocity while keeping the long-term architectural door open.

---

## Context: Current Scout Architecture

Scout's data pipeline:
- **Input**: HL7 log files (TCP listener on host, mounted in extractor pods)
- **Orchestration**: Temporal workflows coordinate activities
- **Extraction**: Java worker (`hl7log-extractor`) splits logs, uploads to MinIO (bronze layer)
- **Transformation**: Python worker (`hl7-transformer`) parses HL7, writes to Delta Lake (silver layer)
- **Storage**: MinIO for objects, Delta Lake for metadata, PostgreSQL for metadata catalog
- **Query**: Trino + Superset for SQL analytics, JupyterHub for programmatic access

**Current HL7 Schema includes DICOM-relevant fields**:
- `study_instance_uid` (from ZDS segment or OBX)
- `orc_2_placer_order_number`, `orc_3_filler_order_number` (order identifiers / accession numbers)
- `patient_ids`, `epic_mrn` (patient identity)
- `modality` (from OBR service identifier)

These fields enable direct joins between HL7 reports and DICOM studies.

**DICOM Baseline Metrics**:
- Chest X-ray: 20–60 MB uncompressed (1–3 images)
- CT scan: 150–500 MB uncompressed (300–1,000 slices)
- MRI: 20–250 MB uncompressed (150–500 slices)
- Cardiac/angio studies: 500 MB–2 GB uncompressed
- **100K exams/year**: ~2–5 TB compressed, ~5–10 TB uncompressed

**Scout Already Has**:
- Orthanc role (port 4242, 100Gi default storage) with DICOMweb enabled
- dcm4chee role (port 11112, multiple storage volumes)
- MinIO for object storage (with S3 API)
- Helm charts for easy K8s deployment

---

## Background: DICOM Basics

### Networking Protocols

**Traditional DIMSE (Dicom Message Service Elements)**:
- Binary protocol over TCP (DICOM port 104, customizable)
- Three-party model for query/retrieve (issuer, source, target)
- **C-ECHO**: Verify connectivity (ping)
- **C-FIND**: Query for studies/series/instances (returns matching results)
- **C-MOVE**: Retrieve studies (source pushes via C-STORE to target)
- **C-GET**: Retrieve studies (data returns on same connection, rarely implemented)

Every DICOM node needs:
- IP address, port, Application Entity Title (AET, 16-char name)
- Upstream PACS must know target AET to send C-MOVE data

**DICOMweb (RESTful, modern)**:
- HTTP/HTTPS instead of custom binary protocol
- **QIDO-RS**: Query (`GET /studies?PatientName=...`)
- **WADO-RS**: Retrieve (`GET /studies/{study_uid}/instances`)
- **STOW-RS**: Store (`POST /studies` with DICOM files)
- Benefits: standard HTTP load balancing, OAuth2 security, browsers, CDN-friendly, simpler firewall rules

Both Orthanc and dcm4chee support DICOMweb natively.

### Metadata Correlation

HL7 radiology reports and DICOM studies are linked by:

| Field | HL7 Location | DICOM Tag | Purpose |
|-------|-------------|-----------|---------|
| **Accession Number** | OBR-18 or OBR-3 | (0008,0050) | **Primary link** — the order ID |
| **Study Instance UID** | ZDS segment or OBX | (0020,000D) | Globally unique study ID |
| **Patient ID** | PID-3 | (0010,0020) | Patient MRN |
| **Placer Order #** | ORC-2 / OBR-2 | (0040,2016) | EHR order ID |
| **Filler Order #** | ORC-3 / OBR-3 | (0040,2017) | RIS fulfillment ID |

**Scout already captures** placer/filler order numbers and study UID in its `reports` table, enabling direct joins.

### DICOM Storage Patterns

**Filesystem**: Traditional `/storage/{PatientID}/{StudyUID}/{SeriesUID}/{image}.dcm`  
**Object Storage** (S3/MinIO): Flat layout with UUID keys, or hierarchical layouts
- Orthanc S3 plugin: stores at bucket root (relying on database index for lookup)
- Cloud-native approach: organize by `{StudyUID}/{SeriesUID}/{SOPInstanceUID}.dcm`

**Key advantage of MinIO**: Eliminates filesystem inode limits, scales to petabytes without directory traversal bottlenecks.

---

## Integration Architecture Options

### Option A: XNAT as Embedded DICOM Subsystem

**Concept**: Deploy XNAT within Scout's K8s cluster. Scout's Temporal workflows orchestrate XNAT's dicom-query-retrieve plugin to pull from upstream PACS. Scout reads XNAT's archive filesystem (via NFS/PVC mount) and PostgreSQL database directly.

```
Temporal Workflow ──→ XNAT REST API (trigger Q/R)
                           ↓
                      PACS (C-FIND + C-MOVE)
                           ↓
                      XNAT Archive (standard hierarchy)
                           ↓
                      Scout reads: /archive/{proj}/{subj}/{exp}/DICOM/
                           ↓
                      pydicom → Delta Lake
```

**POC Strengths**:
- Full integration demo: trigger retrieval → store → analyze
- XNAT's web UI alongside Scout analytics (compelling visual)
- XNAT's data organization (project/subject/experiment) provides structure

**POC Weaknesses**:
- Complex deployment (Tomcat, PostgreSQL, optional LDAP)
- XNAT K8s adoption is recent; Docker Compose is native
- Requires real PACS or simulator for Q/R demo
- Debugging XNAT requires XNAT expertise

**Production Strengths**:
- Leverages XNAT's mature DICOM handling, anonymization, provenance, and plugin ecosystem
- Community proven at research scale
- XNAT container service enables ML pipeline on stored images

**Production Weaknesses**:
- **XNAT designed for research, not clinical ops** — project/subject/experiment don't map to clinical order → study → series workflow
- Archive filesystem is XNAT-internal; coupling Scout to it is fragile
- Direct filesystem/database access bypasses XNAT's access controls
- Vertical scaling only; concurrent Q/R load can become bottleneck
- Upgrading XNAT could break Scout's filesystem assumptions
- Operating two complex systems (Scout + XNAT) with tight coupling

**Political**: Best for XNAT community benefit IF architecture works out. Risk: frustration if XNAT's design doesn't accommodate Scout's patterns.

---

### Option B: XNAT as Retrieval Waypoint

**Concept**: XNAT pulls DICOM from PACS (via Q/R), Scout downloads instances via XNAT's REST API or DICOMweb, stores in MinIO, then extracts metadata. XNAT is a transient cache or can be pruned.

```
Temporal Workflow ──→ XNAT REST API (trigger Q/R)
                           ↓
                      PACS (C-MOVE) → XNAT (temporary)
                           ↓
Temporal Activity ──→ XNAT REST API (download files)
                           ↓
                      MinIO (persistent store)
                           ↓
                      pydicom → Delta Lake
```

**POC Strengths**:
- Clear separation of concerns
- Natural checkpoint: "Q/R works" → "extraction works" → "Delta Lake works"
- Keeps XNAT in the flow

**POC Weaknesses**:
- More code: extraction + upload logic on top of Option A
- Download via REST + re-upload adds latency

**Production Strengths**:
- Scout owns data layout (no coupling to XNAT internals)
- XNAT can be scaled/replaced independently
- S3 lifecycle policies, backups via standard Scout mechanisms

**Production Weaknesses**:
- Double storage during retrieval (XNAT + MinIO) unless aggressively pruned
- REST download is slower than filesystem access for bulk
- Requires XNAT just as a proxy — may not justify operational complexity
- XNAT gets used but in diminished role (less community benefit)

---

### Option C: Scout-Native DICOM Pipeline

**Concept**: Scout builds its own DICOM pipeline using open-source tooling. Orthanc (already in codebase) handles DICOM networking via DICOMweb or DIMSE. pydicom extracts metadata. MinIO stores images. No XNAT required in critical path.

```
Temporal Workflow ──→ Orthanc REST API (configure C-MOVE)
                           ↓
                      PACS ──(C-MOVE)──→ Orthanc (C-STORE SCP)
                                              ↓
                                    Orthanc S3 plugin
                                              ↓
                                    MinIO (s3://lake/dicom/...)
                                              ↓
Temporal Activity ──→ pydicom (read from MinIO)
                           ↓
                      Extract metadata → Delta Lake
```

**Alternative: Pure-Python variant**
```
Temporal Activity ──→ pynetdicom (C-FIND + C-MOVE SCU)
                           ↓
                      PACS ──(C-MOVE)──→ custom C-STORE SCP handler
                                              ↓
                                    Upload to MinIO + extract metadata
```

**POC Strengths**:
- **Fastest path to working demo** — Orthanc already in codebase with Helm chart
- Orthanc S3 plugin → images go straight to MinIO
- Single lightweight container (C++ binary, ~100 MB)
- Can start with just test data pushed into Orthanc (no PACS needed)
- Follows Scout's established patterns exactly

**POC Weaknesses**:
- Doesn't demonstrate XNAT integration (misses political/community goal)
- DICOM Q/R requires configuring PACS to know Orthanc's AET (possible friction)

**Production Strengths**:
- **Minimal dependencies**, maximum control
- Orthanc well-maintained, lightweight
- Horizontal scaling: multiple Orthanc instances behind load balancer
- Data layout fully under Scout control
- No XNAT upgrade concerns
- Serves as DICOMweb endpoint for viewers (OHIF)

**Production Weaknesses**:
- You build what XNAT/dcm4chee provide: archive management, viewer, anonymization, provenance, access control
- No organizational layer (project/subject/experiment) — just files in buckets
- DICOM networking edge cases (association negotiation, transfer syntax, partial transfers, vendor quirks) are real
- Zero XNAT community benefit

---

### Option D: Orthanc as DICOM Facade with XNAT Integration

**Concept**: Hybrid approach. Orthanc handles real-time DICOM networking (better suited to K8s). After ingestion to MinIO + Delta Lake, selected studies are forwarded to XNAT via REST API or C-STORE for organizational/browsing purposes. Or, in existing XNAT environments, Scout can pull from XNAT's DICOMweb instead.

```
Primary path (Orthanc → MinIO → Delta Lake):
    Temporal ──→ Orthanc ──→ PACS ──(C-MOVE)──→ MinIO ──→ Delta Lake
                                                  ↓
                                            pydicom extract

Alternative source (XNAT DICOMweb):
    Temporal ──→ XNAT DICOMweb (QIDO-RS, WADO-RS) ──→ MinIO ──→ Delta Lake
```

**Strengths**: Best of both worlds — Scout has a clean, fast pipeline; XNAT is integrated as an organizational layer. Sites with existing XNAT get easy on-ramp to Scout.

**Weaknesses**: More complex overall. XNAT integration is secondary (less community benefit). Two DICOM stores create data management complexity.

---

### Option E: XNAT as Archive, Scout as Analytics Engine (API Contract)

**Concept**: Clean architectural separation. XNAT is the authoritative archive and organizational hub for DICOM. Scout consumes XNAT's data via DICOMweb/REST API and produces analytics. XNAT can display Scout analytics via plugins.

```
XNAT (manages all DICOM: Q/R, storage, org, viewing)
  ↕ (DICOMweb + REST API — clean contract)
Scout (consumes metadata → Delta Lake, runs analytics)
  ↕ (Trino / BI tools)
XNAT plugins (display Scout analytics within XNAT UI)
```

**Implementation**:
- Temporal workflow queries XNAT periodically (REST API or DICOMweb QIDO-RS)
- Scout extracts metadata without downloading pixel data
- For pixel access (AI inference, thumbnails), WADO-RS retrieves specific instances on-demand
- Scout's Delta Lake table joins DICOM metadata with HL7 reports
- Analytics dashboards combine both data types

**POC Strengths**:
- Clean separation of concerns
- If an XNAT instance exists, integrate immediately without deploying one
- DICOMweb is HTTP — no custom DIMSE networking to debug
- Focus on the interesting part: metadata extraction + correlation + analytics
- Can run without PACS (use XNAT's test data)

**POC Weaknesses**:
- Requires running XNAT instance (more setup) unless one already exists
- DICOMweb support in XNAT may have gaps
- Less visually dramatic than full orchestration

**Production Strengths**:
- **Best alignment with XNAT community goals** — XNAT is first-class, not subordinate
- Clean boundary enables independent scaling, upgrades, operations
- Institutions with XNAT get Scout "for free" on their data
- Institutions without XNAT use Option C — Scout supports both paths
- **Drives XNAT improvements**: event webhooks, streaming change notifications, bulk metadata export APIs, better DICOMweb
- Positioning Scout as consuming platform motivates XNAT development

**Production Weaknesses**:
- Network latency between Scout and XNAT for every access
- Scout doesn't control Q/R pipeline (depends on XNAT or alternative)
- Scales with XNAT's performance limits (XNAT's DICOMweb/REST may have ceiling)
- Two systems to operate; Scout depends on XNAT availability
- XNAT lacks robust event/webhook support today (polling required)

---

## Detailed Comparison

| Criterion | A: XNAT Inside | B: XNAT Waypoint | C: Scout-Native | D: Orthanc+XNAT | E: API Contract |
|-----------|:-:|:-:|:-:|:-:|:-:|
| **POC speed to running demo** | Slow (3-4 wks) | Slow (3-4 wks) | **Fast (3-5 days)** | Medium (1-2 wks) | Medium (1-2 wks)* |
| **POC demo impact** | High | Medium | Medium | Medium | Medium |
| **Deployment complexity** | Very High | Very High | **Low** | Medium | Low-Medium |
| **Production scalability** | Moderate | Good | **Excellent** | Good | Good |
| **Data ownership / control** | Mixed (tight coupling) | Good | **Full** | Full | Partial |
| **XNAT community benefit** | High | Low | None | Low | **High** |
| **Drives XNAT improvements** | Moderate | Low | None | Low | **High** |
| **Operational burden** | Very High | Very High | **Low** | Medium | Medium |
| **Leverages existing Scout patterns** | Partial | Partial | **Full** | Full | Partial |
| **HL7-DICOM correlation** | Good | Good | Good | Good | Good |
| **Architectural cleanliness** | Messy | Acceptable | **Clean** | Acceptable | **Clean** |
| **Independence from XNAT** | Very Low | Low | **High** | High | Moderate |

*\* If XNAT instance already available; much slower if deploying XNAT*

---

## Recommended Approach: 2-Week POC

**Recommendation**: **Option C (Scout-Native) as immediate POC, designed with Option E (API Contract) as the target production architecture.**

### Why This Path

1. **Orthanc is ready now** — already in codebase, Helm chart exists, DICOMweb enabled
2. **Delta Lake schema is the core artifact** — works with any DICOM source
3. **Temporal workflow is reusable** — swapping input sources (Orthanc → XNAT DICOMweb) doesn't require redesign
4. **Separates concerns** — prove DICOM-to-Delta-Lake works independently from XNAT question
5. **Option E is architecturally superior** but requires XNAT features (events, bulk metadata APIs) that may not exist yet — the POC can generate concrete requirements

### Suggested 2-Week Scope

**Week 1: Core Pipeline**
- Define Delta Lake schema for DICOM metadata:
  - `dicom_studies` (Study Instance UID, accession number, patient metadata, study date, modality, etc.)
  - `dicom_series` (Series Instance UID, description, modality, series date, etc.)
  - `dicom_instances` (SOP Instance UID, file path, file size, etc.)
- Write Python Temporal activity:
  - Input: DICOM files in MinIO or filesystem
  - Logic: read with pydicom, extract key tags
  - Output: write to Delta Lake
- Deploy Orthanc (use existing Ansible role + Helm chart)
- Send test DICOM data into Orthanc (via test datasets or DCMTK tools like `storescu`)
- Create Temporal workflow orchestrating extraction → Delta Lake
- **Checkpoint**: Trino query joins `reports` and `dicom_studies` on `study_instance_uid` and accession number

**Week 2: Integration Exploration**
- Configure Orthanc S3 plugin so received DICOM goes directly to MinIO
- Optional: experiment with XNAT DICOMweb as alternative data source (if XNAT instance available)
- Build a simple Q/R trigger (Orthanc REST API or pynetdicom) against test PACS or simulator
- Create Superset dashboard showing combined HL7 + DICOM analytics (e.g., "reports with images by modality over time")
- Document findings: "What would we need from XNAT for Option E to work?"

### Deliverables

- Working Temporal workflow + activities for DICOM ingestion
- Delta Lake tables with DICOM metadata
- Trino/Superset dashboard combining HL7 reports with DICOM study metadata
- Architecture decision document: "Why we chose Option X for production, what XNAT work would unlock Option E"
- Prototype code (ready for refactoring into production pipeline or integration with XNAT)

---

## Key Technical Details

### Delta Lake Schema Sketch

```python
# dicom_studies table
DicomStudy = {
    "study_instance_uid": "1.2.3...",  # Primary key, join to reports.study_instance_uid
    "accession_number": "ACC123456",   # Secondary join key (reports.orc_3_filler_order_number)
    "patient_id": "MRN123",             # Join to reports.epic_mrn
    "patient_name": "DOE^JOHN",
    "patient_dob": "1960-01-15",
    "patient_sex": "M",
    "study_date": "2026-04-05",
    "study_time": "143025",
    "study_description": "CHEST PA AND LATERAL",
    "referring_physician": "DR SMITH",
    "institution_name": "HOSPITAL A",
    "modalities": ["CR"],  # Array of modalities in study
    "num_series": 1,
    "num_instances": 2,
    "updated": "2026-04-09T12:30:00Z",
    "year": 2026  # Partition
}

# dicom_series table
DicomSeries = {
    "series_instance_uid": "1.2.3.4...",
    "study_instance_uid": "1.2.3...",   # FK to dicom_studies
    "modality": "CR",
    "series_description": "CHEST PA",
    "series_date": "2026-04-05",
    "series_number": 1,
    "body_part_examined": "CHEST",
    "num_instances": 2,
    "updated": "2026-04-09T12:30:00Z",
    "year": 2026
}

# dicom_instances table
DicomInstance = {
    "sop_instance_uid": "1.2.3.4.5...",
    "series_instance_uid": "1.2.3.4...",  # FK to dicom_series
    "study_instance_uid": "1.2.3...",     # FK to dicom_studies
    "sop_class_uid": "1.2.840.10008.5.1.4.1.1.2",  # CR Image Storage
    "instance_number": 1,
    "file_path": "s3://lake/dicom/1.2.3.../1.2.3.4.../1.2.3.4.5....dcm",
    "file_size_bytes": 2048576,
    "rows": 2048,
    "columns": 2560,
    "bits_allocated": 16,
    "manufacturer": "GE",
    "manufacturer_model": "DISCOVERY XR220",
    "updated": "2026-04-09T12:30:00Z",
    "year": 2026
}
```

### Temporal Workflow Skeleton

```
IngestDicomWorkflow(input: {
  studyInstanceUids?: [string],     // Optional filter
  accessionNumbers?: [string],       // Optional filter
  dateRange?: {from: string, to: string},
  minioSourcePath: string,           // s3://lake/dicom or similar
  deltaLakeNamespace: string         // e.g., "default"
})
  ├─→ Activity: FindDicomFilesActivity (list .dcm files from MinIO)
  ├─→ Activity: ExtractDicomMetadataActivity (parallel, by file)
  │    ├─→ Read .dcm file from MinIO
  │    ├─→ Extract tags with pydicom
  │    └─→ Return structured metadata
  └─→ Activity: WriteDeltaLakeActivity
       ├─→ Write dicom_studies table
       ├─→ Write dicom_series table
       └─→ Write dicom_instances table
```

### Pydicom Extract Example

```python
from pydicom import dcmread
from io import BytesIO

# Read from S3
response = s3.get_object(Bucket='lake', Key='dicom/.../image.dcm')
dcm = dcmread(BytesIO(response['Body'].read()))

# Extract study-level metadata
study_metadata = {
    "study_instance_uid": dcm.StudyInstanceUID,
    "accession_number": dcm.AccessionNumber,
    "patient_id": dcm.PatientID,
    "patient_name": str(dcm.PatientName),
    "patient_dob": dcm.PatientBirthDate,
    "patient_sex": dcm.PatientSex,
    "study_date": dcm.StudyDate,
    "study_description": dcm.StudyDescription,
    "referring_physician": str(dcm.ReferringPhysicianName),
}

# Series-level
series_metadata = {
    "series_instance_uid": dcm.SeriesInstanceUID,
    "modality": dcm.Modality,
    "series_description": dcm.SeriesDescription,
    "series_number": int(dcm.SeriesNumber),
}

# Instance-level
instance_metadata = {
    "sop_instance_uid": dcm.SOPInstanceUID,
    "sop_class_uid": dcm.SOPClassUID,
    "instance_number": int(dcm.InstanceNumber),
    "rows": int(dcm.Rows),
    "columns": int(dcm.Columns),
}
```

---

## XNAT Specifics (If Pursuing Option A, B, or E)

### XNAT Architecture Basics

**Data Hierarchy**:
```
Project (research study, e.g., "Chest_Imaging_Study")
  └─ Subject (participant, e.g., "SUBJ001")
      └─ Experiment (session/visit, e.g., "SUBJ001_01")
          └─ Scan (acquisition, e.g., "SCAN001" = one series)
              └─ Resource (file collection, e.g., "DICOM", "NIFTI")
                  └─ File (e.g., "image.dcm")
```

**Filesystem Archive**:
```
{xnat_archive}/
  {ProjectID}/
    {SubjectID}/
      {ExperimentID}/
        {ScanID}/
          DICOM/
            image1.dcm
            image2.dcm
```

**REST API** (comprehensive):
- `GET /projects/{projectID}` — project metadata
- `POST /projects/{projectID}/subjects` — create subject
- `PUT /projects/{projectID}/subjects/{subjectID}/experiments/{experimentID}` — create/update experiment
- `POST /projects/{projectID}/subjects/{subjectID}/experiments/{experimentID}/scans/{scanID}/resources/DICOM/files` — upload DICOM
- `GET /projects/{projectID}/subjects/{subjectID}/experiments/{experimentID}/scans/{scanID}/resources/DICOM/files` — list files
- `GET /projects/{projectID}/subjects/{subjectID}/experiments/{experimentID}/scans/{scanID}/resources/DICOM/files/{filename}` — download file

**Dicom Query/Retrieve Plugin**:
- Configurable C-FIND and C-MOVE against upstream PACS
- Triggered via REST API or web UI
- Retrieved studies stored in XNAT archive (organized under project/subject/experiment)

**Event Service**:
- Webhooks/event notifications (recent addition, not all versions have it)
- Can trigger actions when data arrives

### Option E Enabler: XNAT Improvements Needed

If pursuing Option E (API Contract) in production, these XNAT features would unlock major benefits:

1. **Streaming Change Notifications** (webhooks or gRPC streams)
   - Scout could react in real-time to new studies vs. polling periodically
   - Example: `POST https://scout/webhook` with `{event: "study_received", study_uid: "..."}`

2. **Bulk Metadata Export API**
   - DICOMweb QIDO-RS is good but incomplete (XNAT-specific metadata like project/subject/experiment not exposed)
   - RESTful metadata export: `GET /metadata/studies?dateRange=...` returning JSON with all tags at once

3. **Batch Operations**
   - Bulk C-FIND by accession number list
   - Parallel multi-study retrieval

4. **DICOMweb Completeness**
   - Full QIDO-RS support with filters (accession number, modality, etc.)
   - WADO-RS for bulk instance retrieval
   - Metadata in standard DICOM tag format (not XNAT-specific)

These are achievable improvements that would benefit the whole XNAT community, not just Scout.

---

## Decision Framework

Choose based on these factors:

**If primary goal is "Scout processes DICOM as fast as possible"**:
→ **Option C (Scout-Native)**. Done in 1 week, minimal dependencies, maximum control.

**If primary goal is "XNAT gets a major new use case and improvements"**:
→ **Option E (API Contract)**. Requires XNAT work upfront but creates lasting value for community. Position Scout as the killer app for XNAT in analytics/research workflows.

**If primary goal is "Prove XNAT + Scout can work together"**:
→ **Option A (XNAT Inside)** for POC (hard), then decide on production architecture based on pain points.

**If primary goal is "Integrate with existing XNAT deployments"**:
→ **Option E or Option D**. Existing XNAT can be a data source without modification.

**If resources are highly constrained**:
→ **Option C (Scout-Native)**. Lowest operational burden, smallest surface area.

---

## Next Steps

1. **Decide**: Which option(s) to pursue for POC?
2. **Clarify political goals**: What does success look like for XNAT relationship?
3. **Assess resources**: Is XNAT expertise available on team? Can we deploy XNAT in K8s?
4. **Prototype**: If Option C, start with Orthanc + test data in 3 days
5. **Evaluate**: After POC, use actual DICOM data + analytics results to inform production architecture choice

---

## References

### DICOM Standards & Protocols
- [DICOM Part 7: Message Exchange](https://dicom.nema.org/medical/dicom/current/output/chtml/part07/sect_9.3.html)
- [DICOM Part 18: Web Services](https://dicom.nema.org/medical/dicom/current/output/chtml/part18/)
- [DICOMweb Standard Spec](https://www.dicomstandard.org/using/dicomweb)

### Open-Source Tools
- [Orthanc Documentation](https://orthanc.uclouvain.be/book/)
- [Orthanc Cloud Object Storage Plugin](https://orthanc.uclouvain.be/book/plugins/object-storage.html)
- [pydicom: Python DICOM Library](https://pydicom.readthedocs.io/)
- [pynetdicom: DICOM Networking](https://pynetdicom.readthedocs.io/)
- [dcm4chee Architecture](https://github.com/dcm4che/dcm4chee-arc-light)
- [DCMTK: Toolkit & Command-Line Tools](https://dicom.offis.de/dcmtk.php.en)

### XNAT
- [XNAT Documentation](https://wiki.xnat.org/)
- [XNAT REST API](https://wiki.xnat.org/display/XNAT/REST+API)
- [XNAT Dicom Query/Retrieve Plugin](https://wiki.xnat.org/display/XNAT/Dicom+Query%2FRetrieve)
- [XNAT in Kubernetes](https://wiki.xnat.org/display/XNAT/Deploying+XNAT+in+Kubernetes)

### Related Integration Patterns
- [IHE Scheduled Workflow (HL7 + DICOM)](https://wiki.ihe.net/index.php/Scheduled_Workflow)
- [dcm4chee HL7 Conformance](https://dcm4chee-arc-hl7cs.readthedocs.io/)
- [AWS HealthImaging: Cloud-Scale DICOM](https://aws.amazon.com/solutions/guidance/receiving-digital-imaging-and-communications-in-medicine-images-in-amazon-s3/)
- [Cloud-Native DICOM on OCI](https://docs.oracle.com/en/solutions/cloud-native-dicom-on-oci/)

