"""Tests for the transcript -> RequirementsSpecification extractor.

Uses NullTranscriptExtractionBackend throughout — no live model calls,
matching the existing Stage 0 test convention (test_stage_0_knowledge_acquisition.py).
"""
from __future__ import annotations

import pytest

from msb_v3.uac.models import RequirementsSpecification
from msb_v3.uac.transcript_requirements_extractor import (
    NullTranscriptExtractionBackend,
    TranscriptExtractionError,
    extract_requirements_from_transcript,
)


def test_rejects_empty_transcript():
    backend = NullTranscriptExtractionBackend(canned={"profession": "bookkeeper", "jurisdiction": "Minnesota"})
    with pytest.raises(TranscriptExtractionError, match="empty transcript"):
        extract_requirements_from_transcript("", backend)


def test_rejects_whitespace_only_transcript():
    backend = NullTranscriptExtractionBackend(canned={"profession": "bookkeeper", "jurisdiction": "Minnesota"})
    with pytest.raises(TranscriptExtractionError, match="empty transcript"):
        extract_requirements_from_transcript("   \n\t  ", backend)


def test_rejects_missing_profession_rather_than_fabricating():
    backend = NullTranscriptExtractionBackend(canned={"profession": None, "jurisdiction": "Minnesota"})
    with pytest.raises(TranscriptExtractionError, match="profession"):
        extract_requirements_from_transcript("some real transcript text", backend)


def test_rejects_missing_jurisdiction_rather_than_fabricating():
    backend = NullTranscriptExtractionBackend(canned={"profession": "bookkeeper", "jurisdiction": None})
    with pytest.raises(TranscriptExtractionError, match="jurisdiction"):
        extract_requirements_from_transcript("some real transcript text", backend)


def test_extracts_full_requirements_specification():
    backend = NullTranscriptExtractionBackend(
        canned={
            "profession": "residential electrician",
            "jurisdiction": "Minnesota, USA",
            "industry": "residential construction",
            "organization_size": "1-5 people",
            "constraints": ["no dedicated back-office staff"],
            "existing_tools": ["Jobber"],
            "risk_tolerance": "low",
        }
    )
    reqs = extract_requirements_from_transcript("some real transcript text", backend)

    assert isinstance(reqs, RequirementsSpecification)
    assert reqs.profession == "residential electrician"
    assert reqs.jurisdiction == "Minnesota, USA"
    assert reqs.industry == "residential construction"
    assert reqs.organization_size == "1-5 people"
    assert reqs.constraints == ["no dedicated back-office staff"]
    assert reqs.existing_tools == ["Jobber"]
    assert reqs.risk_tolerance == "low"


def test_optional_fields_default_when_not_stated():
    backend = NullTranscriptExtractionBackend(canned={"profession": "bookkeeper", "jurisdiction": "Minnesota, USA"})
    reqs = extract_requirements_from_transcript("some real transcript text", backend)

    assert reqs.industry is None
    assert reqs.organization_size is None
    assert reqs.constraints == []
    assert reqs.existing_tools == []
    assert reqs.risk_tolerance is None


def test_constraints_and_tools_coerced_to_string_lists():
    backend = NullTranscriptExtractionBackend(
        canned={
            "profession": "bookkeeper",
            "jurisdiction": "Minnesota, USA",
            "constraints": "single constraint as a string, not a list",
        }
    )
    reqs = extract_requirements_from_transcript("some real transcript text", backend)

    assert reqs.constraints == ["single constraint as a string, not a list"]
