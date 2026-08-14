import sys
from pathlib import Path

# Make backend/ importable from tests/
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest


@pytest.fixture
def sample_bank():
    """A small, hand-crafted bank for tests that shouldn't depend on
    the real (large) project_bank.json -- keeps unit tests fast and
    isolated from content changes to the real data file."""
    return {
        "proj_a": {
            "title": "Project A -- Test Fixture",
            "github": "testuser/project-a",
            "domain": ["fintech"],
            "keywords": ["python", "sql", "machine learning"],
            "bullets": [
                "Built a test system using Python and SQL.",
                "Achieved a 95\\% accuracy score on the test set.",
                "Deployed via FastAPI with automated testing.",
            ],
        },
        "proj_b": {
            "title": "Project B -- Underscore_Test & Special Chars",
            "github": "testuser/project_b_with_underscores",
            "domain": ["healthcare"],
            "keywords": ["nlp", "healthcare"],
            "bullets": [
                "Processed data with 50\\% reduction in errors.",
                "Used R\\&D methodology for the analysis.",
            ],
        },
    }


@pytest.fixture
def real_bank():
    """Loads the actual production project_bank.json -- used for
    integration-style tests that need to validate the real content."""
    from resume_builder import load_project_bank
    return load_project_bank()
