"""The app factory wires the rule-store copy listeners (T-0034, ADR 0004 §2).

Runs in a fresh interpreter so the check proves ``create_app`` itself imports the
validation service, not an earlier test module.
"""

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

SCRIPT = """
from app.core.config import Settings
from app.core.module_loader import service as module_settings
from app.main import create_app
from app.modules.projects import service as projects

create_app(Settings(environment="test"))

from app.modules.validation import service as rule_store

assert rule_store._on_modules_enabled in module_settings._ENABLE_LISTENERS
assert rule_store._on_project_created in projects._CREATED_LISTENERS
print("ok")
"""


def test_the_app_factory_registers_the_validation_copy_listeners() -> None:
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT], cwd=BACKEND, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
