# Q-SHIELD Vertical Slice Roadmap & Verification Interludes

This document outlines the staged, tracer-bullet implementation roadmap for **Q-SHIELD** (Quantum-Secured Medical Data Exchange Orchestrator).

Each stage represents an additive vertical slice spanning from input to output. Progression between stages is strictly gated by **Verification Interludes** featuring runnable CLI commands.

---

## Roadmap Overview

```text
[Stage 0: Inception & Tooling]
            │
            ▼ (Interlude 0: Lint & Test Harness Gate)
[Stage 1: Core Tracer Bullet (API -> DB -> Policy -> FIFO -> Exec -> Verify)]
            │
            ▼ (Interlude 1: Tracer Bullet Verification)
[Stage 2: Deterministic Baseline Schedulers & Resource Accounting]
            │
            ▼ (Interlude 2: Multi-Policy Baseline Verification)
[Stage 3: High-Performance Discrete-Event Simulation & Parallel Benchmarking]
            │
            ▼ (Interlude 3: Simulation Determinism & Parallel Scaling Verification)
[Stage 4: AI Telemetry & Predictive Scheduling Engine]
            │
            ▼ (Interlude 4: AI Model Quality & Policy Advantage Verification)
[Stage 5: Pluggable QKD Hardware Abstraction & Security Hardening]
            │
            ▼ (Interlude 5: Security Boundary & Zero-Leakage Verification)
[Stage 6: Operational & Research Dashboard, Statistical Evaluation & Pitch Demo]
            │
            ▼ (Interlude 6: Full System End-to-End Acceptance Verification)
```

---

## Stage 0: Environment Setup & Infrastructure Discovery

### Objective
Establish the reproducible Python packaging foundation, directory structure, linter/formatter configs, and configure safety guards for the real vs. simulated QKD interface.

### Preconditions
- Clean repository root. Python 3.11+ installed.

### Concrete Implementation Delta
- [NEW] `pyproject.toml` — Project dependencies (`fastapi`, `uvicorn`, `pydantic`, `sqlalchemy`, `simpy`, `scikit-learn`, `joblib`, `streamlit`, `pytest`, `httpx`, `ruff`, `mypy`).
- [NEW] `.gitignore` — Ignore virtualenvs, caches, SQLite DBs (`*.db`), model artifacts (`*.joblib`), and temporary simulation logs.
- [NEW] `.env.example` — Environment configuration template (`QSHIELD_ENV=simulation`, `QSHIELD_DB_URL=sqlite:///./qshield.db`, `QSHIELD_SECRET_AUDIT_SALT=...`).
- [NEW] `configs/app.yaml` — Base application and logging configuration.
- [NEW] `tests/conftest.py` — Pytest root fixtures, shared memory DB session, test client.

### Interlude 0: Environment & Tooling Verification Gate
> [!IMPORTANT]
> Must pass cleanly without warnings before implementing domain code.

**CLI Verification Commands:**
```bash
# 1. Install editable package and dev dependencies
pip install -e ".[dev]"

# 2. Verify static type checking and formatting
ruff check .
mypy app tests

# 3. Verify pytest collection runs without import errors
pytest --collect-only
```

---

## Stage 1: Core Tracer Bullet (Thin Vertical Slice)

### Objective
Implement the thinnest possible end-to-end path: A client submits a synthetic transfer request via REST API; the request is validated, stored in SQLite, evaluated by a deterministic policy engine, scheduled by a FIFO scheduler, executed via synthetic transport, verified with SHA-256 integrity check, and updated to `COMPLETED`.

```text
POST /transfers ──► Validating ──► SQLite (QUEUED) ──► Policy OK ──► FIFO Pick ──► Exec (SHA-256) ──► COMPLETED
```

### Preconditions
- Interlude 0 green.

