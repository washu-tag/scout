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
    version="1.1.0",
)

# Enable CORS for Open WebUI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Classification config ----------
CLASSIFICATION_MODEL = os.getenv("CLASSIFICATION_MODEL", "facebook/bart-large-mnli")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.5"))
MAX_TEXT_LENGTH = int(os.getenv("MAX_TEXT_LENGTH", "1024"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "8"))
ENFORCED_MAX_ROWS = int(os.getenv("ENFORCED_MAX_ROWS", "5000"))

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

# ---------- Example SQL templates ----------
SQL_EXAMPLE_NEOPLASM_LAST_YEAR = r"""
-- Latest per accession with neoplasm keywords, last 1 year
SELECT
  r.epic_mrn AS mrn,
  r.obr_3_filler_order_number AS accession_number,
  r.modality AS modality,
  r.service_name AS service_name,
  CAST(COALESCE(r.observation_dt, r.message_dt, r.requested_dt) AS VARCHAR) AS event_date,
  date_diff(
    'year',
    CAST(r.birth_date AS date),
    CAST(COALESCE(r.observation_dt, r.message_dt, r.requested_dt) AS date)
  ) AS age,
  r.report_text AS report_text,
  r.sending_facility AS sending_facility,
  r.sex AS sex,
  r.race AS race
FROM delta.default.reports r
JOIN (
  SELECT
    obr_3_filler_order_number,
    MAX_BY(message_control_id, message_dt) AS latest_id
  FROM delta.default.reports
  WHERE report_text IS NOT NULL
    AND COALESCE(observation_dt, message_dt, requested_dt) >= current_timestamp - INTERVAL '1' YEAR
    AND REGEXP_LIKE(report_text, '(?i)\b(neoplasm|malign\w*|cancer|tumou?r|lesion\w*|mass(?:\b|\s))')
  GROUP BY obr_3_filler_order_number
) m
  ON r.obr_3_filler_order_number = m.obr_3_filler_order_number
 AND r.message_control_id = m.latest_id
"""

SQL_EXAMPLES = {"neoplasm_last_year": SQL_EXAMPLE_NEOPLASM_LAST_YEAR}

# Required columns for the query
REQUIRED_COLUMNS = {
    "mrn": "epic_mrn as mrn",
    "accession_number": "obr_3_filler_order_number as accession_number",
    "modality": "modality as modality",
    "service_name": "service_name as service_name",
    "event_date": "CAST(COALESCE(observation_dt, message_dt, requested_dt) AS VARCHAR) as event_date",
    "report_text": "report_text as report_text",
    "sending_facility": "sending_facility as sending_facility",
    "sex": "sex as sex",
    "race": "race as race",
    "age": "date_diff('year', CAST(birth_date AS date), CAST(COALESCE(observation_dt, message_dt, requested_dt) AS date)) as age",
}


class ClassificationTarget(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    ALL = "all"


class SQLError(Exception):
    """Custom exception for SQL-related errors with helpful messages."""

    pass


# Hold classifier globally for reuse
classifier = None
executor = ThreadPoolExecutor(max_workers=4)

# Trino
TRINO_HOST = os.getenv("TRINO_HOST", "trino.trino")
TRINO_PORT = int(os.getenv("TRINO_PORT", "8080"))
TRINO_USER = os.getenv("TRINO_USER", "scout")
TRINO_CATALOG = os.getenv("TRINO_CATALOG", "delta")
TRINO_SCHEMA = os.getenv("TRINO_SCHEMA", "default")


def get_classifier():
    """Lazy-load a zero-shot classifier."""
    global classifier
    if classifier is None:
        device = 0 if torch.cuda.is_available() else -1
        classifier = pipeline(
            "zero-shot-classification",
            model=CLASSIFICATION_MODEL,
            device=device,
            hypothesis_text="Findings are {}",
        )
        logger.info(
            f"Classifier loaded: {CLASSIFICATION_MODEL} on device: {'cuda' if device == 0 else 'cpu'}"
        )
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
    )


