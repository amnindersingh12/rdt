# Contributing Guide

Thanks for your interest in contributing! This guide explains how to set up a development environment, coding standards, and how to submit changes.

## 📦 Quick Start
```bash
# Fork the repo and clone your fork

python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt  # linters & tests
cp config.env.example config.env  # add your credentials
```

Avoid committing secrets. `config.env` is gitignored.

## 🗂 Project Structure (Simplified)
```
main.py                # Bot entrypoint / command handlers
helpers/               # Modular helpers (external, channel, utils, etc.)
docs/                  # Architecture and documentation
tests/                 # Pytest tests
```

See `docs/ARCHITECTURE.md` for a detailed architecture overview.

## 🔧 Development Workflow
1. Create a feature branch: `feat/<short-description>` or `fix/<issue-id>`
2. Write or update tests for new logic (where sensible)
3. Run formatting + lint + tests:
   ```bash
   make format lint test   # or run individual commands below
   ```
4. Commit with conventional message (see below)
5. Open a Pull Request; link related issues
6. Respond to review feedback

## ✅ Commit Message Conventions
Use a subset of Conventional Commits:
```
feat: add multi-tier external fallback
fix: correct audio recovery logic null check
docs: add Heroku deployment section
refactor: split forwarding logic
test: add URL pattern tests
chore: dependency bumps
```
Include scope when useful (`feat(external): ...`).

## 🧪 Testing
We use `pytest`:
```bash
pytest -q
```
Focus areas:
- URL extraction edge cases
- External fallback path success & error reporting
- Channel cloning skip logic for non-media

## 🧹 Code Style
- Python: `black` (88 char line), `ruff` for linting, `isort` ordering (ruff handles it if configured)
- Type hints encouraged (mypy in gradual mode)
- Avoid large monolithic functions; push logic into helpers

Commands (if not using Makefile):
```bash
black .
ruff check . --fix
mypy helpers/external.py
```

## 🔐 Secrets & Security
- Never commit API credentials, session strings, cookies, or tokens.
- Use environment variables via `config.env`.
- Remove any accidentally committed secret immediately and rotate it.

## 🧪 Adding a New External Site
1. Add regex to `SUPPORTED_PATTERNS` in `helpers/external.py`
2. Adjust fallback formats if site needs special handling
3. Test downloads with and without cookies
4. Add a short note to README if user actions are needed

## 🩹 Handling ffmpeg Issues
- Code auto-detects common Heroku paths.
- If adding code requiring ffmpeg, ensure graceful skip when missing.

## 🔍 Opening Issues
Provide:
- Reproduction steps
- Relevant command / URL
- Log excerpt (avoid full secrets)
- Expected vs actual result

## 🚀 Pull Request Checklist
- [ ] Tests pass locally
- [ ] Lint & format applied
- [ ] README / docs updated (if user-facing change)
- [ ] No secrets or stray debug prints

## 🤝 Code of Conduct
See `CODE_OF_CONDUCT.md`.

Happy hacking! 🎉
