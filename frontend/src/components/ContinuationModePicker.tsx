import type { ContinuationSettings } from '../types';

interface ContinuationModePickerProps {
  busy: string;
  continuation: ContinuationSettings | null;
  onChange: (next: ContinuationSettings) => void;
  onSave: () => void;
  onLock: () => void;
}

export function ContinuationModePicker({
  busy,
  continuation,
  onChange,
  onSave,
  onLock,
}: ContinuationModePickerProps) {
  if (!continuation) return null;

  return (
    <div className="panel p-4">
      <div className="text-sm font-semibold">写作模式</div>
      <div className="mt-3 space-y-3 text-sm">
        <label className="block">
          <div className="mb-1 text-zinc-500">续写方向</div>
          <select
            className="input"
            value={continuation.writeMode}
            onChange={(e) => onChange({ ...continuation, writeMode: e.target.value as ContinuationSettings['writeMode'] })}
          >
            <option value="continue_current_book">接着当前书写</option>
            <option value="new_series_book">开系列新书</option>
          </select>
        </label>
        <label className="block">
          <div className="mb-1 text-zinc-500">续写提示</div>
          <textarea
            rows={4}
            className="input min-h-24 w-full"
            value={continuation.continuationHint}
            onChange={(e) => onChange({ ...continuation, continuationHint: e.target.value })}
          />
        </label>
        <div className="rounded-xl border border-dashed border-zinc-300 p-3 text-xs leading-5 text-zinc-500 dark:border-zinc-700">
          作者经历层、续写大纲和写作快照暂不属于本次 B1–B4 原作蒸馏，后续会作为独立步骤接入。
        </div>
        {continuation.writeMode === 'new_series_book' && (
          <>
            <label className="block">
              <div className="mb-1 text-zinc-500">新书标题</div>
              <input
                className="input"
                value={continuation.currentBookTitle}
                onChange={(e) => onChange({ ...continuation, currentBookTitle: e.target.value })}
              />
            </label>
            <label className="block">
              <div className="mb-1 text-zinc-500">时间位置</div>
              <input
                className="input"
                value={continuation.timePosition}
                onChange={(e) => onChange({ ...continuation, timePosition: e.target.value })}
              />
            </label>
            <label className="block">
              <div className="mb-1 text-zinc-500">主角策略</div>
              <input
                className="input"
                value={continuation.protagonistStrategy}
                onChange={(e) => onChange({ ...continuation, protagonistStrategy: e.target.value })}
              />
            </label>
          </>
        )}
        <div className="text-xs text-zinc-500">
          起始章号：{continuation.chapterStartNo} · 原书最后章号：{continuation.latestSourceChapterNo}
        </div>
        <button className="btn-primary w-full" onClick={onSave} disabled={busy === 'continuation'}>
          保存模式设置
        </button>
        <button className="btn-ghost w-full border border-zinc-200 dark:border-zinc-800" onClick={onLock} disabled={busy === 'lock'}>
          锁定写作上下文
        </button>
      </div>
    </div>
  );
}