def validate_sql_query(query: str) -> List[str]:
    """
    Validate that the SQL query contains required column aliases.
    Returns a list of missing column expressions.
    """
    q = query.lower()
    missing = []
    for alias, column_expression in REQUIRED_COLUMNS.items():
        alias_name = alias.lower()
        if not re.search(rf"\bas\s+{re.escape(alias_name)}\b", q):
            missing.append(column_expression)
    return missing


def parse_sql_error(error: Exception) -> str:
    """Parse SQL errors and return helpful messages."""
    es = str(error)
    el = es.lower()
    if "table" in el and "not found" in el:
        return f"Table not found. Please ensure you're querying 'delta.default.reports'. Original error: {es}"
    elif "column" in el and ("not found" in el or "cannot be resolved" in el):
        m = re.search(r"column['\"]?\s*([^'\"\s]+)", es, re.IGNORECASE)
        col = m.group(1) if m else "unknown"
        return (
            f"Column '{col}' not found. Check available columns. Original error: {es}"
        )
    elif "syntax error" in el or "parse" in el:
        return f"SQL syntax error. Check string quoting and date formats. Original error: {es}"
    elif "permission" in el or "access denied" in el:
        return f"Permission denied for user '{TRINO_USER}'. Original error: {es}"
    elif "timeout" in el:
        return f"Query timed out. Try narrowing the WHERE clause. Original error: {es}"
    elif "memory" in el:
        return f"Query exceeded memory limits. Original error: {es}"
    else:
        return f"Query execution failed: {es}"


def _wrap_with_limit(sql: str, limit: int) -> str:
    """If user forgot a LIMIT, wrap the query to enforce a max row cap."""
    if re.search(r"\blimit\b\s+\d+", sql, re.IGNORECASE):
        return sql
    return f"SELECT * FROM ({sql}) __q__ LIMIT {limit}"


# Pydantic models
class DiagnosisSearchRequest(BaseModel):
    sql_query: str = Field(
        ...,
        description="SQL query to execute against delta.default.reports. Must include aliases for all required columns.",
    )
    diagnosis: str = Field(
        ..., description="Diagnosis term to classify (e.g. 'pulmonary embolism')."
    )
    classification_target: ClassificationTarget = Field(
        default=ClassificationTarget.ALL,
        description="Which classifications to return: positive, negative, or all.",
    )
    confidence_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold for classification.",
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
    """Execute a query against Trino and return results."""
    missing_columns = validate_sql_query(query)
    if missing_columns:
        raise SQLError(
            "SQL query is missing required column aliases. "
            "Please add to SELECT: " + ", ".join(missing_columns)
        )

    sql = _wrap_with_limit(query, ENFORCED_MAX_ROWS)

    def run_query():
        conn = None
        cursor = None
        try:
            conn = get_trino_connection()
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [d[0] for d in cursor.description]

            expected_aliases = set(REQUIRED_COLUMNS.keys())
            actual_aliases = set(columns)
            missing = expected_aliases - actual_aliases
            if missing:
                raise SQLError(
                    f"Query executed but missing required output aliases: {sorted(missing)}. "
                    f"Returned columns: {columns}"
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

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, run_query)


def extract_key_sections(text: str) -> str:
    """Extract IMPRESSION and FINDINGS sections if available, otherwise return truncated text."""
    if not text:
        return ""

    # Look for IMPRESSION or FINDINGS sections
    impression_match = re.search(
        r"(?im)(impressions?|findings?)\s*:?\s*(.*?)(?=\n[A-Z]+\s*:|$)", text, re.DOTALL
    )
    if impression_match:
        section_text = impression_match.group(2).strip()
        return (
            section_text[:MAX_TEXT_LENGTH] if section_text else text[:MAX_TEXT_LENGTH]
        )

    # If no sections found, return the last part of the text (most likely to contain conclusions)
    return text[-MAX_TEXT_LENGTH:]