### Concrete Implementation Delta
- [NEW] `app/core/enums.py` — Lifecycle states, priority levels, categories, profiles.
- [NEW] `app/core/exceptions.py` — Domain exceptions (`PolicyViolationError`, `InvalidStateTransitionError`).
- [NEW] `app/core/state_machine.py` — Lifecycle FSM with validated transition table.
- [NEW] `app/models/transfer_request.py` — Pydantic entities and SQLite table definitions.
- [NEW] `app/models/policy_decision.py` — Structured policy outcome schema.
- [NEW] `app/database/connection.py` — SQLite engine, session factory, thread safety.
- [NEW] `app/database/repository.py` — Transfer repository implementation (`create`, `get`, `update_status`, `list`).
- [NEW] `app/security/policy_engine.py` — Deterministic authorization check (allowed sources, destinations, profiles).
- [NEW] `app/scheduler/base.py` — `IScheduler` interface contract.
- [NEW] `app/scheduler/fifo.py` — First-In-First-Out deterministic scheduling policy.
- [NEW] `app/transfer/base.py` — `ITransferExecutor` interface contract.
- [NEW] `app/transfer/integrity.py` — Checksum calculator and validator (SHA-256).
- [NEW] `app/transfer/simulator.py` — Synthetic transfer executor (in-memory buffer, simulated latency).
- [NEW] `app/api/schemas.py` — API request/response models.
- [NEW] `app/api/routes_transfers.py` — FastAPI routes (`POST /transfers`, `GET /transfers/{id}`, `GET /transfers`).
- [NEW] `app/main.py` — FastAPI application root.
- [NEW] `scripts/verify_tracer_bullet.py` — Standalone end-to-end runnable verification script.
- [NEW] `tests/unit/test_state_machine.py` — Valid & invalid state transition tests.
- [NEW] `tests/unit/test_policy_engine.py` — Deterministic authorization tests.
- [NEW] `tests/integration/test_tracer_bullet.py` — End-to-end API-to-completion test.

### Interlude 1: Tracer Bullet Verification Gate
> [!IMPORTANT]
> The single request must flow from API submission to SHA-256 verified `COMPLETED` status.

**CLI Verification Commands:**
```bash
# 1. Run unit tests for FSM, policy, and repository
pytest tests/unit/test_state_machine.py tests/unit/test_policy_engine.py -v

# 2. Run integration test verifying end-to-end execution
pytest tests/integration/test_tracer_bullet.py -v

# 3. Execute standalone CLI tracer bullet script with 5 MB synthetic payload
python scripts/verify_tracer_bullet.py --payload-size-mb 5 --source "univ-lab" --destination "poly-clinic"
```

---

## Stage 2: Deterministic Baseline Schedulers & Resource Accounting

### Objective
Implement multi-policy scheduling with operational priority levels, deadline management, starvation mitigation (priority aging), and queue state management. Compare FIFO, Priority, and Resource-Aware Heuristic policies.

### Preconditions
- Interlude 1 green.

### Concrete Implementation Delta
- [NEW] `app/scheduler/priority.py` — Strict priority-based scheduling with FIFO tie-breaking.
- [NEW] `app/scheduler/resource_aware.py` — Heuristic policy balancing priority, deadline slack, duration, and starvation aging.
- [NEW] `app/services/queue_service.py` — In-memory queue manager tracking eligible requests, wait times, and deadline expirations.
- [NEW] `app/services/scheduling_service.py` — Orchestrator dispatching requests to configured policy and persisting decisions.
- [NEW] `configs/scheduling.yaml` — Configurable weights for deadline slack, priority boost, and starvation aging rate.
- [NEW] `scripts/compare_baselines.py` — CLI benchmark running synthetic queues through FIFO, Priority, and Heuristic policies.
- [NEW] `tests/unit/test_priority_scheduler.py` — Verification of strict priority ordering.
- [NEW] `tests/unit/test_resource_aware_scheduler.py` — Verification of slack time priority and starvation aging.
- [NEW] `tests/integration/test_baseline_scheduling.py` — Comparison test verifying heuristic outperforming FIFO on deadline success.

