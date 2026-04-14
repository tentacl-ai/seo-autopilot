# Contributing to SEO Autopilot

Thanks for your interest in contributing! This document covers the basics.

## Development Setup

```bash
git clone https://github.com/tentacl-ai/seo-autopilot.git
cd seo-autopilot
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/ -v
```

All 229+ tests must pass before submitting a PR. Tests run without any external services (no API keys, no database, no network).

## Project Structure

- **`analyzers/`** — Rule-based analysis modules. Each analyzer is self-contained with its own issue types and detection logic.
- **`sources/`** — Data collection (crawler, PageSpeed, GSC, RSS feeds).
- **`agents/`** — Pipeline agents that orchestrate analyzers and produce results.
- **`tests/`** — Unit tests. One test file per module.

## Adding a New Analyzer

1. Create `seo_autopilot/analyzers/your_analyzer.py`
2. Implement a class with a `detect_issues(pages) -> List[Dict]` method
3. Each issue must follow the standard format:
   ```python
   {
       "category": "your_category",
       "type": "issue_type_name",
       "severity": "critical|high|medium|low|info",
       "title": "Human-readable title",
       "affected_url": "https://...",
       "description": "What's wrong",
       "fix_suggestion": "How to fix it",
       "estimated_impact": "",
   }
   ```
4. Add tests in `tests/test_your_analyzer.py`
5. Integrate in `agents/analyzer.py` (wrap in try/except, non-fatal)

## Code Style

- **Language**: All code, comments, docstrings, and user-facing strings in **English**
- **Type hints**: All public functions must have type annotations
- **Formatting**: We use `black` for formatting
- **No new dependencies** without discussion — keep the core lightweight. Playwright is an optional dependency for JS rendering (`pip install seo-autopilot[rendering]`)

## Commit Messages

- Use conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`
- Keep the first line under 72 characters

## Issue Reports

When reporting bugs, include:
- Python version
- Steps to reproduce
- Expected vs actual behavior
- Full traceback if applicable

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
