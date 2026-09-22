"""Release gates must fail on missing evidence, not silently exclude it."""

import importlib.metadata
from email.message import Message
from types import SimpleNamespace

import pytest

from scripts import check_licenses as policy


def distribution(name, *, version="1.0", expression="MIT", requires=(), legacy=None):
    fields = Message()
    fields["Name"] = name
    if expression is not None:
        fields["License-Expression"] = expression
    if legacy is not None:
        fields["License"] = legacy
    return SimpleNamespace(metadata=fields, version=version, requires=requires, files=[])


@pytest.mark.parametrize("expression", [
    "GPL-3.0-only", "MIT OR GPL-3.0-only", "MIT AND LicenseRef-Unreviewed",
    "Unknown-License", "MIT WITH LLVM-exception", "(MIT OR Apache-2.0", "",
])
def test_unreviewed_or_malformed_license_is_rejected(expression):
    with pytest.raises(policy.PolicyError):
        policy.approved_expression(expression, "synthetic", "1.0")


def test_permissive_spdx_expression_and_version_bounded_exception():
    assert policy.approved_expression("mit AND (Apache-2.0 OR BSD-3-Clause)", "synthetic", "1.0")
    assert policy.approved_expression("MPL-2.0", "certifi", "2026.7.22") == "MPL-2.0"
    for name, version in [("other", "2026.7.22"), ("certifi", "9999")]:
        with pytest.raises(policy.PolicyError):
            policy.approved_expression("MPL-2.0", name, version)


def test_ambiguous_or_conflicting_metadata_does_not_pass():
    missing = distribution("missing", expression=None)
    with pytest.raises(policy.PolicyError, match="missing or ambiguous"):
        policy.license_expression("missing", missing)
    misleading = distribution("misleading", expression=None, legacy="Proprietary")
    misleading.metadata["Classifier"] = "License :: OSI Approved :: MIT License"
    expression, _ = policy.license_expression("misleading", misleading)
    with pytest.raises(policy.PolicyError):
        policy.approved_expression(expression, "misleading", "1.0")
    ambiguous = distribution("ambiguous", expression=None, legacy="BSD")
    with pytest.raises(policy.PolicyError):
        policy.approved_expression(policy.license_expression("ambiguous", ambiguous)[0], "ambiguous", "1.0")


def test_reviewed_license_file_is_required():
    reviewed = distribution("pyasn1-modules", version="0.4.2", expression=None, legacy="BSD")
    with pytest.raises(policy.PolicyError, match="missing or changed"):
        policy.license_expression("pyasn1-modules", reviewed)


def test_runtime_closure_follows_extras_and_ignores_development_tools():
    distributions = {
        "root": distribution("root", requires=["base>=1", 'optional[cloud]; extra == "gcp"', 'dev; extra == "dev"']),
        "base": distribution("base"),
        "optional": distribution("optional", requires=['nested; extra == "cloud"', 'wrong-platform; python_version < "3.0"']),
        "nested": distribution("nested"),
        "dev": distribution("dev", expression="GPL-3.0-only"),
    }
    report = policy.inventory(["root[gcp]"], distributions.__getitem__)
    assert {row["name"] for row in report["packages"]} == {"root", "base", "optional", "nested"}
    assert report["errors"] == []


def test_runtime_graph_revisits_dependency_when_an_extra_arrives_later():
    distributions = {
        "root": distribution("root", requires=["shared", "bridge"]),
        "shared": distribution("shared", requires=['nested; extra == "gcp"']),
        "bridge": distribution("bridge", requires=["shared[gcp]"]),
        "nested": distribution("nested"),
    }
    assert set(policy.runtime_distributions(["root"], distributions.__getitem__)) == set(distributions)


def test_missing_or_wrong_version_runtime_dependency_blocks_report():
    def absent(name):
        raise importlib.metadata.PackageNotFoundError(name)

    with pytest.raises(policy.PolicyError, match="not installed"):
        policy.runtime_distributions(["missing"], absent)
    with pytest.raises(policy.PolicyError, match="does not satisfy"):
        policy.runtime_distributions(["root>=2"], lambda name: distribution(name))


def test_inventory_reports_every_license_failure():
    distributions = {
        "root": distribution("root", requires=["unknown", "restrictive"]),
        "unknown": distribution("unknown", expression=None),
        "restrictive": distribution("restrictive", expression="GPL-3.0-only"),
    }
    report = policy.inventory(["root"], distributions.__getitem__)
    assert len(report["errors"]) == 2
    assert [row["name"] for row in report["packages"]] == ["root"]
