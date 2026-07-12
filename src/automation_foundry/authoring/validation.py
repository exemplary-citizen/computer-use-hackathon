"""Fail-closed validation for untrusted generated automation bundles."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from automation_foundry.contracts import GeneratedTool, ProcedureCheck, ProcedureStep, ReviewConflict

REQUIRED_ARTIFACTS = {
    "SOP.md",
    "SKILL.md",
    "checks.json",
    "inputs.schema.json",
    "procedure.json",
    "tools.py",
    "tool_manifest.json",
    "eval_cases.json",
    "review.json",
    "test_tools.py",
}

_ALLOWED_IMPORTS = {
    "collections",
    "datetime",
    "decimal",
    "functools",
    "itertools",
    "json",
    "math",
    "re",
    "statistics",
    "typing",
}
_FORBIDDEN_CALLS = {
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
    "__import__",
}
_FORBIDDEN_ATTRIBUTES = {
    "__bases__",
    "__builtins__",
    "__class__",
    "__code__",
    "__dict__",
    "__globals__",
    "__loader__",
    "__mro__",
    "__subclasses__",
}
_SELECTOR_PATTERNS = (
    re.compile(r"\b(?:xpath|css selector|dom selector)\b", re.IGNORECASE),
    re.compile(r"(?:click|tap|move)\s+(?:at|to)\s*\(?\s*\d{1,5}\s*[,x]\s*\d{1,5}", re.IGNORECASE),
    re.compile(r"\b(?:screen_?[xy]|pixel_?[xy])\s*[=:]\s*\d+", re.IGNORECASE),
)
_COMMIT_WORDS = re.compile(r"\b(save|commit|submit|send|publish|confirm purchase)\b", re.IGNORECASE)
_STOP_WORDS = re.compile(r"\b(stop|wait|pause|approval|approve|review)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ValidationIssue:
    """One stable, user-safe validation result."""

    code: str
    message: str
    artifact: str | None = None


@dataclass(frozen=True)
class BundleValidationReport:
    """Structural and security findings for one bundle directory."""

    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        """Return true only when no blocking finding exists."""
        return not self.errors


class GeneratedToolValidator(ast.NodeVisitor):
    """Reject Python capabilities outside deterministic JSON transformations."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.functions: set[str] = set()

    def validate(self, code: str, expected_entrypoints: set[str]) -> list[str]:
        """Parse code and return all static-policy failures."""
        try:
            tree = ast.parse(code, filename="tools.py")
        except SyntaxError as exc:
            return [f"tools.py is not valid Python: {exc.msg}"]
        self.visit(tree)
        missing = expected_entrypoints - self.functions
        if missing:
            self.errors.append(f"Missing declared tool entrypoints: {', '.join(sorted(missing))}")
        return self.errors

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root not in _ALLOWED_IMPORTS:
                self.errors.append(f"Forbidden import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        root = (node.module or "").split(".", 1)[0]
        if node.level or root not in _ALLOWED_IMPORTS:
            self.errors.append(f"Forbidden import: {'.' * node.level}{node.module or ''}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
            self.errors.append(f"Forbidden call: {node.func.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr in _FORBIDDEN_ATTRIBUTES or node.attr.startswith("__"):
            self.errors.append(f"Forbidden reflective attribute: {node.attr}")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.functions.add(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.errors.append(f"Async generated tools are forbidden: {node.name}")
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        self.errors.append("While loops are forbidden in generated tools")
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:  # noqa: N802
        self.errors.append("Generators are forbidden in generated tools")
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:  # noqa: N802
        self.errors.append("Generators are forbidden in generated tools")
        self.generic_visit(node)


class BundleValidator:
    """Validate a materialized bundle without executing generated code on the host."""

    def validate(self, version_root: Path) -> BundleValidationReport:
        """Return all structural, portability, conflict, and code-policy failures."""
        errors: list[ValidationIssue] = []
        missing = sorted(name for name in REQUIRED_ARTIFACTS if not (version_root / name).is_file())
        for name in missing:
            errors.append(ValidationIssue("missing_artifact", f"Required artifact is missing: {name}", name))
        if missing:
            return BundleValidationReport(tuple(errors))

        skill = (version_root / "SKILL.md").read_text(encoding="utf-8")
        sop = (version_root / "SOP.md").read_text(encoding="utf-8")
        errors.extend(self._validate_skill(skill))
        for name, content in (("SKILL.md", skill), ("SOP.md", sop)):
            if any(pattern.search(content) for pattern in _SELECTOR_PATTERNS):
                errors.append(
                    ValidationIssue(
                        "app_specific_locator",
                        "Coordinates or implementation-specific selectors are forbidden",
                        name,
                    )
                )

        input_schema = self._read_json(version_root / "inputs.schema.json", errors)
        if isinstance(input_schema, dict):
            errors.extend(self._validate_json_schema(input_schema, "inputs.schema.json"))

        procedure_payload = self._read_json(version_root / "procedure.json", errors)
        steps = self._validate_procedure(procedure_payload, errors)
        persistent_steps = [step for step in steps if step.persistent_action]
        if persistent_steps:
            if not all(step.requires_confirmation_before for step in persistent_steps):
                errors.append(
                    ValidationIssue(
                        "missing_confirmation_boundary",
                        "Every persistent action must require confirmation",
                        "procedure.json",
                    )
                )
            if not _COMMIT_WORDS.search(skill) or not _STOP_WORDS.search(skill):
                errors.append(
                    ValidationIssue(
                        "missing_confirmation_boundary",
                        "Skill must stop for explicit review before its persistent action",
                        "SKILL.md",
                    )
                )

        checks_payload = self._read_json(version_root / "checks.json", errors)
        self._validate_checks(checks_payload, errors)

        manifest_payload = self._read_json(version_root / "tool_manifest.json", errors)
        tools = self._validate_tool_manifest(manifest_payload, errors)
        code = (version_root / "tools.py").read_text(encoding="utf-8")
        code_sha256 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        if any(tool.code_sha256 != code_sha256 for tool in tools):
            errors.append(
                ValidationIssue(
                    "tool_code_hash_mismatch",
                    "Generated tool bytes do not match tool_manifest.json",
                    "tools.py",
                )
            )
        code_errors = GeneratedToolValidator().validate(code, {tool.entrypoint for tool in tools})
        errors.extend(ValidationIssue("unsafe_generated_code", message, "tools.py") for message in code_errors)
        tests_code = (version_root / "test_tools.py").read_text(encoding="utf-8")
        if tools and not tests_code.strip():
            errors.append(
                ValidationIssue(
                    "missing_tool_tests", "Generated tools require sandbox tests", "test_tools.py"
                )
            )
        elif tools:
            errors.extend(self._validate_tool_test_receipt(version_root, code, tests_code))

        eval_cases = self._read_json(version_root / "eval_cases.json", errors)
        if eval_cases is not None and not isinstance(eval_cases, list):
            errors.append(
                ValidationIssue(
                    "invalid_json_shape", "eval_cases.json must contain an array", "eval_cases.json"
                )
            )

        review_payload = self._read_json(version_root / "review.json", errors)
        conflicts = self._validate_review(review_payload, errors)
        if any(conflict.blocks_approval for conflict in conflicts):
            errors.append(
                ValidationIssue(
                    "blocking_conflict",
                    "Resolve every material source conflict before approval",
                    "review.json",
                )
            )
        return BundleValidationReport(tuple(_deduplicate(errors)))

    def _validate_tool_test_receipt(
        self, version_root: Path, code: str, tests_code: str
    ) -> list[ValidationIssue]:
        receipt_path = version_root / "tool_test_result.json"
        if not receipt_path.is_file():
            return [
                ValidationIssue(
                    "tool_tests_not_run",
                    "Generated tool tests must pass inside NemoClaw before approval",
                    "test_tools.py",
                )
            ]
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return [ValidationIssue("invalid_tool_test_result", f"Invalid tool test receipt: {exc}", receipt_path.name)]
        expected_code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        expected_tests_hash = hashlib.sha256(tests_code.encode("utf-8")).hexdigest()
        if (
            not isinstance(receipt, dict)
            or receipt.get("passed") is not True
            or receipt.get("code_sha256") != expected_code_hash
            or receipt.get("tests_sha256") != expected_tests_hash
        ):
            return [
                ValidationIssue(
                    "tool_tests_failed_or_stale",
                    "Sandbox tool tests failed or do not match current generated bytes",
                    receipt_path.name,
                )
            ]
        return []

    def _validate_skill(self, content: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        match = re.match(r"\A---\s*\n(?P<frontmatter>.*?)\n---\s*\n(?P<body>.*)\Z", content, re.DOTALL)
        if not match:
            return [ValidationIssue("invalid_skill_frontmatter", "SKILL.md requires YAML frontmatter", "SKILL.md")]
        description_match = re.search(r"^description:\s*(.+?)\s*$", match.group("frontmatter"), re.MULTILINE)
        if not description_match or not description_match.group(1).strip(" '\""):
            issues.append(ValidationIssue("invalid_skill_description", "Skill description is required", "SKILL.md"))
        elif len(description_match.group(1).strip(" '\"")) > 280:
            issues.append(
                ValidationIssue(
                    "invalid_skill_description",
                    "Skill description exceeds 280 characters",
                    "SKILL.md",
                )
            )
        body = match.group("body").strip()
        if not body:
            issues.append(ValidationIssue("empty_skill_body", "Skill procedure body is required", "SKILL.md"))
        return issues

    def _read_json(self, path: Path, errors: list[ValidationIssue]) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(ValidationIssue("invalid_json", f"Invalid JSON: {exc}", path.name))
            return None

    def _validate_json_schema(self, schema: dict[str, Any], artifact: str) -> list[ValidationIssue]:
        if schema.get("type") != "object" or not isinstance(schema.get("properties", {}), dict):
            return [
                ValidationIssue(
                    "invalid_json_schema",
                    "Input schema must describe an object with properties",
                    artifact,
                )
            ]
        required = schema.get("required", [])
        if not isinstance(required, list) or any(name not in schema.get("properties", {}) for name in required):
            return [ValidationIssue("invalid_json_schema", "Required fields must exist in properties", artifact)]
        return []

    def _validate_tool_manifest(
        self, payload: Any, errors: list[ValidationIssue]
    ) -> list[GeneratedTool]:
        if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
            errors.append(
                ValidationIssue(
                    "invalid_tool_manifest",
                    "tool_manifest.json requires a tools array",
                    "tool_manifest.json",
                )
            )
            return []
        try:
            return TypeAdapter(list[GeneratedTool]).validate_python(payload["tools"])
        except ValidationError as exc:
            errors.append(
                ValidationIssue(
                    "invalid_tool_manifest",
                    f"Tool manifest failed schema validation: {exc}",
                    "tool_manifest.json",
                )
            )
            return []

    def _validate_checks(self, payload: Any, errors: list[ValidationIssue]) -> None:
        if not isinstance(payload, dict):
            errors.append(
                ValidationIssue("invalid_checks", "checks.json must contain an object", "checks.json")
            )
            return
        for field in ("preconditions", "completion_checks"):
            value = payload.get(field)
            if not isinstance(value, list):
                errors.append(
                    ValidationIssue(
                        "invalid_checks", f"checks.json requires a {field} array", "checks.json"
                    )
                )
                continue
            try:
                checks = TypeAdapter(list[ProcedureCheck]).validate_python(value)
            except ValidationError as exc:
                errors.append(
                    ValidationIssue(
                        "invalid_checks",
                        f"{field} failed schema validation: {exc}",
                        "checks.json",
                    )
                )
                continue
            if field == "completion_checks" and not checks:
                errors.append(
                    ValidationIssue(
                        "invalid_checks",
                        "At least one completion check is required",
                        "checks.json",
                    )
                )
    def _validate_procedure(
        self, payload: Any, errors: list[ValidationIssue]
    ) -> list[ProcedureStep]:
        if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
            errors.append(
                ValidationIssue(
                    "invalid_procedure", "procedure.json requires a steps array", "procedure.json"
                )
            )
            return []
        try:
            steps = TypeAdapter(list[ProcedureStep]).validate_python(payload["steps"])
        except ValidationError as exc:
            errors.append(
                ValidationIssue(
                    "invalid_procedure",
                    f"Procedure steps failed schema validation: {exc}",
                    "procedure.json",
                )
            )
            return []
        if not steps:
            errors.append(
                ValidationIssue("invalid_procedure", "At least one semantic step is required", "procedure.json")
            )
        return steps

    def _validate_review(self, payload: Any, errors: list[ValidationIssue]) -> list[ReviewConflict]:
        if not isinstance(payload, dict) or not isinstance(payload.get("conflicts"), list):
            errors.append(ValidationIssue("invalid_review", "review.json requires a conflicts array", "review.json"))
            return []
        try:
            return TypeAdapter(list[ReviewConflict]).validate_python(payload["conflicts"])
        except ValidationError as exc:
            errors.append(
                ValidationIssue(
                    "invalid_review",
                    f"Review conflicts failed schema validation: {exc}",
                    "review.json",
                )
            )
            return []


def _deduplicate(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return list(dict.fromkeys(issues))
