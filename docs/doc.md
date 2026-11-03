# Q-SHIELD
## AI-Driven Orchestration for Quantum-Secured Medical Data Exchange

**Project type:** Research-oriented software system / HPC / AI / Quantum Communication  
**Primary language:** Python  
**Initial deployment context:** University–polyclinic QKD infrastructure  
**Development approach:** Agent-assisted, modular implementation  
**Data policy:** Synthetic medical data for development and experimentation  
**Status:** Implementation phase

---

# 1. Project Overview

Q-SHIELD is a resource-aware orchestration system designed to optimize the transfer of sensitive data over networks that use Quantum Key Distribution (QKD) for cryptographic key establishment.

The system operates as a user-space application that manages transfer requests, evaluates their urgency and resource requirements, predicts transfer behavior, and schedules eligible transfers according to a combination of security policies, network conditions, and operational objectives.

The initial application context is the exchange of medical data between a university and a polyclinic. Examples include medical imaging studies, diagnostic reports, and research datasets.

The system is designed to be extensible to other QKD-enabled environments, such as research institutions, financial infrastructure, and other organizations requiring secure, deadline-aware data exchange.

The central idea is not to use AI merely to predict a transfer duration. Instead, Q-SHIELD uses prediction as one component of a broader scheduling and optimization system.

The project investigates whether predictive, resource-aware scheduling can improve transfer efficiency and deadline satisfaction compared with conventional scheduling policies.

## 1.1 Core Research Question

Can AI-assisted scheduling improve the performance of data exchange over QKD-enabled networks while preserving strict security, integrity, and access-control requirements?

The project will evaluate this question through controlled experiments, comparing AI-assisted scheduling against deterministic baseline policies.

## 1.2 Core Design Principle

**Security is enforced deterministically. AI is used only to optimize decisions within the set of permitted actions.**

The AI component must never be able to:

- Bypass authorization.
- Select an unauthorized destination.
- Weaken a required cryptographic profile.
- Retrieve or expose secret key material.
- Override security policy decisions.
- Mark an incomplete or unverified transfer as successful.

---

# 2. Problem Definition

In a conventional data-transfer system, requests are often processed using relatively simple scheduling strategies, such as:

- First-In-First-Out (FIFO).
- Fixed priority levels.
- Static bandwidth allocation.
- Basic resource-aware heuristics.

These approaches can be insufficient when transfer requests have different operational requirements.

For example:

- A small diagnostic report may need to reach a medical professional within seconds.
- A large medical imaging study may have a deadline of several minutes.
- A research dataset may be large but have a much lower urgency.
- Network throughput may fluctuate.
- QKD key-service availability may affect whether a transfer can proceed under the required security profile.

The scheduler must therefore consider multiple competing objectives:

1. Minimize transfer completion time.
2. Maximize the number of requests completed before their deadlines.
3. Avoid starvation of lower-priority requests.
4. Respect available network resources.
5. Respect the constraints of the cryptographic and key-management infrastructure.
6. Maintain integrity and verifiable transfer completion.
7. Minimize scheduling overhead.

The problem is a constrained scheduling problem with uncertain execution times and potentially dynamic resources.

---

# 3. Project Objectives

## 3.1 Primary Objectives

Q-SHIELD must:

1. Provide an API for submitting and managing transfer requests.
2. Maintain a queue of pending requests.
3. Enforce deterministic security and authorization policies.
4. Collect transfer and resource telemetry.
5. Predict transfer duration and deadline-miss risk.
6. Schedule requests using configurable scheduling policies.
7. Execute transfers through a pluggable transport and cryptographic interface.
8. Support a QKD simulator for development and controlled experiments.
9. Support integration with a real QKD system when its authorized interface is available.
10. Evaluate scheduling performance against conventional baselines.
11. Provide reproducible experiments using synthetic workloads.
12. Expose operational metrics through a dashboard or API.

## 3.2 Secondary Objectives

Where feasible, the system should also:

- Support multiple destinations.
- Support configurable security profiles.
- Simulate variable network conditions.
- Simulate key-resource availability.
- Support parallel experiment execution across CPU cores.
- Provide experiment result export in CSV or JSON.
- Support replaying the same workload under different scheduling policies.
- Provide a clear separation between simulation and real infrastructure modes.

## 3.3 Non-Goals

The initial implementation is not intended to:

- Implement a QKD protocol.
- Replace the QKD hardware or vendor key-management system.
- Implement a new cryptographic algorithm.
- Replace a hospital's existing medical information system.
- Process real patient data.
- Build a general-purpose distributed storage platform.
- Modify the operating system kernel.
- Guarantee that QKD makes an entire communication system secure.
- Claim universal compatibility with every QKD implementation.
- Use AI to make security-critical authorization decisions.

---

# 4. System Architecture

Q-SHIELD is organized into distinct components with explicit interfaces.

