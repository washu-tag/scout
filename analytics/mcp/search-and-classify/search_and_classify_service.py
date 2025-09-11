# search_and_classify_service.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import torch
from transformers import pipeline
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import trino
from enum import Enum
import re
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Radiology Report Diagnosis Search",
    description="Execute SQL queries and classify radiology reports with AI-powered diagnosis detection",
    version="1.1.0"
)

# Enable CORS for Open WebUI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ENFORCED_MAX_ROWS = os.getenv("ENFORCED_MAX_ROWS", "5000")

# ---------- Reference schema (documentation only) ----------
SCHEMA_DOC = """
Expected columns available in delta.default.reports (subset shown):
- epic_mrn (STRING)
- obr_3_filler_order_number (STRING)
- modality (STRING)
- service_name (STRING)
- observation_dt (TIMESTAMP)
- message_dt (TIMESTAMP)
- requested_dt (TIMESTAMP)
- report_text (STRING)
- report_status (STRING)
- sending_facility (STRING)
- sex (STRING)
- race (STRING)
- birth_date (DATE or TIMESTAMP)

Event time is typically: COALESCE(observation_dt, message_dt, requested_dt).
Age example:
  date_diff(
    'year',
    CAST(birth_date AS date),
    CAST(COALESCE(observation_dt, message_dt, requested_dt) AS date)
  )
"""

# ---------- Example SQL templates (Trino-safe, dedup via max_by) ----------
SQL_EXAMPLE_DEDUP_NEOPLASM_LAST_YEAR = rf"""
-- Latest per accession with neoplasm keywords, last 1 year, final reports only
SELECT
  latest.epic_mrn AS mrn,
  t.obr_3_filler_order_number AS accession_number,
  latest.modality AS modality,
  latest.service_name AS service_name,
  CAST(COALESCE(latest.observation_dt, latest.message_dt, latest.requested_dt) AS VARCHAR) AS event_date,
  -- Include age (years) as an optional column if desired
  date_diff(
    'year',
    CAST(latest.birth_date AS date),
    CAST(COALESCE(latest.observation_dt, latest.message_dt, latest.requested_dt) AS date)
  ) AS age,
  latest.report_text AS report_text,
  latest.sending_facility AS sending_facility,
  latest.sex AS sex,
  latest.race AS race
FROM (
  SELECT
    r.obr_3_filler_order_number,
    max_by(
      CAST(ROW(
        r.epic_mrn,
        r.modality,
        r.service_name,
        r.observation_dt,
        r.message_dt,
        r.requested_dt,
        r.report_text,
        r.sending_facility,
        r.sex,
        r.race,
        r.birth_date
      ) AS ROW(
        epic_mrn VARCHAR,
        modality VARCHAR,
        service_name VARCHAR,
        observation_dt TIMESTAMP,
        message_dt TIMESTAMP,
        requested_dt TIMESTAMP,
        report_text VARCHAR,
        sending_facility VARCHAR,
        sex VARCHAR,
        race VARCHAR,
        birth_date DATE
      )),
      COALESCE(r.message_dt, r.observation_dt, r.requested_dt)
    ) AS latest
  FROM delta.default.reports r
  WHERE r.report_text IS NOT NULL
    AND COALESCE(r.observation_dt, r.message_dt, r.requested_dt) >= current_timestamp - INTERVAL '1' YEAR
    AND regexp_like(r.report_text, '(?i)\\b(neoplasm|malign\\w*|cancer|tumou?r|lesion\\w*|mass(?:\\b|\\s))')
    AND lower(r.report_status) LIKE 'f%'   -- final
  GROUP BY r.obr_3_filler_order_number
) t
ORDER BY COALESCE(t.latest.message_dt, t.latest.observation_dt, t.latest.requested_dt) DESC
LIMIT {ENFORCED_MAX_ROWS};
"""