### Interlude 2: Baseline Schedulers Verification Gate
> [!IMPORTANT]
> Verify that the heuristic prevents starvation while improving deadline satisfaction over naive FIFO.

**CLI Verification Commands:**
```bash
# 1. Run unit test suite for scheduling algorithms
pytest tests/unit/test_priority_scheduler.py tests/unit/test_resource_aware_scheduler.py -v

# 2. Run integration test verifying policy comparison
pytest tests/integration/test_baseline_scheduling.py -v

# 3. Run CLI comparative replay benchmark
python scripts/compare_baselines.py --workload-size 100 --seed 42 --policies FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC
```

---

## Stage 3: High-Performance Discrete-Event Simulation & Parallel Benchmarking

### Objective
Build a discrete-event simulation engine (SimPy-based or custom event queue) with stochastic network conditions, workload generators, simulated QKD key pools, and Python `multiprocessing` parallel experiment runner.

### Preconditions
- Interlude 2 green.

### Concrete Implementation Delta
- [NEW] `simulation/engine.py` — Discrete-event simulator core modeling virtual time, request queues, and link occupancy.
- [NEW] `simulation/network_model.py` — Bandwidth variability, latency jitter, and stochastic link drops.
- [NEW] `simulation/key_resource_model.py` — Simulated QKD key buffer, generation rates, and depletion blocking.
- [NEW] `simulation/workload_generator.py` — Synthetic workload generator producing medical mixes (reports, scans, datasets) via statistical distributions (Poisson, Pareto).
- [NEW] `simulation/scenarios.py` — Scenario definitions (nominal, congested, QKD-constrained, high-failure).
- [NEW] `simulation/parallel_runner.py` — Multi-core HPC runner executing parameter matrices (policies × scenarios × seeds) across CPU cores.
- [NEW] `simulation/aggregation.py` — Statistical aggregator computing mean, median, IQR, 95th percentile completion times, and deadline success rates.
- [NEW] `configs/simulation.yaml` — Default simulation parameters.
- [NEW] `configs/scenarios/medical_mixed_workload.yaml` — Standard benchmark scenario.
- [NEW] `scripts/run_experiments.py` — CLI entrypoint for batch experiment execution.
- [NEW] `scripts/export_results.py` — Exporter to structured CSV/JSON summary reports.
- [NEW] `tests/simulation/test_simulator_determinism.py` — Seeded reproducibility test (same seed -> bit-identical outcomes).
- [NEW] `tests/simulation/test_parallel_runner.py` — Multi-process isolation and aggregation test.

### Interlude 3: Simulation & Parallel Scaling Verification Gate
> [!IMPORTANT]
> 100% deterministic reproduction must be proven across identical seeds. Parallel runner must demonstrate linear scaling across available CPU cores.

**CLI Verification Commands:**
```bash
# 1. Verify simulation determinism and mathematical consistency
pytest tests/simulation/test_simulator_determinism.py tests/simulation/test_workload_generator.py -v

# 2. Run multi-core parallel experiment benchmark (4 workers, 5 seeds, 2 scenarios)
python scripts/run_experiments.py \
    --scenario configs/scenarios/medical_mixed_workload.yaml \
    --workers 4 \
    --seeds 5 \
    --policies FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC \
    --output results/experiments/baseline_matrix.json

# 3. Export aggregated statistical summary table
python scripts/export_results.py \
    --input results/experiments/baseline_matrix.json \
    --output-dir results/reports/
```

---

## Stage 4: AI Telemetry & Predictive Scheduling Engine

### Objective
Implement telemetry observation collection, feature engineering, ML model training pipeline (Linear Regression, Random Forest, Gradient Boosting), model serialization (`joblib`) with version metadata, transfer duration prediction, deadline-miss risk calculation, and the AI-Assisted Scheduling Policy.