```text
                    ┌──────────────────────────┐
                    │      Client / User       │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │       FastAPI API        │
                    │ Request submission/status│
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │   Request Queue / DB     │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │   Security Policy Engine │
                    │ Authorization / Profiles│
                    └────────────┬─────────────┘
                                 │
                         Eligible requests
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │      AI Predictor        │
                    │ Duration / Risk Estimate │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │    Scheduling Engine     │
                    │ Priority / Resources /   │
                    │ Deadlines / Constraints  │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │    Transfer Executor     │
                    └────────────┬─────────────┘
                                 │
                   ┌─────────────┴─────────────┐
                   │                           │
                   ▼                           ▼
        ┌────────────────────┐      ┌────────────────────┐
        │ QKD / Key Service  │      │ Transport / Crypto │
        │ Adapter            │      │ Adapter            │
        └────────────────────┘      └────────────────────┘
                   │                           │
                   └─────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │ Receiver / Verification  │
                    │ Integrity / Completion   │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │ Metrics / Audit / Events │
                    └──────────────────────────┘
```

The architecture must support two operational modes:

### Simulation Mode

The system uses simulated network conditions, synthetic requests, and a simulated QKD/key-resource interface.

This mode is the primary environment for development, testing, and repeatable benchmarking.

### Real Infrastructure Mode

The system uses authorized interfaces to communicate with the available QKD infrastructure.

Real infrastructure mode must only be enabled after the QKD interface, transport mechanism, key-management behavior, and permitted test procedures have been confirmed.

The simulation and real modes must share the same core scheduling and policy interfaces wherever practical.

---

# 5. Transfer Request Model

Every transfer request must be represented by a structured object.

The initial request model should contain the following fields:

| Field | Type | Description |
|---|---|---|
| `request_id` | UUID | Unique identifier |
| `source_id` | String | Logical source identifier |
| `destination_id` | String | Authorized destination |
| `data_size_bytes` | Integer | Expected payload size |
| `priority` | Integer or enum | Operational priority |
| `submitted_at` | Timestamp | Submission time |
| `deadline` | Timestamp or null | Optional completion deadline |
| `security_profile` | String | Required cryptographic profile |
| `data_category` | Enum | Synthetic report, imaging, research, etc. |
| `status` | Enum | Current lifecycle status |
| `retry_count` | Integer | Number of retries |
| `metadata` | JSON object | Non-sensitive extension fields |

The request must not contain actual medical content in its scheduling metadata.

For the initial implementation, use synthetic payloads generated locally.

## 5.1 Priority Levels

Define configurable priority levels, for example:

- `CRITICAL`
- `HIGH`
- `NORMAL`
- `LOW`

Priority must be treated as an operational scheduling attribute rather than as a security permission.

A high-priority request is not automatically authorized.

## 5.2 Request Lifecycle

The request lifecycle should be explicit:

```text
SUBMITTED
    │
    ▼
VALIDATING
    │
    ├── INVALID ─────────────► REJECTED
    │
    ▼
QUEUED
    │
    ▼
SCHEDULED
    │
    ▼
TRANSFERRING
    │
    ├── RETRYABLE ERROR ────► QUEUED
    ├── NON-RETRYABLE ERROR ► FAILED
    │
    ▼
VERIFYING
    │
    ├── VERIFICATION ERROR ─► FAILED
    │
    ▼
COMPLETED
```

The implementation should also account for cancellation and deadline expiration.

The exact state transitions must be centrally defined and tested.

---

# 6. Security and Policy Engine

The security policy engine is a deterministic component.

It must run before scheduling and before any transfer execution.

The policy engine determines whether a request is eligible to proceed.

## 6.1 Responsibilities

The policy engine must validate:

- Whether the source is authorized.
- Whether the destination is authorized.
- Whether the requested security profile is permitted.
- Whether the required transport mechanism is available.
- Whether the request complies with configured security constraints.
- Whether the request is permitted to use the selected key-management interface.

The policy engine should return a structured decision:

```text
PolicyDecision:
    allowed: boolean
    reason_code: string
    explanation: string
    required_security_profile: string
```

## 6.2 Security Constraints

The following rules are mandatory:

1. The scheduler must only receive eligible requests.
2. AI predictions cannot alter policy decisions.
3. Secret key material must never be written to logs.
4. Medical payloads must not be written to ordinary application logs.
5. Request metadata must be minimized.
6. Transfer completion must require integrity verification.
7. Security failures must be recorded without exposing sensitive data.
8. QKD key retrieval must occur only through an authorized interface.
9. The system must not invent key availability when operating against a real QKD system.
10. Simulation data must be clearly distinguished from real telemetry.

## 6.3 Cryptographic Model

QKD provides key material through a key-management interface. It does not itself encrypt arbitrary files.

The transfer executor must use a separately defined cryptographic and transport mechanism.

The implementation must not assume that one byte of QKD key material is required for every byte of transferred data.

The key consumption model depends on the cryptographic scheme:

- One-time pad: key consumption is approximately equal to the protected message length.
- Conventional symmetric encryption: key consumption is typically much smaller than the data volume, depending on the key-management and encryption design.
- Other systems may use QKD-derived keys for session establishment or key rotation.

The actual model must be documented based on the available infrastructure.

If the QKD provider's cryptographic integration is unknown, the initial simulator must expose an abstract key-resource model rather than make unsupported claims about actual key consumption.

