from server.seedbuilder import build_repo_from_draft
from novel_engine.story_contract import load_story_contract


def test_seed_draft_can_supply_existing_outline_contract_layer():
    draft = {
        "worldBible": {
            "settingCore": "一个会被众人信念补写历史的修仙世界。",
            "geography": "青云山。",
            "culture": "宗门林立。",
            "physicsRules": ["信念不能无成本改写历史。"],
            "protagonistWant": "活下去并保住宗门。",
            "theme": "谎言与真实。",
            "candidateEndings": [],
        },
        "personas": [],
        "outlineContract": {
            "version": 1,
            "template_id": "",
            "story_scale": {
                "id": "serial_long",
                "volume_count_min": 9,
                "volume_count_max": 9,
                "arcs_per_volume": 2,
                "chapter_target_per_arc": 20,
                "planning_mode": "rolling",
            },
            "volume_blueprint": [
                {
                    "title": "第一卷·杂役竟是老祖使者",
                    "short_goal": "挡住灭门危机。",
                    "obstacle": "三宗围山。",
                    "conflict_chain": ["编出老祖", "补史显迹"],
                    "key_twist": "虚构历史成为真实。",
                    "gain_and_hook": "青云宗成为一郡最强。",
                }
            ],
            "active_unit": {
                "locked": True,
                "name": "青云宗保卫与重建",
                "unit_goal": "完成第一卷闭环。",
                "chapter_steps": ["石碑开裂", "全宗跪拜"],
            },
        },
    }

    repo = build_repo_from_draft(draft)
    contract = load_story_contract(repo)

    assert contract is not None
    assert contract["story_scale"]["volume_count_min"] == 9
    assert contract["volume_blueprint"][0]["title"] == "第一卷·杂役竟是老祖使者"
    assert contract["active_unit"]["locked"] is True
