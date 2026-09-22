# Contributing

ReliaMesh is open-source reliability infrastructure for AI agents, maintained by
Prefiler Labs Private Limited under Apache-2.0. Contributions are accepted under
the project's license; contributors retain their own copyright. Do not contribute
code or data you do not have the right to license.

Use Python 3.12+ and a virtual environment:

```sh
python -m pip install -e ".[dev]" -e ./sdk/python
python -m pytest
python -m ruff check .
```

Keep changes focused, explain the user-visible behavior, and include tests for
meaningful logic and regressions. Use synthetic events in tests and examples.
Never include customer content, credentials, production exports, or private
company documents in a pull request, issue, fixture, or commit.

Protocol changes must consider old SDKs, event validation, privacy, retention,
deduplication, and deterministic detection. Adding arbitrary metadata bags or raw
content collection requires rethinking the change: data minimization is a project
boundary. Preserve independent SQLite self-hosting and explicit opt-in behavior
for any future network contribution. Do not add a paid-service dependency or
blockchain functionality to the core path.

Keep the Python SDK free of runtime dependencies. Read and update documentation
when behavior changes. Server dependencies must remain compatible with Apache-2.0
distribution; record required notices and review license changes. Releases should
be reproducible from a tagged commit and pass tests, build, security/dependency
checks, and a real installation smoke test before publication.

Use [private security reporting](SECURITY.md) for vulnerabilities. For ordinary
bugs, provide a minimal synthetic reproduction and exact version. Discuss broad
changes before investing in a large implementation.