---

# 7. QKD Integration Layer

Q-SHIELD must use a pluggable QKD adapter.

The core scheduler must not depend directly on a particular vendor's API.

## 7.1 Adapter Interface

Define an abstract interface such as:

```python
class QKDAdapter:
    def get_status(self, source_id: str, destination_id: str):
        ...

    def get_resource_metrics(self, source_id: str, destination_id: str):
        ...

    def request_key(self, source_id: str, destination_id: str, key_id: str):
        ...
```

This is an initial conceptual interface. The final method signatures must be adapted to the actual provider's API and the cryptographic integration model.

The interface must distinguish between:

- Resource status.
- Optional telemetry.
- Authorized key retrieval.
- Errors and unavailable capabilities.

The adapter must never expose secret key values to the scheduler or predictor.

Where key retrieval is required, it should be mediated by the approved cryptographic or transport component.

## 7.2 Required Implementations

### `QKDBaseAdapter`

Defines the common interface.

### `QKDSimulatorAdapter`

Provides simulated key-resource behavior for experiments.

It should support:

- Configurable initial resource availability.
- Resource replenishment.
- Resource consumption according to a configurable model.
- Temporary unavailability.
- Simulated errors.
- Optional stochastic behavior.
- Deterministic behavior using a random seed.

### `QKDRealAdapter`

Integrates with the available QKD infrastructure.

This component must remain disabled until the vendor/API, permissions, and test procedure are known.

It must not contain fabricated API endpoints, guessed request formats, or placeholder behavior presented as functional integration.

If the real interface cannot be used, the project must still function in simulation mode.

## 7.3 QKD Telemetry

The system may use the following telemetry when available:

- Link status.
- Key generation rate.
- Key availability or buffer level.
- Key service latency.
- Key retrieval success rate.
- Key-resource replenishment rate.
- QKD service errors.

Not all QKD systems expose all of these metrics.

Every telemetry field must be optional and carry metadata describing its source and validity.

The scheduler must not depend on a telemetry field that is unavailable in the real infrastructure.

---

# 8. Network and Transfer Telemetry

The telemetry component collects operational measurements needed by the predictor and scheduler.

Potential measurements include:

- Observed throughput.
- Transfer duration.
- Transfer size.
- Queue length.
- Number of active transfers.
- Link status.
- Recent transfer failures.
- Time remaining until deadline.
- Estimated available bandwidth.
- QKD resource measurements, when available.

The telemetry layer should provide:

1. A normalized internal representation.
2. Timestamps.
3. Source identifiers.
4. Validation of missing or invalid measurements.
5. Rolling statistics.
6. Historical observations for model training.
7. A clear distinction between measured and simulated data.

The telemetry layer must not collect medical payload contents.

---

# 9. AI Prediction Component

The AI predictor estimates transfer behavior.

It is not responsible for deciding whether a request is secure or authorized.

## 9.1 Prediction Tasks

The initial predictor should support:

### Task A: Transfer Duration Prediction

Estimate the time required to complete a transfer.

Output:

```text
predicted_duration_seconds
```

### Task B: Deadline-Miss Risk

Estimate the probability that a transfer will miss its deadline under current conditions.

Output:

```text
deadline_miss_probability
```

Deadline risk may initially be derived from predicted duration and deadline slack. A separately trained probability model may be introduced only if sufficient training data exists.

## 9.2 Input Features

The initial model should use operational metadata, not medical contents.

Candidate features:

- `data_size_bytes`
- `priority`
- `queue_length`
- `active_transfer_count`
- `recent_throughput_mbps`
- `throughput_mean`
- `throughput_std`
- `deadline_slack_seconds`
- `link_status`
- `recent_failure_rate`
- `estimated_available_bandwidth`
- `key_service_latency`, if available
- `key_resource_level`, if available
- `key_replenishment_rate`, if available

Feature availability must be configurable.

Missing QKD telemetry must be handled explicitly rather than silently replaced with invented values.

## 9.3 Model Strategy

Start with simple models and compare them.

Recommended initial sequence:

1. Analytical estimate based on payload size and observed throughput.
2. Historical average baseline.
3. Linear Regression or Ridge Regression.
4. Random Forest Regressor.
5. Gradient Boosting Regressor.

The model should not become more complex unless the evaluation demonstrates a meaningful improvement.

The first working system must be functional without an AI model.

## 9.4 Training Data

Training data may come from:

- Synthetic transfer workloads.
- Simulated network conditions.
- Controlled experiments on the available infrastructure.
- Historical transfer telemetry, if authorized and available.

Synthetic training data must be labeled as synthetic.

The model must not be trained on real patient content.

The initial training pipeline should:

- Generate or ingest structured telemetry.
- Validate feature ranges.
- Split training and evaluation data.
- Prevent leakage between repeated measurements from the same experiment.
- Fit the model.
- Evaluate it.
- Save the model artifact.
- Save the feature schema.
- Save model version and training configuration.

## 9.5 Prediction Evaluation

Measure:

