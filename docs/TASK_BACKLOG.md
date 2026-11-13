# Q-SHIELD Atomic Task Backlog & Execution Milestones

This document specifies the sequential, checkbox-driven implementation backlog for **Q-SHIELD**.

Tasks are grouped into sequential milestones matching [ROADMAP_STAGES.md](file:///home/stade/repos/qshield/docs/ROADMAP_STAGES.md). Each task specifies the assigned agent role, file boundaries, acceptance criteria, and a concrete definition of done.

Every milestone terminates with an **Explicit Milestone Verification Interlude** containing runnable CLI commands. **No agent or workflow shall advance to the next milestone until all interlude commands pass green.**

---

## Agent Role Legend (from spec.md §21.1)

- **Agent A**: Architecture and Integration (System boundaries, configuration, CLI scripts, glue)
- **Agent B**: API and Persistence (FastAPI routes, Pydantic schemas, SQLite repositories, state machine)
- **Agent C**: Scheduling and Policy (Policy engine, FIFO, Priority, Heuristic, AI-assisted policy)
- **Agent D**: Simulation and HPC (SimPy/discrete-event engine, network/QKD models, parallel runner, aggregation)
- **Agent E**: AI and Evaluation (Feature engineering, training pipeline, baselines, ML models, metrics)
- **Agent F**: QKD and Transfer Integration (QKD adapters, simulator, crypto adapter, integrity verification)
- **Agent G**: Testing and Quality (Unit, integration, security, zero-leakage verification, test harness)

---

## Milestone 0: Inception, Environment & Tooling

- [x] **TASK-0.1** (Agent A) — Project Packaging & Dependencies
  - **Files**: `pyproject.toml`, `.gitignore`, `.env.example`
  - **Description**: Configure packaging for Python 3.11+, pinning `fastapi`, `uvicorn`, `pydantic>=2.0`, `sqlalchemy>=2.0`, `simpy`, `scikit-learn`, `joblib`, `streamlit`, `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy`. Setup `.gitignore` and `.env.example`.
  - **DoD**: `pip install -e ".[dev]"` succeeds without dependency conflicts.

- [x] **TASK-0.2** (Agent A) — Global Configuration & Logging Setup
  - **Files**: `app/core/config.py`, `configs/app.yaml`
  - **Description**: Implement `Settings` using Pydantic Settings reading environment variables with fallback to `configs/app.yaml`. Configure structured logging that redacts passwords, tokens, and keys.
  - **DoD**: `python -c "from app.core.config import settings; print(settings.APP_ENV)"` outputs `simulation`.

- [x] **TASK-0.3** (Agent G) — Pytest Test Harness & Mock Repositories
  - **Files**: `tests/conftest.py`, `tests/fixtures/mock_repository.py`
  - **Description**: Implement shared Pytest fixtures: in-memory SQLite engine, thread-safe `InMemoryTransferRepository`, mock telemetry generator, and FastAPI test client.
  - **DoD**: `pytest --collect-only` executes and discovers fixtures without errors.

- [x] **TASK-0.4** (Agent G) — Static Linter & Type Check Setup
  - **Files**: `pyproject.toml` (ruff and mypy sections)
  - **Description**: Configure strict Ruff linting and Mypy static type checking across `app` and `tests`.
  - **DoD**: `ruff check .` and `mypy app tests` run cleanly with zero errors.

### Milestone 0 Verification Interlude
```bash
# Run environment and static quality checks
ruff check .
mypy app tests
pytest --collect-only
```

---

## Milestone 1: Minimal Working Tracer Bullet

- [x] **TASK-1.1** (Agent A) — Core Enums, State Machine & Domain Exceptions
  - **Files**: `app/core/enums.py`, `app/core/exceptions.py`, `app/core/state_machine.py`
  - **Description**: Implement `PriorityLevel`, `TransferStatus`, `DataCategory`, `SecurityProfile`. Build the `TransferStateMachine` class enforcing the deterministic lifecycle transitions specified in `docs/ARCHITECTURE_CONTRACTS.md`.
  - **DoD**: Invalid state transitions (e.g. `COMPLETED -> QUEUED` or `SUBMITTED -> TRANSFERRING`) raise `InvalidStateTransitionError`.

- [x] **TASK-1.2** (Agent B) — Domain Models & SQLite Schema
  - **Files**: `app/models/transfer_request.py`, `app/models/policy_decision.py`, `app/database/connection.py`, `app/database/schema.py`
  - **Description**: Implement Pydantic domain models with PHI validation guards. Define SQLAlchemy ORM tables (`transfer_requests`, `state_transitions`, `policy_decisions`). Create database engine and session factory.
  - **DoD**: SQLite schema creates all tables in memory or file without warnings.

- [x] **TASK-1.3** (Agent B) — Transfer Repository CRUD Implementation
  - **Files**: `app/database/repository.py`
  - **Description**: Implement `ITransferRepository` interface for SQLite: `create_request`, `get_request`, `update_status` (recording an immutable transition record), `list_requests` with pagination and status filters.
  - **DoD**: Unit tests verify creating and retrieving requests preserves all timestamp and UUID fields.

- [x] **TASK-1.4** (Agent C) — Deterministic Security Policy Engine
  - **Files**: `app/security/policy_engine.py`, `app/security/authorization.py`, `configs/security.yaml`
  - **Description**: Implement `IPolicyEngine` with strict source/destination authorization tables and security profile validation. Returns `PolicyDecision` object with reason codes (`AUTH_SUCCESS`, `DEST_UNAUTHORIZED`, etc.).
  - **DoD**: Unauthorized destination returns `allowed=False` with `reason_code="DEST_UNAUTHORIZED"`.

- [x] **TASK-1.5** (Agent C) — FIFO Scheduler Implementation
  - **Files**: `app/scheduler/base.py`, `app/scheduler/fifo.py`
  - **Description**: Implement `IScheduler` interface and `FIFOScheduler`. Filters queue for policy-eligible requests and selects the oldest request by `submitted_at`. Returns structured `SchedulingDecision`.
  - **DoD**: Candidate requests submitted at $t_1, t_2, t_3$ are always selected in submission order.

- [x] **TASK-1.6** (Agent F) — Transfer Integrity & Synthetic Transfer Executor
  - **Files**: `app/transfer/base.py`, `app/transfer/integrity.py`, `app/transfer/simulator.py`, `app/transfer/executor.py`
  - **Description**: Implement SHA-256 payload checksum generator/validator (`IntegrityVerifier`). Implement `SyntheticTransferExecutor` that generates a synthetic buffer, calculates SHA-256, simulates transfer time, and verifies integrity upon completion.
  - **DoD**: Valid synthetic transfer returns `success=True` and `verification_success=True`. Artificially corrupted checksum returns `success=False`.

- [x] **TASK-1.7** (Agent B) — FastAPI Routes & Main Entrypoint
  - **Files**: `app/api/schemas.py`, `app/api/routes_transfers.py`, `app/main.py`
  - **Description**: Build FastAPI application with `/api/v1/transfers` routes (`POST` create, `GET` by ID, `GET` list, `POST` cancel). Wrap responses in `ApiResponse[T]` envelope. Include global exception handlers.
  - **DoD**: HTTP client can POST a transfer and receive a 201 Created with valid JSON matching `ApiResponse[TransferRequest]`.

- [x] **TASK-1.8** (Agent G) — Tracer Bullet Unit & Integration Test Suite
  - **Files**: `tests/unit/test_state_machine.py`, `tests/unit/test_policy_engine.py`, `tests/integration/test_tracer_bullet.py`
  - **Description**: Write comprehensive tests for FSM transitions, policy authorization, and end-to-end integration: `POST /transfers -> VALIDATING -> QUEUED -> SCHEDULED -> TRANSFERRING -> VERIFYING -> COMPLETED`.
  - **DoD**: `pytest tests/integration/test_tracer_bullet.py` passes 100%.

- [x] **TASK-1.9** (Agent A) — Standalone Tracer Bullet CLI Script
  - **Files**: `scripts/verify_tracer_bullet.py`
  - **Description**: Create runnable verification CLI script that executes the complete lifecycle for a configurable payload size and prints step-by-step progress and SHA-256 checksums to stdout.
  - **DoD**: Running script exits with returncode 0 and prints `TRACER BULLET COMPLETE: OK`.

### Milestone 1 Verification Interlude
```bash
# 1. Unit tests for FSM and Policy
pytest tests/unit/test_state_machine.py tests/unit/test_policy_engine.py -v

# 2. Integration test for complete tracer bullet
pytest tests/integration/test_tracer_bullet.py -v

# 3. CLI script execution
python scripts/verify_tracer_bullet.py --payload-size-mb 5 --source "univ-lab" --destination "poly-clinic"
```

---

## Milestone 2: Deterministic Baseline Schedulers & Resource Accounting

- [x] **TASK-2.1** (Agent C) — Priority-Based Scheduler
  - **Files**: `app/scheduler/priority.py`
  - **Description**: Implement `PriorityScheduler`. Sorts eligible requests by `priority` (CRITICAL > HIGH > NORMAL > LOW) with FIFO tie-breaking on `submitted_at`.
  - **DoD**: CRITICAL request submitted later takes precedence over earlier NORMAL request.

- [x] **TASK-2.2** (Agent A) — Queue Service & Deadline Tracking
  - **Files**: `app/services/queue_service.py`
  - **Description**: Implement queue management service. Evaluates pending requests, computes current wait times, detects deadline expirations, and transitions expired requests to `EXPIRED` status.
  - **DoD**: Requests with past deadlines are transitioned to `EXPIRED` and excluded from scheduling.

- [x] **TASK-2.3** (Agent C) — Resource-Aware Heuristic Scheduler with Starvation Aging
  - **Files**: `app/scheduler/resource_aware.py`, `configs/scheduling.yaml`
  - **Description**: Implement `ResourceAwareHeuristicScheduler`. Scoring function balances priority weight, deadline slack ($t_{\text{deadline}} - t_{\text{now}}$), and starvation aging:
    $$\text{score} = w_p \cdot \text{priority} - w_s \cdot \text{slack\_seconds} + w_a \cdot \text{wait\_time\_seconds}$$
    Enforces maximum consecutive high-priority selections before granting service to aged low-priority requests.
  - **DoD**: Starving LOW-priority request eventually out-scores a newly arrived HIGH-priority request after configured aging threshold.

- [x] **TASK-2.4** (Agent C) — Scheduling Dispatch Service
  - **Files**: `app/services/scheduling_service.py`
  - **Description**: Service that coordinates queue inspection, selects active policy (FIFO, Priority, Heuristic), executes scheduling step, records `SchedulingDecision` to repository, and triggers execution.
  - **DoD**: Service logs structured scheduling decisions with duration and rationale to SQLite.

- [x] **TASK-2.5** (Agent G) — Baseline Schedulers Unit & Comparative Tests
  - **Files**: `tests/unit/test_priority_scheduler.py`, `tests/unit/test_resource_aware_scheduler.py`, `tests/integration/test_baseline_scheduling.py`
  - **Description**: Unit tests covering priority sorting, slack prioritization, and starvation mitigation. Integration test comparing all three policies on identical 50-request queue.
  - **DoD**: `pytest tests/unit/test_resource_aware_scheduler.py` passes 100%.

- [x] **TASK-2.6** (Agent A) — Baseline Policy Comparison CLI Tool
  - **Files**: `scripts/compare_baselines.py`, `configs/scenarios/baseline_test.yaml`
  - **Description**: CLI script that ingests a test workload, simulates queue execution under FIFO, Priority, and Heuristic policies, and outputs a formatted table comparing mean latency, deadline miss count, and max starvation wait time.
  - **DoD**: `python scripts/compare_baselines.py --workload configs/scenarios/baseline_test.yaml` executes and prints comparison summary.

### Milestone 2 Verification Interlude
```bash
# 1. Scheduler unit tests
pytest tests/unit/test_priority_scheduler.py tests/unit/test_resource_aware_scheduler.py -v

# 2. Integration comparative test
pytest tests/integration/test_baseline_scheduling.py -v

# 3. CLI comparison script
python scripts/compare_baselines.py --workload-size 100 --seed 42 --policies FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC
```

---

## Milestone 3: High-Performance Discrete-Event Simulation & Parallel Benchmarking

- [x] **TASK-3.1** (Agent D) — Network Variability & Latency Model
  - **Files**: `simulation/network_model.py`
  - **Description**: Implement network simulation module supporting configurable baseline throughput, Gaussian throughput variability, RTT propagation delay, and stochastic packet drop events.
  - **DoD**: Deterministic throughput generation when configured with fixed seed.

- [x] **TASK-3.2** (Agent D) — Simulated Key Resource Buffer
  - **Files**: `simulation/key_resource_model.py`
  - **Description**: Discrete key-pool model tracking available 256-bit keys, replenishment rate ($R_{\text{gen}}$), buffer ceiling, and key consumption per transfer. Blocks or throttles transfers when key buffer is depleted.
  - **DoD**: Buffer depletes when consumption exceeds generation; transfers pause until replenishment occurs.

- [x] **TASK-3.3** (Agent D) — Workload Generator & Scenario Configurations
  - **Files**: `simulation/workload_generator.py`, `simulation/scenarios.py`, `configs/scenarios/medical_mixed_workload.yaml`
  - **Description**: Implement synthetic medical workload generator. Generates requests using Poisson arrivals, Pareto payload distributions, and realistic medical categories (5 MB diagnostic reports, 2 GB scans, 500 MB research sets).
  - **DoD**: Generator produces identical request sets given identical seeds.

- [x] **TASK-3.4** (Agent D) — Discrete-Event Simulator Core
  - **Files**: `simulation/engine.py`
  - **Description**: Discrete-event engine running virtual-time simulations. Orchestrates request arrivals, queueing, scheduling policy decisions, network transfer delays, and completion events without sleeping real CPU time.
  - **DoD**: 1,000 transfer requests simulate in under 5 seconds of real execution time.

- [x] **TASK-3.5** (Agent D) — Multi-Core Parallel Experiment Runner
  - **Files**: `simulation/parallel_runner.py`
  - **Description**: HPC parallel execution harness using Python `multiprocessing.Pool`. Distributes independent (scenario × policy × seed) simulation jobs across available CPU cores with isolated random states.
  - **DoD**: Runner distributes 20 jobs across 4 cores, achieves >3x speedup over sequential execution, and records individual run outcomes.

- [x] **TASK-3.6** (Agent D) — Experiment Result Aggregator & Exporter
  - **Files**: `simulation/aggregation.py`, `scripts/export_results.py`
  - **Description**: Statistical analysis module calculating mean, median, IQR, 95th percentile completion times, deadline success rates, and relative improvements. Exports formatted CSV and JSON reports.
  - **DoD**: Aggregates 100 simulation runs into summary metrics with valid 95% confidence intervals.

- [x] **TASK-3.7** (Agent G) — Simulation Determinism & Parallel Scaling Tests
  - **Files**: `tests/simulation/test_simulator_determinism.py`, `tests/simulation/test_workload_generator.py`, `tests/simulation/test_parallel_runner.py`, `scripts/run_experiments.py`
  - **Description**: Test suite verifying that identical seeds yield bit-for-bit identical results and parallel runs do not leak state. Build `scripts/run_experiments.py` CLI tool.
  - **DoD**: `pytest tests/simulation/` passes 100%.

### Milestone 3 Verification Interlude
```bash
# 1. Run simulation test suite
pytest tests/simulation/test_simulator_determinism.py tests/simulation/test_workload_generator.py -v

# 2. Run multi-core parallel experiment matrix
python scripts/run_experiments.py \
    --scenario configs/scenarios/medical_mixed_workload.yaml \
    --workers 4 \
    --seeds 5 \
    --policies FIFO,PRIORITY,RESOURCE_AWARE_HEURISTIC \
    --output results/experiments/baseline_matrix.json

# 3. Export aggregated results report
python scripts/export_results.py \
    --input results/experiments/baseline_matrix.json \
    --output-dir results/reports/
```

---

## Milestone 4: AI Telemetry & Predictive Scheduling Engine

- [x] **TASK-4.1** (Agent B) — Telemetry Collector & Historical Observation Store
  - **Files**: `app/telemetry/collector.py`, `app/telemetry/history.py`, `app/telemetry/metrics.py`
  - **Description**: Implement real-time telemetry snapshot builder (`TelemetrySnapshot`) aggregating active transfers, queue depth, rolling throughput, and QKD metrics. Record completed transfer records to SQLite historical store.
  - **DoD**: Telemetry snapshot reflects active transfers in real time.

- [x] **TASK-4.2** (Agent E) — Feature Engineering & Normalization Pipeline
  - **Files**: `app/predictor/features.py`
  - **Description**: Transform `TransferRequest` and `TelemetrySnapshot` into normalized `PredictionFeatures`. Handle missing optional QKD metrics explicitly with sentinel values or imputation flags.
  - **DoD**: Unit test confirms consistent vector dimensions and handles `None` QKD fields without throwing exceptions.

- [x] **TASK-4.3** (Agent E) — Analytical & Historical Baseline Predictors
  - **Files**: `app/predictor/baseline.py`
  - **Description**: Implement analytical estimator ($\text{size} \times 8 / \text{throughput}$) and historical moving-average baseline conforming to `IPredictor`.
  - **DoD**: Analytical predictor computes correct transfer durations for known sizes and bandwidths.

- [x] **TASK-4.4** (Agent E) — ML Training Pipeline & Regressors
  - **Files**: `app/predictor/train.py`, `app/predictor/evaluate.py`
  - **Description**: Build training pipeline using `scikit-learn` supporting Ridge Regression, Random Forest, and Gradient Boosting Regressors. Implement cross-validation, train/test split by seed to avoid leakage, and compute MAE, RMSE, and $R^2$.
  - **DoD**: Pipeline trains on JSON simulation dataset and achieves $R^2 > 0.85$ on test set.

- [x] **TASK-4.5** (Agent E) — Model Registry & Artifact Serialization
  - **Files**: `app/predictor/model_registry.py`, `scripts/train_model.py`
  - **Description**: Save/load trained models with `joblib`. Include metadata sidecar (`model_version`, feature schema, training date, hyperparameters, evaluation metrics). Provide CLI script `scripts/train_model.py`.
  - **DoD**: CLI script trains and serializes model; registry loads model and successfully predicts against new feature vector.

- [x] **TASK-4.6** (Agent C) — AI-Assisted Scheduling Policy
  - **Files**: `app/scheduler/ai_assisted.py`
  - **Description**: Implement `AIAssistedScheduler`. Uses predicted duration and predicted deadline-miss probability:
    $$\text{utility} = \alpha \cdot \text{priority} + \beta \cdot (1 - P(\text{miss})) - \gamma \cdot \text{duration}_{\text{pred}} + \delta \cdot \text{age}$$
    Gracefully falls back to analytical baseline if the ML model is unavailable. Deterministic security policy is strictly preserved.
  - **DoD**: Scheduler ranks candidate requests using model predictions while strictly respecting policy eligibility.

- [x] **TASK-4.7** (Agent G) — AI Model & Policy Integration Tests
  - **Files**: `tests/unit/test_feature_extraction.py`, `tests/unit/test_predictor_models.py`, `tests/integration/test_ai_scheduling.py`
  - **Description**: Test feature extraction edge cases, model inference speed (<10 ms per decision), model fallback behavior, and comparative deadline satisfaction.
  - **DoD**: `pytest tests/integration/test_ai_scheduling.py` passes 100%.

- [x] **TASK-4.8** (Agent E) — Comparative Policy Evaluation CLI Tool
  - **Files**: `scripts/evaluate_policy_gain.py`
  - **Description**: CLI script running identical workloads through Heuristic vs AI-Assisted policies and outputting comparative deadline success rate, MAE, and throughput improvements.
  - **DoD**: Script completes multi-seed comparison and outputs JSON/markdown summary.

### Milestone 4 Verification Interlude
```bash
# 1. Run predictor unit tests
pytest tests/unit/test_feature_extraction.py tests/unit/test_predictor_models.py tests/integration/test_ai_scheduling.py -v

# 2. Train gradient boosting predictor from Stage 3 baseline matrix
python scripts/train_model.py \
    --data results/experiments/baseline_matrix.json \
    --model-type gradient_boosting \
    --output models/duration_predictor_v1.joblib

# 3. Evaluate prediction accuracy vs baselines
python -m app.predictor.evaluate \
    --model models/duration_predictor_v1.joblib \
    --test-data results/experiments/baseline_matrix.json

# 4. Run comparative policy evaluation (AI-Assisted vs Baseline Heuristic)
python scripts/evaluate_policy_gain.py \
    --model models/duration_predictor_v1.joblib \
    --scenario configs/scenarios/medical_mixed_workload.yaml \
    --seeds 5 \
    --output results/experiments/ai_vs_heuristic.json
```

---

## Milestone 5: Pluggable QKD Hardware Abstraction & Security Hardening

- [ ] **TASK-5.1** (Agent F) — QKD Adapter Abstract Interface & Capabilities
  - **Files**: `app/qkd/base.py`, `app/qkd/capabilities.py`
  - **Description**: Define `IQKDAdapter` abstract base class with `get_status()`, `get_resource_metrics()`, and `acquire_key_handle()`. Define `QKDCapabilities` negotiation schema.
  - **DoD**: Abstract interface enforces signature contracts without exposing raw key bytes.

- [ ] **TASK-5.2** (Agent F) — Leaky-Bucket QKD Simulator Adapter
  - **Files**: `app/qkd/simulator.py`
  - **Description**: Implement `QKDSimulatorAdapter` with configurable initial key count, replenishment rate, buffer limit, and simulated temporary outages. Returns opaque `KeyHandle` objects.
  - **DoD**: Requests when buffer is 0 return status indicating resource wait; replenish restores key availability.

- [ ] **TASK-5.3** (Agent F) — Defensive Real QKD Adapter Placeholder
  - **Files**: `app/qkd/real_adapter.py`
  - **Description**: Implement `QKDRealAdapter`. Enforces spec §7.2: raises explicit `QKDHardwareNotConfiguredError` with instructions on physical credentials and endpoints. Does NOT contain fabricated endpoints.
  - **DoD**: Attempting to initialize without authorized hardware config throws expected exception with informative guidance.

- [ ] **TASK-5.4** (Agent F) — Pluggable Transport Crypto Adapter
  - **Files**: `app/transfer/crypto_adapter.py`
  - **Description**: Implement `CryptoAdapter` supporting `QKD_AES_256_GCM` (using key derived from `KeyHandle`) and `QKD_OTP_SIM`. Performs encryption, decryption, and SHA-256 verification.
  - **DoD**: Roundtrip encrypt and decrypt produces identical plaintext bytes and identical SHA-256 hash.

- [ ] **TASK-5.5** (Agent A) — Cryptographic Audit Logger & Automatic Redaction
  - **Files**: `app/security/audit.py`
  - **Description**: Implement append-only `AuditLogger` writing to SQLite `audit_events`. Pydantic validator automatically scrubs and redacts any key, secret, or payload terms from log parameters.
  - **DoD**: Audit event containing `"key_bytes": "deadbeef"` is saved as `"[REDACTED_BY_AUDIT_POLICY]"`.

- [ ] **TASK-5.6** (Agent G) — Security Boundary & Zero-Leakage Test Suite
  - **Files**: `tests/security/test_security_boundaries.py`, `tests/security/test_leakage_guards.py`
  - **Description**: Test that unauthorized sources/destinations are rejected, AI predictions cannot override policy decisions, and no raw keys or synthetic medical data appear in logs.
  - **DoD**: `pytest tests/security/` passes 100%.

- [ ] **TASK-5.7** (Agent A) — Automated Compliance Audit CLI Tool
  - **Files**: `scripts/audit_security_compliance.py`
  - **Description**: Automated scanner that reads log files and SQLite database tables searching for regex patterns matching private keys, secrets, or unredacted payload markers. Exits non-zero if any leak is detected.
  - **DoD**: Running script on test database returns returncode 0 with message `AUDIT CLEAN: Zero secrets or PHI detected`.

### Milestone 5 Verification Interlude
```bash
# 1. Run security boundaries and leakage test suite
pytest tests/security/test_security_boundaries.py tests/security/test_leakage_guards.py -v

# 2. Run QKD adapter unit tests
pytest tests/unit/test_qkd_simulator.py -v

# 3. Execute automated compliance audit on generated DB and logs
python scripts/audit_security_compliance.py \
    --db-path ./qshield.db \
    --log-dir ./logs \
    --strict
```

---

## Milestone 6: Operational & Research Dashboard, Statistical Evaluation & Pitch Demo

- [ ] **TASK-6.1** (Agent B) — Streamlit Dashboard & Operational Monitor
  - **Files**: `dashboard/app.py`, `dashboard/pages/operations.py`, `dashboard/components/metrics_cards.py`
  - **Description**: Build Streamlit multi-page interface. Implement Operations page displaying live transfer queue, active transfer progress bars, QKD key buffer gauges, and recent completion status.
  - **DoD**: Streamlit page loads data from API or SQLite repository without exceptions.

- [ ] **TASK-6.2** (Agent D) — Research Experiment Analytics Page
  - **Files**: `dashboard/pages/experiments.py`, `dashboard/components/charts.py`
  - **Description**: Implement Experiments page displaying policy comparison charts (box plots of transfer duration, deadline success rate bars, throughput over time, and starvation wait time histograms).
  - **DoD**: Page visualizes benchmark results from `results/experiments/*.json`.

- [ ] **TASK-6.3** (Agent E) — Predictor Performance & Feature Importance View
  - **Files**: `dashboard/pages/model_evaluation.py`
  - **Description**: Build Model Evaluation page displaying duration prediction scatter plots (actual vs. predicted), MAE/RMSE metrics, and feature importance bar charts.
  - **DoD**: Streamlit page loads `joblib` model artifact and renders evaluation curves.

- [ ] **TASK-6.4** (Agent D) — Statistical Reporting & Confidence Intervals Tool
  - **Files**: `scripts/export_results.py`, `simulation/aggregation.py`
  - **Description**: Enhance result exporter to produce publication-ready LaTeX/Markdown tables and compute 95% confidence intervals via bootstrapping.
  - **DoD**: `python scripts/export_results.py --format markdown` outputs valid markdown table with $\pm 95\%$ CI bounds.

- [ ] **TASK-6.5** (Agent A) — End-to-End Pitch Demonstration Script
  - **Files**: `scripts/run_demonstration.py`
  - **Description**: Build interactive pitch demonstration scenario simulating University-Polyclinic exchange: executes the 3-tier workload (5 MB report, 2 GB MRI scan, 500 MB dataset), injects a simulated network drop/QKD depletion, demonstrates retry/recovery, and displays policy comparison summary.
  - **DoD**: `python scripts/run_demonstration.py --scenario configs/scenarios/medical_mixed_workload.yaml` executes cleanly and outputs formatted demonstration summary.

- [ ] **TASK-6.6** (Agent G) — System-Wide Integration Tests & Acceptance Gate
  - **Files**: `tests/integration/test_dashboard_data.py`, full test suite execution
  - **Description**: Test dashboard data aggregation queries headlessly. Execute complete test suite verifying zero regressions across all modules.
  - **DoD**: All unit, integration, simulation, and security test suites pass.

### Milestone 6 Verification Interlude
```bash
# 1. Run complete comprehensive test suite
pytest tests/ -v --durations=10

# 2. Verify Streamlit dashboard starts headlessly
streamlit run dashboard/app.py --server.headless=true --server.port=8501 &
DASHBOARD_PID=$!
sleep 4
curl -f http://localhost:8501/_stcore/health
kill $DASHBOARD_PID

# 3. Run full Pitch Demonstration Scenario
python scripts/run_demonstration.py \
    --scenario configs/scenarios/medical_mixed_workload.yaml \
    --duration-seconds 60 \
    --report-output results/demonstration_summary.md
```
