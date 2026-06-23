"""固化 worldbible.build_factions 的 prompt 关键约束。

防呆作用：以后任何对这两段 prompt 的"无心修改"——例如：
  · 删掉「保留点名势力」硬约束 → 又会回到种子里点过名的本地势力被 LLM 自由发挥换名
  · 重新塞回 "乌涂盟/七井会" 等项目特定例子 → 通用模块被项目语料污染
  · 改掉 alliance_members 通用键名为 mingxu_nine_sects 之类项目专用名 → 锁死单题材
立刻 CI 红灯。

测试策略：把源码当字符串读，校验关键 substring 是否到位/是否泄漏。
不调 LLM，毫秒级。
"""
from __future__ import annotations

from pathlib import Path

import pytest


WORLDBIBLE = (Path(__file__).resolve().parents[1]
              / "src" / "novel_engine" / "worldbible.py").read_text(encoding="utf-8")


# ---------- ① 总览 prompt：保留点名势力·硬约束 ----------

def test_overview_prompt_has_preserve_named_constraint():
    """① 总览 prompt 里必须出现『保留点名势力·硬约束』及『必须沿用原名』，
    防止种子里点过名的本地势力被 LLM 自由换名（曾让乌涂盟→乌涂苟帮）。"""
    assert "保留点名势力·硬约束" in WORLDBIBLE
    assert "必须沿用原名" in WORLDBIBLE


def test_overview_prompt_uses_generic_terms_only():
    """① 总览 prompt 中描述"点名势力"时不得列具体项目名作为例子。
    凡是历史项目里出现过的具体名字一律禁止。"""
    leak_words = [
        "乌涂盟", "七井会", "残喘门", "乌涂苟帮", "七井万金会", "漏风崖隐修会",
        "青衍宗",  # 项目专属师门名
        "明序九宗", "明序九派",  # 项目专属联盟标签
    ]
    # 仅检查 over_sys 那一段 prompt 字符串（避开同文件其它合法引用）
    over_block_start = WORLDBIBLE.find("over_sys = (")
    over_block_end = WORLDBIBLE.find(")", over_block_start)
    assert over_block_start > 0 and over_block_end > over_block_start, "找不到 over_sys 块"
    over_block = WORLDBIBLE[over_block_start:over_block_end]
    for w in leak_words:
        assert w not in over_block, (
            f"通用 prompt 不准沾项目语料：'{w}' 出现在 over_sys 块里。"
            "请改用泛指（如『被明确叫出名字的宗门/帮派/会社/组织』）。"
        )


# ---------- ⑦ 远景势力扫描 prompt ----------

def test_far_scan_prompt_has_two_step_extraction():
    """⑦ prompt 必须保留两步语义：① 抽显式点名势力；② 据数字暗示补全联盟成员。"""
    assert "抽出所有被明确点名" in WORLDBIBLE
    assert "alliance_members" in WORLDBIBLE  # 通用 JSON 键名
    # 数字暗示泛例：至少要有"N 大 / N 个 / N 家"这种结构
    assert "N 大正派" in WORLDBIBLE
    assert "N 大宗门" in WORLDBIBLE or "N 个城邦" in WORLDBIBLE or "N 家家族" in WORLDBIBLE


def test_far_scan_prompt_no_project_specific_examples():
    """⑦ prompt 不准把项目特定名字写成"举例"——否则下次拿这工具写都市/科幻
    都会被这些仙侠词汇污染。"""
    far_block_start = WORLDBIBLE.find("def _scan_and_add_far_factions")
    assert far_block_start > 0, "找不到 _scan_and_add_far_factions"
    # 取整个函数体到下一个 def 之前
    next_def = WORLDBIBLE.find("\ndef ", far_block_start + 1)
    far_block = WORLDBIBLE[far_block_start: next_def if next_def > 0 else len(WORLDBIBLE)]

    leak_words = [
        "乌涂盟", "七井会", "残喘门", "乌涂苟帮", "七井万金会", "漏风崖隐修会",
        "青衍宗", "玉鼎宗", "剑墟阁", "玄霄宫", "太虚门",  # 任何项目里 LLM 生成过的具名
        "摘星楼", "九鼎门",  # 早期 prompt 里曾被列为例子的具名
        "明序",  # 项目联盟名
    ]
    for w in leak_words:
        assert w not in far_block, (
            f"远景势力扫描 prompt 不准沾项目语料：'{w}' 出现在函数体里。"
            "请改用泛指或抽象描述。"
        )


def test_far_scan_prompt_uses_generic_alliance_key():
    """JSON 输出键名必须是通用的 alliance_members，
    不得退回任何项目专用键（mingxu_nine_sects / qingyan_disciples 等）。"""
    far_block_start = WORLDBIBLE.find("def _scan_and_add_far_factions")
    next_def = WORLDBIBLE.find("\ndef ", far_block_start + 1)
    far_block = WORLDBIBLE[far_block_start: next_def if next_def > 0 else len(WORLDBIBLE)]
    assert '"alliance_members"' in far_block, "应使用通用键名 alliance_members"
    forbidden_keys = ["mingxu_nine_sects", "mingxu_sects", "nine_sects"]
    for k in forbidden_keys:
        assert k not in far_block, f"禁止使用项目专用键名 '{k}'"


# ---------- build_factions 必须调用 ⑦ ----------

def test_build_factions_invokes_far_scan():
    """build_factions 的执行链尾必须挂上 ⑦ 远景扫描；
    不然新远景宗门永远入不了库，问题又会复现。"""
    assert "_scan_and_add_far_factions(repo, llm, world_blob)" in WORLDBIBLE
    assert '"far_factions"' in WORLDBIBLE  # 统计返回字段
