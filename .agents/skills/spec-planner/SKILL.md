---
name: spec-planner
description: Dissects large system specifications into architectural contracts, vertical slices, and staged testing milestones.
---

# Specification Decomposition & Planning Guide

When invoked with a spec file:

1. **Extraction & Interface Contracts (`docs/ARCHITECTURE_CONTRACTS.md`)**
   - Extract core entities and state schemas.
   - Define agent communication schemas (e.g., Pydantic interfaces, message envelopes, API endpoints).
   - Establish mock boundaries so agents can be tested in isolation.

2. **Vertical Slice Roadmap (`docs/ROADMAP_STAGES.md`)**
   - Break implementation into end-to-end "tracer bullets" (thin vertical slices from input to final output).
   - Structure around **Stages** (additive capability) and **Interludes** (testing/validation gates).
   - For every stage, specify:
     * *Preconditions* (what mocks or previous stage outputs are required).
     * *Concrete Implementation Delta* (which agents/files are created or modified).
     * *Interlude Test Verification* (exact automated test suite, CLI command, or fixture replay script).

3. **Atomic Task Plan (`docs/TASK_BACKLOG.md`)**
   - Sequential, checkbox-driven implementation steps that an agent can tackle one by one without context explosion.