- Mean Absolute Error (MAE).
- Root Mean Squared Error (RMSE).
- Prediction error by payload-size category.
- Prediction error under different network conditions.
- Deadline-risk calibration, if probability outputs are used.

The predictor must not be evaluated only on randomly split individual rows if those rows originate from the same simulated scenario.

Evaluation must include unseen workload configurations or seeds.

---

# 10. Scheduling Engine

The scheduling engine is the central decision-making component.

It receives eligible requests, telemetry, predictions, and current resource state.

It selects the next request or set of requests to execute.

## 10.1 Scheduling Inputs

The scheduler may use:

- Request priority.
- Deadline.
- Predicted transfer duration.
- Predicted deadline-miss risk.
- Queue position.
- Current network conditions.
- Active transfer count.
- Resource availability.
- Security profile.
- Optional QKD resource constraints.
- Historical scheduling behavior.

## 10.2 Scheduling Outputs

The scheduler must return a structured decision containing:

```text
SchedulingDecision:
    selected_request_id
    estimated_start_time
    predicted_completion_time
    predicted_deadline_miss_probability
    policy_name
    decision_reason
```

The decision reason should be concise and based on observable inputs.

## 10.3 Scheduling Policies

Implement at least the following policies.

### Policy 1: FIFO

Requests are scheduled in submission order.

This provides a basic baseline.

### Policy 2: Priority-Based

Requests are scheduled according to configured priority levels.

Within the same priority, use FIFO.

### Policy 3: Resource-Aware Heuristic

A deterministic heuristic considers priority, deadline slack, predicted duration, and current resource conditions.

A possible initial heuristic is:

- Reject or defer requests that are not policy-eligible.
- Prefer high-priority requests.
- Among comparable requests, prefer those with less deadline slack.
- Consider whether the predicted transfer can complete before the deadline.
- Avoid allowing low-priority requests to monopolize resources.
- Use aging to reduce starvation.

The heuristic must be documented and configurable.

### Policy 4: AI-Assisted Scheduling

Use predicted duration and deadline risk in addition to the same inputs available to the resource-aware heuristic.

The AI-assisted policy must not bypass security constraints.

Its purpose is to improve scheduling decisions, not merely to reorder requests using arbitrary model scores.

## 10.4 Fairness and Starvation

The scheduler must account for starvation.

Implement a configurable mechanism such as:

- Priority aging.
- Maximum consecutive high-priority selections.
- Minimum service opportunities for lower-priority requests.
- Deadline-aware fairness.

The chosen mechanism must be documented and evaluated.

## 10.5 Scheduling Mode

The initial implementation may use non-preemptive scheduling.

Once a transfer begins, the scheduler does not interrupt it unless cancellation or a defined failure condition occurs.

Preemption may be introduced later if the transport supports it safely.

---

# 11. Transfer Execution

The transfer executor is responsible for performing a transfer after the scheduler selects it.

It must be independent of the scheduling policy.

## 11.1 Responsibilities

- Obtain the selected request.
- Validate its current state.
- Invoke the configured transport.
- Interact with the cryptographic component.
- Request required key operations through the authorized interface.
- Track progress.
- Handle timeouts and retries.
- Verify transfer integrity.
- Update request status.
- Emit transfer metrics.

## 11.2 Initial Simulator

The initial implementation should use a synthetic transfer executor.

It should simulate:

- Transfer duration.
- Variable throughput.
- Network failures.
- Temporary resource unavailability.
- Transfer completion.
- Integrity verification success or failure.
- Retry behavior.

Simulation must be deterministic when configured with a fixed seed.

## 11.3 Real Transfer Mode

Real transfer mode must be implemented only after the permitted transport and cryptographic stack are known.

Do not invent a transport protocol or claim that simulated encryption represents the real QKD integration.

The real transfer component must use the approved transport and security mechanisms.

---

# 12. Simulation and Experiment Framework

The simulation framework is a major component of the project.

It allows Q-SHIELD to evaluate scheduling strategies under controlled conditions without requiring continuous access to the physical QKD infrastructure.

The simulator should model:

- Transfer requests.
- Request arrivals.
- Priorities.
- Deadlines.
- Network throughput.
- Network variability.
- Queueing.
- Concurrent transfers, if supported by the model.
- Optional QKD key-resource availability.
- Resource replenishment.
- Transfer failures.
- Retries.
- Scheduling decisions.
- Completion and deadline outcomes.

## 12.1 Workload Generator

The workload generator should support configurable distributions for:

- Payload size.
- Request arrival rate.
- Priority distribution.
- Deadline distribution.
- Source and destination.
- Security profile.
- Network condition.
- Failure probability.

It should also support explicit scenario definitions.

Example scenario:

```yaml
scenario_name: medical_mixed_workload

network:
  baseline_throughput_mbps: 100
  throughput_variability: 0.15
  failure_probability: 0.02

workload:
  request_count: 100
  arrival_rate_per_minute: 10
  priorities:
    critical: 0.10
    high: 0.25
    normal: 0.45
    low: 0.20

payload_size:
  minimum_bytes: 100000
  maximum_bytes: 2000000000

deadline:
  enabled: true
```

