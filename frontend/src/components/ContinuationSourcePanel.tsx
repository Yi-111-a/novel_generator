import type { SourceChapter } from '../types';

interface ContinuationSourcePanelProps {
  busy: boolean;
  sourceText: string;
  sourceChapters: SourceChapter[];
  onSourceTextChange: (value: string) => void;
  onImport: () => void;
}

export function ContinuationSourcePanel({
  busy,
  sourceText,
  sourceChapters,
  onSourceTextChange,
  onImport,
}: ContinuationSourcePanelProps) {
  return (
    <>
      <div className="panel p-4">
        <div className="text-sm font-semibold">原文导入</div>
        <textarea
          rows={10}
          className="input mt-3 min-h-52 w-full"
          placeholder="把原著/原稿正文粘贴到这里，系统会按章节标题自动切分并写入 source_chapters。"
          value={sourceText}
          onChange={(e) => onSourceTextChange(e.target.value)}
        />
        <button className="btn-primary mt-3 w-full" onClick={onImport} disabled={busy || !sourceText.trim()}>
          导入原文并启动蒸馏
        </button>
      </div>

      <div className="panel p-4">
        <div className="text-sm font-semibold">已导入原文章节</div>
        <div className="mt-3 space-y-3">
          {sourceChapters.length === 0 ? (
            <p className="text-sm text-zinc-500">还没有导入原文。</p>
          ) : (
            sourceChapters.slice(0, 8).map((chapter) => (
              <div key={chapter.id} className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                <div className="text-sm font-medium">
                  第 {chapter.chapterNo} 章 {chapter.title || ''}
                </div>
                <p className="mt-2 text-xs text-zinc-500">{chapter.summary || '暂无摘要'}</p>
              </div>
            ))
          )}
        </div>
      </div>
    </>
  );
}
