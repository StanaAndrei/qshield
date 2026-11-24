# Q-SHIELD

**AI-Driven Orchestration for Quantum-Secured Medical Data Exchange**

Q-SHIELD is a resource-aware orchestration system designed to optimize the exchange of sensitive medical and diagnostic data over networks utilizing **Quantum Key Distribution (QKD)** for cryptographic key establishment.

Developed for healthcare and research environments (e.g., University Lab to Polyclinic links), Q-SHIELD unites three core pillars:
1. **Deterministic Security & Zero-Leakage Policy Engine**: Strictly enforces authorization, cryptographic profiles, and data integrity. AI never makes or overrides security decisions.
2. **AI-Assisted & Multi-Policy Scheduling**: Predicts transfer durations and deadline-miss probabilities to optimize queue scheduling against deterministic baselines (FIFO, Strict Priority, and Resource-Aware Heuristic).
3. **High-Performance Discrete-Event Simulation & Benchmarking**: Simulates stochastic network jitter, link drops, and QKD key-buffer replenishment dynamics across multi-core parallel experiment matrices.

---

## Architecture & Operational Flow

```text
[ Client / API ]
       │
       ▼ (POST /api/v1/transfers)
[ Deterministic Security Policy Engine ] ──► Reject Unauthorized (DEST_UNAUTHORIZED)
       │ (AUTH_SUCCESS)
       ▼
[ Queue Service & SQLite Repository ]
       │
       ▼ (Eligible Requests + QKD Buffer Telemetry)
[ Pluggable Scheduling Engine ]
       ├── FIFO
       ├── Strict Priority
       ├── Resource-Aware Heuristic (Starvation Aging & Deadline Slack)
       └── AI-Assisted (Predictive Duration & Deadline Risk via ML Regressor)
       │
       ▼ (Dispatched Transfer)
[ Transport & Pluggable Crypto Layer ] ──► Simulated / Real QKD Key Buffer (KeyHandle)
       │ (Payload Encryption + SHA-256 Checksum)
       ▼
[ Integrity Verification & State Machine ] ──► COMPLETED / FAILED
```

---

## Prerequisites