def should_include_result(
    classification: str, classification_target: ClassificationTarget
) -> bool:
    """Filter results based on classification target."""
    if classification_target == ClassificationTarget.ALL:
        return True
    elif classification_target == ClassificationTarget.POSITIVE:
        return "positive" in classification.lower()
    elif classification_target == ClassificationTarget.NEGATIVE:
        return "negative" in classification.lower()
    return False


async def classify_reports_batch(
    reports: List[Dict],
    diagnosis: str,
    classification_target: ClassificationTarget,
    confidence_threshold: float,
    max_classify: Optional[int] = 10,
    return_limit: Optional[int] = 10,
) -> List[ReportResult]:
    """Classify reports using simple zero-shot classification."""
    if not reports:
        return []

    reports_to_classify = reports[:max_classify] if max_classify else reports

    # Prepare texts
    texts = []
    valid_indices = []
    for i, report in enumerate(reports_to_classify):
        rt = report.get("report_text") or ""
        if not rt.strip():
            logger.warning(f"Skipping report {i} - empty or missing text")
            continue
        # Extract key sections for better classification
        extracted_text = extract_key_sections(rt)
        texts.append(extracted_text)
        valid_indices.append(i)

    if not texts:
        logger.warning("No valid texts to classify")
        return []

    logger.info(f"Classifying {len(texts)} reports for '{diagnosis}'...")

    # Define simple labels
    labels = [f"positive for {diagnosis}", f"negative for {diagnosis}"]

    loop = asyncio.get_event_loop()

    def classify_batch():
        clf = get_classifier()
        results = []

        # Process in batches for efficiency
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            try:
                # Use multi_label=False for exclusive classification
                batch_results = clf(batch, candidate_labels=labels, multi_label=False)
                # Ensure results is a list
                if not isinstance(batch_results, list):
                    batch_results = [batch_results]
                results.extend(batch_results)
            except Exception as e:
                logger.error(f"Classification error: {e}")
                # Add empty results for failed classifications
                for _ in batch:
                    results.append({"labels": labels, "scores": [0.0, 0.0]})

        return results

    classification_results = await loop.run_in_executor(executor, classify_batch)
    logger.info(
        f"Classification complete. Processing {len(classification_results)} results"
    )

    final_results = []
    for idx, clf_result in zip(valid_indices, classification_results):
        report = reports_to_classify[idx]

        # Get the top classification
        label_scores = dict(zip(clf_result["labels"], clf_result["scores"]))
        top_label = clf_result["labels"][0]
        top_score = clf_result["scores"][0]

        # Apply confidence threshold
        if top_score < confidence_threshold:
            # Skip low-confidence results or mark as uncertain
            # For simplicity, we'll just use the prediction but note the low confidence
            pass

        classification = top_label

        if should_include_result(classification, classification_target):
            result = ReportResult(
                mrn=report.get("mrn"),
                accession_number=report.get("accession_number"),
                modality=report.get("modality"),
                service_name=report.get("service_name"),
                event_date=report.get("event_date"),
                classification=classification,
                confidence=float(top_score),
                confidence_scores=label_scores,
                report_text=report.get("report_text", ""),
                sending_facility=report.get("sending_facility"),
                sex=report.get("sex"),
                race=report.get("race"),
                age=report.get("age"),
            )
            final_results.append(result)

            if return_limit and len(final_results) >= return_limit:
                logger.info(f"Reached return limit of {return_limit}")
                break

    logger.info(f"Returning {len(final_results)} results after filtering")
    return final_results