These values are examples for configuration, not claims about the real network.

## 12.2 Reproducibility

Every experiment must record:

- Experiment ID.
- Scenario ID.
- Random seed.
- Scheduling policy.
- Predictor model version.
- Simulator configuration.
- Software version.
- Start time.
- Result metrics.

The same experiment configuration and seed should produce the same results when deterministic mode is enabled.

## 12.3 Parallel Execution

The experiment framework should support running independent experiments across CPU cores.

This is the primary HPC contribution in the initial version.

Use Python multiprocessing or another appropriate process-based execution model.

The framework should:

- Partition experiments into independent jobs.
- Avoid shared mutable state between runs.
- Aggregate results.
- Preserve individual run outputs.
- Support configurable worker count.
- Measure execution time and scaling.

The first implementation should use standard Python multiprocessing.

Additional HPC frameworks such as Dask or Slurm may be introduced only if they provide a meaningful benefit.

---

# 13. Evaluation Methodology

The system must be evaluated against conventional scheduling policies.

The evaluation should be repeatable and statistically defensible.

## 13.1 Baselines

Compare:

1. FIFO.
2. Priority-Based.
3. Resource-Aware Heuristic.
4. AI-Assisted Scheduling.

All policies must be evaluated on the same workload configurations and random seeds.

## 13.2 Primary Metrics

### Deadline Success Rate

Fraction of requests completed before their deadlines.

### Completion Time

Measure:

- Mean completion time.
- Median completion time.
- 95th percentile completion time.

### Throughput

Amount of data successfully transferred per unit of time.

### Resource Utilization

Measure utilization of modeled network resources and any modeled QKD resources.

### Prediction Error

Measure MAE and RMSE for predicted transfer duration.

### Starvation

Measure whether lower-priority requests experience excessive waiting times.

### Scheduling Overhead

Measure the time required to make a scheduling decision.

### Failure and Retry Metrics

Measure transfer failures, retries, and recovery behavior.

### QKD Resource Metrics

When modeled or exposed, measure:

- Key-resource blocking.
- Resource depletion events.
- Resource utilization.
- Waiting time caused by key-resource unavailability.

Do not report these metrics as real QKD measurements when they come from simulation.

## 13.3 Experimental Design

The initial evaluation should include:

- Multiple workload scenarios.
- Multiple random seeds.
- Small, medium, and large payloads.
- Different request arrival rates.
- Different network variability levels.
- Different priority distributions.
- Different deadline strictness.
- Optional QKD resource constraints.

A reasonable initial experiment plan is:

- 20 scenario configurations.
- 4 scheduling policies.
- 5 random seeds per scenario.

This produces 400 independent policy runs.

This is a proposed experiment design, not a claim that these runs have already been completed.

The experiment framework must allow the number of scenarios and seeds to be changed.

## 13.4 Statistical Analysis

Report:

- Mean and standard deviation.
- Median and interquartile range where useful.
- Confidence intervals.
- Relative improvement over baseline.
- Per-scenario performance.

Avoid presenting a single aggregate number without explaining the conditions.

The project must report negative or inconclusive results honestly.

If AI-assisted scheduling does not outperform the heuristic, investigate why and document the result.

---

# 14. API Design

Use FastAPI for the initial API.

The API should expose request submission, status inspection, system telemetry, and experiment control.

## 14.1 Initial Endpoints

### Submit Transfer

```http
POST /transfers
```

Accepts a transfer request.

Returns:

- Request ID.
- Current status.
- Validation result.

### Get Transfer

```http
GET /transfers/{request_id}
```

Returns:

- Request metadata.
- Status.
- Scheduling information.
- Progress.
- Completion information.
- Failure reason, if applicable.

### List Transfers

```http
GET /transfers
```

Supports filtering by:

- Status.
- Priority.
- Destination.
- Time range.

### Cancel Transfer

```http
POST /transfers/{request_id}/cancel
```

Cancels a pending transfer or requests cancellation of an active transfer if supported.

### System Metrics

```http
GET /metrics
```

Returns operational metrics.

### Health Check

```http
GET /health
```

Returns system health.

### Simulation Control

Simulation endpoints may be added for:

- Starting an experiment.
- Retrieving experiment status.
- Retrieving results.
- Listing available scenarios.

Simulation control must be separated from production transfer endpoints.

---

# 15. Persistence and Audit

Use SQLite for the initial implementation.

The database should contain tables for:

- Transfer requests.
- Transfer state transitions.
- Scheduling decisions.
- Transfer metrics.
- Experiment configurations.
- Experiment results.
- Model metadata.
- Audit events.

The schema should be designed so that SQLite can later be replaced by PostgreSQL without rewriting the business logic.

## 15.1 Audit Requirements

Audit events should record:

- Event type.
- Request ID.
- Timestamp.
- Component.
- Policy decision.
- Scheduling policy.
- Status transition.
- Failure code.

Audit logs must not include:

- Secret key values.
- Medical payload contents.
- Unnecessary patient-identifying metadata.

---

# 16. Dashboard

Use Streamlit for the initial dashboard.

The dashboard should provide:

## 16.1 Operational View

