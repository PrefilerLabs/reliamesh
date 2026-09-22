"""Fail-closed license inventory of installed runtime dependencies.

Run after installing the runtime lock and both local packages with --no-deps.
Development tools are excluded unless a shipped package actually depends on them.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import sys
from collections import deque
from pathlib import Path

from packaging.licenses import InvalidLicenseExpression, canonicalize_license_expression
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ALLOWED = frozenset({
    "Apache-2.0", "MIT", "MIT-0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "PSF-2.0",
    "0BSD", "Unlicense",
})
ALIASES = {
    "Apache 2.0": "Apache-2.0",
    "Apache License, Version 2.0": "Apache-2.0",
    "Apache Software License": "Apache-2.0",
    "MIT License": "MIT",
    "3-Clause BSD License": "BSD-3-Clause",
    "2-Clause BSD License": "BSD-2-Clause",
}
CLASSIFIERS = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
}
# Reviewed against the installed wheel's actual LICENSE.txt, not inferred from
# the ambiguous "BSD" metadata field. A release or license-file change fails.
FILE_REVIEWS = {
    ("pyasn1-modules", "0.4.2"): (
        "BSD-2-Clause", "LICENSE.txt",
        "2aad5fc00f705c4a1addb83eed10a6a75d286a3779f0cf8519d87e62bc4735fd",
    ),
}
# Certifi stays an unmodified, separately distributed dependency with its MPL
# license and source offer preserved. This does not relicense ReliaMesh.
PACKAGE_ALLOWANCES = {("certifi", "2026.7.22"): frozenset({"MPL-2.0"})}


class PolicyError(Exception):
    """An unresolved runtime dependency or license blocks release."""


def runtime_distributions(roots, lookup=metadata.distribution):
    """Resolve the installed dependency closure, including requested extras."""
    pending = deque(Requirement(root) for root in roots)
    visited = {}
    distributions = {}
    environment = default_environment()
    while pending:
        requirement = pending.popleft()
        if requirement.url:
            raise PolicyError("direct URL runtime dependencies require a reviewed lock policy")
        name = canonicalize_name(requirement.name)
        try:
            distribution = lookup(requirement.name)
        except metadata.PackageNotFoundError as error:
            raise PolicyError(f"runtime dependency is not installed: {name}") from error
        if not requirement.specifier.contains(distribution.version, prereleases=True):
            raise PolicyError(f"installed version does not satisfy runtime requirement: {requirement}")
        extras = set(requirement.extras)
        if name in visited and extras.issubset(visited[name]):
            continue
        extras |= visited.get(name, set())
        visited[name] = extras
        distributions[name] = distribution
        for value in distribution.requires or []:
            dependency = Requirement(value)
            if dependency.marker and not any(
                dependency.marker.evaluate({**environment, "extra": extra})
                for extra in {"", *extras}
            ):
                continue
            pending.append(dependency)
    return distributions


def license_expression(name, distribution):
    fields = distribution.metadata
    declared = fields.get("License-Expression")
    if declared:
        return declared, "License-Expression"
    review = FILE_REVIEWS.get((name, distribution.version))
    if review:
        expression, filename, expected = review
        for path in distribution.files or []:
            if Path(path).name == filename:
                actual = hashlib.sha256(distribution.locate_file(path).read_bytes()).hexdigest()
                if actual == expected:
                    return expression, "reviewed license-file SHA-256"
        raise PolicyError(f"reviewed license file missing or changed: {name}")
    legacy = fields.get("License", "").strip()
    if legacy and legacy != "UNKNOWN":
        # An unrecognized declaration never falls back to a permissive classifier.
        return ALIASES.get(legacy, legacy), "License"
    classifiers = [value for value in fields.get_all("Classifier", []) if value.startswith("License ::")]
    specific = [value for value in classifiers if value != "License :: OSI Approved"]
    if len(specific) == 1 and specific[0] in CLASSIFIERS:
        return CLASSIFIERS[specific[0]], "unambiguous license classifier"
    raise PolicyError(f"missing or ambiguous license evidence: {name}")


def approved_expression(expression, name, version):
    try:
        normalized = str(canonicalize_license_expression(expression))
    except InvalidLicenseExpression as error:
        raise PolicyError(f"invalid or unknown SPDX expression: {name}") from error
    tokens = re.findall(r"[^\s()]+", normalized)
    if "WITH" in tokens:
        raise PolicyError(f"license exception requires explicit review: {name}")
    allowed = ALLOWED | PACKAGE_ALLOWANCES.get((name, version), frozenset())
    identifiers = set(tokens) - {"AND", "OR"}
    if not identifiers or not identifiers.issubset(allowed):
        denied = ", ".join(sorted(identifiers - allowed))
        raise PolicyError(f"unapproved license for {name}: {denied}")
    return normalized


def inventory(roots, lookup=metadata.distribution):
    records = []
    errors = []
    for name, distribution in sorted(runtime_distributions(roots, lookup).items()):
        try:
            expression, source = license_expression(name, distribution)
            normalized = approved_expression(expression, name, distribution.version)
            records.append({
                "name": name, "version": distribution.version,
                "license_expression": normalized, "evidence": source,
            })
        except PolicyError as error:
            errors.append(str(error))
    return {"roots": roots, "packages": records, "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", help="installed distribution requirement")
    parser.add_argument("--json-output", type=Path)
    arguments = parser.parse_args(argv)
    roots = arguments.root or ["reliamesh-server[gcp]", "reliamesh-sdk"]
    try:
        report = inventory(roots)
    except PolicyError as error:
        report = {"roots": roots, "packages": [], "errors": [str(error)]}
    encoded = json.dumps(report, indent=2) + "\n"
    if arguments.json_output:
        arguments.json_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.json_output.write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