SQL_EXAMPLE_DEDUP_CT_PE_LAST_MONTH = rf"""
-- CT reads on white women >=40, last 1 month, PE keywords, final reports only
SELECT
  latest.epic_mrn AS mrn,
  t.obr_3_filler_order_number AS accession_number,
  latest.modality AS modality,
  latest.service_name AS service_name,
  CAST(COALESCE(latest.observation_dt, latest.message_dt, latest.requested_dt) AS VARCHAR) AS event_date,
  date_diff(
    'year',
    CAST(latest.birth_date AS date),
    CAST(COALESCE(latest.observation_dt, latest.message_dt, latest.requested_dt) AS date)
  ) AS age,
  latest.report_text AS report_text,
  latest.sending_facility AS sending_facility,
  latest.sex AS sex,
  latest.race AS race
FROM (
  SELECT
    r.obr_3_filler_order_number,
    max_by(
      CAST(ROW(
        r.epic_mrn,
        r.modality,
        r.service_name,
        r.observation_dt,
        r.message_dt,
        r.requested_dt,
        r.report_text,
        r.sending_facility,
        r.sex,
        r.race,
        r.birth_date
      ) AS ROW(
        epic_mrn VARCHAR,
        modality VARCHAR,
        service_name VARCHAR,
        observation_dt TIMESTAMP,
        message_dt TIMESTAMP,
        requested_dt TIMESTAMP,
        report_text VARCHAR,
        sending_facility VARCHAR,
        sex VARCHAR,
        race VARCHAR,
        birth_date DATE
      )),
      COALESCE(r.message_dt, r.observation_dt, r.requested_dt)
    ) AS latest
  FROM delta.default.reports r
  WHERE r.report_text IS NOT NULL
    AND COALESCE(r.observation_dt, r.message_dt, r.requested_dt) >= current_timestamp - INTERVAL '1' MONTH
    AND upper(r.modality) = 'CT'
    AND upper(r.sex) IN ('F', 'FEMALE')
    AND regexp_like(r.race, '(?i)^white')
    AND date_diff(
          'year',
          CAST(r.birth_date AS date),
          CAST(COALESCE(r.observation_dt, r.message_dt, r.requested_dt) AS date)
        ) >= 40
    AND regexp_like(r.report_text, '(?i)\\b(pulmonary\\s+embol(?:ism)?|\\bPE\\b|thromboembol\\w*)')
    AND lower(r.report_status) LIKE 'f%'   -- final
  GROUP BY r.obr_3_filler_order_number
) t
ORDER BY COALESCE(t.latest.message_dt, t.latest.observation_dt, t.latest.requested_dt) DESC
LIMIT {ENFORCED_MAX_ROWS};
"""

SQL_EXAMPLES = {
    "neoplasm_last_year": SQL_EXAMPLE_DEDUP_NEOPLASM_LAST_YEAR,
    "ct_white_female_40plus_pe_last_month": SQL_EXAMPLE_DEDUP_CT_PE_LAST_MONTH,
}

# Initialize classifier globally for reuse
classifier = None
executor = ThreadPoolExecutor(max_workers=4)

# Trino connection configuration
TRINO_HOST = os.getenv("TRINO_HOST", "trino.trino")
TRINO_PORT = os.getenv("TRINO_PORT", 8080)
TRINO_USER = os.getenv("TRINO_USER", "scout")
TRINO_CATALOG = os.getenv("TRINO_CATALOG", "delta")
TRINO_SCHEMA = os.getenv("TRINO_SCHEMA", "default")

# Required columns for the query (must appear in SELECT with these aliases)
REQUIRED_COLUMNS = {
    "mrn": "epic_mrn as mrn",
    "accession_number": "obr_3_filler_order_number as accession_number",
    "modality": "modality",
    "service_name": "service_name",
    "event_date": "CAST(COALESCE(observation_dt, message_dt, requested_dt) AS VARCHAR) as event_date",
    "report_text": "report_text",
    "sending_facility": "sending_facility",
    "sex": "sex",
    "race": "race"
    "age": "date_diff('year', CAST(birth_date AS date), CAST(COALESCE(observation_dt, message_dt, requested_dt) AS date)) as age"
}

