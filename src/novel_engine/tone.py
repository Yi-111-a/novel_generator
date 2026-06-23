"""§16 文风契约（Tone Contract / 闸门⓪）。

把"文风/基调"提到整条流水线的最上游：先于世界圣经与总纲。它数据化为一份
贯穿规划/表演/渲染三层的母约束（`ToneProfile`），锁定时由用户确认、之后只读。

本模块提供：
  build_tone_profile(repo, llm, genre, theme)  锁定第一步：据用户选择 + 题材生成契约
                                               （LLM 优先；无 LLM / 解析失败走每题材确定性预设）
  tone_gate(scene_text, profile, llm)          每场渲染后的硬闸门：是否交付 primary_effect、
                                               是否触犯 diction_dont；不过则带反馈要求重渲

设计依据见 `剧组化重构-详细设计.md` §16。
"""
from __future__ import annotations

import json

from .llm.base import LLMClient
from .models import ToneProfile
from .repository import Repository

# ---- 每题材的确定性预设（无 LLM 或解析失败时的回退；也作为 LLM 缺字段的补全底） ----
# 字段对应 ToneProfile；tension_curve_bias / reveal_cadence / complexity 用于参数化规划层。
GENRE_PRESETS: dict[str, dict] = {
    "comedy": {
        "primary_effect": "laugh",
        "register": "诙谐口语",
        "sentence_rhythm": "短句高频、节奏轻快",
        "diction_do": ["夸张", "反差", "俏皮比喻", "口语俚语"],
        "diction_dont": ["沉重说教", "冗长心理独白", "阴郁意象"],
        "device_kit": ["反转", "误会", "夸张", "巧合"],
        "pacing": "明快，包袱密集",
        "tension_curve_bias": "波浪（误会建立→升级→抖响）",
        "reveal_cadence": "均匀延迟",
        "complexity": "中",
    },
    "horror": {
        "primary_effect": "dread",
        "register": "阴冷凝重",
        "sentence_rhythm": "长短交错、留白与停顿制造悬置",
        "diction_do": ["感官细节", "未知暗示", "失控征兆", "冷色意象"],
        "diction_dont": ["插科打诨", "轻佻玩笑", "提前解释谜底"],
        "device_kit": ["悬置", "未知", "失控", "氛围递进"],
        "pacing": "慢热递进，至爆发",
        "tension_curve_bias": "单调递进到爆发",
        "reveal_cadence": "高度克制（withhold）",
        "complexity": "中",
    },
    "xuanhuan_powerfantasy": {
        "primary_effect": "catharsis_satisfaction",
        "register": "直白爽利",
        "sentence_rhythm": "短句为主、节奏快、爽点密",
        "diction_do": ["利落动作", "即时反馈", "实力展现", "痛快反击"],
        "diction_dont": ["冗长心理描写", "拖沓铺陈", "暧昧不给痛快"],
        "device_kit": ["打脸", "升级", "即时反馈", "扮猪吃虎"],
        "pacing": "快，payoff 密",
        "tension_curve_bias": "锯齿（频繁小高潮）",
        "reveal_cadence": "快速给信息+快速兑现",
        "complexity": "低",
    },
    "tragedy": {
        "primary_effect": "grief",
        "register": "典雅书面、克制",
        "sentence_rhythm": "绵长铺陈、沉缓",
        "diction_do": ["命运感", "反讽", "克制的悲悯", "象征"],
        "diction_dont": ["廉价煽情", "大团圆转折", "插科打诨"],
        "device_kit": ["戏剧反讽", "不可逆抉择", "宿命铺垫"],
        "pacing": "舒缓，步步收紧",
        "tension_curve_bias": "递进到不可逆",
        "reveal_cadence": "均匀延迟",
        "complexity": "高",
    },
    "mystery": {
        "primary_effect": "curiosity",
        "register": "冷静克制、理性",
        "sentence_rhythm": "长短交错、信息精确",
        "diction_do": ["线索铺设", "细节伏笔", "理性推断"],
        "diction_dont": ["提前泄底", "情绪喧宾夺主", "无据巧合"],
        "device_kit": ["误导", "伏笔回收", "信息延迟", "视点遮蔽"],
        "pacing": "均匀延迟，逐步揭开",
        "tension_curve_bias": "均匀上升、终局爆发",
        "reveal_cadence": "均匀延迟",
        "complexity": "高",
    },
    "literary": {
        "primary_effect": "resonance",
        "register": "典雅书面",
        "sentence_rhythm": "绵长铺陈、节奏舒缓",
        "diction_do": ["意象", "通感", "细腻心理", "象征"],
        "diction_dont": ["套路爽点", "扁平说明", "口水对白"],
        "device_kit": ["意象呼应", "留白", "复调", "象征"],
        "pacing": "舒缓",
        "tension_curve_bias": "波浪、内省",
        "reveal_cadence": "均匀延迟",
        "complexity": "高",
    },
}

