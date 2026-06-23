"""Story contract for outline generation.

The planner is intentionally generic, but long-form web fiction needs one
strong promise at a time: finish the current unit before teasing every future
thread.  This module stores that promise as a small JSON bible section and
turns it into prompt clauses for parts, arcs and chapters.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .repository import Repository

CONTRACT_SECTION = "outline_contract"
CONTRACT_SOURCE = "contract"


@dataclass(frozen=True)
class StoryScale:
    id: str
    volume_count_min: int
    volume_count_max: int
    arcs_per_volume: int
    chapter_target_per_arc: int
    planning_mode: str = "rolling"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "volume_count_min": self.volume_count_min,
            "volume_count_max": self.volume_count_max,
            "arcs_per_volume": self.arcs_per_volume,
            "chapter_target_per_arc": self.chapter_target_per_arc,
            "planning_mode": self.planning_mode,
        }

    @property
    def suggested_volume_count(self) -> int:
        return max(self.volume_count_min, min(self.volume_count_max, (self.volume_count_min + self.volume_count_max) // 2))


STORY_SCALES: dict[str, StoryScale] = {
    "short": StoryScale("short", 3, 5, 2, 5),
    "standard": StoryScale("standard", 5, 8, 2, 6),
    "serial_long": StoryScale("serial_long", 8, 12, 2, 8),
    "epic": StoryScale("epic", 12, 20, 3, 8),
}


def resolve_story_scale(value: Any = None, *, template_id: str = "",
                        contract: dict[str, Any] | None = None) -> StoryScale:
    raw = value if value is not None else ((contract or {}).get("story_scale") or None)
    if isinstance(raw, StoryScale):
        return raw
    if isinstance(raw, str):
        found = STORY_SCALES.get(raw.strip())
        if found:
            return found
    if isinstance(raw, dict):
        sid = str(raw.get("id") or "").strip()
        base = STORY_SCALES.get(sid) or STORY_SCALES.get(
            "serial_long" if template_id == "yin_yang_shouhou" else "standard"
        )
        try:
            vmin = int(raw.get("volume_count_min", base.volume_count_min))
            vmax = int(raw.get("volume_count_max", base.volume_count_max))
            arcs = int(raw.get("arcs_per_volume", base.arcs_per_volume))
            chapters = int(raw.get("chapter_target_per_arc", base.chapter_target_per_arc))
        except Exception:
            return base
        if vmax < vmin:
            vmax = vmin
        return StoryScale(
            sid or base.id,
            max(1, vmin),
            max(1, vmax),
            max(1, arcs),
            max(1, chapters),
            str(raw.get("planning_mode") or "rolling"),
        )
    return STORY_SCALES["serial_long"] if template_id == "yin_yang_shouhou" else STORY_SCALES["standard"]

YIN_YANG_CARD_OVERRIDES: dict[str, dict[str, str]] = {
    "p_chen": {
        "name": "陈野",
        "one_liner": "二十五岁，前客服主管；失业、分手、房租到期后继承无忧售后服务有限公司，被迫处理死人差评。",
        "appearance": "男，25岁。瘦削清醒，常穿便宜夹克和洗旧衬衫，眼下有熬夜黑眼圈；紧张时会整理旧工牌，说狠话前反而先露出客服式微笑。",
        "social_role": "前电商客服主管，刚被公司裁员，房租到期，被迫接手二叔留下的无忧售后服务有限公司。开局不是阴司老员工，也没有成熟法术；他靠客服流程、套话、投诉处理经验和阴阳后台处理第一单。",
        "psychology": "嘴毒、抗压强、职业病重。起初只想活下去、还房租、别被警方当凶手；接到林晚差评后逐渐形成执念：死人差评必须处理到五星，恶人欠下的账必须结清。",
        "backstory": "陈野原本是普通客服主管，熟悉话术、工单、投诉升级和客户安抚。公司裁员后，女友嫌他没前途分手，房租也快到期。他收到二叔遗留店铺的消息，来到老城区破店「无忧售后服务有限公司」，看见门口贴着「差评必回，生死不误」。午夜十二点，老电脑自动开机，第一条差评来自已死亡三年的林晚：我已经死了，为什么凶手还在用我的脸活着？",
        "arc": "从只想糊口、怕麻烦的失业客服，成长为把玄学事件当售后工单处理的阴阳售后员；通过一单单五星结算获得权限、人脉和信念，最终成为能重开人间因果秩序的阴阳仲裁官。",
    },
    "p_shen": {
        "name": "沈知夏",
        "one_liner": "刑警队长，理性冷静，不信鬼神；前期怀疑陈野，后期成为官方合作人。",
        "backstory": "沈知夏是江州市刑警队长，习惯用证据说话。林晚案中，她发现陈野总能提前触及命案关键线索，因此首先怀疑他是凶手同伙或诈骗者。随着尸骨、替身和证据链逐步闭合，她被迫承认陈野的线索来源异常但有效。她的父亲多年前牺牲，死亡档案存在疑点；这条隐藏线后期会由来自父亲的差评引爆。",
        "arc": "从怀疑陈野、只相信卷宗证据，到建立特殊协作小组，成为陈野在阳间最关键的官方合作人；后期必须面对父亲旧案和阴阳秩序的冲突。",
    },
    "p_lupan": {
        "name": "陆判",
        "one_liner": "阴间派来的实习判官，懂规则不懂现代社会，监督陈野处理工单。",
        "backstory": "陆判是阴司派到无忧售后局后台的实习判官，负责解释规则、监督陈野不要违规。他熟悉阴司条例，却不懂现代社会和客服流程，常把地府律令、绩效考核、阳间外卖混成一团。",
        "arc": "从只会照章办事的实习判官，变成懂得在规则缝隙里替亡者争取公道的搭档。",
    },
    "p_linwan": {
        "name": "林晚",
        "one_liner": "第一单亡者客户，已死亡三年，投诉丈夫和整容替身冒用她的脸与身份。",
        "appearance": "作为亡者客户，她主要以后台差评、记忆碎片、冷静到吓人的文字出现；被提及时常关联断裂婚戒、红色雨伞和别墅地下室。",
        "social_role": "林晚是第一案受害者。真正的她三年前已被丈夫和整容替身合谋杀害，尸骨埋在锦澜湾别墅地下室；活在丈夫身边的「林晚」是假林晚。",
        "backstory": "林晚被丈夫与整容替身合谋杀害。凶手把尸骨藏进锦澜湾别墅地下室，又让假林晚冒用身份继承遗产。三年后，她在阴阳售后平台留下第一条一星差评：我已经死了，为什么凶手还在用我的脸活着？",
        "arc": "从怨气濒临失控的一星客户，到在陈野完成清算后改为五星并解脱；她的五星结算给陈野带来第一项奖励「亡者回执」。",
    },
    "p_tianming": {
        "name": "天命公司",
        "one_liner": "长线反派组织，替富人改命、借寿、替死、换身份，批量制造死人差评。",
        "appearance": "组织实体，不作为第一案登场人物正面出现；前期只能以合同、名片、体检报告、被涂黑字段等影子形式露出。",
        "social_role": "阳间反派组织，包装成命运咨询、体检、保险和高端私人服务，实则利用地府漏洞为富人转嫁业债与死亡风险。",
        "psychology": "组织逻辑是把命数、寿命和因果全部标价，认为穷人的命可以被替换，低估死人差评聚合后的反噬。",
        "backstory": "天命公司长期利用地府系统漏洞替富人改命、借寿、替死、换身份。前期它不抢林晚案主舞台，只在多个单元案背后留下代理人和被涂黑的痕迹；中后期才显露完整黑幕。",
        "arc": "从隐藏在单元案后的影子，到被陈野和沈知夏逐步逼出阳间网络，最终在地府仲裁庭被集体差评清算。",
    },
}


def load_story_contract(repo: Repository) -> dict[str, Any] | None:
    rows = [
        r for r in repo.list_bible_sections(CONTRACT_SECTION)
        if r.get("source") == CONTRACT_SOURCE and (r.get("body_full") or "").strip()
    ]
    if not rows:
        return None
    try:
        data = json.loads(rows[-1]["body_full"])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def save_story_contract(repo: Repository, contract: dict[str, Any]) -> None:
    repo.conn.execute(
        "DELETE FROM world_bible_sections WHERE section=? AND source=?",
        (CONTRACT_SECTION, CONTRACT_SOURCE),
    )
    repo.conn.commit()
    repo.add_bible_section(
        CONTRACT_SECTION,
        "三层大纲合同",
        json.dumps(contract, ensure_ascii=False, indent=2),
        source=CONTRACT_SOURCE,
        summary="总纲、分卷、当前单元锁定、信息释放闸门。",
    )


def ensure_story_contract(repo: Repository, template=None, theme: str = "") -> dict[str, Any]:
    existing = load_story_contract(repo)
    if existing:
        template_id = existing.get("template_id") or (getattr(template, "id", "") if template else "")
        changed = _normalize_contract(existing, template_id=template_id)
        if changed:
            save_story_contract(repo, existing)
        return existing
    contract = build_default_contract(repo, template=template, theme=theme)
    _normalize_contract(contract, template_id=contract.get("template_id", ""))
    save_story_contract(repo, contract)
    return contract


def _normalize_contract(contract: dict[str, Any], *, template_id: str = "") -> bool:
    changed = False
    tid = str(contract.get("template_id") or template_id or "")
    scale = resolve_story_scale(contract=contract, template_id=tid)
    if contract.get("story_scale") != scale.to_dict():
        contract["story_scale"] = scale.to_dict()
        changed = True
    if "volume_blueprint" not in contract:
        contract["volume_blueprint"] = []
        changed = True
    if tid == "yin_yang_shouhou":
        changed = _merge_yin_yang_volume_blueprint(contract) or changed
    else:
        vols = contract.get("volume_blueprint") or []
        for idx, vol in enumerate(vols):
            if isinstance(vol, dict):
                changed = _normalize_volume(vol, idx + 1) or changed
    return changed


def _normalize_volume(vol: dict[str, Any], seq: int) -> bool:
    changed = False
    defaults = {
        "title": f"第{seq}卷",
        "short_goal": "",
        "obstacle": "",
        "conflict_chain": [],
        "key_twist": "",
        "gain_and_hook": "",
        "allowed": [],
        "shadow_only": [],
        "forbidden": [],
    }
    for key, value in defaults.items():
        if key not in vol:
            vol[key] = value
            changed = True
    if not isinstance(vol.get("conflict_chain"), list):
        vol["conflict_chain"] = [str(vol.get("conflict_chain") or "").strip()]
        changed = True
    for key in ("allowed", "shadow_only", "forbidden"):
        if not isinstance(vol.get(key), list):
            vol[key] = [str(vol.get(key) or "").strip()]
            changed = True
    return changed


def apply_story_contract_card_overrides(repo: Repository, contract: dict[str, Any] | None = None) -> None:
    contract = contract or load_story_contract(repo) or {}
    if contract.get("template_id") != "yin_yang_shouhou":
        return
    for agent_id, fields in YIN_YANG_CARD_OVERRIDES.items():
        if repo.get_card_for_agent(agent_id) is None:
            continue
        sets = []
        args = []
        for key, value in fields.items():
            sets.append(f"{key}=?")
            args.append(value)
        args.append(agent_id)
        repo.conn.execute(f"UPDATE character_cards SET {', '.join(sets)} WHERE agent_id=?", args)
        if fields.get("name"):
            repo.conn.execute("UPDATE persona SET name=? WHERE agent_id=?", (fields["name"], agent_id))
            repo.conn.execute("UPDATE entities SET name=? WHERE entity_id=?", (fields["name"], agent_id))
            repo.conn.execute(
                "UPDATE character_names SET primary_name=?, short_name=? WHERE agent_id=?",
                (fields["name"], fields["name"], agent_id),
            )
    repo.conn.commit()


def build_default_contract(repo: Repository, template=None, theme: str = "") -> dict[str, Any]:
    wb = repo.get_world_bible()
    title = (getattr(wb, "title", "") or "").strip()
    seed_text = "\n".join(
        str(x)
        for x in [
            title,
            theme,
            getattr(wb, "setting", ""),
            getattr(wb, "premise", ""),
            getattr(wb, "conflict", ""),
        ]
        if x
    )
    template_id = getattr(template, "id", "") if template else ""
    contract = _generic_contract(title=title, theme=theme, seed_text=seed_text, template_id=template_id)
    if template_id == "yin_yang_shouhou" or "阴阳售后" in seed_text or "死人差评" in seed_text:
        contract = _yin_yang_afterservice_contract(title=title, theme=theme)
    return contract


def _generic_contract(title: str, theme: str, seed_text: str, template_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "template_id": template_id,
        "title": title,
        "story_scale": resolve_story_scale(template_id=template_id).to_dict(),
        "principles": [
            "三层大纲：总纲只保留终极目标、最大对立、全书走向；分卷只保留阶段闭环；章节只服务本章小目标。",
            "当前单元未闭环前，不提前展开下一单元或终局势力。",
            "长线伏笔只能低显著度露出，不能抢走当前单元的目标、反派和爽点。",
        ],
        "global_outline": {
            "protagonist_life_goal": "从困境中获得掌控权，并完成与题材机制绑定的终极职责。",
            "main_opposition": "一个能长期利用世界漏洞、压迫普通人的反派体系。",
            "whole_book_path": "启程：主角接触异常机制；考验：完成多个闭环单元；成长：遭遇代价并升级方法；决战：直面对立体系；结局：重建秩序并交代人物归宿。",
        },
        "release_gates": {
            "rule": "信息按阶段释放。未到期信息只许作为低显著度影子，不许命名、不许展开、不许成为本章目标。",
            "early_allowed": ["主角困境", "当前单元委托", "当前单元证据链", "当前单元对手"],
            "early_forbidden": ["最终反派核心方案", "下一单元完整案情", "主线旧案完整真相", "与当前单元无关的大机构黑幕"],
        },
        "active_unit": {
            "locked": False,
            "name": "首个闭环单元",
            "unit_goal": "完成第一个可见闭环，让读者理解题材爽点。",
            "chapter_steps": [],
            "completion_signal": "当前单元反派付出代价，主角获得一次可见收获，并抛出下一单元钩子。",
        },
        "volume_blueprint": [],
        "forbidden_terms": [],
        "progress_terms": ["进度", "奖励", "结算"],
    }


def _volume(title: str, short_goal: str, obstacle: str, conflict_chain: list[str],
            key_twist: str, gain_and_hook: str, *, allowed: list[str] | None = None,
            shadow_only: list[str] | None = None, forbidden: list[str] | None = None) -> dict[str, Any]:
    return {
        "title": title,
        "short_goal": short_goal,
        "obstacle": obstacle,
        "conflict_chain": conflict_chain,
        "key_twist": key_twist,
        "gain_and_hook": gain_and_hook,
        "allowed": allowed or [],
        "shadow_only": shadow_only or [],
        "forbidden": forbidden or [],
    }


def _yin_yang_default_volumes() -> list[dict[str, Any]]:
    first_forbidden = [
        "天命公司正面登场", "天命公司核心人物", "第三人民医院主案", "器官买卖主案",
        "沈知夏父亲旧案", "警号旧档主线", "校车案正文展开",
    ]
    first_shadow = ["陌生公司名称", "被涂黑字段", "更高权限"]
    return [
        _volume(
            "第一卷·林晚一星差评案",
            "完成林晚案，让读者看到接单、查证、打脸、五星结算的完整爽点。",
            "丈夫与整容替身掌握身份、财产和舆论，警方一开始怀疑陈野。",
            ["死人差评接入", "公开记录与活人林晚冲突", "别墅地下室浮出尸骨", "丈夫和替身互相甩锅"],
            "活着的林晚是替身；真正的林晚早被害死，尸骨在别墅地下室。",
            "林晚五星，陈野获得亡者回执；第二条差评只在卷末指向校车危机。",
            allowed=["无忧售后服务有限公司", "阴阳售后局后台", "锦澜湾别墅区", "别墅地下室", "江州市刑警支队"],
            shadow_only=first_shadow,
            forbidden=first_forbidden,
        ),
        _volume(
            "第二卷·校车不能上",
            "救下死者女儿，打穿校车司机与背后的买命代理链条。",
            "家长误解、警方怀疑、凶手提前布置事故，孩子每天都在倒计时里接近死亡。",
            ["校车差评接入", "陈野阻拦被当成人贩子", "司机事故记录异常", "死亡路线被提前重排"],
            "校车司机不是单纯肇事者，而是替人转嫁灾厄的执行人。",
            "孩子获救，沈知夏开始盯上陈野；批量差评背后的组织影子第一次出现。",
            allowed=["明德小学", "城南校车公司", "事故路线"],
            shadow_only=["陌生命运咨询合同", "黑金名片一角"],
            forbidden=["天命公司正面登场", "医院器官案正文展开", "沈知夏父亲旧案正文展开"],
        ),
        _volume(
            "第三卷·我的器官还活着",
            "处理医院器官案，证明死者身体被拆成了活人的获利链。",
            "医院、家属、受益人形成证据壁垒，亡者的投诉被伪装成医疗纠纷。",
            ["器官差评接入", "病历和死亡时间冲突", "受益者接连出现异常", "地下移植链暴露"],
            "真正的核心不是单次盗卖，而是用阴阳漏洞延长富人寿命。",
            "陈野获得追踪因果标记的权限；天命公司的代理合同露出更清楚的轮廓。",
            allowed=["第三人民医院", "旧器官移植中心", "地下移植链"],
            shadow_only=["天命代理人", "命数合同编号"],
            forbidden=["天命公司总部正面登场", "地府仲裁庭正文展开", "沈知夏父亲旧案完整真相"],
        ),
        _volume(
            "第四卷·我妈每年给凶手烧纸",
            "清算一桩被亲情和乡俗掩盖的旧命案，让活人停止向凶手供奉。",
            "受害者母亲被假真相洗脑多年，村镇人情网把证据埋成了忌讳。",
            ["烧纸差评接入", "母亲拒绝相信亡者", "旧坟和族谱互相矛盾", "当年目击者被逼沉默"],
            "被祭拜的不是受害者恩人，而是当年真正的凶手和获利者。",
            "陈野学会处理活人执念；下一单把职场替死案推到他面前。",
            allowed=["老家村镇", "旧坟", "族谱祠堂"],
            shadow_only=["买命中介传闻", "被转走的阴德"],
            forbidden=["天命公司核心人物登场", "地府仲裁庭正文展开"],
        ),
        _volume(
            "第五卷·替老板死的员工",
            "揭开职场替死工单，让老板把转嫁给员工的死亡风险还回来。",
            "公司法务、公关和合同条款把替死包装成自愿加班事故。",
            ["员工差评接入", "劳动合同暗藏命数条款", "同事被威胁改口", "老板试图二次转嫁"],
            "员工不是意外死亡，而是替老板挡下被购买的死劫。",
            "陈野拿到第一份完整命数合同；天命公司的商业模型成形。",
            allowed=["替死公司", "办公室", "法务档案室"],
            shadow_only=["天命客户档案", "命运管理服务"],
            forbidden=["天命公司终局方案", "沈知夏父亲旧案完整真相"],
        ),
        _volume(
            "第六卷·女儿身边的爸爸不是爸爸",
            "处理家庭替身案，拆穿冒充父亲的人如何吞掉一家人的命。",
            "孩子不愿相信父亲被替换，假父亲掌握监护权和社会身份。",
            ["女儿差评接入", "亲子记忆出现破绽", "假父亲反控陈野破坏家庭", "真父亲残留回执发声"],
            "假父亲是被买来的替身，真正父亲早被替换进一份死亡名单。",
            "陈野和沈知夏建立更稳定的合作；下一卷把他们带进整村旧案。",
            allowed=["女儿家", "学校门口", "监护权办公室"],
            shadow_only=["批量替身名单", "命数中转人"],
            forbidden=["天命公司总部正面登场", "地府仲裁庭正文展开"],
        ),
        _volume(
            "第七卷·全村都知道我怎么死",
            "打穿整村沉默的旧案，让集体包庇付出代价。",
            "村庄共同利益、乡规和旧账把受害者的死亡变成所有人的秘密。",
            ["全村差评接入", "证词互相保护", "祠堂账本暴露利益分配", "村民集体反扑"],
            "不是没人知道死因，而是全村都从受害者死亡中拿过好处。",
            "陈野第一次引发大规模亡者回声；沈知夏父亲旧警号被牵出。",
            allowed=["旧村", "祠堂", "村委会"],
            shadow_only=["旧警号", "父亲档案缺页"],
            forbidden=["沈知夏父亲旧案完整展开", "天命公司终局方案"],
        ),
        _volume(
            "第八卷·沈知夏父亲旧案",
            "让沈知夏直面父亲死亡真相，完成官方合作线的情感和证据闭环。",
            "旧案卷宗被改写，上级压力和亲情执念同时压住沈知夏。",
            ["父亲差评接入", "旧警号回执出现", "卷宗关键页被涂黑", "当年同僚立场反转"],
            "沈知夏父亲不是普通牺牲，而是撞破天命公司和阴司漏洞后被灭口。",
            "沈知夏正式成为陈野的阳间合作者；天命公司批量借寿线进入正面战场。",
            allowed=["江州市刑警支队", "旧档案室", "父亲旧案现场"],
            shadow_only=["地府仲裁庭传票", "阴司内鬼称谓"],
            forbidden=["地府仲裁庭最终审判正文展开"],
        ),
        _volume(
            "第九卷·天命公司批量借寿",
            "正面清算天命公司阳间网络，阻止一场批量借寿。",
            "天命公司掌握合同、客户和阴司漏洞，能把反噬转嫁给普通人。",
            ["批量差评爆发", "借寿阵启动", "客户名单牵出权贵", "陈野用售后系统反向结算"],
            "天命公司真正依赖的是地府仲裁流程里的漏洞和内鬼。",
            "阳间网络被撕开，陈野拿到仲裁资格；最终卷进入地府仲裁庭。",
            allowed=["天命咨询公司", "客户档案库", "借寿现场"],
            shadow_only=["仲裁庭席位", "阴司内鬼真名"],
            forbidden=["结局改写人间因果秩序正文提前发生"],
        ),
        _volume(
            "第十卷·地府仲裁庭",
            "把所有死人差评合并成集体仲裁，在地府仲裁庭清算终局黑幕。",
            "仲裁庭规则偏向权势和程序漏洞，陈野必须用一单单完成的五星回执作证。",
            ["集体仲裁立案", "天命公司和阴司内鬼互相切割", "亡者回执组成证据链", "陈野付出终局代价"],
            "售后系统不是安慰亡者的工具，而是人间因果秩序的最后申诉口。",
            "天命公司被清算，地府漏洞被修补，陈野成为真正的阴阳仲裁官或守店人。",
            allowed=["地府仲裁庭", "阴司案卷库", "集体仲裁席"],
        ),
    ]


def _merge_yin_yang_volume_blueprint(contract: dict[str, Any]) -> bool:
    changed = False
    defaults = _yin_yang_default_volumes()
    old = [v for v in (contract.get("volume_blueprint") or []) if isinstance(v, dict)]
    merged: list[dict[str, Any]] = []
    for idx, default in enumerate(defaults):
        current = old[idx] if idx < len(old) else {}
        item = dict(default)
        for key, value in current.items():
            if key == "title":
                continue
            if value not in (None, "", []):
                item[key] = value
        _normalize_volume(item, idx + 1)
        merged.append(item)
    if contract.get("volume_blueprint") != merged:
        contract["volume_blueprint"] = merged
        changed = True
    return changed


def _yin_yang_afterservice_contract(title: str, theme: str) -> dict[str, Any]:
    return {
        "version": 1,
        "template_id": "yin_yang_shouhou",
        "title": title or "我给死人做售后，差评全变五星",
        "story_scale": STORY_SCALES["serial_long"].to_dict(),
        "principles": [
            "这本不是恐怖文，核心是爽文：鬼不是吓人的，鬼是给主角递刀的。",
            "每一条差评背后都有恶人以为自己赢了；每次售后完成，恶人被按在地上清算。",
            "当前单元未五星结算前，不开启下一案，不让长线组织抢当前案的爽点。",
        ],
        "global_outline": {
            "protagonist_life_goal": "陈野从底层客服变成阴阳两界最强售后，替死者讨回公道，最终重开人间因果秩序。",
            "main_opposition": "天命公司等利用地府漏洞替富人改命、借寿、替死、换身份的阳间组织。",
            "whole_book_path": "启程：失业继承无忧售后服务有限公司，接到死人一星差评；考验：用客服流程处理一个个冤魂工单；成长：与警方合作并付出代价，发现差评背后有人批量制造冤魂；决战：进入地府仲裁庭清算天命公司；结局：陈野成为阴阳仲裁官。",
        },
        "volume_blueprint": _yin_yang_default_volumes(),
        "release_gates": {
            "rule": "第一卷只完成林晚案。天命公司、沈知夏父亲旧案、医院器官案、警号旧档都只能在结算后作为远景影子，不得成为第一卷章节目标。",
            "early_allowed": ["陈野失业继承破店", "阴阳售后局后台", "林晚一星差评", "丈夫", "假林晚", "别墅地下室", "沈知夏以刑警身份介入"],
            "early_forbidden": ["天命公司正面登场", "天命公司核心人物", "第三人民医院主案", "器官买卖主案", "沈知夏父亲旧案", "警号旧档主线", "校车案正文展开"],
            "shadow_only": ["陌生公司名称一闪而过", "警方档案里一处被涂黑的字段", "后台提示存在更高权限"],
        },
        "active_unit": {
            "locked": True,
            "name": "林晚一星差评案",
            "unit_goal": "陈野查清林晚被害与替身换身份，逼丈夫和替身崩盘，让林晚改五星。",
            "core_question": "我死了三年，可我丈夫为什么每天还在和我睡觉？",
            "must_include": ["林晚", "丈夫", "假林晚", "别墅地下室", "尸骨", "沈知夏", "亡者回执", "五星结算"],
            "forbidden_before_completion": ["天命公司", "第三人民医院", "器官", "沈知夏父亲", "警号", "校车司机正案"],
            "chapter_steps": [
                "失业、分手、房租到期；陈野继承无忧售后服务有限公司，午夜收到林晚一星差评。",
                "陈野查公开资料和物业记录，发现死了三年的林晚仍以妻子身份活在丈夫身边。",
                "陈野上门试探假林晚，客服套话逼出破绽，丈夫反咬他勒索。",
                "后台开放亡者最后三分钟记忆，陈野定位别墅地下室异常。",
                "尸骨出现，沈知夏介入，丈夫把嫌疑推向陈野。",
                "陈野用只有真林晚知道的秘密逼假林晚当众露馅。",
                "丈夫与替身互相甩锅，证据链闭合，恶人破防被警方控制。",
                "林晚五星结算，陈野获得亡者回执；第二条差评钩子：我女儿明天会死，别让她上那辆校车。",
            ],
            "chapter_specs": [
                {
                    "loc_hint": "无忧",
                    "question": "陈野会不会把这条死人差评当成诈骗，错过唯一的接单窗口？",
                    "exit": "陈野正式接入阴阳售后局后台，林晚一星差评生成工单，48小时处理倒计时开始。",
                    "beats": [
                        "陈野失业、分手、房租到期，被迫进无忧售后服务有限公司收拾二叔留下的破店，发现门口贴着「差评必回，生死不误」。",
                        "午夜十二点电脑自动开机，阴阳售后局后台弹出林晚一星差评：我已经死了，为什么凶手还在用我的脸活着？陈野以为诈骗，却被后台扣上工号0001。",
                        "屏幕冷光映着褪色键盘，陈野指尖停在「接单」键上，窗外旧招牌吱呀一声晃了半圈。"
                    ],
                },
                {
                    "loc_hint": "无忧",
                    "question": "陈野能不能证明林晚已死，而不是自己被一条鬼差评耍了？",
                    "exit": "陈野查到林晚三年前死亡记录与近期物业签收记录冲突，确认活着的林晚有问题。",
                    "beats": [
                        "陈野用客服查单习惯翻公开记录、物业签收和婚姻资料，发现林晚三年前已死亡，锦澜湾却每月都有「林晚」签收快递。",
                        "后台开放林晚最后三分钟记忆的碎片，陈野只看到别墅地砖、男人袖口和一枚断裂婚戒，却足够锁定锦澜湾18号。",
                        "打印机吐出一页泛黄签收单，墨迹还没干，林晚两个字在纸上像被水泡开的黑痕。"
                    ],
                },
                {
                    "loc_hint": "锦澜湾",
                    "question": "陈野能不能在不惊动丈夫的情况下，逼假林晚露出第一处破绽？",
                    "exit": "假林晚被客服套话问出生活习惯破绽，丈夫程行反咬陈野勒索并报警。",
                    "beats": [
                        "陈野假扮售后回访上门，围绕婚戒、母亲遗物和旧账单套话，假林晚答错只有真林晚知道的生活细节。",
                        "丈夫程行突然回家，把陈野堵在客厅，抢先报警说他冒充殡葬中介勒索，假林晚顺势装受害者。",
                        "客厅香薰甜得发腻，陈野低头看见茶几玻璃下压着一张旧婚照，照片边角被人剪去一小块。"
                    ],
                },
                {
                    "loc_hint": "锦澜湾",
                    "question": "陈野能不能用亡者最后三分钟记忆找到真正尸骨？",
                    "exit": "陈野确认别墅地下室暗墙异常，林晚尸骨位置第一次浮出水面。",
                    "beats": [
                        "后台临时开放「亡者最后三分钟记忆」，陈野忍着窒息感读取画面，看见林晚被拖进地下室，指甲刮过暗墙水泥缝。",
                        "陈野趁警方和程行纠缠时潜入地下室，用断裂婚戒刮开墙角新补的水泥，闻到腐败和消毒水混在一起的味道。",
                        "水泥粉落在鞋面上，陈野手里的婚戒卡进墙缝，金属边缘沾出一线暗褐色。"
                    ],
                },
                {
                    "loc_hint": "锦澜湾",
                    "question": "尸骨出现后，陈野会先洗清嫌疑，还是先保住林晚的证据？",
                    "exit": "沈知夏带队介入，地下室发现尸骨；程行把嫌疑引向陈野，陈野被警方重点怀疑。",
                    "beats": [
                        "沈知夏带技术组赶到锦澜湾，地下室暗墙被打开，白骨和红色雨伞残片暴露出来，案件从失踪变成命案。",
                        "程行当众哭诉陈野敲诈失败后栽赃，假林晚配合演戏，陈野被扣在现场，只能把关键线索藏进售后账本夹层。",
                        "警戒线在别墅门口抖动，雨水顺着红色伞骨往下滴，滴在证物袋透明塑料上。"
                    ],
                },
                {
                    "loc_hint": "刑警",
                    "question": "陈野能不能让假林晚说出只有死者才知道的秘密？",
                    "exit": "假林晚在审讯室被亡者回声逼出矛盾口供，沈知夏开始相信陈野的线索不是普通诈骗。",
                    "beats": [
                        "审讯室里，陈野借后台播放林晚七秒记忆回声，问出母亲遗物藏在哪个旧盒子里，假林晚本能答错。",
                        "沈知夏抓住矛盾追问，陈野用客服式礼貌狠话一步步压迫假林晚，逼她承认自己整容后冒用身份。",
                        "录音笔红灯一闪一闪，假林晚指甲掐进掌心，桌面上留下一小弯粉色甲油。"
                    ],
                },
                {
                    "loc_hint": "刑警",
                    "question": "丈夫和替身会不会在证据链闭合前互相甩锅？",
                    "exit": "程行和替身互咬，杀人、埋尸、冒名继承的证据链闭合，两人被警方控制。",
                    "beats": [
                        "陈野把地下室尸骨、物业记录、整容资料和林晚记忆碎片串成工单流程图，让程行以为替身已经全招。",
                        "程行和替身当场互相甩锅，一个说只是换身份骗遗产，一个说尸体是程行亲手埋的，沈知夏顺势完成证据闭环。",
                        "手铐合上的咔哒声在走廊里回弹，陈野看着流程图最后一格，从「待处理」改成「清算中」。"
                    ],
                },
                {
                    "loc_hint": "无忧",
                    "question": "林晚能不能改成五星，而下一条差评会把陈野推向哪里？",
                    "exit": "林晚五星结算，陈野获得亡者回执；第二条差评出现，校车危机只作为下一案钩子。",
                    "beats": [
                        "陈野回到无忧售后，后台弹出林晚评价修改窗口，一星变五星，亡者回执奖励到账：死者可留下七字以内真话。",
                        "林晚的怨气散去前留下回执，陈野第一次明白售后不是哄死人满意，而是让欠账的人付账。",
                        "电脑风扇慢慢停下，屏幕黑了一秒又亮起：我女儿明天会死，别让她上那辆校车。陈野杯里的冷水晃出一圈波纹。"
                    ],
                },
            ],
            "completion_signal": "林晚评分从一星改五星；丈夫和替身崩盘；陈野获得亡者回执；只在末尾抛校车差评。",
        },
        "forbidden_terms": ["黑化值", "反派养成器", "修仙", "丹田", "飞升", "灵根"],
        "progress_terms": ["工单进度", "怨气值", "评分", "五星结算", "权限点", "亡者回执"],
    }


def contract_prompt_block(contract: dict[str, Any], *, part_seq: int | None = None,
                          chapter_idx: int | None = None) -> str:
    if not contract:
        return ""
    lines: list[str] = ["【故事合同·硬约束】"]
    g = contract.get("global_outline") or {}
    if g:
        lines.append(f"总纲：主角终极目标={g.get('protagonist_life_goal', '')}；最大对立={g.get('main_opposition', '')}；全书走向={g.get('whole_book_path', '')}")
    scale = resolve_story_scale(contract=contract, template_id=str(contract.get("template_id") or ""))
    lines.append(
        f"体量：{scale.id}，{scale.volume_count_min}-{scale.volume_count_max}卷，"
        f"每卷约{scale.arcs_per_volume}个arc，每arc约{scale.chapter_target_per_arc}章，规划模式={scale.planning_mode}。"
    )
    principles = contract.get("principles") or []
    if principles:
        lines.append("原则：" + "；".join(str(x) for x in principles[:4]))
    vols = contract.get("volume_blueprint") or []
    if part_seq is not None and 1 <= part_seq <= len(vols):
        v = vols[part_seq - 1] or {}
        lines.append(
            f"当前卷纲：{v.get('title', '')}；短期目标={v.get('short_goal', '')}；"
            f"阻碍={v.get('obstacle', '')}；连续冲突={' / '.join(str(x) for x in (v.get('conflict_chain') or []))}；"
            f"反转={v.get('key_twist', '')}；收获与钩子={v.get('gain_and_hook', '')}"
        )
        if v.get("allowed"):
            lines.append("本卷允许展开：" + "、".join(str(x) for x in v.get("allowed") or []))
        if v.get("shadow_only"):
            lines.append("本卷只能低显著度掠过，不能做标题/目标/反转：" + "、".join(str(x) for x in v.get("shadow_only") or []))
        if v.get("forbidden"):
            lines.append("本卷禁止出现/展开：" + "、".join(str(x) for x in v.get("forbidden") or []))
    rg = contract.get("release_gates") or {}
    if rg:
        lines.append("信息释放：" + str(rg.get("rule", "")))
        if part_seq == 1:
            forbidden = rg.get("early_forbidden") or []
            if forbidden:
                lines.append("第一阶段禁止提前展开：" + "、".join(str(x) for x in forbidden))
    au = contract.get("active_unit") or {}
    if au.get("locked") and (part_seq in (None, 1)):
        lines.append(f"当前锁定单元：{au.get('name')}。目标：{au.get('unit_goal')}")
        if au.get("core_question"):
            lines.append(f"核心钩子：{au.get('core_question')}")
        fb = au.get("forbidden_before_completion") or []
        if fb:
            lines.append("未结算前禁止抢戏：" + "、".join(str(x) for x in fb))
        steps = au.get("chapter_steps") or []
        if chapter_idx is not None and 0 <= chapter_idx < len(steps):
            lines.append(f"本章锁定小目标：{steps[chapter_idx]}")
        elif steps:
            lines.append("本单元章节阶梯：" + " / ".join(str(x) for x in steps[:8]))
    forbidden_terms = contract.get("forbidden_terms") or []
    if forbidden_terms:
        lines.append("禁用污染词：" + "、".join(str(x) for x in forbidden_terms))
    return "\n".join(x for x in lines if x.strip()) + "\n"


def first_part_override(contract: dict[str, Any]) -> dict[str, Any] | None:
    vols = contract.get("volume_blueprint") or []
    au = contract.get("active_unit") or {}
    if not vols and not au.get("locked"):
        return None
    v = vols[0] if vols else {}
    return {
        "title": v.get("title") or "第一卷·首个闭环",
        "goal": _volume_goal_text(v) or au.get("unit_goal", ""),
        "region": "无忧售后服务有限公司、锦澜湾别墅区、江州市刑警支队",
        "key_twist": v.get("key_twist") or "",
        "new_crisis_hook": v.get("gain_and_hook") or au.get("completion_signal", ""),
    }


def _volume_goal_text(v: dict[str, Any]) -> str:
    if not v:
        return ""
    chain = "；".join(str(x) for x in (v.get("conflict_chain") or []) if str(x).strip())
    fields = [
        ("本卷短期目标", v.get("short_goal", "")),
        ("阻碍势力/困难", v.get("obstacle", "")),
        ("连续递进冲突事件", chain),
        ("关键反转", v.get("key_twist", "")),
        ("卷末收获+下一卷危机", v.get("gain_and_hook", "")),
    ]
    return "；".join(f"{k}：{val}" for k, val in fields if str(val).strip())


def first_arc_override(contract: dict[str, Any], personas: list[Any]) -> dict[str, Any] | None:
    au = contract.get("active_unit") or {}
    if not au.get("locked"):
        return None
    focus = []
    if personas:
        focus.append({"agent_id": personas[0].agent_id, "weight": 0.85})
        if len(personas) > 1:
            focus.append({"agent_id": personas[1].agent_id, "weight": 0.45})
    steps = au.get("chapter_steps") or []
    return {
        "title": au.get("name") or "首个闭环单元",
        "summary": au.get("unit_goal") or "",
        "target_chapters": max(3, min(20, len(steps) or 8)),
        "focus_agents": focus,
    }
