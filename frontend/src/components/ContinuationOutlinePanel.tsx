import { BookMarked, FileText, Settings2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getAdapter } from '../adapters';
import { ContinuationModePicker } from './ContinuationModePicker';
import { ContinuationProgress } from './ContinuationProgress';
import { ContinuationSourcePanel } from './ContinuationSourcePanel';
import { StyleDiagnosticsPanel } from './StyleDiagnosticsPanel';
import { VoiceProfilePanel } from './VoiceProfilePanel';
import type {
  AcceptedChapter,
  ChapterDraft,
  ContinuationDistillConfig,
  ContinuationJobStatus,
  ContinuationSettings,
  ContinuationStyleDiagnostics,
  DraftContextSnapshot,
  SourceChapter,
  StoryBibleStatus,
  StoryBibleV2,
  WritingSettings,
} from '../types';

export function ContinuationOutlinePanel({ projectId }: { projectId: string }) {
  const adapter = getAdapter();
  const navigate = useNavigate();
  const [settings, setSettings] = useState<WritingSettings | null>(null);
  const [bible, setBible] = useState<StoryBibleV2 | null>(null);
  const [status, setStatus] = useState<StoryBibleStatus | null>(null);
  const [continuation, setContinuation] = useState<ContinuationSettings | null>(null);
  const [job, setJob] = useState<ContinuationJobStatus | null>(null);
  const [diagnostics, setDiagnostics] = useState<ContinuationStyleDiagnostics | null>(null);
  const [distillConfig, setDistillConfig] = useState<ContinuationDistillConfig>({
    sampleMode: 'full',
    graphDetail: 'medium',
    styleSampleSegments: 6,
    generateAws: true,
    enableStyleSkill: true,
    extractUnresolvedThreads: true,
    extractCharacterEndings: true,
    extractFactionState: true,
    extractExpandableRegions: true,
  });
  const [sourceChapters, setSourceChapters] = useState<SourceChapter[]>([]);
  const [accepted, setAccepted] = useState<AcceptedChapter[]>([]);
  const [draft, setDraft] = useState<ChapterDraft | null>(null);
  const [sourceText, setSourceText] = useState('');
  const [guidance, setGuidance] = useState('');
  const [targetWords, setTargetWords] = useState(1800);
  const [outlineOnly, setOutlineOnly] = useState(false);
  const [busy, setBusy] = useState('');
  const audit = (draft?.contextSnapshot as DraftContextSnapshot | undefined)?.audit;

  const refresh = async () => {
    const [nextSettings, nextBible, nextStatus, nextContinuation, nextJob, nextAccepted, nextSourceChapters, nextDrafts, nextDiagnostics] = await Promise.all([
      adapter.getWritingSettings(projectId),
      adapter.getStoryBible(projectId).catch(() => null),
      adapter.getStoryBibleStatus(projectId).catch(() => null),
      adapter.getContinuationSettings(projectId).catch(() => null),
      adapter.getContinuationJob(projectId).catch(() => null),
      adapter.getAcceptedChapters(projectId),
      adapter.getSourceChapters(projectId).catch(() => []),
      adapter.getChapterDrafts(projectId).catch(() => []),
      adapter.getContinuationStyleDiagnostics(projectId).catch(() => null),
    ]);
    setSettings(nextSettings);
    setBible(nextBible);
    setStatus(nextStatus);
    setContinuation(nextContinuation);
    setJob(nextJob);
    setAccepted(nextAccepted);
    setSourceChapters(nextSourceChapters);
    setDraft(nextDrafts.find((row) => row.status === 'pending_acceptance' || row.status === 'draft') ?? null);
    setDiagnostics(nextDiagnostics);
    if (!targetWords) setTargetWords(nextSettings.targetWords);
  };

  useEffect(() => {
    refresh().catch(() => {});
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const id = window.setInterval(() => {
      refresh().catch(() => {});
    }, 4000);
    return () => window.clearInterval(id);
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveSettings = async () => {
    if (!settings) return;
    setBusy('settings');
    try {
      const next = await adapter.saveWritingSettings(projectId, settings);
      setSettings(next);
    } finally {
      setBusy('');
    }
  };

  const buildBible = async () => {
    setBusy('bible');
    try {
      await adapter.buildStoryBible(projectId);
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const importSource = async () => {
    if (!sourceText.trim()) return;
    setBusy('source');
    try {
      await adapter.importContinuationSources(projectId, { text: sourceText, filename: 'source.txt' });
      await adapter.startContinuationDistill(projectId, distillConfig);
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const saveContinuation = async () => {
    if (!continuation) return;
    setBusy('continuation');
    try {
      await adapter.saveContinuationSettings(projectId, continuation);
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const lockContinuation = async () => {
    setBusy('lock');
    try {
      await adapter.lockContinuation(projectId);
      await refresh();
      navigate(`/p/${projectId}/outline`);
    } finally {
      setBusy('');
    }
  };

  const generateDraft = async () => {
    setBusy('draft');
    try {
      const next = await adapter.createChapterDraft(projectId, {
        guidance,
        targetWords,
        outlineOnly,
        mode: 'manual',
      });
      setDraft(next);
    } finally {
      setBusy('');
    }
  };

  const acceptDraft = async () => {
    if (!draft) return;
    setBusy('accept');
    try {
      await adapter.acceptChapterDraft(projectId, draft.id);
      setDraft(null);
      setGuidance('');
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const rejectDraft = async () => {
    if (!draft) return;
    setBusy('reject');
    try {
      await adapter.rejectChapterDraft(projectId, draft.id);
      setDraft(null);
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="space-y-6">
      <section className="panel p-4">
        <div className="flex items-center gap-2 text-lg font-semibold">
          <BookMarked className="h-5 w-5 text-indigo-400" />
          续写链
        </div>
        <p className="mt-2 text-sm text-zinc-500">
          这里直接使用 source chapters、story bible、章节草稿和验收后的正文，不再依赖旧的页面耦合链路。
        </p>
      </section>

      <section className="grid gap-4 xl:grid-cols-[320px_1fr]">
        <div className="space-y-4">
          <div className="panel p-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Settings2 className="h-4 w-4 text-cyan-400" />
              写作设置
            </div>
            {settings && (
              <div className="mt-3 space-y-3 text-sm">
                <label className="block">
                  <div className="mb-1 text-zinc-500">目标字数</div>
                  <input
                    className="input"
                    type="number"
                    value={settings.targetWords}
                    onChange={(e) => setSettings({ ...settings, targetWords: Number(e.target.value) || 1800 })}
                  />
                </label>
                <label className="block">
                  <div className="mb-1 text-zinc-500">自动连写章数</div>
                  <input
                    className="input"
                    type="number"
                    value={settings.autoChapterCount}
                    onChange={(e) => setSettings({ ...settings, autoChapterCount: Number(e.target.value) || 1 })}
                  />
                </label>
                <label className="flex items-center gap-2 text-zinc-500">
                  <input
                    type="checkbox"
                    checked={settings.requireHumanAcceptance}
                    onChange={(e) => setSettings({ ...settings, requireHumanAcceptance: e.target.checked })}
                  />
                  每章都需要人工验收
                </label>
                <button className="btn-primary w-full" onClick={saveSettings} disabled={busy === 'settings'}>
                  保存设置
                </button>
              </div>
            )}
          </div>

          <ContinuationProgress
            bible={bible}
            busy={busy === 'bible'}
            distillConfig={distillConfig}
            job={job}
            status={status}
            onConfigChange={setDistillConfig}
            onBuildBible={buildBible}
          />

          <ContinuationSourcePanel
            busy={busy === 'source'}
            sourceText={sourceText}
            sourceChapters={sourceChapters}
            onSourceTextChange={setSourceText}
            onImport={importSource}
          />

          <ContinuationModePicker
            busy={busy}
            continuation={continuation}
            onChange={setContinuation}
            onSave={saveContinuation}
            onLock={lockContinuation}
          />

          <StyleDiagnosticsPanel diagnostics={diagnostics} draft={draft} />
          <VoiceProfilePanel diagnostics={diagnostics} draft={draft} />
        </div>

        <div className="space-y-4">
          <div className="panel p-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <FileText className="h-4 w-4 text-emerald-400" />
              章节草稿
            </div>
            <div className="mt-3 space-y-3">
              <textarea
                rows={4}
                className="input min-h-28 w-full"
                placeholder="这一章想推进什么，留什么悬念，要稳住哪些人物状态……"
                value={guidance}
                onChange={(e) => setGuidance(e.target.value)}
              />
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <label className="flex items-center gap-2">
                  <span className="text-zinc-500">目标字数</span>
                  <input
                    className="input w-28"
                    type="number"
                    value={targetWords}
                    onChange={(e) => setTargetWords(Number(e.target.value) || 1800)}
                  />
                </label>
                <label className="flex items-center gap-2 text-zinc-500">
                  <input
                    type="checkbox"
                    checked={outlineOnly}
                    onChange={(e) => setOutlineOnly(e.target.checked)}
                  />
                  只生成提纲
                </label>
              </div>
              <button className="btn-primary" onClick={generateDraft} disabled={busy === 'draft' || (!!continuation && !continuation.continuationReady)}>
                生成下一章草稿
              </button>
              {continuation && !continuation.continuationReady ? (
                <p className="text-xs text-amber-600 dark:text-amber-300">先在左侧锁定写作上下文，才能正式生成章节。</p>
              ) : null}
            </div>
          </div>

          {draft && (
            <div className="panel p-4">
              <div className="flex items-center justify-between gap-3 text-sm font-semibold">
                <div>第 {draft.chapterNo} 章 {draft.title || ''}</div>
                <span className="rounded-full bg-amber-500/15 px-2 py-1 text-[11px] font-medium text-amber-500">
                  {draft.status === 'pending_acceptance' ? '待验收' : '草稿'}
                </span>
              </div>
              {draft.outline && (
                <pre className="mt-3 whitespace-pre-wrap rounded-lg bg-zinc-100 p-3 text-xs text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                  {draft.outline}
                </pre>
              )}
              {draft.prose && (
                <div className="mt-3 whitespace-pre-wrap text-sm leading-7 text-zinc-700 dark:text-zinc-200">
                  {draft.prose}
                </div>
              )}
              {audit && (
                <div className="mt-4 rounded-lg border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-950/40">
                  <div className="flex items-center justify-between text-sm font-semibold">
                    <span>章级审计</span>
                    <span className={`rounded-full px-2 py-1 text-[11px] ${audit.ok ? 'bg-emerald-500/15 text-emerald-500' : 'bg-rose-500/15 text-rose-500'}`}>
                      {(draft.contextSnapshot as any).audit?.ok ? '通过' : '需留意'}
                    </span>
                  </div>
                  <div className="mt-3 space-y-2 text-xs">
                    {Object.entries(audit.checks).map(([key, value]) => (
                      <div key={key} className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                        <div className="font-medium text-zinc-700 dark:text-zinc-200">{key}</div>
                        <div className={value.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}>
                          {value.ok ? '通过' : '未通过'}
                        </div>
                        {value.feedback ? <div className="mt-1 whitespace-pre-wrap text-zinc-500">{value.feedback}</div> : null}
                      </div>
                    ))}
                    {audit.rewriteAdvice ? (
                      <div className="rounded-lg border border-amber-300/50 bg-amber-50 p-3 text-amber-700 dark:bg-amber-950/20 dark:text-amber-300">
                        {audit.rewriteAdvice}
                      </div>
                    ) : null}
                  </div>
                </div>
              )}
              <div className="mt-4 flex gap-2">
                <button className="btn-primary" onClick={acceptDraft} disabled={busy === 'accept'}>
                  接受草稿
                </button>
                <button className="btn-ghost border border-zinc-200 dark:border-zinc-800" onClick={rejectDraft} disabled={busy === 'reject'}>
                  丢弃
                </button>
              </div>
            </div>
          )}

          <div className="panel p-4">
            <div className="text-sm font-semibold">已验收章节</div>
            <div className="mt-3 space-y-3">
              {accepted.length === 0 ? (
                <p className="text-sm text-zinc-500">还没有验收后的章节。</p>
              ) : (
                accepted.map((chapter) => (
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
        </div>
      </section>
    </div>
  );
}
