# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

Scout is a radiology report exploration platform built on Kubernetes that processes and analyzes HL7 medical imaging reports. The platform uses a microservices architecture with the following key components:

- **Launchpad**: React frontend (landing page and service navigation)
- **Extractor**: Data ingestion services (Java + Python)
- **Orchestrator**: Temporal workflow management
- **Lake**: Data storage (MinIO + Delta Lake + Hive Metastore)
- **Analytics**: Apache Superset dashboards and JupyterHub notebooks
- **Monitoring**: Grafana + Prometheus + Loki stack

## Development Commands

### Frontend (Launchpad)
```bash
cd launchpad/
npm run dev          # Start development server
npm run build        # Build for production
npm run lint         # Run ESLint
npm run preview      # Preview production build
```

### Java Services (Extractor/Tests)
```bash
cd extractor/hl7log-extractor/  # or tests/
./gradlew build                  # Build project
./gradlew test                   # Run tests
./gradlew checkstyleMain         # Run checkstyle linting
./gradlew bootRun                # Run Spring Boot application (extractor only)
```

### Python Services (HL7 Transformer)
```bash
cd extractor/hl7-transformer/
pip install -e .                # Install in development mode
python -m hl7scout.healthapi     # Run health API
```

### Infrastructure & Deployment
```bash
cd ansible/
make all                        # Deploy full Scout platform
make deps                       # Install Ansible dependencies
make install-k3s               # Install Kubernetes cluster
make install-launchpad         # Deploy launchpad service
make install-extractor         # Deploy extractor services
make install-analytics         # Deploy analytics stack
make echo                      # Show deployment configuration
```

### Documentation
```bash
cd docs/
make html                      # Build Sphinx documentation
```

### Code Quality
```bash
# Run all pre-commit hooks manually
pre-commit run --all-files

# Install pre-commit hooks
pre-commit install
```

## Code Architecture

### Data Flow
1. **Ingestion**: HL7 log files → HL7 Log Extractor (Java) → Individual messages in MinIO
2. **Transformation**: HL7 Transformer (Python) → Parsed data in Delta Lake
3. **Analytics**: Trino query engine → Superset dashboards and Jupyter notebooks

### Key Technologies
- **Frontend**: React 19, Vite, TailwindCSS
- **Backend**: Java 21 (Spring Boot), Python 3.8+ (FastAPI, PySpark)
- **Orchestration**: Temporal workflows, Kubernetes (Helm charts)
- **Storage**: PostgreSQL (metadata), MinIO (S3-compatible), Delta Lake (analytics)
- **Analytics**: Apache Superset, JupyterHub, Trino SQL engine

### Project Structure
- `launchpad/` - React frontend application
- `extractor/hl7log-extractor/` - Java service for splitting HL7 log files
- `extractor/hl7-transformer/` - Python service for HL7 parsing and Delta Lake loading
- `orchestrator/` - Temporal workflow definitions
- `helm/` - Kubernetes deployment charts for each service
- `ansible/` - Infrastructure automation and deployment playbooks
- `tests/` - Integration tests using Spark SQL and TestNG
- `docs/` - Sphinx documentation
- `analytics/` - Superset dashboard and chart definitions

### Development Workflow
- Pre-commit hooks enforce code formatting (Prettier, Black) and linting (ESLint, Checkstyle)
- CI/CD pipeline runs linting, builds Docker images, and deploys to test environment
- Services communicate via Temporal workflows and REST APIs
- All services are containerized and deployed via Helm charts

### Testing
- **Java**: TestNG framework with Spark SQL integration tests
- **Frontend**: ESLint for code quality
- **Integration**: Full platform testing in Kubernetes environment
- **Python**: Built-in health check endpoints

### Configuration
- Environment-specific values in `ansible/vars/` and Helm `values.yaml` files
- Ansible inventory files for deployment targets
- Docker images versioned and published to GitHub Container Registry