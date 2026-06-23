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
        <label className="block rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-zinc-700 dark:text-zinc-200">最高级蒸馏：作者经历层</div>
              <div className="mt-1 text-xs text-zinc-500">从随笔与经历材料里抽作者人格，再把它压进续写。</div>
            </div>
            <input
              type="checkbox"
              checked={continuation.experienceLayerEnabled}
              onChange={(e) => onChange({ ...continuation, experienceLayerEnabled: e.target.checked })}
            />
          </div>
        </label>
        {continuation.experienceLayerEnabled && (
          <>
            <label className="block">
              <div className="mb-1 text-zinc-500">经历层模式</div>
              <select
                className="input"
                value={continuation.experienceLayerMode}
                onChange={(e) => onChange({ ...continuation, experienceLayerMode: e.target.value })}
              >
                <option value="essay">仅作者随笔</option>
                <option value="essay_plus_text">随笔 + 原作校准</option>
              </select>
            </label>
            <label className="block">
              <div className="mb-1 text-zinc-500">经历层材料路径</div>
              <input
                className="input"
                value={continuation.experienceSourcePath}
                onChange={(e) => onChange({ ...continuation, experienceSourcePath: e.target.value })}
                placeholder="C:\\Users\\...\\江南随笔.epub"
              />
            </label>
            <label className="block">
              <div className="mb-1 text-zinc-500">蒸馏级别</div>
              <select
                className="input"
                value={continuation.experienceStyleLevel}
                onChange={(e) => onChange({ ...continuation, experienceStyleLevel: e.target.value })}
              >
                <option value="high">高</option>
                <option value="max">最高</option>
              </select>
            </label>
          </>
        )}
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
