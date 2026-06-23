import type { ChapterDraft, ContinuationStyleDiagnostics } from '../types';

function entriesOf(record: Record<string, number> | undefined) {
  return Object.entries(record || {}).sort((a, b) => b[1] - a[1]);
}

export function StyleDiagnosticsPanel({
  diagnostics,
  draft,
}: {
  diagnostics: ContinuationStyleDiagnostics | null;
  draft: ChapterDraft | null;
}) {
  const candidates = ((draft?.contextSnapshot as any)?.style_candidates as Array<Record<string, any>> | undefined) || [];
  const selected = ((draft?.contextSnapshot as any)?.style_selection as Record<string, any> | undefined) || null;
  const corpus = diagnostics?.corpus;

  return (
    <div className="panel p-4">
      <div className="text-sm font-semibold">风格诊断台</div>
      <div className="mt-3 grid gap-4 xl:grid-cols-2">
        <div className="space-y-3">
          <div className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            <div className="font-medium">语料库概况</div>
            <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-zinc-500">
              <div>片段 {corpus?.segmentCount ?? 0}</div>
              <div>聚类 {corpus?.clusterCount ?? 0}</div>
              <div>负例 {corpus?.negativeSampleCount ?? 0}</div>
            </div>
            <div className="mt-2 text-xs text-zinc-500">禁用片段 {corpus?.disabledSegmentCount ?? 0}</div>
          </div>

          <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
            <div className="font-medium text-sm">声部覆盖</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {entriesOf(corpus?.voiceCoverage).map(([key, value]) => (
                <span key={key} className="rounded-full bg-zinc-100 px-2 py-1 dark:bg-zinc-900">
                  {key} {value}
                </span>
              ))}
              {entriesOf(corpus?.voiceCoverage).length === 0 ? <span className="text-zinc-500">暂无</span> : null}
            </div>
          </div>

          <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
            <div className="font-medium text-sm">语篇覆盖</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {entriesOf(corpus?.discourseCoverage).map(([key, value]) => (
                <span key={key} className="rounded-full bg-zinc-100 px-2 py-1 dark:bg-zinc-900">
                  {key} {value}
                </span>
              ))}
              {entriesOf(corpus?.discourseCoverage).length === 0 ? <span className="text-zinc-500">暂无</span> : null}
            </div>
          </div>

          <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
            <div className="font-medium text-sm">场景覆盖</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {entriesOf(corpus?.sceneCoverage).map(([key, value]) => (
                <span key={key} className="rounded-full bg-zinc-100 px-2 py-1 dark:bg-zinc-900">
                  {key} {value}
                </span>
              ))}
              {entriesOf(corpus?.sceneCoverage).length === 0 ? <span className="text-zinc-500">暂无</span> : null}
            </div>
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            <div className="font-medium">最新候选对比</div>
            {candidates.length === 0 ? (
              <p className="mt-2 text-xs text-zinc-500">生成草稿后，这里会显示多候选评分和选中结果。</p>
            ) : (
              <div className="mt-2 space-y-2">
                {candidates.map((candidate, index) => {
                  const score = candidate.scoreBreakdown || {};
                  const picked = selected?.selectedCandidateId === candidate.candidateId;
                  return (
                    <div key={candidate.candidateId || index} className={`rounded-lg border p-3 text-xs ${picked ? 'border-emerald-500/50 bg-emerald-500/10' : 'border-zinc-200 dark:border-zinc-800'}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">候选 {String.fromCharCode(65 + index)}</span>
                        <span>{Number(score.finalScore || 0).toFixed(3)}</span>
                      </div>
                      <div className="mt-2 text-zinc-500">
                        声部 {Number(score.voiceSimilarity || 0).toFixed(2)} / 风格 {Number(score.stylometricSimilarity || 0).toFixed(2)} / 重复惩罚 {Number(score.repetitionPenalty || 0).toFixed(2)}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
            <div className="font-medium text-sm">低置信片段</div>
            <div className="mt-2 space-y-2">
              {(corpus?.lowConfidenceSegments || []).slice(0, 4).map((segment) => (
                <div key={segment.id} className="rounded-lg bg-zinc-100 p-2 dark:bg-zinc-900">
                  <div className="text-zinc-500">{segment.discourseType} / {segment.voiceType} / {segment.confidence.toFixed(2)}</div>
                  <div className="mt-1 whitespace-pre-wrap">{segment.text}</div>
                </div>
              ))}
              {(corpus?.lowConfidenceSegments || []).length === 0 ? <div className="text-zinc-500">暂无</div> : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
