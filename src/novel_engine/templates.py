"""题材模板注册中心：在新建项目时让用户选一份"成熟范式"作为起点，
而不是从 GENRE_PRESETS 的瘦预设上自己捏。

每个模板携带：
  · tone_overrides：覆盖 GENRE_PRESETS 的 ToneProfile 字段（register/diction/tone_reference 等）
  · structural    ：规划/写作层约定（如章节标题钩子词、每章必填的 beat 维度、是否注册系统 NPC）
  · world_hints   ：种子共创阶段对作者的写作建议（在前端选模板后展示）

build_tone_profile 在收到 template_id 时按模板覆盖；
未传或不识别 template_id 时退回原 _norm_genre/GENRE_PRESETS 链路。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenreTemplate:
    id: str
    label: str
    description: str
    tone_overrides: dict[str, Any] = field(default_factory=dict)
    structural: dict[str, Any] = field(default_factory=dict)
    world_hints: list[str] = field(default_factory=list)

    def to_card(self) -> dict[str, Any]:
        """前端模板选择卡片需要的字段。"""
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "world_hints": list(self.world_hints),
        }


# ------------------------------------------------------------------ 注册表

SHUANGWEN_ZHUANGBI = GenreTemplate(
    id="shuangwen_zhuangbi",
    label="爽文 · 装逼打脸系统",
    description=(
        "范本：《最强装逼打脸系统》《我有一座恐怖屋》一脉。"
        "主角带一套数值化系统（黑化值/进度条/任务奖励），"
        "系统体官方文体 × 主角内心 OS × 市井叙述三声道反差出笑点；"
        "每章一个'立 flag → 反派自爆 → 系统播报'的小高潮闭环。"
    ),
    tone_overrides={
        "genre": "xuanhuan_powerfantasy",
        "primary_effect": "catharsis_satisfaction",
        "register": "市井口语 + 系统体冷面 + 主角内心 OS（三声道反差）",
        "sentence_rhythm": "短句高频，系统播报与人物对话交替；危机时急促，市井闲笔时舒缓",
        "diction_do": [
            "系统提示音（叮——）",
            "内心 OS 双声道（嘴上一套、心里一套）",
            "反差幽默（一本正经胡说八道）",
            "金句立 flag（主角扔狠话给读者爽）",
            "数值即时反馈（黑化值 +N / 进度条 涨/退）",
            "市井俗语 / 接地气吐槽",
            "感官词承载情绪（饿、冷、疼、汗）",
        ],
        "diction_dont": [
            "典雅书面 / 文绉绉",
            "古风套话（剑光朗朗、月色如水、风骨清绝）",
            "冗长心理描写",
            "拖沓铺陈 / 无端煽情",
            "现代政治/管理学/心理学术语",
            "正面口号式宣告",
        ],
        "device_kit": [
            "系统拟人吐槽（系统永远官方文体，与主角形成反差）",
            "内心 OS 双声道",
            "立 flag → 打脸 → 自爆 闭环",
            "无形装逼 / 以怂破刚",
            "数值反馈循环（每章必有进度条变化）",
            "反派上头自爆（主角啥也没干）",
            "章节钩子词标题",
        ],
        "pacing": "极快，每章一小高潮；payoff 密集",
        "tension_curve_bias": "锯齿（频繁小高潮、立 flag 与打脸交替）",
        "reveal_cadence": "快速给信息+快速兑现（不藏底）",
        "complexity": "低",
        "tone_reference": (
            "叮——\n"
            "【反派养成器】监测到目标顾长夜情绪波动，黑化值 +12。\n"
            "宿主，请继续维持高水平的'坐视不理'。\n\n"
            "萧守拙端着一碗冷掉的稀粥，蹲在崖边数蚂蚁。\n"
            "心里 OS：又涨了。这小子今天又被人骂了吧。\n"
            "嘴上：「长夜啊，做人嘛，最要紧的是开心。来，喝粥。」\n"
            "顾长夜攥紧拳头：「师父，他们欺人太甚。」\n"
            "萧守拙慢悠悠咽一口：「欺就欺，咱躲一躲。」\n"
            "（OS：这就对了，气死你，进度条快涨。）\n"
            "叮——【反派养成器】黑化值 +5。预计七日内突破第三阶。"
        ),
    },
    structural={
        # 章节标题强制带钩子词（任一命中即合格）。
        # 词库覆盖六类爽文章名套路：①系统体 ②主角声口 ③数值/任务 ④现代俗语硬塞
        # ⑤直接威胁/喊话 ⑥反差自吹/吐槽
        "chapter_title_hooks": [
            # 系统体
            "叮", "进度条", "黑化值", "绑定", "宿主", "任务", "大礼包", "兑换",
            # 主角声口（第一人称/口头禅）
            "我", "老子", "在下", "本座", "师父", "徒弟", "徒儿",
            # 数值/动作
            "+", "爆表", "满级", "秒杀", "一拳", "百", "千", "万",
            # 现代俗语 / 网感
            "他妈", "雾草", "草", "牛逼", "辣鸡", "刺激", "上天", "认真",
            # 直接威胁/喊话
            "都得死", "跪下", "滚", "等我", "看我",
            # 反差自吹 / 装逼
            "无形", "装逼", "打脸", "苟", "怂", "自爆", "上头", "啥也没干",
            "你也想", "凭什么", "差点", "不背锅", "谁起的",
        ],
        # 每章 chapter_plan 必填两个 beat 维度（planner 阶段强制注入）
        "chapter_must_have_beats": ["payoff_beat", "humor_beat", "breath_beat"],
        # 系统当作虚拟角色注册到 entities，让 SceneWriter 能引用其播报台词
        "system_npc": {
            "enabled": True,
            "agent_id": "sys_yangcheng",
            "name": "反派养成器",
            "voice": "官方文体；永远叫宿主；播报数值；冷面带反讽",
            "default_register": "system_announcement",
        },
        # 主角默认双声道（嘴上 / 内心 OS）
        "protagonist_dual_voice": True,
    },
    world_hints=[
        "建议给主角一套数值化的'养成系统'（黑化值/苟分/任务/进度条），系统体官方文体，是一切反差幽默的底层引擎。",
        "建议每章固定结构：立 flag → 反派上头 → 主角看戏 → 系统播报数值 → 自爆收尾。",
        "建议主角双声道：嘴上典雅(\"长夜啊\")vs 内心吐槽(\"气死你，进度条快涨\")。",
        "建议章节标题带钩子词（叮 / 进度条 / 无形 / 反派 / 装逼 / 苟 / 自爆 / 上头）。",
        "建议禁用古风套话（剑光朗朗/月色如水），改用市井俗语+感官词。",
    ],
)


YIN_YANG_SHOUHOU = GenreTemplate(
    id="yin_yang_shouhou",
    label="都市悬疑 · 阴阳售后打脸",
    description=(
        "死人差评驱动的都市单元爽文。主角不是道士，是客服出身的阴阳售后员；"
        "每单按'接差评 → 查证据 → 逼恶人现形 → 五星结算'闭环推进。"
    ),
    tone_overrides={
        "genre": "urban_suspense_powerfantasy",
        "primary_effect": "catharsis_satisfaction",
        "register": "现代市井口语 + 客服流程话术 + 阴间系统冷面播报",
        "sentence_rhythm": "短句推进，投诉内容和后台提示切入快；破案段紧，打脸段狠，收尾给情绪回甘",
        "diction_do": [
            "差评/工单/回执/售后/五星/投诉",
            "客服式礼貌狠话",
            "死人递证据，活人当场破防",
            "因果后台播报",
            "官方合作与刑警质疑",
            "都市细节：监控、物业、医院、学校、别墅、网约车",
            "每单结算奖励",
        ],
        "diction_dont": [
            "恐怖片式故弄玄虚",
            "玄学术语堆砌",
            "主角无能旁观",
            "长篇法术斗法",
            "恶人轻易逃脱",
            "说教式正义宣言",
        ],
        "device_kit": [
            "死人差评开场",
            "客服流程反差幽默",
            "死无对证被后台打穿",
            "证据链递进",
            "当众打脸",
            "五星结算与奖励到账",
            "下一单差评钩子",
        ],
        "pacing": "极快，每 3-6 章完成一单；每章都要有证据、反制或结算推进",
        "tension_curve_bias": "锯齿（接单悬念与清算爽点高频交替）",
        "reveal_cadence": "快揭快打脸，单元案不拖谜底，长线组织逐步露头",
        "complexity": "中低",
        "tone_reference": (
            "叮——\n"
            "【阴阳售后局后台】新差评已接入。\n"
            "客户：林晚。状态：已死亡三年。评分：一星。\n"
            "投诉内容：我已经死了，为什么凶手还在用我的脸活着？\n\n"
            "陈野盯着屏幕看了三秒，职业病先犯了。\n"
            "他清了清嗓子：您好，请问您是要申诉、追责、赔偿，还是申请凶手报应加急？\n"
            "屏幕右下角弹出一行小字：客户情绪稳定度 3%。建议别嘴贱。\n"
            "陈野默默把'亲亲'两个字删了。"
        ),
    },
    structural={
        "chapter_title_hooks": [
            "差评", "五星", "售后", "死人", "投诉", "工单", "回执", "报应",
            "一星", "凶手", "下地狱", "传票", "阴阳", "后台", "客户", "加急",
            "打脸", "破防", "死无对证", "活着", "校车", "别墅", "尸骨",
        ],
        "chapter_must_have_beats": [
            "complaint_beat",
            "evidence_beat",
            "punishment_beat",
            "payoff_beat",
            "humor_beat",
            "breath_beat",
        ],
        "system_npc": {
            "enabled": True,
            "agent_id": "sys_afterlife_service",
            "name": "阴阳售后局后台",
            "voice": "冷面客服系统；用工单、评分、回执、结算话术播报因果",
            "default_register": "system_announcement",
        },
        "progress_terms": ["工单进度", "怨气值", "评分", "五星结算", "权限点", "亡者回执"],
        "forbidden_terms": ["黑化值", "反派养成器", "修仙", "丹田", "飞升", "灵根"],
        "protagonist_dual_voice": True,
    },
    world_hints=[
        "开局第一单必须狠：死人差评直接指向活人凶手，3-6 章内完成清算。",
        "鬼不是吓人的，鬼是给主角递刀的；恶人以为死无对证，后台让死人开口。",
        "主角技能从客服流程升级到因果仲裁，奖励要即时、好懂、能服务下一单。",
        "每个单元结尾都要有五星结算和下一条差评钩子。",
    ],
)


REGISTRY: dict[str, GenreTemplate] = {
    SHUANGWEN_ZHUANGBI.id: SHUANGWEN_ZHUANGBI,
    YIN_YANG_SHOUHOU.id: YIN_YANG_SHOUHOU,
}


def get(template_id: str) -> GenreTemplate | None:
    """按 id 取模板；未命中返回 None。"""
    if not template_id:
        return None
    return REGISTRY.get(template_id.strip())


def list_cards() -> list[dict[str, Any]]:
    """供 /api/templates 返回前端使用。"""
    return [t.to_card() for t in REGISTRY.values()]