### Preconditions
- Interlude 3 green (baseline matrix JSON dataset generated for training).

### Concrete Implementation Delta
- [NEW] `app/telemetry/collector.py` — Real-time telemetry ingestion and snapshot generation.
- [NEW] `app/telemetry/history.py` — Observation logger recording features and actual transfer outcomes.
- [NEW] `app/predictor/features.py` — Feature extractor transforming telemetry + request into normalized `PredictionFeatures`.
- [NEW] `app/predictor/baseline.py` — Analytical size/throughput and historical moving average baselines.
- [NEW] `app/predictor/train.py` — Model training pipeline with cross-validation and feature importance evaluation.
- [NEW] `app/predictor/evaluate.py` — Metric calculator (MAE, RMSE, R2, deadline risk calibration).
- [NEW] `app/predictor/model_registry.py` — Model persistence manager loading/saving `joblib` artifacts with schema metadata.
- [NEW] `app/scheduler/ai_assisted.py` — AI-Assisted Scheduling Policy using predicted duration and deadline risk.
- [NEW] `scripts/train_model.py` — CLI training script with model type selection.
- [NEW] `scripts/evaluate_policy_gain.py` — CLI comparison between AI-assisted policy and heuristic.
- [NEW] `tests/unit/test_feature_extraction.py` — Feature normalization and sentinel handling tests.
- [NEW] `tests/unit/test_predictor_models.py` — Training and inference unit tests.
- [NEW] `tests/integration/test_ai_scheduling.py` — Integration test verifying AI scheduler operates cleanly with fallback on missing features.

### Interlude 4: AI Predictor & Comparative Benchmark Verification Gate
> [!IMPORTANT]
> AI model must achieve lower RMSE than the analytical baseline. The AI-assisted policy must demonstrate equal or superior deadline success over baseline heuristic on test scenarios.

**CLI Verification Commands:**
```bash
# 1. Run unit tests for feature pipeline and models
pytest tests/unit/test_feature_extraction.py tests/unit/test_predictor_models.py -v

# 2. Train gradient boosting predictor from simulated experiment telemetry
python scripts/train_model.py \
    --data results/experiments/baseline_matrix.json \
    --model-type gradient_boosting \
    --output models/duration_predictor_v1.joblib

# 3. Evaluate prediction accuracy vs analytical baseline
python -m app.predictor.evaluate \
    --model models/duration_predictor_v1.joblib \
    --test-data results/experiments/baseline_matrix.json

# 4. Run comparative policy evaluation (AI-Assisted vs Resource-Aware Heuristic vs FIFO)
python scripts/evaluate_policy_gain.py \
    --model models/duration_predictor_v1.joblib \
    --scenario configs/scenarios/medical_mixed_workload.yaml \
    --seeds 5 \
    --output results/experiments/ai_vs_heuristic.json
```

---

## Stage 5: Pluggable QKD Hardware Abstraction & Security Hardening

### Objective
Enforce strict physical/simulated separation: Implement `QKDBaseAdapter`, `QKDSimulatorAdapter` (dynamic buffer, generation rate, depletion stalls), and `QKDRealAdapter` (defensive placeholder raising `QKDNotConfiguredError` until physical credentials exist). Implement tamper-evident audit logging and automated tests verifying zero secret key / PHI leakage in logs or database tables.

### Preconditions
- Interlude 4 green.

