import logging
import tempfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse


log = logging.getLogger(__name__)

# Temporary file for Spark health check
SPARK_HEALTH_TEMP_FILE = Path(tempfile.tempdir) / "spark_health_check"
HEALTHY = "healthy"
UNHEALTHY = "unhealthy"
HEALTHY_JSON_RESPONSE = JSONResponse(status_code=200, content={"status": HEALTHY})


app = FastAPI()


@app.get("/healthz")
async def healthz():
    try:
        log.info("Running health check")
        if not SPARK_HEALTH_TEMP_FILE.exists():
            # No health information available. Assume healthy.
            log.info("No health information available (assume healthy)")
            return HEALTHY_JSON_RESPONSE

        # Read the contents of the temporary file
        contents = SPARK_HEALTH_TEMP_FILE.read_text()
        if contents != HEALTHY:
            log.warning("Health check file reports failure: %s", contents)
            return unhealthy_json_response(f"Spark reported status: {contents}")

        log.info("Health check succeeded")
        return HEALTHY_JSON_RESPONSE
    except Exception as e:
        log.error("Health check failed with exception", e)
        return unhealthy_json_response(str(e))


def unhealthy_json_response(reason: str) -> JSONResponse:
    return JSONResponse(
        status_code=503, content={"status": UNHEALTHY, "reason": reason}
    )


async def start_spark_health_check_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
