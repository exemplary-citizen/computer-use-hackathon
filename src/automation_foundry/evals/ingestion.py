"""Reviewer-confirmed ingestion scoring against versioned gold annotations."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GoldStep(BaseModel):
    """One annotated business-critical semantic step."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    intent: str = Field(min_length=1)
    confirmation_boundary: bool = False


class GoldFixture(BaseModel):
    """Versioned expected behavior for one supported ingestion mode."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    fixture_name: str
    source_mode: str
    valid_input: bool
    critical_steps: list[GoldStep] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    completion_checks: list[str] = Field(default_factory=list)
    expected_conflicts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_fixture_shape(self) -> GoldFixture:
        """Require valid workflows to annotate a confirmation boundary."""
        if self.valid_input and not any(step.confirmation_boundary for step in self.critical_steps):
            raise ValueError("Valid gold fixtures require a confirmation boundary")
        return self


class IngestionObservation(BaseModel):
    """Reviewer-confirmed matches for one generated bundle."""

    model_config = ConfigDict(extra="forbid")

    fixture_name: str
    matched_step_ids: set[str] = Field(default_factory=set)
    matched_confirmation_ids: set[str] = Field(default_factory=set)
    evidence_backed_step_ids: set[str] = Field(default_factory=set)
    visible_conflicts: set[str] = Field(default_factory=set)
    generated_skill: str = ""
    rejected_before_generation: bool = False


class IngestionScore(BaseModel):
    """Aggregate release-gate metrics and exact failure reasons."""

    critical_step_recall: float = Field(ge=0, le=1)
    confirmation_boundary_recall: float = Field(ge=0, le=1)
    evidence_coverage: float = Field(ge=0, le=1)
    conflicts_visible: bool
    malformed_inputs_rejected: bool
    portable_skills: bool
    passed: bool
    failures: list[str]


_LOCATOR_PATTERNS = (
    re.compile(r"\b(?:xpath|css selector|dom selector)\b", re.IGNORECASE),
    re.compile(r"(?:click|tap|move)\s+(?:at|to)\s*\(?\s*\d{1,5}\s*[,x]\s*\d{1,5}", re.IGNORECASE),
    re.compile(r"\b(?:screen_?[xy]|pixel_?[xy])\s*[=:]\s*\d+", re.IGNORECASE),
    re.compile(r"\bcrm\s*b\b", re.IGNORECASE),
)


def load_gold_fixtures(root: Path) -> list[GoldFixture]:
    """Load every gold.json below a fixture root in stable order."""
    return [
        GoldFixture.model_validate_json(path.read_text(encoding="utf-8")) for path in sorted(root.glob("*/gold.json"))
    ]


def score_ingestion(fixtures: list[GoldFixture], observations: list[IngestionObservation]) -> IngestionScore:
    """Calculate aggregate thresholds from reviewer-confirmed semantic matches."""
    fixture_by_name = {fixture.fixture_name: fixture for fixture in fixtures}
    observation_by_name = {observation.fixture_name: observation for observation in observations}
    if set(fixture_by_name) != set(observation_by_name):
        missing = sorted(set(fixture_by_name) - set(observation_by_name))
        extra = sorted(set(observation_by_name) - set(fixture_by_name))
        raise ValueError(f"Observation set differs from gold fixtures; missing={missing}, extra={extra}")

    total_steps = matched_steps = total_boundaries = matched_boundaries = 0
    total_evidence = matched_evidence = 0
    conflicts_visible = malformed_rejected = portable = True
    failures: list[str] = []
    for name, fixture in fixture_by_name.items():
        observation = observation_by_name[name]
        gold_step_ids = {step.id for step in fixture.critical_steps}
        boundary_ids = {step.id for step in fixture.critical_steps if step.confirmation_boundary}
        _reject_unknown_ids(name, observation.matched_step_ids, gold_step_ids, "step")
        _reject_unknown_ids(name, observation.matched_confirmation_ids, boundary_ids, "confirmation")
        _reject_unknown_ids(name, observation.evidence_backed_step_ids, gold_step_ids, "evidence")
        if fixture.valid_input:
            total_steps += len(gold_step_ids)
            matched_steps += len(observation.matched_step_ids)
            total_boundaries += len(boundary_ids)
            matched_boundaries += len(observation.matched_confirmation_ids)
            total_evidence += len(observation.matched_step_ids)
            matched_evidence += len(observation.matched_step_ids & observation.evidence_backed_step_ids)
            missing_conflicts = set(fixture.expected_conflicts) - observation.visible_conflicts
            if missing_conflicts:
                conflicts_visible = False
                failures.append(f"{name}: missing conflicts {sorted(missing_conflicts)}")
            if any(pattern.search(observation.generated_skill) for pattern in _LOCATOR_PATTERNS):
                portable = False
                failures.append(f"{name}: skill contains app-specific locator or CRM B knowledge")
        elif not observation.rejected_before_generation:
            malformed_rejected = False
            failures.append(f"{name}: malformed input reached generation")

    critical_recall = _ratio(matched_steps, total_steps)
    confirmation_recall = _ratio(matched_boundaries, total_boundaries)
    evidence_coverage = _ratio(matched_evidence, total_evidence)
    if critical_recall < 0.9:
        failures.append(f"Critical-step recall {critical_recall:.1%} is below 90%")
    if confirmation_recall < 1:
        failures.append(f"Confirmation-boundary recall {confirmation_recall:.1%} is below 100%")
    if evidence_coverage < 1:
        failures.append(f"Evidence coverage {evidence_coverage:.1%} is below 100%")
    passed = (
        critical_recall >= 0.9
        and confirmation_recall == 1
        and evidence_coverage == 1
        and conflicts_visible
        and malformed_rejected
        and portable
    )
    return IngestionScore(
        critical_step_recall=critical_recall,
        confirmation_boundary_recall=confirmation_recall,
        evidence_coverage=evidence_coverage,
        conflicts_visible=conflicts_visible,
        malformed_inputs_rejected=malformed_rejected,
        portable_skills=portable,
        passed=passed,
        failures=failures,
    )


def _reject_unknown_ids(name: str, actual: set[str], allowed: set[str], category: str) -> None:
    unknown = actual - allowed
    if unknown:
        raise ValueError(f"{name}: unknown {category} IDs: {sorted(unknown)}")


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0
