import type { ChapterDraft, ContinuationStyleDiagnostics } from '../types';

function entriesOf(record: Record<string, number> | undefined) {
  return Object.entries(record || {}).sort((a, b) => b[1] - a[1]);
}

export function VoiceProfilePanel({
  diagnostics,
  draft,
}: {
  diagnostics: ContinuationStyleDiagnostics | null;
  draft: ChapterDraft | null;
}) {
  const latestDraft = diagnostics?.latestDraft;
  const activePacket = (draft?.stylePacket || latestDraft?.stylePacket || {}) as Record<string, any>;
  const router = (activePacket.router || {}) as Record<string, any>;
  const latestSnapshot = (latestDraft?.contextSnapshot || {}) as Record<string, any>;
  const styleState = (((draft?.contextSnapshot as any)?.continuation_snapshot || latestSnapshot.continuation_snapshot || {}) as any)?.style_state || {};
  const corpus = diagnostics?.corpus;

  return (
    <div className="panel p-4">
      <div className="text-sm font-semibold">声部路由</div>
      <div className="mt-3 grid gap-4 xl:grid-cols-2">
        <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
          <div className="font-medium text-sm">当前 Voice Router</div>
          <div className="mt-2 space-y-1 text-zinc-500">
            <div>primary: {String(router.primary_profile || 'narrator_default')}</div>
            <div>secondary: {String(router.secondary_profile || 'neutral')}</div>
            <div>speaker: {String(router.speaker || router.pov || 'n/a')}</div>
            <div>scene: {String(router.scene_function || 'general')}</div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div className="rounded-lg bg-zinc-100 p-2 dark:bg-zinc-900">作者先验 {Number(router.author_prior_weight || 0).toFixed(2)}</div>
            <div className="rounded-lg bg-zinc-100 p-2 dark:bg-zinc-900">叙述者 {Number(router.narrator_weight || 0).toFixed(2)}</div>
            <div className="rounded-lg bg-zinc-100 p-2 dark:bg-zinc-900">角色声部 {Number(router.character_voice_weight || 0).toFixed(2)}</div>
            <div className="rounded-lg bg-zinc-100 p-2 dark:bg-zinc-900">场景语域 {Number(router.scene_register_weight || 0).toFixed(2)}</div>
          </div>
        </div>

        <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
          <div className="font-medium text-sm">风格状态快照</div>
          <div className="mt-2 space-y-1 text-zinc-500">
            <div>active POV: {String(styleState.active_pov_character_id || 'n/a')}</div>
            <div>scene register: {String(styleState.scene_register || 'neutral')}</div>
            <div>recent segments: {Array.isArray(styleState.recent_style_segment_ids) ? styleState.recent_style_segment_ids.length : 0}</div>
            <div>template hashes: {Array.isArray(styleState.recent_template_hashes) ? styleState.recent_template_hashes.length : 0}</div>
          </div>
          <div className="mt-3 rounded-lg bg-zinc-100 p-2 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
            drift {JSON.stringify(styleState.chapter_style_drift || {})}
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-3 xl:grid-cols-2">
        <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
          <div className="font-medium text-sm">角色对白覆盖</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {entriesOf(corpus?.characterVoiceCoverage).map(([key, value]) => (
              <span key={key} className="rounded-full bg-zinc-100 px-2 py-1 dark:bg-zinc-900">
                {key} {value}
              </span>
            ))}
            {entriesOf(corpus?.characterVoiceCoverage).length === 0 ? <span className="text-zinc-500">暂无</span> : null}
          </div>
        </div>

        <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
          <div className="font-medium text-sm">语域覆盖</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {entriesOf(corpus?.registerCoverage).map(([key, value]) => (
              <span key={key} className="rounded-full bg-zinc-100 px-2 py-1 dark:bg-zinc-900">
                {key} {value}
              </span>
            ))}
            {entriesOf(corpus?.registerCoverage).length === 0 ? <span className="text-zinc-500">暂无</span> : null}
          </div>
        </div>
      </div>
    </div>
  );
}
