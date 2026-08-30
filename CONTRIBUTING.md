# Contributing

```bash
git clone https://github.com/capraCoder/db-fahrplan-mcp
cd db-fahrplan-mcp
python -m pip install -e ".[dev]"
pytest              # offline tests (recorded bahn.de fixtures)
pytest -m live      # hits bahn.de — needs network, may be blocked from cloud IPs
ruff check . && mypy
```

- PRs against `main`. CI must be green (Linux + Windows, Python 3.10–3.13, and mcp 2.x).
- If bahn.de changes a field, update the fixture in `tests/fixtures/` from a real response
  (strip nothing that the parser reads) and adjust the parser — do not special-case tests.
- Keep stdout clean: it is the MCP wire. Log to stderr.
- One new tool per PR, with a docstring written for an LLM reader (what it returns, when to use it).
