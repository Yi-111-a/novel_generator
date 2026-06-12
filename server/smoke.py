"""端到端冒烟测试：用 TestClient 走一遍 HttpAdapter 契约。

运行：python -m server.smoke
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi.testclient import TestClient

from server.app import app


def _ready_draft() -> dict:
    """一份达标的种子（用于绕过需真实 LLM 的共创对话来测试其余契约）。"""
    def persona(pid, name, want, vname, flaw):
        return {
            "id": pid, "name": name, "want": want,
            "values": [{"name": vname, "weight": 0.9}], "fatalFlaw": flaw,
            "obstacles": ["阻碍一"], "costThreshold": "可舍命不可舍真相",
            "voice": "冷峭少言", "mannerisms": ["垂眸"], "motifObjects": [],
            "arcState": "", "costLedger": [],
        }

    def ending(eid, summary, w):
        return {"id": eid, "summary": summary, "themeExpression": "复仇的代价", "requiredConditions": ["真相公开"], "activeWeight": w}

    return {
        "worldBible": {
            "settingCore": "青冥修真界，二十年前一场灭门旧案。",
            "geography": "九峰环抱的青冥山脉。",
            "culture": "门第森严，重道义。",
            "physicsRules": ["没有手机电网", "凡人不能御剑"],
            "protagonistWant": "查清真相、手刃真凶",
            "theme": "复仇的代价",
            "candidateEndings": [ending("end_a", "血债血偿但自身万劫不复", 0.55), ending("end_b", "最后收剑，以真相终结仇恨", 0.45)],
        },
        "personas": [persona("shen_yan", "沈砚", "查清真相", "道义", "偏执"), persona("xuan_shuang", "玄霜真人", "掩盖真相", "权位", "多疑")],
        "completeness": {"ready": False, "checklist": []},
    }


def main() -> None:
    c = TestClient(app)

    # 配置
    assert c.get("/api/config").status_code == 200
    assert c.post("/api/config/test", json={"llmApiKey": "x", "baseUrl": "y", "modelName": "m"}).json()["ok"] is True

    # 新建项目
    p = c.post("/api/projects", json={"title": "测试小说"}).json()
    pid = p["id"]
    assert p["status"] == "seeding"
    print("项目:", pid, p["status"])

    # 共创不做离线兜底：未配置 key 时直接报错（HTTP 400）；已配置则走真实 LLM。
    cfg = c.get("/api/config").json()
    if not cfg.get("llmApiKey"):
        r = c.post(f"/api/projects/{pid}/seed/chat", json={"content": "我想写个故事"})
        assert r.status_code == 400, f"无 key 共创应报错，实际 {r.status_code}"
        print("无 key 共创 → 正确报错:", r.json().get("detail", "")[:24])
    else:
        print("检测到已配置 key：共创走真实 LLM（冒烟不发实网请求，直接灌种子验证其余契约）")

    # 直接灌入一份达标种子（绕过需真实 LLM 的对话），验证其余契约
    assert c.put(f"/api/projects/{pid}/seed/draft", json=_ready_draft()).json()["ok"] is True
    draft = c.get(f"/api/projects/{pid}/seed/draft").json()
    assert draft["completeness"]["ready"], "灌入的种子应达标"
    print("种子 ready；角色数:", len(draft["personas"]))

    # 锁定 → 开始写作
    assert c.post(f"/api/projects/{pid}/seed/lock").json()["ok"] is True
    metas = {m["id"]: m for m in c.get("/api/projects").json()}
    assert metas[pid]["status"] == "writing"

    # 单步推进几次，产出事件
    for _ in range(4):
        assert c.post(f"/api/projects/{pid}/control", json={"action": "step"}).json()["ok"] is True
    world = c.get(f"/api/projects/{pid}/world").json()
    print("事件数:", len(world["events"]), "tick:", world["tick"])
    assert len(world["events"]) >= 1

    # 运行态各接口
    personas = c.get(f"/api/projects/{pid}/personas").json()
    assert personas and "fatalFlaw" in personas[0]
    aid = personas[0]["id"]
    knowledge = c.get(f"/api/projects/{pid}/knowledge/{aid}").json()
    print("角色账本条数:", len(knowledge))

    threads = c.get(f"/api/projects/{pid}/threads").json()
    endings = c.get(f"/api/projects/{pid}/endings").json()
    foreshadows = c.get(f"/api/projects/{pid}/foreshadows").json()
    scenes = c.get(f"/api/projects/{pid}/scenes").json()
    reader = c.get(f"/api/projects/{pid}/reader-knowledge?upto=99").json()
    print("threads/endings/foreshadows/scenes/reader:", len(threads), len(endings), len(foreshadows), len(scenes), len(reader))
    assert endings and threads

    # 校验 camelCase 契约关键字段存在
    assert "centralQuestion" in threads[0]
    assert "activeWeight" in endings[0]
    if foreshadows:
        assert {"foreshadowId", "linkedFactId", "mustResolve"} <= set(foreshadows[0])
    if scenes:
        assert {"discourseOrder", "proseText", "newlyRevealed"} <= set(scenes[0])

    # 上帝动作
    assert c.post(f"/api/projects/{pid}/god", json={"kind": "set_thread_priority", "threadId": threads[0]["threadId"], "weight": 0.7}).json()["ok"] is True

    # 上帝新增实体：角色应进入 personas
    n_before = len(c.get(f"/api/projects/{pid}/personas").json())
    assert c.post(f"/api/projects/{pid}/god", json={"kind": "add_entity", "entityType": "character", "name": "客卿·墨"}).json()["ok"] is True
    personas2 = c.get(f"/api/projects/{pid}/personas").json()
    assert len(personas2) == n_before + 1
    assert any(p["name"] == "客卿·墨" for p in personas2)
    print("上帝新增角色 → personas:", n_before, "→", len(personas2))

    # 删除项目
    assert c.delete(f"/api/projects/{pid}").json()["ok"] is True
    assert pid not in {m["id"] for m in c.get("/api/projects").json()}

    print("\n[OK] 冒烟测试全部通过：HttpAdapter 契约可用。")


if __name__ == "__main__":
    main()
