import { FileArchive, FileText, UploadCloud, X } from 'lucide-react';
import { useRef, useState } from 'react';
import type { DragEvent } from 'react';
import type { SourceChapter } from '../types';

interface ContinuationSourcePanelProps {
  busy: boolean;
  sourceFiles: File[];
  sourceText: string;
  sourceChapters: SourceChapter[];
  onSourceFilesChange: (files: File[]) => void;
  onSourceTextChange: (value: string) => void;
  onImport: () => void;
}

const ACCEPTED_SUFFIXES = ['.txt', '.epub'];
const MAX_FILE_BYTES = 50 * 1024 * 1024;
const MAX_TOTAL_BYTES = 100 * 1024 * 1024;
const MAX_FILES = 20;

function isAccepted(file: File) {
  return ACCEPTED_SUFFIXES.some((suffix) => file.name.toLowerCase().endsWith(suffix));
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function ContinuationSourcePanel({
  busy,
  sourceFiles,
  sourceText,
  sourceChapters,
  onSourceFilesChange,
  onSourceTextChange,
  onImport,
}: ContinuationSourcePanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState('');

  const addFiles = (incoming: File[]) => {
    const invalid = incoming.find((file) => !isAccepted(file) || file.size > MAX_FILE_BYTES);
    if (invalid) {
      setFileError(
        !isAccepted(invalid)
          ? `${invalid.name} 不是 TXT 或 EPUB 文件。`
          : `${invalid.name} 超过 50 MB。`,
      );
      return;
    }
    const next = [...sourceFiles];
    incoming.forEach((file) => {
      const duplicate = next.some((item) => item.name === file.name && item.size === file.size);
      if (!duplicate) next.push(file);
    });
    if (next.length > MAX_FILES) {
      setFileError(`一次最多选择 ${MAX_FILES} 个文件。`);
      return;
    }
    if (next.reduce((sum, file) => sum + file.size, 0) > MAX_TOTAL_BYTES) {
      setFileError('本次文件总大小不能超过 100 MB。');
      return;
    }
    setFileError('');
    onSourceFilesChange(next);
  };

  const dropFiles = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    addFiles(Array.from(event.dataTransfer.files));
  };

  const hasInput = sourceFiles.length > 0 || sourceText.trim().length > 0;

  return (
    <>
      <div className="panel p-4">
        <div className="text-sm font-semibold">原文导入</div>
        <div
          className={`mt-3 rounded-xl border border-dashed px-4 py-6 text-center transition ${
            dragging
              ? 'border-amber-400 bg-amber-50 dark:bg-amber-950/20'
              : 'border-zinc-300 bg-zinc-50/70 hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900/50'
          }`}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={dropFiles}
        >
          <UploadCloud className="mx-auto h-7 w-7 text-amber-500" />
          <div className="mt-2 text-sm font-medium">拖入原作文件</div>
          <div className="mt-1 text-xs text-zinc-500">支持 TXT、EPUB，可一次选择多个文件；单个文件不超过 50 MB</div>
          <button className="btn-ghost mt-3 border border-zinc-200 dark:border-zinc-700" type="button" onClick={() => inputRef.current?.click()}>
            选择文件
          </button>
          <input
            ref={inputRef}
            className="sr-only"
            type="file"
            accept=".txt,.epub,text/plain,application/epub+zip"
            multiple
            onChange={(event) => {
              addFiles(Array.from(event.target.files ?? []));
              event.target.value = '';
            }}
          />
        </div>

        {sourceFiles.length > 0 && (
          <div className="mt-3 space-y-2">
            {sourceFiles.map((file) => (
              <div key={`${file.name}-${file.size}`} className="flex items-center gap-3 rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
                {file.name.toLowerCase().endsWith('.epub') ? (
                  <FileArchive className="h-4 w-4 shrink-0 text-amber-500" />
                ) : (
                  <FileText className="h-4 w-4 shrink-0 text-zinc-500" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">{file.name}</div>
                  <div className="text-xs text-zinc-500">{formatBytes(file.size)}</div>
                </div>
                <button
                  type="button"
                  className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                  aria-label={`移除 ${file.name}`}
                  onClick={() => onSourceFilesChange(sourceFiles.filter((item) => item !== file))}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        )}

        {fileError && <p className="mt-2 text-xs text-red-500">{fileError}</p>}
        <div className="my-4 flex items-center gap-3 text-[11px] uppercase tracking-[0.18em] text-zinc-400">
          <span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
          或粘贴正文
          <span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
        </div>
        <textarea
          rows={10}
          className="input mt-3 min-h-52 w-full"
          placeholder="把原著/原稿正文粘贴到这里，系统会按章节标题自动切分并写入 source_chapters。"
          value={sourceText}
          onChange={(e) => onSourceTextChange(e.target.value)}
          disabled={sourceFiles.length > 0}
        />
        {sourceFiles.length > 0 && <p className="mt-2 text-xs text-zinc-500">已选择文件，本次会优先导入文件；移除文件后可改用粘贴正文。</p>}
        <button className="btn-primary mt-3 w-full" onClick={onImport} disabled={busy || !hasInput}>
          {busy ? '正在导入与蒸馏…' : `导入${sourceFiles.length > 0 ? ` ${sourceFiles.length} 个文件` : '原文'}并启动蒸馏`}
        </button>
        <p className="mt-2 text-center text-xs text-zinc-500">重新导入会替换当前项目的原文与切章结果。</p>
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