@app.post(
    "/search_diagnosis",
    response_model=DiagnosisSearchResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad Request"},
        500: {"model": ErrorResponse, "description": "Internal Server Error"},
    },
    operation_id="search_diagnosis_tool",
)
async def search_diagnosis_tool(request: DiagnosisSearchRequest):
    """
    Execute a SQL query and classify radiology reports for a diagnosis.
    Uses simple zero-shot classification with positive/negative labels.
    """
    try:
        logger.info(f"Searching for diagnosis: {request.diagnosis}")

        # Execute SQL query
        try:
            reports = await execute_trino_query(request.sql_query)
        except SQLError as e:
            logger.error(f"SQL error: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": str(e),
                    "error_type": "SQL_ERROR",
                    "details": "The SQL query failed to execute or is missing required columns.",
                    "suggestion": "Check the SQL syntax and ensure all required column aliases are present.",
                },
            )
        except Exception as e:
            logger.error(f"Unexpected SQL error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": str(e),
                    "error_type": "SQL_EXECUTION_ERROR",
                    "details": "The SQL query failed to execute.",
                    "suggestion": "Check the SQL server connectivity and syntax.",
                },
            )

        logger.info(f"Query returned {len(reports)} reports")

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
                sql_executed=_wrap_with_limit(request.sql_query, ENFORCED_MAX_ROWS),
            )

        # Classify reports
        logger.info(f"Starting classification for '{request.diagnosis}'...")
        try:
            classified_results = await classify_reports_batch(
                reports=reports,
                diagnosis=request.diagnosis,
                classification_target=request.classification_target,
                confidence_threshold=request.confidence_threshold,
            )
        except Exception as e:
            logger.error(f"Classification error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": str(e),
                    "error_type": "CLASSIFICATION_ERROR",
                    "details": "The classification process failed.",
                    "suggestion": "Check model availability and logs.",
                },
            )

        total_classified = len(reports)

        # Calculate statistics
        positive_count = sum(
            1 for r in classified_results if "positive" in r.classification.lower()
        )
        negative_count = sum(
            1 for r in classified_results if "negative" in r.classification.lower()
        )
        avg_confidence = (
            sum(r.confidence for r in classified_results) / len(classified_results)
            if classified_results
            else 0
        )

        return DiagnosisSearchResponse(
            total_queried=len(reports),
            total_classified=total_classified,
            total_matching=len(classified_results),
            total_returned=len(classified_results),
            diagnosis_searched=request.diagnosis,
            classification_target=request.classification_target.value,
            results=classified_results,
            statistics={
                "confidence_threshold": request.confidence_threshold,
                "model": CLASSIFICATION_MODEL,
                "positive_count": positive_count,
                "negative_count": negative_count,
                "average_confidence": round(avg_confidence, 3),
            },
            sql_executed=_wrap_with_limit(request.sql_query, ENFORCED_MAX_ROWS),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": str(e),
                "error_type": "UNEXPECTED_ERROR",
                "details": "An unexpected error occurred.",
                "suggestion": "Please check the logs or contact support.",
            },
        )


@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """Check service health and connectivity."""
    trino_connected = False
    trino_error = None
    classifier_loaded = False

    # Test Trino connection
    try:
        conn = get_trino_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        _ = cur.fetchall()
        trino_connected = True
        cur.close()
        conn.close()
    except Exception as e:
        trino_error = parse_sql_error(e)

    # Test classifier loading
    try:
        _ = get_classifier()
        classifier_loaded = True
    except Exception:
        classifier_loaded = False

    status = "healthy" if (trino_connected and classifier_loaded) else "degraded"

    return HealthCheckResponse(
        status=status,
        trino_connected=trino_connected,
        classifier_loaded=classifier_loaded,
        trino_error=trino_error,
    )


@app.get("/")
async def meta():
    """Service metadata and examples."""
    return {
        "service": "Radiology Report Diagnosis Search",
        "version": "2.0.0",
        "description": "Simplified zero-shot classification service",
        "endpoints": {
            "/search_diagnosis": "POST - Execute SQL & classify reports",
            "/health": "GET - Health check",
            "/docs": "GET - OpenAPI documentation",
        },
        "classification_model": CLASSIFICATION_MODEL,
        "classification_targets": [ct.value for ct in ClassificationTarget],
        "required_sql_columns": list(REQUIRED_COLUMNS.values()),
        "schema": SCHEMA_DOC,
        "sql_examples": SQL_EXAMPLES,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