class ClassificationTarget(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNCERTAIN = "uncertain"
    ALL = "all"
    POSITIVE_AND_UNCERTAIN = "positive_and_uncertain"
    NEGATIVE_AND_UNCERTAIN = "negative_and_uncertain"

class SQLError(Exception):
    """Custom exception for SQL-related errors with helpful messages."""
    pass

def get_classifier():
    """Lazy-load a zero-shot NLI classifier."""
    global classifier
    if classifier is None:
        device = torch.device("cpu")
        try:
            if torch.cuda.is_available():
                device = torch.device("cuda")
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = torch.device("mps")
        except Exception:
            # fall back to CPU if any capability check causes issues
            device = torch.device("cpu")

        # transformers accepts torch.device in recent versions; otherwise it treats non-cuda as CPU
        classifier = pipeline(
            "zero-shot-classification",
            model="facebook/bart-large-mnli",
            device=device
        )
        # keep the tail of long texts
        classifier.tokenizer.truncation_side = "left"
        logger.info(f"Classifier loaded on device: {device}")
    return classifier

def get_trino_connection():
    """Create a Trino connection."""
    return trino.dbapi.connect(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user=TRINO_USER,
        catalog=TRINO_CATALOG,
        schema=TRINO_SCHEMA,
        http_scheme="http",
        session_properties={
            "exchange_compression": "true",
            "filter_pushdown_enabled": "true"
        }
    )

def validate_sql_query(query: str) -> List[str]:
    """
    Validate that the SQL query text contains required column aliases.
    Returns a list of missing column expressions to guide the user.
    """
    q = query.lower()
    missing = []
    for alias, column_expression in REQUIRED_COLUMNS.items():
        alias_name = alias.lower()
        # look for 'as <alias>' anywhere in the SELECT
        if not re.search(rf'\bas\s+{re.escape(alias_name)}\b', q):
            missing.append(column_expression)
    return missing

def parse_sql_error(error: Exception) -> str:
    """Parse SQL errors and return helpful messages."""
    es = str(error)
    el = es.lower()
    if "table" in el and "not found" in el:
        return ("Table not found. Please ensure you're querying 'delta.default.reports'. "
                f"Original error: {es}")
    elif "column" in el and ("not found" in el or "cannot be resolved" in el):
        m = re.search(r"column['\"]?\s*([^'\"\s]+)", es, re.IGNORECASE)
        col = m.group(1) if m else "unknown"
        return (f"Column '{col}' not found. Check available columns in your table such as: "
                "epic_mrn, obr_3_filler_order_number, modality, service_name, "
                "observation_dt, message_dt, requested_dt, report_text, report_status, "
                "sending_facility, sex, race, birth_date. "
                f"Original error: {es}")
    elif "syntax error" in el or "parse" in el:
        return ("SQL syntax error. Check string quoting, date/timestamp formats, "
                "and commas between columns. " + f"Original error: {es}")
    elif "permission" in el or "access denied" in el:
        return (f"Permission denied for user '{TRINO_USER}'. " + f"Original error: {es}")
    elif "timeout" in el or "timed out" in el:
        return ("Query timed out. Narrow the WHERE clause or add a LIMIT. " + f"Original error: {es}")
    elif "memory" in el:
        return ("Query exceeded memory; reduce the result set or LIMIT. " + f"Original error: {es}")
    else:
        return f"Query execution failed: {es}"

def _wrap_with_limit(sql: str, limit: int) -> str:
    """If user forgot a LIMIT, wrap the query to enforce a max row cap."""
    if re.search(r'\blimit\b\s+\d+', sql, re.IGNORECASE):
        return sql
    return f"SELECT * FROM ({sql}) __q__ LIMIT {limit}"

# Pydantic models
class DiagnosisSearchRequest(BaseModel):
    sql_query: str = Field(
        ...,
        description=(
            "SQL query to execute against delta.default.reports.\n\n"
            "REQUIRED columns (aliases in SELECT): "
            + ", ".join(REQUIRED_COLUMNS.values())
            + "\n\n"
            "Dedup + age example (neoplasm, last 1 year):\n"
            + SQL_EXAMPLE_DEDUP_NEOPLASM_LAST_YEAR
            + "\n\n"
            "Dedup + age example (CT, white women >=40, PE, last month):\n"
            + SQL_EXAMPLE_DEDUP_CT_PE_LAST_MONTH
        )
    )
    diagnosis: str = Field(
        ...,
        description="Diagnosis/condition to classify, e.g., 'pulmonary embolism', 'neoplasm', 'fracture'."
    )
    classification_target: ClassificationTarget = Field(
        ClassificationTarget.ALL,
        description=(
            "Which classifications to return:\n"
            "- 'positive': Only positive\n"
            "- 'negative': Only negative\n"
            "- 'uncertain': Only uncertain\n"
            "- 'all' (default)\n"
            "- 'positive_and_uncertain'\n"
            "- 'negative_and_uncertain'"
        )
    )
    confidence_threshold: float = Field(
        0.5,
        description="Minimum confidence score; below this, results are treated as 'uncertain'.",
        ge=0.0, le=1.0
    )
    max_classify: Optional[int] = Field(
        None,
        description="Max number of rows to classify (prevents very large batches). If None, classify all.",
        gt=0, le=10000
    )
    return_limit: Optional[int] = Field(
        None,
        description="Max number of results to return after classification (post-filter). If None, return all.",
        gt=0, le=1000
    )

class ReportResult(BaseModel):
    mrn: Optional[str]
    accession_number: Optional[str]
    modality: Optional[str]
    service_name: Optional[str]
    event_date: Optional[str]
    classification: str
    confidence: float
    confidence_scores: Dict[str, float]
    report_text: str
    sending_facility: Optional[str]
    sex: Optional[str]
    race: Optional[str]
    age: Optional[int]

class DiagnosisSearchResponse(BaseModel):
    total_queried: int
    total_classified: int
    total_matching: int
    total_returned: int
    diagnosis_searched: str
    classification_target: str
    results: List[ReportResult]
    statistics: Dict[str, Any]
    sql_executed: str

class ErrorResponse(BaseModel):
    error: str
    error_type: str
    details: Optional[str]
    suggestion: Optional[str]

class HealthCheckResponse(BaseModel):
    status: str
    trino_connected: bool
    classifier_loaded: bool
    trino_error: Optional[str] = None

async def execute_trino_query(query: str) -> List[Dict]:
    """Execute a query against Trino and return results as list[dict]."""

    # Validate the query text contains required aliases
    missing_columns = validate_sql_query(query)
    if missing_columns:
        raise SQLError(
            "SQL query is missing required column aliases. "
            "Please add to SELECT: " + ", ".join(missing_columns)
        )

    # Enforce a maximum row cap if user forgot a LIMIT
    safe_sql = _wrap_with_limit(query, ENFORCED_MAX_ROWS)

    loop = asyncio.get_event_loop()

    def run_query():
        conn = None
        cursor = None
        try:
            conn = get_trino_connection()
            cursor = conn.cursor()
            logger.info(f"Executing query (capped): {safe_sql[:200]}...")
            cursor.execute(safe_sql)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            # Validate returned columns
            expected_aliases = set(REQUIRED_COLUMNS.keys())
            actual_aliases = set(columns)
            missing = expected_aliases - actual_aliases
            if missing:
                raise SQLError(
                    "Query executed but missing required output aliases: "
                    f"{sorted(missing)}. Returned columns: {columns}"
                )
            return [dict(zip(columns, row)) for row in rows]
        except trino.exceptions.TrinoQueryError as e:
            raise SQLError(parse_sql_error(e)) from e
        except Exception as e:
            if isinstance(e, SQLError):
                raise
            raise SQLError(parse_sql_error(e)) from e
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    return await loop.run_in_executor(executor, run_query)

def generate_diagnosis_labels(diagnosis: str) -> List[str]:
    term = diagnosis.lower().replace("_", " ").strip()
    return [
        f"positive for {term}",
        f"negative for {term}",
        "uncertain"
    ]

def should_include_result(
    classification: str,
    confidence: float,
    classification_target: ClassificationTarget,
    confidence_threshold: float
) -> bool:
    """Filter based on target and threshold."""
    cl = classification.lower()
    is_pos = "positive for" in cl
    is_neg = "negative for" in cl
    is_unc = "uncertain" in cl

    # For pos/neg, if confidence below threshold we treat as uncertain
    if not is_unc and confidence < confidence_threshold:
        is_unc = True
        is_pos = False
        is_neg = False

    if classification_target == ClassificationTarget.ALL:
        return True
    elif classification_target == ClassificationTarget.POSITIVE:
        return is_pos and confidence >= confidence_threshold
    elif classification_target == ClassificationTarget.NEGATIVE:
        return is_neg and confidence >= confidence_threshold
    elif classification_target == ClassificationTarget.UNCERTAIN:
        return is_unc or confidence < confidence_threshold
    elif classification_target == ClassificationTarget.POSITIVE_AND_UNCERTAIN:
        return is_pos or is_unc or confidence < confidence_threshold
    elif classification_target == ClassificationTarget.NEGATIVE_AND_UNCERTAIN:
        return is_neg or is_unc or confidence < confidence_threshold
    return False

async def classify_reports_batch(
    reports: List[Dict],
    diagnosis: str,
    classification_target: ClassificationTarget,
    confidence_threshold: float,
    max_classify: Optional[int] = None,
    return_limit: Optional[int] = None
) -> List[ReportResult]:
    """Classify reports using zero-shot classification."""
    if not reports:
        return []

    reports_to_classify = reports[:max_classify] if max_classify else reports
    labels = generate_diagnosis_labels(diagnosis)
    clf = get_classifier()

    texts = []
    valid_indices = []

    for i, report in enumerate(reports_to_classify):
        report_text = report.get("report_text")
        if report_text:
            texts.append(report_text)
            valid_indices.append(i)
        else:
            logger.warning(f"Skipping report {i} - empty or missing text")

    if not texts:
        logger.warning("No valid texts to classify")
        return []

    logger.info(f"Classifying {len(texts)} reports for '{diagnosis}' (target: {classification_target})...")
    loop = asyncio.get_event_loop()

    def classify_batch():
        batch_size = 16
        all_results = []
        for i in range(0, len(texts), batch_size):
            b_end = min(i + batch_size, len(texts))
            batch = texts[i:b_end]
            logger.info(f"Processing batch {i//batch_size + 1} ({i+1}-{b_end} / {len(texts)})")
            try:
                outputs = clf(
                    batch,
                    labels,
                    multi_label=False,
                    hypothesis_template="The radiology report is {label}."
                )
                if isinstance(outputs, list):
                    all_results.extend(outputs)
                else:
                    all_results.append(outputs)
            except Exception as e:
                logger.error(f"Classification error on batch {i//batch_size + 1}: {str(e)}")
                # Fallback: mark all in this batch as uncertain
                for _ in batch:
                    all_results.append({
                        "labels": [f"uncertain about {diagnosis}", f"negative for {diagnosis}", f"positive for {diagnosis}"],
                        "scores": [1.0, 0.0, 0.0]
                    })
        return all_results

    classification_results = await loop.run_in_executor(executor, classify_batch)
    logger.info(f"Classification complete. Processing {len(classification_results)} results")

    final_results: List[ReportResult] = []

    for idx, clf_result in zip(valid_indices, classification_results):
        report = reports_to_classify[idx]
        pairs = sorted(
            zip(clf_result["labels"], clf_result["scores"]),
            key=lambda x: x[1],
            reverse=True
        )
        best_label, best_score = pairs[0]
        margin = best_score - (pairs[1][1] if len(pairs) > 1 else 0.0)

        effective_classification = best_label
        # Treat small margins or low absolute confidence as uncertain
        if (best_score < confidence_threshold) or (margin < 0.10 and "uncertain" not in best_label.lower()):
            effective_classification = "uncertain (low confidence/margin)"

        if should_include_result(effective_classification, best_score, classification_target, confidence_threshold):
            confidence_scores = {label: score for label, score in pairs}
            result = ReportResult(
                mrn=report.get("mrn"),
                accession_number=report.get("accession_number"),
                modality=report.get("modality"),
                service_name=report.get("service_name"),
                event_date=report.get("event_date"),
                classification=effective_classification,
                confidence=best_score,
                confidence_scores=confidence_scores,
                report_text=report.get("report_text", ""),
                sending_facility=report.get("sending_facility"),
                sex=report.get("sex"),
                race=report.get("race"),
                age=report.get("age")
            )
            final_results.append(result)
            if return_limit and len(final_results) >= return_limit:
                logger.info(f"Reached return limit of {return_limit}")
                break

    logger.info(f"Returning {len(final_results)} results after filtering")
    return final_results

@app.post("/search_diagnosis", response_model=DiagnosisSearchResponse, responses={
    400: {"model": ErrorResponse, "description": "Bad Request - Invalid SQL or missing columns"},
    500: {"model": ErrorResponse, "description": "Internal Server Error"}
})
async def search_diagnosis(request: DiagnosisSearchRequest):
    """
    Execute a SQL query and classify the resulting radiology reports for a specific diagnosis.

    The SQL query must include all required column aliases in the SELECT clause.
    """
    try:
        logger.info(f"Searching for diagnosis: {request.diagnosis} (target: {request.classification_target})")

        # Execute the provided SQL query
        try:
            reports = await execute_trino_query(request.sql_query)
        except SQLError as e:
            logger.error(f"SQL execution error: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": str(e),
                    "error_type": "SQL_ERROR",
                    "details": "The SQL query failed to execute or is missing required columns.",
                    "suggestion": "Check the SQL syntax and ensure all required column aliases are present."
                }
            )

        logger.info(f"Query returned {len(reports)} candidate reports")

        if not reports:
            return DiagnosisSearchResponse(
                total_queried=0,
                total_classified=0,
                total_matching=0,
                total_returned=0,
                diagnosis_searched=request.diagnosis,
                classification_target=request.classification_target.value,
                results=[],
                statistics={"message": "No reports found matching the SQL query"},
                sql_executed=_wrap_with_limit(request.sql_query, ENFORCED_MAX_ROWS)
            )

        # Classify reports
        logger.info(f"Starting classification for '{request.diagnosis}'...")
        try:
            classified_results = await classify_reports_batch(
                reports=reports,
                diagnosis=request.diagnosis,
                classification_target=request.classification_target,
                confidence_threshold=request.confidence_threshold,
                max_classify=request.max_classify,
                return_limit=request.return_limit
            )
        except Exception as e:
            logger.error(f"Classification error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": f"Classification failed: {str(e)}",
                    "error_type": "CLASSIFICATION_ERROR",
                    "details": "The AI model failed to classify the reports.",
                    "suggestion": "Try reducing max_classify or check the diagnosis term."
                }
            )

        logger.info(f"Classification complete. {len(classified_results)} results match criteria")

        # Statistics
        num_to_classify = min(len(reports), request.max_classify) if request.max_classify else len(reports)
        statistics: Dict[str, Any] = {
            "total_candidates": len(reports),
            "total_classified": num_to_classify,
            "classification_target": request.classification_target.value,
            "confidence_threshold": request.confidence_threshold
        }
        counts = {
            "positive": sum(1 for r in classified_results if "positive" in r.classification.lower()),
            "negative": sum(1 for r in classified_results if "negative" in r.classification.lower()),
            "uncertain": sum(1 for r in classified_results if "uncertain" in r.classification.lower())
        }
        statistics["classification_counts"] = counts
        if classified_results:
            confidences = [r.confidence for r in classified_results]
            statistics["avg_confidence"] = sum(confidences) / len(confidences)
            statistics["confidence_stats"] = {
                "min": min(confidences),
                "max": max(confidences),
                "median": sorted(confidences)[len(confidences)//2]
            }
        if request.return_limit and len(classified_results) == request.return_limit:
            statistics["note"] = f"Results limited to {request.return_limit} reports"

        return DiagnosisSearchResponse(
            total_queried=len(reports),
            total_classified=num_to_classify,
            total_matching=len(classified_results),
            total_returned=len(classified_results),
            diagnosis_searched=request.diagnosis,
            classification_target=request.classification_target.value,
            results=classified_results,
            statistics=statistics,
            sql_executed=_wrap_with_limit(request.sql_query, ENFORCED_MAX_ROWS)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in search_diagnosis: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": str(e),
                "error_type": "UNEXPECTED_ERROR",
                "details": "An unexpected error occurred.",
                "suggestion": "Please check the logs or contact support."
            }
        )

