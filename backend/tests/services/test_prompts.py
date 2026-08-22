from app.services.prompts import SYSTEM_EVIDENCE_POLICY, build_prompt, starter_templates


def test_all_starter_templates_enforce_visible_evidence() -> None:
    templates = starter_templates()

    assert {item["slug"] for item in templates} == {"general", "manager", "chef"}
    prompt = build_prompt(templates[0], "IMG_001.png")
    assert SYSTEM_EVIDENCE_POLICY in prompt.system
    assert "not_visible" in prompt.system
    assert "气味" not in prompt.user


def test_prompt_contains_every_rule_id_and_fixed_schema() -> None:
    template = starter_templates()[2]

    prompt = build_prompt(template, "chef.png")

    for rule in template["rules"]:
        assert rule["rule_id"] in prompt.user
    assert "schema_version" in prompt.user
    assert "chef.png" in prompt.user


def test_starter_templates_return_independent_rule_lists() -> None:
    first = starter_templates()
    first[0]["rules"].append({"rule_id": "extra", "name": "extra", "text": "extra"})

    second = starter_templates()

    assert all(rule["rule_id"] != "extra" for rule in second[0]["rules"])
