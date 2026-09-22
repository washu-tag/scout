"""RDS IAM database authentication for Superset's metadata DB (aws mode, opt-in).

Inert unless DB_IAM_AUTH is true. Then every new connection to the metadata DB
(DB_HOST as DB_USER) gets a fresh IAM auth token as its password, minted with the
pod's IRSA credentials, over TLS (IAM auth requires it).

The listener is registered on the Engine class, so it reaches every engine in the
process: Flask-SQLAlchemy's, the one `superset db upgrade` (alembic) builds from
SQLALCHEMY_DATABASE_URI alone, the celery workers', and the dashboards import Job's.
Tokens are only checked at login, so pooled connections outlive their 15 minutes.
"""

import os

if os.getenv("DB_IAM_AUTH", "").strip().lower() in ("1", "true", "yes"):
    import boto3
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    _IAM_DB_HOST = os.environ["DB_HOST"]
    _IAM_DB_USER = os.environ["DB_USER"]
    _IAM_DB_PORT = int(os.getenv("DB_PORT") or 5432)
    _IAM_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    _IAM_SSLMODE = os.getenv("DB_SSLMODE", "verify-full")
    _IAM_SSLROOTCERT = os.getenv("DB_SSLROOTCERT", "/etc/rds-ca/ca.pem")
    _iam_rds_clients: dict = {}

    # Password-less: the chart's default URI embeds DB_PASS unquoted.
    SQLALCHEMY_DATABASE_URI = (
        f"postgresql+psycopg2://{_IAM_DB_USER}@{_IAM_DB_HOST}:{_IAM_DB_PORT}"
        f"/{os.getenv('DB_NAME')}"
    )

    def _iam_rds_client():
        # Per process: gunicorn and celery fork after this module is imported.
        pid = os.getpid()
        if pid not in _iam_rds_clients:
            _iam_rds_clients[pid] = boto3.session.Session().client(
                "rds", region_name=_IAM_REGION
            )
        return _iam_rds_clients[pid]

    @event.listens_for(Engine, "do_connect")
    def _iam_do_connect(dialect, conn_rec, cargs, cparams):
        # Fires for every engine, Trino included; only touch the metadata DB login.
        if (
            dialect.name != "postgresql"
            or cparams.get("host") != _IAM_DB_HOST
            or cparams.get("user") != _IAM_DB_USER
        ):
            return
        cparams["password"] = _iam_rds_client().generate_db_auth_token(
            DBHostname=_IAM_DB_HOST,
            Port=int(cparams.get("port") or _IAM_DB_PORT),
            DBUsername=_IAM_DB_USER,
            Region=_IAM_REGION,
        )
        cparams["sslmode"] = _IAM_SSLMODE
        cparams["sslrootcert"] = _IAM_SSLROOTCERT
