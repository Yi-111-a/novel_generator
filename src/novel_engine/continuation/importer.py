from __future__ import annotations

import html
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..models import SourceChapter, SourceDocument
from ..repository import Repository
@dataclass
class ImportedSource:
    filename: str
    format: str
    text: str


def load_sources(*, text: str = "", filename: str = "", file_path: str = "", file_paths: list[str] | None = None) -> list[ImportedSource]:
    if text.strip():
        return [ImportedSource(filename=filename or "source.txt", format=_suffix_to_format(filename or "source.txt"), text=text)]

    paths = [p for p in ([file_path] if file_path else []) + list(file_paths or []) if str(p).strip()]
    out: list[ImportedSource] = []
    for raw in paths:
        path = Path(raw).resolve()
        suffix = path.suffix.lower()
        if suffix == ".epub":
            out.append(ImportedSource(filename=path.name, format="epub", text=_extract_epub_text(path)))
        else:
            out.append(ImportedSource(filename=path.name, format=_suffix_to_format(path.name), text=path.read_text(encoding="utf-8", errors="ignore")))
    return out


def import_into_repo(repo: Repository, *, project_id: str, created_at: str, text: str = "", filename: str = "", file_path: str = "", file_paths: list[str] | None = None) -> dict[str, int | str]:
    sources = load_sources(text=text, filename=filename, file_path=file_path, file_paths=file_paths)
    if not sources:
        return {"documents": 0, "chapters": 0}

    repo.clear_source_material()
    chapter_no = 1
    total_dropped = 0
    for source in sources:
        cleaned_text, cleanup_log = _strip_paratext(source.text)
        total_dropped += cleanup_log.get("dropped_line_count", 0)
        doc = SourceDocument(
            project_id=project_id,
            filename=source.filename,
            format=source.format,
            raw_text=cleaned_text,
            created_at=created_at,
        )
        doc.id = repo.insert_source_document(doc)
        try:
            repo.conn.execute(
                "UPDATE source_documents SET cleanup_log_json=? WHERE id=?",
                (json.dumps(cleanup_log, ensure_ascii=False), doc.id),
            )
            repo.conn.commit()
        except Exception:
            pass
        chapters = _split_text_into_chapters(cleaned_text) or [("正文", cleaned_text.strip())]
        for title, content in chapters:
            body = (content or "").strip()
            if not body:
                continue
            repo.insert_source_chapter(
                SourceChapter(
                    project_id=project_id,
                    source_document_id=doc.id,
                    chapter_no=chapter_no,
                    title=(title or f"第{chapter_no}章").strip(),
                    text=body,
                    word_count=len(body),
                    summary=body.replace("\n", " ")[:220],
                    created_at=created_at,
                )
            )
            chapter_no += 1
    return {"documents": len(sources), "chapters": max(0, chapter_no - 1)}


