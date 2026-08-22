from app.core.db import Base, make_engine, make_session_factory
from app.repositories import SettingsRepository, TemplateRepository


def test_template_update_creates_immutable_version(tmp_path) -> None:
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)

    with sessions() as session:
        repo = TemplateRepository(session)
        template = repo.create("通用仪容仪表", "员工可见项目巡检")
        first = repo.add_version(
            template.id,
            [{"rule_id": "badge", "name": "工牌"}],
            {},
        )
        second = repo.add_version(
            template.id,
            [{"rule_id": "uniform", "name": "制服"}],
            {},
        )
        session.commit()

        assert first.version == 1
        assert second.version == 2
        assert repo.get_version(first.id).rules_json[0]["rule_id"] == "badge"
        assert template.current_version_id == second.id


def test_settings_repository_round_trips_public_json(tmp_path) -> None:
    engine = make_engine(tmp_path / "settings.db")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)

    with sessions() as session:
        repo = SettingsRepository(session)
        repo.set("ark", {"model": "doubao-seed-test", "concurrency": 3})
        session.commit()

    with sessions() as session:
        value = SettingsRepository(session).get("ark")
        assert value == {"model": "doubao-seed-test", "concurrency": 3}
