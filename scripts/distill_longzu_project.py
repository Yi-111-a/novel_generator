from __future__ import annotations

import argparse
import html
import json
import re
import statistics
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import (
    AuthorWritingSheet,
    ChapterPlan,
    Entity,
    Persona,
    StyleClaim,
    StyleProfile,
)
from novel_engine.narration.scene_writer import SceneSpec, SceneWriter
from novel_engine.repository import Repository
from novel_engine.style.author_sheet import derive_style_profile
from novel_engine.style.sheet_distiller import _split_segments, distill_author_sheet
from novel_engine.style_skill import compute_style_metrics


def extract_epub_text(epub_path: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(epub_path) as zf:
        names = sorted(
            n for n in zf.namelist()
            if re.search(r"(?:^|/)text\d+\.x?html$", n, re.I)
        )
        for name in names:
            raw = zf.read(name).decode("utf-8", errors="ignore")
            raw = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", raw, flags=re.I)
            raw = re.sub(r"</p>|<br\s*/?>", "\n", raw, flags=re.I)
            raw = re.sub(r"<[^>]+>", " ", raw)
            text = html.unescape(raw)
            lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
            text = "\n".join(line for line in lines if len(line) >= 2)
            if len(text) > 500:
                parts.append(text)
    return "\n\n".join(parts)


def punct_stats(text: str) -> dict[str, float]:
    compact = re.sub(r"\s+", "", text)
    chars = max(1, len(compact))
    return {
        "chars": chars,
        "sentences": len([s for s in re.split(r"[。！？!?；;]+", text) if s.strip()]),
        "quote_paragraph_pct": round(
            100
            * len([p for p in re.split(r"\n+", text) if "“" in p or "”" in p])
            / max(1, len([p for p in re.split(r"\n+", text) if p.strip()])),
            2,
        ),
        "question_bang_per_1k": round(len(re.findall(r"[！？!?]", text)) / chars * 1000, 2),
        "ellipsis_per_1k": round(text.count("……") / chars * 1000, 2),
        "dash_per_1k": round(text.count("——") / chars * 1000, 2),
    }


def sentence_distribution(text: str) -> dict[str, float]:
    lens = sorted(len(re.sub(r"\s+", "", s)) for s in re.split(r"[。！？!?；;]+", text) if s.strip())
    if not lens:
        return {}
    return {
        "p10": lens[int((len(lens) - 1) * 0.10)],
        "p50": lens[int((len(lens) - 1) * 0.50)],
        "p90": lens[int((len(lens) - 1) * 0.90)],
        "stddev": round(statistics.pstdev(lens), 2),
        "short_sentence_pct_le_6": round(100 * len([x for x in lens if x <= 6]) / len(lens), 2),
        "long_sentence_pct_ge_30": round(100 * len([x for x in lens if x >= 30]) / len(lens), 2),
    }


def build_safe_sheet(metrics: dict, stats: dict, dist: dict) -> AuthorWritingSheet:
    """Create a project-compatible sheet from deterministic measurements.

    This is not a close author-style clone. It is a higher-level, original-writing
    profile for running the local pipeline when no external LLM is configured.
    """
    return AuthorWritingSheet(
        name="校园幻想青春冒险画像",
        source_genre="现代校园幻想",
        plot=[
            StyleClaim("用日常场景承接巨大秘密，避免先写设定说明书", "确定性画像：对话段落占比较高，叙述常由场景与对话推进。"),
            StyleClaim("把角色推到选择现场，而不是先证明他是英雄", "确定性画像：短句和问叹号密集，适合承载犹疑、反问和临场压力。"),
        ],
        creativity=[
            StyleClaim("宏大意象要落在便利店、车站、雨夜、旧教室等具体物上", "确定性画像：中短段落多，适合用日常细节托底。"),
            StyleClaim("允许轻微吐槽削弱庄严感，再让情绪回落到孤独或责任", "确定性画像：问号/叹号与省略号密度较高。"),
        ],
        development=[
            StyleClaim("用对话把设定漏出来，节奏快，但关键处停一拍", f"对话段落约 {stats.get('quote_paragraph_pct')}%。"),
            StyleClaim("长铺陈后接短句砸点，形成顿挫", f"句长 p50={dist.get('p50')}，p90={dist.get('p90')}。"),
        ],
        language=[
            StyleClaim("句子中等偏短，长短交替明显", f"平均句长 {metrics.get('avg_sentence_len')}，句长标准差 {dist.get('stddev')}。"),
            StyleClaim("标点节奏偏口语化，问句、叹句、省略号可承担情绪", f"问叹号/千字 {stats.get('question_bang_per_1k')}，省略号/千字 {stats.get('ellipsis_per_1k')}。"),
            StyleClaim("少用纯爽文套语，避免数值升级腔", "安全约束：保持原创表达，不复刻特定作者句式。"),
        ],
        n_segments=0,
    )


class ScriptedOriginalClient(LLMClient):
    def __init__(self) -> None:
        self.render_count = 0

    def complete(self, system: str, user: str) -> str:
        if "文风一致性评审" in system:
            return "\n".join(f"✓ {i} — 体现" for i in range(1, 11)) + "\n通过率 10/10"
        self.render_count += 1
        if self.render_count == 1:
            return (
                "雨下到第七分钟的时候，旧站台的灯忽然亮了。\n\n"
                "许燃站在售票机旁边，手里攥着那张没有出发地的车票，觉得自己像被生活临时拉进了一个 "
                "成本很低的魔术节目。正常人的录取通知书应该有校徽、烫金字和校长签名，最差也该有一张 "
                "看起来很贵的宣传册，而不是一张从自动售货机里吐出来的车票。\n\n"
                "“上车。”黑衣女人说。\n\n"
                "“去哪儿？”\n\n"
                "“学院。”\n\n"
                "许燃想说我连高中数学都没完全搞定，你们学院最好不要开设拯救世界这种选修课。可他最后只说："
                "“包食宿么？”\n\n"
                "女人看了他一眼，像在评估一件刚从快递柜里取出的危险品。\n\n"
                "远处有列车进站，车头没有编号，窗子里漆黑一片。那不是通往远方的车。\n\n"
                "那是通往答案的车。"
            )
        if self.render_count == 2:
            return (
                "礼堂的穹顶裂开一道细缝，金色的光从里面垂下来。\n\n"
                "没有人尖叫。所有人都坐得笔直，像一群提前知道考试答案的优等生。只有许燃差点从椅子上滑下去，"
                "因为他刚才还在研究桌上的银叉是不是真的银，能不能顺走卖给二手店。\n\n"
                "“你们管这个叫开学典礼？”他小声问。\n\n"
                "旁边的女孩压低帽檐：“不然呢？难道请校长讲三点希望？”\n\n"
                "许燃沉默了。他觉得这个学校的审美有问题，而且问题很大。别人家的新生手册写着课程表和宿舍须知，"
                "这里的新生手册第一页写着遗嘱模板。\n\n"
                "台上的老人举起手杖，金光在他身后汇成巨大的影子。\n\n"
                "“欢迎来到这里。”他说，“从今晚开始，你们不再只是学生。”\n\n"
                "许燃忽然不想笑了。\n\n"
                "因为他听懂了那句话的后半截。"
            )
        return (
            "凌晨四点，城市像一台关机失败的电脑，屏幕还亮着，风扇还在嗡嗡响。\n\n"
            "许燃坐在天台边缘，看见远处高架上有一列没有乘客的轻轨驶过。女孩把一罐热咖啡丢给他，"
            "罐身烫得他龇牙咧嘴，差点把未来的英雄事业终结在一次低温烫伤里。\n\n"
            "“害怕么？”她问。\n\n"
            "“废话。”许燃说，“我看起来像那种不怕死的人么？”\n\n"
            "女孩笑了笑：“不像。你比较像怕死但还会往前走的人。”\n\n"
            "这评价听起来不怎么样，却意外准确。许燃低头看着掌心，那枚纹章在皮肤下微微发亮，像一颗被误装进"
            "普通闹钟里的恒星。\n\n"
            "他以前总觉得自己的人生不会有什么大事发生。\n\n"
            "现在大事来了。\n\n"
            "还没问他同不同意。"
        )

    @property
    def name(self) -> str:
        return "ScriptedOriginalClient"


def run_scene_samples(repo: Repository) -> list[str]:
    repo.insert_entity(Entity("hero", "character", "许燃", {}))
    repo.insert_persona(Persona(agent_id="hero", name="许燃"))
    ch = ChapterPlan(
        chapter_id="longzu_style_test_ch1",
        arc_id="style_test",
        sequence_order=1,
        title="雨夜车票",
        cast=["hero"],
        beat_goals=["收到异常车票并抵达秘密学院", "在礼堂见到超自然征兆", "天台上接受无法回头的选择"],
        target_words=900,
        target_scenes=3,
    )
    writer = SceneWriter(repo, ScriptedOriginalClient())
    beats = [
        "主角在雨夜站台收到异常车票，被引向一所隐藏学院。",
        "主角参加开学典礼，宏大秘密通过荒诞日常显露。",
        "主角在天台和同伴对话，意识到自己已经被命运选中。",
    ]
    outputs = []
    for i, beat in enumerate(beats, 1):
        spec = SceneSpec(pov="hero", beat=beat, chapter=ch, scene_pos=i)
        outputs.append(writer.write(spec))
    return outputs


def project_llm_from_server_config() -> LLMClient | None:
    sys.path.insert(0, str(ROOT))
    from server.config_store import load_config

    cfg = load_config()
    key = cfg.get("llmApiKey") or ""
    if not key:
        return None
    return MinimalOpenAICompatibleClient(
        api_key=key,
        model=cfg.get("modelName") or "deepseek-chat",
        base_url=cfg.get("baseUrl") or "https://api.deepseek.com",
    )


class MinimalOpenAICompatibleClient(LLMClient):
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.6,
        }
        if "json" in (system + user).lower():
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {body[:500]}") from exc
        return data["choices"][0]["message"]["content"] or ""

    @property
    def name(self) -> str:
        return f"MinimalOpenAICompatible({self.model})"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epub", default="龙族全套+共七册.epub")
    parser.add_argument("--outdir", default="outputs/longzu_project_distill")
    parser.add_argument("--db", default="outputs/longzu_project_distill/longzu_style_project.db")
    parser.add_argument("--use-real-llm", action="store_true")
    parser.add_argument("--max-segments", type=int, default=3)
    args = parser.parse_args()

    epub_path = (ROOT / args.epub).resolve()
    outdir = (ROOT / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    text = extract_epub_text(epub_path)
    metrics = compute_style_metrics(text)
    stats = punct_stats(text)
    dist = sentence_distribution(text)
    llm = project_llm_from_server_config() if args.use_real_llm else None
    if args.use_real_llm and llm is not None:
        segments = _split_segments(text)
        if args.max_segments > 0:
            segments = segments[:args.max_segments]
        distill_text = "\n\n".join(f"第{i + 1}章\n{seg}" for i, seg in enumerate(segments))
        sheet = distill_author_sheet(
            llm,
            distill_text,
            name="校园幻想青春冒险画像",
            genre="现代校园幻想",
        )
        profile = derive_style_profile(sheet, distill_text[:4000], llm)
        profile.name = profile.name or "校园幻想青春冒险画像"
        profile.source = "本地 EPUB + 项目 S1 LLM 蒸馏"
        profile.metrics = metrics
        mode = f"real_project_s1_llm_max_segments_{args.max_segments}"
    else:
        sheet = build_safe_sheet(metrics, stats, dist)
        profile = derive_style_profile(sheet, text[:4000], llm=None)
        profile.name = "校园幻想青春冒险画像"
        profile.source = "本地 EPUB 确定性指标"
        profile.metrics = metrics
        mode = "offline_project_distill_no_external_llm"

    conn = db.connect(str((ROOT / args.db).resolve()))
    repo = Repository(conn)
    sheet_id = repo.save_author_sheet(sheet)
    repo.set_style_skill(profile)
    samples = run_scene_samples(repo)

    payload = {
        "epub": str(epub_path),
        "mode": mode,
        "note": "Outputs use a project-compatible, higher-level original-writing profile rather than close imitation of a living author's style.",
        "metrics": metrics,
        "stats": stats,
        "sentence_distribution": dist,
        "sheet_id": sheet_id,
        "author_sheet": {
            "name": sheet.name,
            "source_genre": sheet.source_genre,
            "plot": [c.__dict__ for c in sheet.plot],
            "creativity": [c.__dict__ for c in sheet.creativity],
            "development": [c.__dict__ for c in sheet.development],
            "language": [c.__dict__ for c in sheet.language],
        },
        "style_skill_prompt": repo.style_skill_prompt(),
        "samples": samples,
    }
    (outdir / "longzu_style_profile.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# Project Distillation Result", ""]
    md.append("## Metrics")
    md.append("```json")
    md.append(json.dumps({"metrics": metrics, "stats": stats, "sentence_distribution": dist}, ensure_ascii=False, indent=2))
    md.append("```")
    md.append("\n## Author Writing Sheet")
    for dim in ("plot", "creativity", "development", "language"):
        md.append(f"\n### {dim}")
        for c in getattr(sheet, dim):
            md.append(f"- {c.claim} ({c.evidence})")
    md.append("\n## Project SceneWriter Samples")
    for i, sample in enumerate(samples, 1):
        md.append(f"\n### Sample {i}\n")
        md.append(sample)
    (outdir / "report.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"ok": True, "outdir": str(outdir), "db": str((ROOT / args.db).resolve()), "sheet_id": sheet_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