DEFAULT_GENRE = "literary"


def _norm_genre(genre: str | None) -> str:
    raw = (genre or "").strip()
    g = raw.lower()
    if g in GENRE_PRESETS:
        return g
    # 中文/别名宽松匹配：用户写「玄幻仙侠 / 反传统反派养成 / 爽文+黑色幽默」这种复合串很常见，
    # 必须按"任一关键词出现"匹配，否则一律回落 literary（禁爽点）就是"古风文绉绉"的根因。
    # 顺序=优先级：具体类型词优先于通用"文学"。
    ordered: list[tuple[str, str]] = [
        ("爽文", "xuanhuan_powerfantasy"),
        ("玄幻", "xuanhuan_powerfantasy"),
        ("仙侠", "xuanhuan_powerfantasy"),
        ("修真", "xuanhuan_powerfantasy"),
        ("网文", "xuanhuan_powerfantasy"),
        ("搞笑", "comedy"),
        ("喜剧", "comedy"),
        ("惊悚", "horror"),
        ("恐怖", "horror"),
        ("悲剧", "tragedy"),
        ("推理", "mystery"),
        ("悬疑", "mystery"),
        ("纯文学", "literary"),
        ("文学", "literary"),
    ]
    for kw, target in ordered:
        if kw in raw:
            return target
    return g if g in GENRE_PRESETS else DEFAULT_GENRE


def _preset_profile(genre: str, theme: str = "") -> ToneProfile:
    g = _norm_genre(genre)
    pre = GENRE_PRESETS[g]
    ref = pre.get("tone_reference", "")
    return ToneProfile(
        genre=g,
        primary_effect=pre["primary_effect"],
        register=pre["register"],
        sentence_rhythm=pre["sentence_rhythm"],
        diction_do=list(pre["diction_do"]),
        diction_dont=list(pre["diction_dont"]),
        device_kit=list(pre["device_kit"]),
        pacing=pre["pacing"],
        tension_curve_bias=pre["tension_curve_bias"],
        reveal_cadence=pre["reveal_cadence"],
        complexity=pre["complexity"],
        tone_reference=ref,
        confirmed=False,
    )


