import { Sparkles } from 'lucide-react';
import type { ContinuationDistillConfig, ContinuationJobStatus, StoryBibleStatus, StoryBibleV2 } from '../types';

interface ContinuationProgressProps {
  bible: StoryBibleV2 | null;
  busy: boolean;
  distillConfig: ContinuationDistillConfig;
  job: ContinuationJobStatus | null;
  status: StoryBibleStatus | null;
  onConfigChange: (next: ContinuationDistillConfig) => void;
  onBuildBible: () => void;
}

export function ContinuationProgress({
  bible,
  busy,
  distillConfig,
  job,
  status,
  onConfigChange,
  onBuildBible,
}: ContinuationProgressProps) {
  return (
    <div className="panel p-4">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <Sparkles className="h-4 w-4 text-amber-400" />
        Story Bible
      </div>
      <button className="btn-primary mt-3 w-full" onClick={onBuildBible} disabled={busy}>
        重建 Story Bible
      </button>
      <div className="mt-3 space-y-3 text-sm">
        <label className="block">
          <div className="mb-1 text-zinc-500">章节抽样</div>
          <select
            className="input"
            value={distillConfig.sampleMode}
            onChange={(e) => onConfigChange({ ...distillConfig, sampleMode: e.target.value as ContinuationDistillConfig['sampleMode'] })}
          >
            <option value="fast">快速抽样</option>
            <option value="representative">代表性抽样</option>
            <option value="full">全量</option>
          </select>
        </label>
        <label className="block">
          <div className="mb-1 text-zinc-500">图谱层级</div>
          <select
            className="input"
            value={distillConfig.graphDetail}
            onChange={(e) => onConfigChange({ ...distillConfig, graphDetail: e.target.value as ContinuationDistillConfig['graphDetail'] })}
          >
            <option value="light">轻</option>
            <option value="medium">中</option>
            <option value="heavy">重</option>
          </select>
        </label>
        <label className="block">
          <div className="mb-1 text-zinc-500">文风抽样段数</div>
          <input
            className="input"
            type="number"
            value={distillConfig.styleSampleSegments}
            onChange={(e) => onConfigChange({ ...distillConfig, styleSampleSegments: Number(e.target.value) || 6 })}
          />
        </label>
        <label className="flex items-center gap-2 text-zinc-500">
          <input
            type="checkbox"
            checked={distillConfig.generateAws}
            onChange={(e) => onConfigChange({ ...distillConfig, generateAws: e.target.checked })}
          />
          生成 AWS
        </label>
        <label className="flex items-center gap-2 text-zinc-500">
          <input
            type="checkbox"
            checked={distillConfig.enableStyleSkill}
            onChange={(e) => onConfigChange({ ...distillConfig, enableStyleSkill: e.target.checked })}
          />
          启用 style_skill
        </label>
        <label className="flex items-center gap-2 text-zinc-500">
          <input
            type="checkbox"
            checked={distillConfig.extractUnresolvedThreads}
            onChange={(e) => onConfigChange({ ...distillConfig, extractUnresolvedThreads: e.target.checked })}
          />
          抽未解伏笔
        </label>
        <label className="flex items-center gap-2 text-zinc-500">
          <input
            type="checkbox"
            checked={distillConfig.extractCharacterEndings}
            onChange={(e) => onConfigChange({ ...distillConfig, extractCharacterEndings: e.target.checked })}
          />
          抽角色终局
        </label>
        <label className="flex items-center gap-2 text-zinc-500">
          <input
            type="checkbox"
            checked={distillConfig.extractFactionState}
            onChange={(e) => onConfigChange({ ...distillConfig, extractFactionState: e.target.checked })}
          />
          抽势力格局
        </label>
        <label className="flex items-center gap-2 text-zinc-500">
          <input
            type="checkbox"
            checked={distillConfig.extractExpandableRegions}
            onChange={(e) => onConfigChange({ ...distillConfig, extractExpandableRegions: e.target.checked })}
          />
          抽可扩展区域
        </label>
      </div>
      {bible && (
        <div className="mt-3 space-y-2 text-xs text-zinc-500">
          <div>来源类型：{bible.sourceType}</div>
          <div>时间线条目：{bible.timeline.length}</div>
          <div>开放问题：{bible.openThreads.length}</div>
          {job ? <div>蒸馏进度：{job.progress ?? 0}/{job.total ?? 0} · {job.status}</div> : null}
          {status?.pendingDraftId ? <div>待验收章节：第 {status.pendingChapterNo} 章</div> : null}
          {status?.continuationPhase ? <div>当前阶段：{status.continuationPhase}</div> : null}
          {job?.steps?.length ? (
            <div className="mt-2 grid grid-cols-1 gap-2">
              {job.steps.map((step) => (
                <div key={step.code} className="flex items-center justify-between rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
                  <span>{step.code} {step.label}</span>
                  <span className={step.status === 'done' ? 'text-emerald-500' : step.status === 'running' ? 'text-amber-500' : 'text-zinc-400'}>
                    {step.status === 'done' ? '完成' : step.status === 'running' ? '进行中' : '待执行'}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
