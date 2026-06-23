"""把龙族七册 epub 切出第一册 → 龙族1.txt。

策略：解 toc.ncx 拿 navPoint 列表；找标题里含"龙族"+ "II"/"2"/"悼亡者" 的入口当第二册起点；
把它对应的 text file 之前的所有正文导出。如果 TOC 解不出来，fallback 按全文 1/7 截断。
"""
from __future__ import annotations

import html
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPUB = ROOT / "龙族全套+共七册.epub"
OUT = ROOT / "龙族1.txt"


def parse_toc(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    raw = zf.read("OEBPS/toc.ncx").decode("utf-8", errors="ignore")
    pattern = re.compile(
        r"<navPoint[^>]*?>\s*<navLabel>\s*<text>([^<]+)</text>\s*</navLabel>\s*<content src=\"([^\"]+)\"",
        re.S,
    )
    return [(t.strip(), src.strip()) for t, src in pattern.findall(raw)]


BOOK2_HINTS = ("II", "Ⅱ", "悼亡者", "第二册", "卷二", "(二)", "（二）", " 2 ", "2：", "2：")


def find_book2_start(toc: list[tuple[str, str]]) -> str | None:
    """返回第二册第一个 navPoint 的 src 文件名（不含锚点）。"""
    for title, src in toc:
        if "龙族" not in title:
            continue
        if any(h in title for h in BOOK2_HINTS):
            return src.split("#")[0]
    return None


HTML_TAG = re.compile(r"<[^>]+>")
SCRIPT_STYLE = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", re.I)
BLOCK_BR = re.compile(r"</p>|<br\s*/?>", re.I)


def html_to_text(raw: str) -> str:
    raw = SCRIPT_STYLE.sub("", raw)
    raw = BLOCK_BR.sub("\n", raw)
    raw = HTML_TAG.sub(" ", raw)
    text = html.unescape(raw)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if len(ln) >= 2)


def main() -> int:
    if not EPUB.exists():
        print(f"epub not found: {EPUB}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(EPUB) as zf:
        toc = parse_toc(zf)
        print(f"TOC navPoints: {len(toc)}")
        for t, src in toc[:30]:
            print(f"  {src:30s} | {t}")

        book2_src = find_book2_start(toc)
        print(f"\nbook2 start file: {book2_src}")

        text_files = sorted(
            n for n in zf.namelist()
            if re.search(r"OEBPS/text\d+\.x?html$", n, re.I)
        )
        if book2_src:
            book2_basename = Path(book2_src).name
            cutoff = next(
                (i for i, n in enumerate(text_files) if Path(n).name == book2_basename),
                None,
            )
        else:
            cutoff = None

        if cutoff is None or cutoff <= 0:
            cutoff = len(text_files) // 7
            print(f"  TOC unclear, fallback cutoff = {cutoff} (1/7 of total {len(text_files)})")
        else:
            print(f"  cutoff = file index {cutoff} ({text_files[cutoff]})")

        book1_files = text_files[:cutoff]
        print(f"book1 files: {len(book1_files)}  ({book1_files[0]} … {book1_files[-1]})")

        parts: list[str] = []
        for name in book1_files:
            raw = zf.read(name).decode("utf-8", errors="ignore")
            txt = html_to_text(raw)
            if len(txt) > 200:
                parts.append(txt)

    out_text = "\n\n".join(parts)
    OUT.write_text(out_text, encoding="utf-8")
    print(f"\nwrote {OUT} : {len(out_text):,} chars / {len(parts)} sections")
    return 0


if __name__ == "__main__":
    sys.exit(main())
