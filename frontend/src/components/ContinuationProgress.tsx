import { Sparkles } from 'lucide-react';
import type {
  ContinuationDistillConfig,
  ContinuationJobStatus,
  DistilledKnowledgePackage,
  StoryBibleStatus,
  StoryBibleV2,
} from '../types';

interface ContinuationProgressProps {
  bible: StoryBibleV2 | null;
  busy: boolean;
  distillConfig: ContinuationDistillConfig;
  job: ContinuationJobStatus | null;
  knowledgePackage: DistilledKnowledgePackage | null;
  status: StoryBibleStatus | null;
  onConfigChange: (next: ContinuationDistillConfig) => void;
  onBuildBible: () => void;
}

export function ContinuationProgress({
  bible,
  busy,
  distillConfig,
  job,
  knowledgePackage,
  status,
  onConfigChange,
  onBuildBible,
}: ContinuationProgressProps) {
  return (
    <div className="panel p-4">
      <div className="flex items-center gap-2 text-sm font-semibold">
        <Sparkles className="h-4 w-4 text-amber-400" />
        叙事蒸馏
      </div>
      <button className="btn-primary mt-3 w-full" onClick={onBuildBible} disabled={busy}>
        重建 Story Bible
      </button>

      <div className="mt-3 space-y-3 text-sm">
        <label className="block">
          <div className="mb-1 text-zinc-500">目标分块字数</div>
          <input
            className="input"
            type="number"
            min={10000}
            max={120000}
            step={5000}
            value={distillConfig.targetChunkChars}
            onChange={(event) => onConfigChange({
              ...distillConfig,
              targetChunkChars: Number(event.target.value) || 40000,
            })}
          />
          <div className="mt-1 text-xs text-zinc-400">保持完整章节，默认约 3–5 万字一块。</div>
        </label>
        <label className="block">
          <div className="mb-1 text-zinc-500">每块最多章节</div>
          <input
            className="input"
            type="number"
            min={1}
            max={60}
            value={distillConfig.maxChaptersPerChunk}
            onChange={(event) => onConfigChange({
              ...distillConfig,
              maxChaptersPerChunk: Number(event.target.value) || 25,
            })}
          />
        </label>
        <label className="block">
          <div className="mb-1 text-zinc-500">并发分块数</div>
          <input
            className="input"
            type="number"
            min={1}
            max={12}
            value={distillConfig.distillWorkers}
            onChange={(event) => onConfigChange({
              ...distillConfig,
              distillWorkers: Number(event.target.value) || 4,
            })}
          />
        </label>
        <div className="rounded-lg border border-zinc-200 p-3 text-xs leading-5 text-zinc-500 dark:border-zinc-800">
          每个分块只调用一次模型，同时抽取实体、事件、状态变化、叙事知识、情节线程和文风样本。
          只有覆盖校验失败时才会拆小重试。
        </div>
      </div>

      {(bible || job) && (
        <div className="mt-3 space-y-2 text-xs text-zinc-500">
          {bible ? <div>来源类型：{bible.sourceType}</div> : null}
          {bible ? <div>时间线条目：{bible.timeline.length}</div> : null}
          {bible ? <div>开放问题：{bible.openThreads.length}</div> : null}
          {job ? <div>蒸馏进度：{job.progress ?? 0}/{job.total ?? 0} · {job.status}</div> : null}
          {job?.error ? <div className="text-red-500">失败原因：{job.error}</div> : null}
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

      {knowledgePackage?.stats && (
        <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50/60 p-3 dark:border-emerald-900 dark:bg-emerald-950/20">
          <div className="text-xs font-semibold text-emerald-700 dark:text-emerald-300">小说知识包</div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-zinc-600 dark:text-zinc-400">
            <div>实体：{knowledgePackage.stats.entities ?? 0}</div>
            <div>事件：{knowledgePackage.stats.events ?? 0}</div>
            <div>知识声明：{knowledgePackage.stats.assertions ?? 0}</div>
            <div>状态变化：{knowledgePackage.stats.stateChanges ?? 0}</div>
            <div>情节线程：{knowledgePackage.stats.threads ?? 0}</div>
            <div>文风样本：{knowledgePackage.stats.styleSamples ?? 0}</div>
            <div className={(knowledgePackage.stats.unverifiedEvidence ?? 0) > 0 ? 'text-amber-600 dark:text-amber-300' : ''}>
              未核验证据：{knowledgePackage.stats.unverifiedEvidence ?? 0}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
