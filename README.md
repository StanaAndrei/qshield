# Q-SHIELD

**AI-Driven Orchestration for Quantum-Secured Medical Data Exchange**

Q-SHIELD is a resource-aware orchestration system designed to optimize the exchange of sensitive medical and diagnostic data over networks that utilize Quantum Key Distribution (QKD) for cryptographic key establishment.

---

## Prerequisites

- **Python**: 3.11 or 3.12
- **Package Manager**: [`uv`](https://github.com/astral-sh/uv) (recommended) or standard `pip` / `venv`

---

## Quickstart & Environment Setup

### 1. Create and Activate Virtual Environment

**Using `uv` (Fast & Recommended):**
```bash
# Create a virtual environment using Python 3.12 (or 3.11)
uv venv --python 3.12 .venv

# Activate the virtual environment
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

### 3. Initialize Local Environment Variables

```bash
cp .env.example .env
```

---

## Verification & Definition of Done (DoD) Commands

Run the following commands to verify the environment and baseline configuration:

### 1. Verify Configuration & Settings Loader
```bash
python -c "from app.core.config import settings; print(settings.APP_ENV)"
```
**Expected output:**
```text
simulation
```

### 2. Verify Structured Redacted Logging
```bash
python -c "import logging; from app.core.config import settings; log = logging.getLogger('test'); log.info('User password=super_secret_123 key_material: abcdef456 passed');"
```
**Expected output:**
```text
YYYY-MM-DD HH:MM:SS [INFO] [test]: User password=***REDACTED*** key_material=***REDACTED*** passed
```

### 3. Code Style & Static Type Checking
```bash
# Linter check
ruff check .

# Static type check
mypy app
```
**Expected output:** All checks pass with 0 errors.

### 4. Pytest Collection Gate
```bash
pytest --collect-only
```

---

## Project Structure

```text
├── configs/               # Application and scenario YAML configurations
│   └── app.yaml           # Central application settings and redaction rules
├── app/                   # Core application source code
│   └── core/              # Foundational configuration and logging
│       ├── __init__.py
│       └── config.py      # Pydantic Settings & redacting log filter
├── docs/                  # Architecture contracts, roadmap, and specifications
│   ├── ARCHITECTURE_CONTRACTS.md
│   ├── ROADMAP_STAGES.md
│   ├── TASK_BACKLOG.md
│   └── spec.md
├── tests/                 # Unit, integration, and security test suites
├── pyproject.toml         # Packaging configuration, tool options (Ruff, Mypy, Pytest)
├── .env.example           # Environment template
└── README.md              # Project documentation and quickstart
```