- Current queue.
- Request status.
- Priority.
- Deadline.
- Predicted duration.
- Predicted deadline risk.
- Active transfers.
- Recent failures.
- Resource status.

## 16.2 Experiment View

- Scheduling policy comparison.
- Deadline success rate.
- Completion time distributions.
- Throughput.
- Prediction error.
- Resource utilization.
- Starvation metrics.
- Confidence intervals.

## 16.3 Visualization Requirements

Charts should make it possible to compare policies across the same workloads.

The dashboard must distinguish:

- Simulated results.
- Real infrastructure measurements.
- Predictions.
- Actual observed outcomes.

Do not present simulated data as real infrastructure performance.

---

# 17. Proposed Technology Stack

| Component | Technology |
|---|---|
| Main language | Python |
| API | FastAPI |
| Data validation | Pydantic |
| Persistence | SQLite |
| Simulation | SimPy or custom discrete-event simulator |
| ML | scikit-learn |
| Experiment parallelization | multiprocessing |
| Dashboard | Streamlit |
| Testing | pytest |
| Configuration | YAML |
| Logging | Python logging |
| Model persistence | joblib, with version metadata |
| Packaging | pyproject.toml |
| Version control | Git |

The implementation should prefer simple, well-supported tools over unnecessary infrastructure.

Docker may be introduced for reproducible deployment, but it is not a prerequisite for the first working prototype.

---

# 18. Proposed Repository Structure

```text
q-shield/
│
├── description.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
│
├── configs/
│   ├── app.yaml
│   ├── security.yaml
│   ├── simulation.yaml
│   └── scheduling.yaml
│
├── app/
│   ├── main.py
│   │
│   ├── api/
│   │   ├── routes_transfers.py
│   │   ├── routes_metrics.py
│   │   ├── routes_simulation.py
│   │   └── schemas.py
│   │
│   ├── core/
│   │   ├── config.py
│   │   ├── enums.py
│   │   ├── exceptions.py
│   │   └── state_machine.py
│   │
│   ├── models/
│   │   ├── transfer_request.py
│   │   ├── scheduling_decision.py
│   │   ├── policy_decision.py
│   │   └── telemetry.py
│   │
│   ├── services/
│   │   ├── request_service.py
│   │   ├── queue_service.py
│   │   ├── scheduling_service.py
│   │   ├── transfer_service.py
│   │   └── experiment_service.py
│   │
│   ├── security/
│   │   ├── policy_engine.py
│   │   ├── authorization.py
│   │   └── audit.py
│   │
│   ├── scheduler/
│   │   ├── base.py
│   │   ├── fifo.py
│   │   ├── priority.py
│   │   ├── resource_aware.py
│   │   └── ai_assisted.py
│   │
│   ├── predictor/
│   │   ├── features.py
│   │   ├── baseline.py
│   │   ├── train.py
│   │   ├── predict.py
│   │   ├── evaluate.py
│   │   └── model_registry.py
│   │
│   ├── transfer/
│   │   ├── base.py
│   │   ├── simulator.py
│   │   ├── executor.py
│   │   ├── integrity.py
│   │   └── crypto_adapter.py
│   │
│   ├── qkd/
│   │   ├── base.py
│   │   ├── simulator.py
│   │   ├── capabilities.py
│   │   └── real_adapter.py
│   │
│   ├── telemetry/
│   │   ├── collector.py
│   │   ├── metrics.py
│   │   └── history.py
│   │
│   └── database/
│       ├── connection.py
│       ├── repository.py
│       └── schema.py
│
├── simulation/
│   ├── engine.py
│   ├── network_model.py
│   ├── key_resource_model.py
│   ├── workload_generator.py
│   ├── scenarios.py
│   ├── experiments.py
│   ├── parallel_runner.py
│   └── aggregation.py
│
├── dashboard/
│   ├── app.py
│   ├── pages/
│   │   ├── operations.py
│   │   ├── experiments.py
│   │   └── model_evaluation.py
│   └── components/
│
├── scripts/
│   ├── generate_workload.py
│   ├── train_model.py
│   ├── run_experiments.py
│   └── export_results.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── simulation/
│   └── security/
│
├── data/
│   ├── synthetic/
│   └── generated/
│
├── models/
│
└── results/
    ├── experiments/
    └── reports/
```

This is a target structure, not a requirement to create every file immediately.

The repository should grow incrementally as features are implemented.

---

# 19. Implementation Roadmap

## Phase 0 — Infrastructure Discovery

Before implementing real QKD integration, determine:

- QKD vendor and system model.
- Available SDK or API.
- Key-management interface.
- Available telemetry.
- Authorized access credentials.
- Permitted test endpoints.
- Approved cryptographic and transport mechanism.
- Whether non-sensitive test transfers are permitted.

The real adapter must be based on verified documentation.

If the interface is unavailable, continue with simulation mode.

## Phase 1 — Minimal Working System

Implement:

1. Project structure.
2. Transfer request model.
3. API submission endpoint.
4. SQLite persistence.
5. Request lifecycle.
6. Deterministic policy engine.
7. FIFO scheduler.
8. Synthetic transfer executor.
9. Integrity verification.
10. Basic metrics.
11. Unit tests.