@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """Check service health and connectivity without relying on the validator."""
    trino_connected = False
    trino_error = None
    classifier_loaded = False

    # Direct Trino ping
    try:
        conn = get_trino_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        _ = cur.fetchall()
        trino_connected = True
    except Exception as e:
        trino_error = parse_sql_error(e)
        logger.error(f"Trino health check failed: {trino_error}")
    finally:
        try:
            cur.close()  # type: ignore
        except Exception:
            pass
        try:
            conn.close()  # type: ignore
        except Exception:
            pass

    # Classifier check
    try:
        clf = get_classifier()
        if clf:
            classifier_loaded = True
    except Exception as e:
        logger.error(f"Classifier health check failed: {str(e)}")

    status = "healthy" if (trino_connected and classifier_loaded) else "degraded"

    return HealthCheckResponse(
        status=status,
        trino_connected=trino_connected,
        classifier_loaded=classifier_loaded,
        trino_error=trino_error
    )

@app.get("/")
async def root():
    """Root endpoint with API information, schema, and SQL examples."""
    return {
        "service": "Radiology Report Diagnosis Search",
        "version": "1.1.0",
        "endpoints": {
            "/search_diagnosis": "POST - Execute SQL and classify reports for diagnosis",
            "/health": "GET - Service health check",
            "/docs": "GET - OpenAPI documentation"
        },
        "classification_targets": [ct.value for ct in ClassificationTarget],
        "required_sql_columns": list(REQUIRED_COLUMNS.values()),
        "schema": SCHEMA_DOC,
        "sql_examples": {
            "neoplasm_last_year": SQL_EXAMPLES["neoplasm_last_year"],
            "ct_white_female_40plus_pe_last_month": SQL_EXAMPLES["ct_white_female_40plus_pe_last_month"]
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
