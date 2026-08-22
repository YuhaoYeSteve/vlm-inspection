from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_upgrade_creates_initial_schema(tmp_path) -> None:
    backend_dir = Path(__file__).parents[2]
    config = Config(backend_dir / "alembic.ini")
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    database_path = tmp_path / "migration.db"
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    tables = set(inspect(create_engine(f"sqlite:///{database_path}")).get_table_names())
    assert {
        "app_settings",
        "inference_attempts",
        "inference_results",
        "prompt_templates",
        "prompt_template_versions",
        "report_exports",
        "task_images",
        "tasks",
    } <= tables