At the end of this phase, a synthetic request must be submitted through the API, scheduled, executed, verified, and marked completed.

## Phase 2 — Baseline Scheduling

Implement:

1. Priority-based scheduling.
2. Resource-aware heuristic.
3. Queue management.
4. Deadline handling.
5. Starvation mitigation.
6. Scheduling decision logging.
7. Baseline comparison.

At the end of this phase, the system must support comparing the baseline policies on the same workload.

## Phase 3 — Simulation Framework

Implement:

1. Network model.
2. Workload generator.
3. Key-resource simulator.
4. Failure and retry simulation.
5. Experiment configuration.
6. Seed-based reproducibility.
7. Parallel experiment execution.
8. Metric aggregation.
9. CSV/JSON export.

The simulator must support multiple independent scenarios and scheduling policies.

## Phase 4 — AI Predictor

Implement:

1. Feature extraction.
2. Analytical baseline.
3. Historical-average predictor.
4. Model training pipeline.
5. Duration prediction.
6. Deadline-risk calculation.
7. Model artifact persistence.
8. Evaluation.
9. AI-assisted scheduling policy.

The predictor must be integrated only after baseline scheduling is functional.

## Phase 5 — QKD Integration

If authorized integration is available:

1. Implement the real QKD adapter.
2. Validate status queries.
3. Validate telemetry mapping.
4. Confirm key-management behavior.
5. Validate cryptographic integration.
6. Run non-sensitive test transfers.
7. Test resource unavailability.
8. Test service failure and recovery.
9. Document limitations.

If integration is unavailable, continue with the simulator and explicitly document which parts are simulated.

## Phase 6 — Dashboard and Evaluation

Implement:

1. Operational dashboard.
2. Experiment dashboard.
3. Policy comparison charts.
4. Prediction evaluation.
5. Statistical analysis.
6. Reproducible result reports.
7. Final documentation.

---

# 20. Testing Requirements

Testing is mandatory throughout development.

## 20.1 Unit Tests

Test:

- Request validation.
- State transitions.
- Policy decisions.
- Feature extraction.
- Predictor outputs.
- Scheduler behavior.
- Metric calculations.
- Simulator determinism.
- Retry logic.
- Integrity verification.

## 20.2 Integration Tests

Test:

- API submission to database.
- Request submission to scheduling.
- Scheduling to transfer execution.
- Transfer completion to verification.
- Verification to final request state.
- Telemetry to prediction.
- Prediction to scheduling.
- Experiment execution to result aggregation.

## 20.3 Security Tests

Test:

- Unauthorized source rejection.
- Unauthorized destination rejection.
- Invalid security profile rejection.
- AI output cannot override policy.
- Secret key material is not logged.
- Medical payload is not logged.
- Failed integrity verification does not result in `COMPLETED`.
- Invalid state transitions are rejected.

## 20.4 Simulation Tests

Test:

- Fixed seeds produce repeatable outcomes.
- Resource replenishment behaves as configured.
- Resource exhaustion is handled.
- Deadline misses are correctly recorded.
- Retries are counted.
- Policy comparisons use the same workloads.
- Parallel execution does not contaminate experiment state.

---

# 21. Agent-Assisted Development Strategy

The implementation will be developed using coding agents.

Agents must work from this specification and from explicitly defined interfaces.

The project should not be delegated as a collection of vague tasks such as "build the scheduler" or "implement the AI."

Each agent task must specify:

- Objective.
- Relevant files.
- Interface contracts.
- Acceptance criteria.
- Tests required.
- Dependencies.
- Files the agent is allowed to modify.

## 21.1 Recommended Agent Roles

### Agent A — Architecture and Integration

Responsibilities:

- Maintain module boundaries.
- Review interface contracts.
- Resolve integration conflicts.
- Keep configuration consistent.
- Ensure the application runs end-to-end.

This agent should not rewrite modules without a clear reason.

### Agent B — API and Persistence

Responsibilities:

- FastAPI endpoints.
- Pydantic schemas.
- SQLite repositories.
- Request lifecycle.
- API tests.

### Agent C — Scheduling and Policy

Responsibilities:

- Deterministic policy engine.
- FIFO scheduler.
- Priority scheduler.
- Resource-aware heuristic.
- AI-assisted policy integration.
- Scheduler tests.

### Agent D — Simulation and HPC

Responsibilities:

- Network simulation.
- Workload generation.
- Key-resource simulation.
- Experiment framework.
- Parallel execution.
- Reproducibility and aggregation.

### Agent E — AI and Evaluation

Responsibilities:

- Feature extraction.
- Training pipeline.
- Baseline models.
- Duration prediction.
- Deadline-risk evaluation.
- Model metrics and artifact management.

### Agent F — QKD and Transfer Integration

Responsibilities:

- QKD adapter interface.
- QKD simulator.
- Transfer executor.
- Integrity verification.
- Real adapter only when documentation is available.

### Agent G — Testing and Quality

Responsibilities:

- Unit and integration tests.
- Security tests.
- End-to-end test scenarios.
- Review failure handling.
- Detect regressions.

For a small team or limited agent budget, combine roles rather than creating excessive parallel agents.

## 21.2 Agent Collaboration Rules

1. The architecture and integration agent defines interfaces before parallel implementation begins.
2. Agents must not independently invent conflicting schemas.
3. Agents must not change public interfaces without notifying the integration agent.
4. Each module must include tests.
5. Each agent must report files changed and tests executed.
6. Agents must not claim tests passed unless they actually ran them.
7. Agents must not claim real QKD integration without verified infrastructure access.
8. Agents must not introduce additional infrastructure without justification.
9. Agents must not add unrelated features.
10. Integration must happen continuously, not only at the end.

## 21.3 Definition of Done for an Agent Task

A task is complete only when:

- The implementation satisfies the specified behavior.
- The module follows the agreed interface.
- Relevant tests exist.
- Tests have been executed.
- Errors and edge cases are handled.
- No secrets or sensitive payloads are logged.
- The agent provides a concise implementation report.

---

# 22. Initial Demonstration Scenario

The initial demonstration should use synthetic medical data.

Example workload:

| Request | Payload | Priority | Deadline |
|---|---:|---|---|
| Diagnostic Report A | 5 MB | High | 30 seconds |
| Imaging Study B | 2 GB | Normal | 10 minutes |
| Research Dataset C | 500 MB | Low | 30 minutes |

These values are illustrative.

The demonstration should show:

1. Requests submitted through the API.
2. Requests entering the queue.
3. Policy eligibility checks.
4. Telemetry being collected.
5. Predictions being generated.
6. Different scheduling policies selecting different requests.
7. Transfers being executed in simulation.
8. Integrity verification.
9. Completion and deadline outcomes.
10. A comparison of scheduling metrics.

The demo should also include at least one failure scenario, such as temporary resource unavailability or a simulated network interruption.

The purpose is to demonstrate that Q-SHIELD is a working orchestration system, not simply a prediction model.

---

# 23. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| QKD API is unavailable | Use a simulator and maintain a vendor-independent adapter |
| QKD telemetry is limited | Make telemetry optional and evaluate policies without unavailable fields |
| AI does not outperform heuristic | Report the result and investigate feature/model limitations |
| Synthetic data does not represent real network behavior | Use controlled measurements when authorized and document the gap |
| Scheduling becomes too complex | Begin with FIFO and deterministic policies |
| Agent implementations conflict | Define interfaces and acceptance criteria first |
| Real transfer integration is delayed | Keep the simulator as the primary development environment |
| Security assumptions are incorrect | Verify cryptographic and key-management behavior with the infrastructure owner |
| Experiments are not reproducible | Record configuration, seeds, model versions, and software versions |
| HPC contribution is superficial | Measure parallel experiment throughput and scaling |

---

# 24. Final Success Criteria

The initial project is considered successful when:

1. The system can accept structured transfer requests through an API.
2. Requests are validated and stored.
3. Security policy is enforced independently of AI.
4. Multiple scheduling policies work.
5. A synthetic transfer can be scheduled, executed, verified, and completed.
6. The simulator supports variable network conditions and optional QKD resource constraints.
7. The AI predictor produces evaluated duration estimates.
8. AI-assisted scheduling can be compared with conventional policies.
9. Experiments are reproducible.
10. Independent experiments can run in parallel.
11. Results include meaningful metrics and statistical analysis.
12. A dashboard visualizes operations and experiment results.
13. Real QKD integration is implemented only where verified access permits it.
14. The project clearly distinguishes real measurements from simulated results.
15. The repository contains documentation, tests, and reproducible experiment instructions.

---

# 25. Long-Term Generalization

Q-SHIELD should be designed so that the scheduling framework can be reused in other QKD-enabled contexts.

The reusable components are:

- Request model.
- Policy engine.
- Scheduling engine.
- Prediction interface.
- Telemetry abstraction.
- Experiment framework.
- Resource-aware decision logic.

Context-specific components include:

- Authorization rules.
- Data categories.
- Priority definitions.
- Deadlines.
- Security profiles.
- Transfer protocols.
- QKD adapter.
- Resource models.

The system should not assume that all QKD networks expose identical capabilities.

A new QKD implementation should be integrated by implementing the adapter and validating its capabilities, rather than rewriting the scheduler.

The initial project will validate this architecture against one available infrastructure and a simulator. Broader compatibility will require additional integrations and testing.

---

# 26. Implementation Philosophy

Q-SHIELD must be built incrementally.

The correct order is:

1. Make a basic transfer work.
2. Make it secure by deterministic policy.
3. Make it schedulable.
4. Make it measurable.
5. Make it simulatable.
6. Add AI predictions.
7. Evaluate whether AI improves scheduling.
8. Integrate real QKD capabilities when verified.
9. Optimize performance.
10. Generalize the system.

The project must prioritize correctness, reproducibility, security, and measurable improvement over architectural complexity.

**The primary deliverable is a working, evaluated QKD-aware orchestration system — not an AI model in isolation.**