### Concrete Implementation Delta
- [NEW] `app/qkd/base.py` — `IQKDAdapter` interface contract.
- [NEW] `app/qkd/simulator.py` — `QKDSimulatorAdapter` with leaky-bucket replenishment, stochastic outage, and seed tracking.
- [NEW] `app/qkd/capabilities.py` — Capability negotiation model (supported key lengths, telemetry fields).
- [NEW] `app/qkd/real_adapter.py` — `QKDRealAdapter` (fails safe, explicitly unconfigured per spec Section 7.2).
- [NEW] `app/security/audit.py` — Cryptographic audit logger with automatic redaction filters.
- [NEW] `app/transfer/crypto_adapter.py` — Pluggable crypto adapter with `QKD_AES_256_GCM` and `QKD_OTP_SIM`.
- [NEW] `scripts/audit_security_compliance.py` — Automated scanner searching logs and SQLite DB for leaked secrets or PHI.
- [NEW] `tests/unit/test_qkd_simulator.py` — Key consumption, buffer replenishment, and depletion blocking tests.
- [NEW] `tests/security/test_security_boundaries.py` — Verifies unauthorized sources/destinations are rejected and policy cannot be overridden by AI.
- [NEW] `tests/security/test_leakage_guards.py` — Injects known honeytokens and verifies they never appear in logs or audit tables.

### Interlude 5: Security Boundary & Zero-Leakage Verification Gate
> [!IMPORTANT]
> Zero-tolerance security gate: Any key material or payload found in audit logs or database tables halts development.

**CLI Verification Commands:**
```bash
# 1. Run security boundary and zero-leakage test suites
pytest tests/security/test_security_boundaries.py tests/security/test_leakage_guards.py -v

# 2. Run QKD simulator and buffer dynamics tests
pytest tests/unit/test_qkd_simulator.py -v

# 3. Execute automated compliance audit on generated test database and logs
python scripts/audit_security_compliance.py \
    --db-path ./qshield.db \
    --log-dir ./logs \
    --strict
```

---

## Stage 6: Operational & Research Dashboard, Statistical Evaluation & Pitch Demo

### Objective
Implement the Streamlit dashboard for both live operational monitoring (queue state, active transfers, QKD link health) and research analytics (cross-policy box plots, completion distributions, deadline success, 95% confidence intervals). Provide an end-to-end interactive demonstration script simulating polyclinic-university transfer.

### Preconditions
- Interlude 5 green.

### Concrete Implementation Delta
- [NEW] `dashboard/app.py` — Streamlit multi-page application shell.
- [NEW] `dashboard/pages/operations.py` — Real-time queue monitor, active transfer progress, and QKD buffer gauges.
- [NEW] `dashboard/pages/experiments.py` — Policy comparison visualizations (box plots, deadline success bar charts, throughput).
- [NEW] `dashboard/pages/model_evaluation.py` — Predictor accuracy charts, feature importance, residual distributions.
- [NEW] `dashboard/components/metrics_cards.py` — KPI metric widgets.
- [NEW] `dashboard/components/charts.py` — Plotly charts for distribution, time-series, and confidence intervals.
- [NEW] `scripts/run_demonstration.py` — Interactive/automated pitch demo executing the 3-tier workload (Diagnostic Report 5 MB, Imaging Study 2 GB, Research Dataset 500 MB) with simulated failure and recovery.
- [NEW] `tests/integration/test_dashboard_data.py` — Headless validation that dashboard queries and aggregations run error-free.

### Interlude 6: Full System End-to-End Acceptance Verification Gate
> [!IMPORTANT]
> Complete system acceptance gate: All unit, integration, simulation, and security tests green; Streamlit starts headlessly; pitch demonstration completes successfully.

**CLI Verification Commands:**
```bash
# 1. Run comprehensive test suite across all modules
pytest tests/ -v --durations=10

# 2. Headless dashboard launch verification
streamlit run dashboard/app.py --server.headless=true --server.port=8501 &
PID=$!
sleep 4
curl -f http://localhost:8501/_stcore/health
kill $PID

# 3. Run full Pitch Demonstration Scenario (University - Polyclinic medical exchange)
python scripts/run_demonstration.py \
    --scenario configs/scenarios/medical_mixed_workload.yaml \
    --duration-seconds 60 \
    --report-output results/demonstration_summary.md
```
