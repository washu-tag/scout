import logging
import tempfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse


log = logging.getLogger(__name__)

# Temporary file for Spark health check
SPARK_HEALTH_TEMP_FILE = Path(tempfile.mkstemp()[1])
HEALTHY = "healthy"
UNHEALTHY = "unhealthy"
HEALTHY_JSON_RESPONSE = JSONResponse(status_code=200, content={"status": HEALTHY})


app = FastAPI()


@app.get("/healthz")
async def healthz():
    try:
        log.debug("Running health check")
        if not SPARK_HEALTH_TEMP_FILE.exists():
            # No health information available. Assume healthy.
            log.debug("No health information available (assume healthy)")
            return HEALTHY_JSON_RESPONSE

        # Read the contents of the temporary file
        contents = SPARK_HEALTH_TEMP_FILE.read_text()
        if not contents:
            # No health information available. Assume healthy.
            log.debug("No health information available (assume healthy)")
            return HEALTHY_JSON_RESPONSE
        messages = [line for line in contents.splitlines() if line]
        reason = messages[0] if messages else "unknown"

        log.warning('Health check file reports failure: "%s"', reason)
        return unhealthy_json_response(reason, messages)
    except Exception as e:
        log.exception(e)
        return unhealthy_json_response("Health check failed with exception")


def unhealthy_json_response(
    reason: str, messages: list[str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"status": UNHEALTHY, "reason": reason, "messages": messages or []},
    )


async def start_spark_health_check_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