def _complete_json(llm: LLMClient, system: str, user: str):
    try:
        raw = llm.complete(system + " 输出必须是合法 json。", user)
    except Exception:
        return None
    text = (raw or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    try:
        return json.loads(text)
    except Exception:
        o, c = text.find("{"), text.rfind("}")
        if 0 <= o < c:
            try:
                return json.loads(text[o : c + 1])
            except Exception:
                return None
        return None


def build_tone_profile(
    repo: Repository,
    llm: LLMClient | None = None,
    genre: str = "",
    theme: str = "",
    setting_hint: str = "",
    template_id: str = "",
) -> ToneProfile:
    """锁定第一步（闸门⓪）：生成文风契约并写入 DB（未确认状态，等用户确认）。

    幂等：若已存在已确认的契约则直接返回（只读保护）。
    template_id 指定题材模板 → 按模板覆盖预设字段，结构化约定更厚（如系统拟人 NPC、
    章节钩子词）。**模板覆盖优先于 LLM**：如果模板已经写死了某字段（register/
    tone_reference 等），LLM 输出对这些字段就不再生效，避免 LLM 把成熟范式稀释成"通用感"。
    未传 template_id → 退回旧链路：_norm_genre + GENRE_PRESETS + LLM 增强。
    """
    existing = repo.get_tone_profile()
    if existing.confirmed:
        return existing

    # ① 模板优先：按模板 tone_overrides 推断 genre + 写好的预设字段
    from . import templates as _tmpls
    tmpl = _tmpls.get(template_id)
    if tmpl is not None:
        ov = tmpl.tone_overrides
        g = _norm_genre(ov.get("genre") or genre)
        profile = _preset_profile(g, theme)
        # 用模板字段覆盖预设（字符串非空 / 列表非空 才覆盖，与 _merge_into_preset 同语义）
        profile = _apply_overrides(profile, ov)
    else:
        g = _norm_genre(genre)
        profile = _preset_profile(g, theme)

    if llm is not None:
        data = _complete_json(
            llm,
            "你是小说的文风总监。基于题材与主题，定下一份贯穿全书的【文风契约】，"
            "它将同时约束规划/表演/渲染三层，确认后全程不变。"
            "primary_effect 是本书每一场都必须交付的主效果（如喜剧=laugh、恐怖=dread、爽文=catharsis_satisfaction）。"
            "tone_reference 写 1-2 句**定调样例正文**（直接示范该基调的腔调，不是说明）。"
            "【语言要求】除 primary_effect 用给定的英文关键词外，其余所有字段"
            "（register/sentence_rhythm/diction_do/diction_dont/device_kit/pacing/"
            "tension_curve_bias/reveal_cadence/complexity/tone_reference）一律**用中文填写**，"
            "不要夹杂英文词。"
            "【时代隔离墙 era_logic】再判断这是不是**前现代/古代/中世纪/远古/奇幻**设定："
            "若是→给出 era_logic：{enabled:true, moral_index(0-100,越低越残酷), "
            "religiosity(0-100,宗教/超自然狂热度), science_level(0-100,科学认知度), "
            "banned_modern_words:[该时代不该出现的现代词/概念，如 心理学/潜意识/效率/系统性/反思/人权/资源优化/基因/细菌], "
            "forced_attribution:'角色面对灾难/异象时该时代的归因逻辑（神罚/诅咒/女巫/血统/命运…）'}；"
            "若是**现代/未来/都市/科幻**设定→ era_logic={enabled:false}。"
            "只输出 JSON：{genre, primary_effect, register, sentence_rhythm, "
            "diction_do:[…], diction_dont:[…], device_kit:[…], pacing, "
            "tension_curve_bias, reveal_cadence, complexity, tone_reference, era_logic:{…}}",
            f"题材：{g}；主题：{theme or '（未定）'}；设定基调提示：{setting_hint or '（无）'}。只输出 JSON。",
        )
        if isinstance(data, dict):
            profile = _merge_into_preset(profile, data)
            # 模板已写死的字段，LLM 不得稀释——再次覆盖一次
            if tmpl is not None:
                profile = _apply_overrides(profile, tmpl.tone_overrides)

    repo.set_tone_profile(profile)
    return profile


def _apply_overrides(base: ToneProfile, ov: dict) -> ToneProfile:
    """把模板的 tone_overrides 应用到 ToneProfile：字符串/列表只在非空时覆盖。
    与 _merge_into_preset 共用同一套语义；era_logic 用 dict 整体替换。"""
    def s(key, default):
        v = ov.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else default

    def lst(key, default):
        v = ov.get(key)
        if isinstance(v, list):
            out = [str(x).strip() for x in v if str(x).strip()]
            if out:
                return out
        return default

    el = ov.get("era_logic")
    era_logic = el if isinstance(el, dict) else base.era_logic
    return ToneProfile(
        genre=s("genre", base.genre),
        primary_effect=s("primary_effect", base.primary_effect),
        register=s("register", base.register),
        sentence_rhythm=s("sentence_rhythm", base.sentence_rhythm),
        diction_do=lst("diction_do", base.diction_do),
        diction_dont=lst("diction_dont", base.diction_dont),
        device_kit=lst("device_kit", base.device_kit),
        pacing=s("pacing", base.pacing),
        tension_curve_bias=s("tension_curve_bias", base.tension_curve_bias),
        reveal_cadence=s("reveal_cadence", base.reveal_cadence),
        complexity=s("complexity", base.complexity),
        tone_reference=s("tone_reference", base.tone_reference),
        confirmed=base.confirmed,
        era_logic=era_logic,
    )


def _merge_into_preset(base: ToneProfile, data: dict) -> ToneProfile:
    """用 LLM 输出覆盖预设里非空的字段；列表字段取 LLM 的（若非空），否则保留预设。"""
    def s(key, default):
        v = data.get(key)
        return str(v).strip() if isinstance(v, str) and v.strip() else default

    def lst(key, default):
        v = data.get(key)
        if isinstance(v, list):
            out = [str(x).strip() for x in v if str(x).strip()]
            if out:
                return out
        return default

    # B0.6 时代隔离墙：采用 LLM 给的 era_logic（dict）；非 dict 则保留 base（默认空=禁用）。
    el = data.get("era_logic")
    era_logic = el if isinstance(el, dict) else base.era_logic

    return ToneProfile(
        genre=_norm_genre(s("genre", base.genre)),
        primary_effect=s("primary_effect", base.primary_effect),
        register=s("register", base.register),
        sentence_rhythm=s("sentence_rhythm", base.sentence_rhythm),
        diction_do=lst("diction_do", base.diction_do),
        diction_dont=lst("diction_dont", base.diction_dont),
        device_kit=lst("device_kit", base.device_kit),
        pacing=s("pacing", base.pacing),
        tension_curve_bias=s("tension_curve_bias", base.tension_curve_bias),
        reveal_cadence=s("reveal_cadence", base.reveal_cadence),
        complexity=s("complexity", base.complexity),
        tone_reference=s("tone_reference", base.tone_reference),
        confirmed=False,
        era_logic=era_logic,
    )


# ---- ⑥ 情感共鸣 E 比率（方法.txt 主题6 / Cognitive Science 2025）----
# 论文：真正触发共鸣的不是抽象情感形容词，而是"具象的生理与环境映射"。
# E = 感官/触觉词 / 抽象情感词；当 E > 3.5 时读者共鸣呈指数上升。
# 高张力场若一味"宣告情感"（绝望/痛苦/崩溃）而少生理细节 → 判不达标，要求改写。
_SENSORY_WORDS = [
    "指尖", "指腹", "手指", "掌心", "喉咙", "喉头", "嗓子", "胸口", "胃部", "胃里",
    "后背", "脊背", "脊梁", "皮肤", "嘴唇", "舌尖", "牙关", "眼眶", "鼻尖", "耳畔",
    "太阳穴", "冷汗", "汗", "冰凉", "发凉", "发冷", "滚烫", "发烫", "刺痛", "酸胀",
    "发酸", "发紧", "绷紧", "僵硬", "颤抖", "哆嗦", "战栗", "发抖", "心跳", "呼吸",
    "气息", "喘", "吞咽", "干涩", "发干", "发麻", "痉挛", "抽搐", "血",
]
_ABSTRACT_EMOTION_WORDS = [
    "绝望", "痛苦", "悲伤", "悲痛", "愤怒", "恐惧", "惊恐", "喜悦", "快乐", "幸福",
    "无助", "孤独", "焦虑", "释然", "欣慰", "崩溃", "心碎", "惆怅", "忧伤", "激动",
    "失落", "沮丧", "恐慌", "惊慌", "无法释怀", "痛不欲生", "悲愤", "绝望透顶",
]


def sensory_ratio(text: str) -> tuple[int, int]:
    """返回 (感官/触觉词命中数, 抽象情感词命中数)。"""
    t = text or ""
    s = sum(t.count(w) for w in _SENSORY_WORDS)
    a = sum(t.count(w) for w in _ABSTRACT_EMOTION_WORDS)
    return s, a


def emotion_ratio_gate(
    text: str, tension: float, e_floor: float = 3.5, min_abstract: int = 2,
) -> tuple[bool, str]:
    """§主题6：仅高张力场（tension≥0.7）启用。本场堆了 ≥min_abstract 个抽象情感词、
    但感官/抽象比 E < e_floor → 判"情感太抽象"，要求改写为生理与环境承载。
    抽象情感词不多则放行（不误伤平和场/克制场）。"""
    if tension < 0.7 or not (text or "").strip():
        return True, ""
    s, a = sensory_ratio(text)
    if a < min_abstract:
        return True, ""
    if s / a < e_floor:
        return False, (
            "本场情感写得太抽象（直接宣告「绝望/痛苦/崩溃」之类），读者无法共鸣。"
            "请删掉大部分抽象情感词，改用**具象的生理与环境反应**承载情感"
            "（胃部绞紧、指尖发凉、喉咙发干、呼吸乱掉、手抖到握不住），让读者自己感到那份情绪。"
        )
    return True, ""


def tone_gate(
    scene_text: str,
    profile: ToneProfile,
    llm: LLMClient | None = None,
    threshold: float = 0.5,
) -> tuple[bool, str]:
    """§16.4 每场"文风义务"硬闸门：本场是否交付 primary_effect + 是否触犯 diction_dont。

    返回 (ok, feedback)。ok=False 时 feedback 指出"哪条没达标"，供带反馈重渲一次。
    - 先做确定性检查：禁忌词直接命中即判违例（无需 LLM）。
    - 有 LLM 时再让其打分（命中主效果 + 未违禁），低于阈值则不过。
    - 无 LLM 且无禁忌命中 → 放行（离线宽松，不阻断流水线）。
    """
    if not profile.is_set() or not (scene_text or "").strip():
        return True, ""

    # 1) 确定性：禁忌词直接命中
    for bad in profile.diction_dont:
        b = (bad or "").strip()
        if b and len(b) <= 8 and b in scene_text:  # 仅短词做字面命中，长描述交给 LLM
            return False, f"触犯文风禁忌「{b}」，请去除并重写以贴合{profile.primary_effect or '本书'}基调。"

    # 1b) B0.6 时代隔离墙：现代禁用词命中即出戏（前现代/奇幻设定）
    el = profile.era_logic or {}
    if el.get("enabled"):
        for bad in (el.get("banned_modern_words") or []):
            b = (bad or "").strip()
            if b and len(b) <= 8 and b in scene_text:
                return False, (f"出现了不属于这个时代的现代词/概念「{b}」，请用当时代的认知与说法重写"
                               f"（角色不懂现代科学/心理学/管理学话术）。")

    if llm is None:
        return True, ""

    # 2) LLM 打分
    data = _complete_json(
        llm,
        "你是文风审校。判断给定正文是否符合既定文风契约："
        f"主效果应为「{profile.primary_effect}」（{profile.register}）；"
        f"禁忌：{('、'.join(profile.diction_dont[:6]) or '无')}。"
        "给出 0..1 的 effect_score（多大程度交付了主效果）、布尔 violates_dont、一句 reason。"
        "只输出 JSON：{effect_score:0到1, violates_dont:true/false, reason:\"…\"}",
        f"正文：{scene_text[:1200]}\n只输出 JSON。",
    )
    if not isinstance(data, dict):
        return True, ""  # 评分失败不阻断
    try:
        score = float(data.get("effect_score", 1.0))
    except Exception:
        score = 1.0
    violates = bool(data.get("violates_dont", False))
    reason = str(data.get("reason", "")).strip()
    if violates or score < threshold:
        fb = reason or f"未充分交付主效果「{profile.primary_effect}」"
        return False, f"{fb}；请重写以贴合文风契约。"
    return True, ""