def _suffix_to_format(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix == ".epub":
        return "epub"
    if suffix == ".docx":
        return "docx"
    return "txt"


def _extract_epub_text(epub_path: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(epub_path) as zf:
        names = sorted(
            n
            for n in zf.namelist()
            if re.search(r"(?:^|/)(?:text\d+|chapter\d+|part\d+|section\d+|.*chapter.*)\.x?html$", n, re.I)
        )
        if not names:
            names = sorted(n for n in zf.namelist() if n.lower().endswith((".html", ".xhtml", ".htm")))
        for name in names:
            raw = zf.read(name).decode("utf-8", errors="ignore")
            raw = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", raw, flags=re.I)
            raw = re.sub(r"</p>|<br\s*/?>|</div>|</h\d>", "\n", raw, flags=re.I)
            raw = re.sub(r"<[^>]+>", " ", raw)
            text = html.unescape(raw)
            lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
            cleaned = "\n".join(line for line in lines if len(line) >= 2)
            if len(cleaned) > 50:
                parts.append(cleaned)
    return "\n\n".join(parts)


# 覆盖中文小说常见章节标记：
# - 第X章/节/回/幕/卷/部/集（X 可以是中文数字或阿拉伯数字，字间可有空格，如「第 一 章」「第一幕」）
# - 单字符特殊标记：序章/楔子/引子/开篇/尾声/终章/后记/番外/外传，字间允许空格（如「序 章」）
# - 英文 Chapter N / Part N / Book N / Prologue / Epilogue
_CHAPTER_RE = re.compile(
    r"^\s*("
    r"第\s*[一二三四五六七八九十百千零两0-9]+\s*[章节回幕卷部集篇]"
    r"|序\s*章|序\s*幕|楔\s*子|引\s*子|开\s*篇|尾\s*声|终\s*章|终\s*幕|后\s*记|番\s*外|外\s*传"
    r"|Chapter\s+\d+|Part\s+\d+|Book\s+\d+|Prologue|Epilogue"
    r")\b.*$",
    re.IGNORECASE,
)
_TITLE_MAX_LEN = 40           # 章节标题独占一行；过长说明误把正文当标题
_CHAPTER_MIN_BODY = 200       # 章节正文 < 200 字视为伪章节（目录、空标题）丢弃


def _split_text_into_chapters(text: str) -> list[tuple[str, str]]:
    lines = text.replace("\r", "").split("\n")
    chunks: list[tuple[str, list[str]]] = []
    current_title = "正文"
    current_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) <= _TITLE_MAX_LEN and _CHAPTER_RE.match(stripped):
            if current_lines:
                chunks.append((current_title, current_lines))
            current_title = stripped
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append((current_title, current_lines))
    # 过滤：正文不足 _CHAPTER_MIN_BODY 字 → 目录区、空标题区，不算独立章
    result: list[tuple[str, str]] = []
    for title, body in chunks:
        body_text = "\n".join(body).strip()
        if len(body_text) >= _CHAPTER_MIN_BODY:
            result.append((title, body_text))
    return result


# ===== B0.5 通用 paratext 清洗 =====
# 目标：去掉版权页、出版信息、广告、读者俱乐部、扫码关注、ISBN/编码等"非正文"行。
# 全部用通用模式，不含任何具体出版社/作者/作品名 —— 换任何长篇都成立。
_PARATEXT_PATTERNS = [
    r"©|copyright|版\s*权|著作权",
    r"\bISBN\b|\bDNA-BN\b|\bEAN\b|条形?码",
    r"出版许可|新出网证|准印证|图书在版编目|CIP",
    r"出版社|出版集团|出版股份|传媒(有限)?公司|文化(传播|发展)?有限公司|印刷|发行|经销",
    r"[\w.\-]+@[\w.\-]+\.\w+",                    # 邮箱
    r"www\.|https?://|\.com\b|\.net\b|\.cn\b",     # 网址
    r"网\s*址|电子邮箱|客服|微信|公众号|微博|扫码|二维码|读者俱乐部|读者群",
    r"定价|价格|￥|\bRMB\b|元/册",
    r"Publishing|Digital\s+Media|CO\.,?\s*LTD|Group|Press\b",
    r"本书(电子)?版|未经.{0,6}(许可|授权|同意)|不得.{0,8}(翻印|复制|转载|节录)",
    r"最后修订|修订日期|版次|印次|开本|字数：|页数：",
    r"^[A-Z0-9\-\s.,]{12,}$",                       # 整行全大写/编码（注册声明类）
]
_PARATEXT_RE = re.compile("|".join(_PARATEXT_PATTERNS), re.IGNORECASE)

# 真章节标题（用于定位正文起点；比 _CHAPTER_RE 略宽，含"目 录"排除）
_REAL_CHAPTER_START_RE = _CHAPTER_RE


def _strip_paratext(text: str) -> tuple[str, dict]:
    """切章前清洗：逐行剔除版权/出版/广告类 paratext，并裁掉首个真章节标题之前的前言区。

    返回 (cleaned_text, cleanup_log)。通用规则，无任何作品/出版社专名。
    """
    raw_lines = text.replace("\r", "").split("\n")
    dropped: list[str] = []

    # ① 行级：命中 paratext 模式的整行丢弃（正文极少出现 ©/ISBN/邮箱/网址）
    kept: list[str] = []
    for line in raw_lines:
        s = line.strip()
        if s and len(s) <= 60 and _PARATEXT_RE.search(s):
            dropped.append(s)
        else:
            kept.append(line)

    # ② 块级：裁掉"文件开头 → 第一个真章节标题"之间的前言/版权/目录区。
    #    仅当确实存在真章节标题时才裁（否则全文可能本就无章节标记，保守不动）。
    first_chapter_idx = None
    for i, line in enumerate(kept):
        s = line.strip()
        if s and len(s) <= _TITLE_MAX_LEN and _REAL_CHAPTER_START_RE.match(s):
            # "目 录" 区的章节名也会命中；要求其后 800 字内有足够正文才认作真起点
            tail = "\n".join(kept[i + 1:i + 40])
            if len(tail.strip()) >= 400:
                first_chapter_idx = i
                break
    if first_chapter_idx is not None and first_chapter_idx > 0:
        for line in kept[:first_chapter_idx]:
            if line.strip():
                dropped.append(line.strip())
        kept = kept[first_chapter_idx:]

    cleaned = "\n".join(kept).strip()
    log = {
        "dropped_line_count": len(dropped),
        "dropped_sample": dropped[:40],
        "front_matter_trimmed": bool(first_chapter_idx),
        "original_char_count": len(text),
        "cleaned_char_count": len(cleaned),
    }
    return cleaned, log
