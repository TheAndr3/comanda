import pytest

from comanda.config import Settings
from comanda.data.seed import seed_database
from comanda.tools import ToolContext


@pytest.fixture
def repo(tmp_path):
    return seed_database(tmp_path / "test.db")


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "test.db", runs_dir=tmp_path / "runs")


@pytest.fixture
def ctx_for(repo, settings):
    def make(customer_id: str) -> ToolContext:
        return ToolContext(repo=repo, settings=settings, customer_id=customer_id)

    return make
