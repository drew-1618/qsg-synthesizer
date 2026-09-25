# QSG Synthesizer - Project Instructions

## Architecture Overview
A containerized FastAPI service running a multi-agent pipeline that ingests telecom hardware Quick Start Guides (QSGs) or spec sheets, isolates extraction into bounded subagents, normalizes metrics via deterministic tools, and produces a comparative usability battlecard.

## Repository Standards
- Python: 3.11+
- Framework: FastAPI + Uvicorn
- Testing: pytest -q (Always run tests quietly to save context tokens)
- Formatting: Black / Ruff conventions

## Agent Principles
- Never dump raw multi-page documents into the primary orchestrator prompt.
- Always delegate document parsing to isolated subagents in `.claude/agents/`.
- Use deterministic tools in `app/tools/` for IP validation and unit conversion rather than LLM guessing.