- **Python**: 3.11 or 3.12
- **Package Manager**: [`uv`](https://github.com/astral-sh/uv) (recommended) or standard `pip` / `venv`
- **Supported OS**: Linux, macOS, WSL2

---

## Quickstart & Environment Setup

### 1. Create and Activate Virtual Environment

**Using `uv` (Fast & Recommended):**
```bash
# Create virtual environment with Python 3.12 (or 3.11)
uv venv --python 3.12 .venv

# Activate virtual environment
source .venv/bin/activate
```

*Or using standard `python3`:*
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Editable Package and Dev Dependencies

**Using `uv`:**
```bash
uv pip install -e ".[dev]"
```

*Or using standard `pip`:*
```bash
pip install -e ".[dev]"
```

### 3. Initialize Local Configuration

```bash
cp .env.example .env
```

---

## Step-by-Step Execution Guide

Below are the complete steps to run, verify, train, benchmark, and visualize the system.

### Step 1: Verification & Definition of Done (DoD) Checks

Verify configuration loading, secret redaction filter, code formatting, type safety, and test collection:

```bash
# 1. Verify Configuration & Environment
python -c "from app.core.config import settings; print(settings.APP_ENV)"
# Expected output: simulation

# 2. Verify Log Secret Redaction
python -c "import logging; from app.core.config import settings; log = logging.getLogger('test'); log.info('User password=super_secret_123 key_material: abcdef456 passed');"

# 3. Linter & Static Type Checks
ruff check .
mypy app tests

# 4. Pytest Collection Gate
pytest --collect-only
```

*What this does:* Validates that the runtime environment is properly configured, dependencies are satisfied, loggers scrub credentials and key bytes automatically, and static type invariants hold.

---

### Step 2: Run the End-to-End Tracer Bullet

Execute a complete end-to-end synthetic medical data transfer through the lifecycle state machine:

```bash
python scripts/verify_tracer_bullet.py \
    --payload-size-mb 5.0 \
    --source "univ-lab" \
    --destination "poly-clinic" \
    --priority NORMAL \
    --security-profile QKD_AES_256_GCM
```

*What this does:*
- Submits a transfer request (`univ-lab` -> `poly-clinic`).
- Evaluates authorization through the deterministic `PolicyEngine`.
- Enqueues the request and schedules it via `FIFOScheduler`.
- Generates a synthetic medical buffer, queries the QKD key resource adapter, and executes transport.
- Computes SHA-256 checksums to verify payload integrity and transitions status to `COMPLETED`.

---

### Step 3: Run the REST API Service

Launch the FastAPI application server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Interactive API Docs (Swagger UI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **OpenAPI Schema**: [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)

**Example Transfer Submission:**
```bash
curl -X POST "http://localhost:8000/api/v1/transfers" \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "univ-lab",
    "destination_id": "poly-clinic",
    "data_size_bytes": 5242880,
    "priority": 20,
    "security_profile": "QKD_AES_256_GCM",
    "data_category": "DIAGNOSTIC_REPORT",
    "metadata": {"modality": "CT_REPORT"}
  }'
```

*What this does:* Serves REST endpoints for submitting transfer requests, querying transfer statuses, listing queue backlogs, and canceling pending jobs, backed by SQLite persistence.

---

### Step 4: Compare Baseline Schedulers (FIFO vs Priority vs Heuristic)

Benchmark deterministic baseline scheduling algorithms against identical workloads:

```bash
python scripts/compare_baselines.py \
    --workload-size 100 \
    --seed 42 \
    --policies FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC
```

*What this does:* Simulates a 100-request queue to compare:
- **FIFO**: Naive arrival-order scheduling.
- **Priority**: Strict priority ordering (vulnerable to low-priority starvation).
- **Resource-Aware Heuristic**: Balances priority, deadline slack, and starvation aging counters to maintain high deadline satisfaction without starving low-priority diagnostic batches.

---

### Step 5: Execute HPC Parallel Simulation Experiments

Run stochastic discrete-event simulation experiments across CPU cores:

```bash
python scripts/run_experiments.py \
    --scenario configs/scenarios/medical_mixed_workload.yaml \
    --workers 4 \
    --seeds 5 \
    --policies FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC \
    --output results/experiments/baseline_matrix.json
```

Export statistical summaries with confidence intervals:
```bash
python scripts/export_results.py \
    --input results/experiments/baseline_matrix.json \
    --output-dir results/reports/ \
    --format markdown
```

*What this does:*
- Simulates virtual-time network variability (Poisson arrivals, Pareto file sizes, Gaussian throughput jitter, stochastic packet drops) and QKD key-buffer replenishment.
- Utilizes Python multiprocessing to isolate seed executions across multiple CPU workers.
- Generates statistical summaries (mean, median, IQR, 95th percentile completion times, deadline success rates).

---

### Step 6: Train and Evaluate the AI Predictor Model

Train a Gradient Boosting / Random Forest regressor to predict transfer duration and deadline-miss probability:

```bash
# 1. Train the duration prediction model
python scripts/train_model.py \
    --data results/experiments/baseline_matrix.json \
    --model-type gradient_boosting \
    --output models/duration_predictor_v1.joblib

# 2. Evaluate model performance against baseline estimators
python -m app.predictor.evaluate \
    --model models/duration_predictor_v1.joblib \
    --test-data results/experiments/baseline_matrix.json

# 3. Evaluate AI-Assisted Scheduling Policy advantage vs Heuristic
python scripts/evaluate_policy_gain.py \
    --model models/duration_predictor_v1.joblib \
    --scenario configs/scenarios/medical_mixed_workload.yaml \
    --seeds 5 \
    --output results/experiments/ai_vs_heuristic.json
```

*What this does:*
- Extracts normalized feature vectors (payload size, queue depth, rolling network throughput, QKD key buffer levels).
- Trains an ML regressor (`models/duration_predictor_v1.joblib`) with cross-validation.
- Powers the `AIAssistedScheduler` which ranks candidates via utility scoring combining predicted transfer time, deadline risk, and starvation aging.

---

### Step 7: Security Boundary & Compliance Audit

Verify zero-leakage constraints, data minimization, and audit trail compliance:

```bash
# 1. Run security boundaries and zero-leakage unit/integration tests
pytest tests/security/test_security_boundaries.py tests/security/test_leakage_guards.py -v

# 2. Run automated compliance scanner across database and logs
python scripts/audit_security_compliance.py \
    --db-path ./qshield.db \
    --log-dir ./logs \
    --strict
```

*What this does:*
- Ensures unauthorized destinations or forbidden metadata keys (PHI, SSN, MRN, secret tokens) are rejected at the gate.
- Scans SQLite tables and log files for any accidental leakage of private cryptographic keys or medical payload bytes.

---

### Step 8: Run the Streamlit Interactive Dashboard

Launch the web-based operations and research visualization platform:

```bash
streamlit run dashboard/streamlit_app.py
```

Default URL: [http://localhost:8501](http://localhost:8501)

**Dashboard Pages:**
- **Operations**: Real-time transfer queue monitor, active transfer progress, QKD key buffer levels, and system health telemetry.
- **Experiments**: Policy comparison analytics (duration boxplots, deadline success rates, starvation wait histograms).
- **Model Evaluation**: ML predictor evaluation curves (actual vs. predicted durations), MAE/RMSE metrics, and feature importance analysis.

---

### Step 9: Run the Full Pitch Demonstration Scenario

Execute the end-to-end interactive demonstration simulating University Lab to Polyclinic exchange under fluctuating QKD conditions:

```bash
python scripts/run_demonstration.py \
    --scenario configs/scenarios/medical_mixed_workload.yaml \
    --duration-seconds 60 \
    --report-output results/demonstration_summary.md
```

*What this does:*
- Injects a multi-tier medical workload (5 MB diagnostic report, 2 GB MRI scan, 500 MB research dataset).
- Simulates link congestion and QKD key-buffer depletion.
- Demonstrates retry logic, starvation mitigation, and AI-assisted scheduling advantages in real time.

---

## Test Suite Execution

Run all test suites across the repository:

```bash
# Full test suite
pytest tests/ -v

# Milestone-specific suites
pytest tests/unit/ -v            # Core FSM, policy, and scheduler unit tests
pytest tests/integration/ -v     # End-to-end tracer bullet and scheduling integration
pytest tests/simulation/ -v      # Discrete-event simulation and parallel runner determinism
pytest tests/security/ -v        # Zero-leakage and access-control validation
```

---

## Project Structure

```text
├── app/                           # Core application package
│   ├── api/                       # FastAPI routes, request/response schemas
│   ├── core/                      # Configuration, logging, state machine, enums, exceptions
│   ├── database/                  # SQLAlchemy schema, SQLite connection, repository
│   ├── models/                    # Pydantic domain models (TransferRequest, PolicyDecision)
│   ├── predictor/                 # Feature engineering, ML models, evaluation & registry
│   ├── qkd/                       # QKD hardware abstraction, simulator adapter, capabilities
│   ├── scheduler/                 # Schedulers (FIFO, Priority, Resource-Aware, AI-Assisted)
│   ├── security/                  # Deterministic policy engine, authorization, audit logger
│   ├── services/                  # Queue service, scheduling dispatch service
│   ├── telemetry/                 # Telemetry collection, snapshots, historical metrics
│   └── transfer/                  # Crypto adapter, integrity verifier (SHA-256), synthetic executor
├── configs/                       # Application & scenario configurations
│   ├── app.yaml                   # Base application settings & redaction rules
│   ├── scheduling.yaml            # Scheduler weights (slack, priority boost, aging rate)
│   ├── security.yaml              # Authorization tables & security profiles
│   └── scenarios/                 # Workload scenarios (e.g., medical_mixed_workload.yaml)
├── dashboard/                     # Multi-page Streamlit application
│   ├── app.py                     # Dashboard entrypoint & navigation
│   ├── components/                # Reusable UI widgets and charts
│   └── pages/                     # Operations, Experiments, Model Evaluation
├── docs/                          # Architecture contracts, specifications & pitch deck
│   ├── ARCHITECTURE_CONTRACTS.md  # Core data schemas, API contracts, interface specifications
│   ├── ROADMAP_STAGES.md          # Vertical slice roadmap & verification interludes
│   ├── TASK_BACKLOG.md            # Atomic milestone task breakdown & DoD checklist
│   ├── spec.md                    # System specification document
│   └── Q_SHIELD_Hackathon_Pitch.pdf # Project pitch deck & presentation slides
├── models/                        # Serialized ML models (*.joblib) and sidecar metadata
├── results/                       # Simulation benchmark results and experiment reports
├── scripts/                       # CLI tools for verification, benchmarking, training, and demo
├── tests/                         # Unit, integration, simulation, and security test suites
├── pyproject.toml                 # Packaging configuration, tool options (Ruff, Mypy, Pytest)
├── .env.example                   # Environment configuration template
└── README.md                      # Project documentation and execution guide
```
