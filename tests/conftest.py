import os
import tempfile

_TEST_DIRECTORY = tempfile.mkdtemp(prefix="purchasing-agent-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIRECTORY}/tests.db"
os.environ["CHECKPOINT_DB"] = f"{_TEST_DIRECTORY}/checkpoints.db"
os.environ["LLM_ENABLED"] = "false"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from purchasing.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    command.upgrade(Config("alembic.ini"), "head")
    with TestClient(app) as test_client:
        yield test_client
