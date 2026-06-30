import { AlertTriangle, Check, Copy, Eye, List } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { getAdapter } from '../adapters';
import { useProjectCtx, SeedingGate } from '../components/Layouts';
import { Empty } from '../components/ui';
import { useAppStore } from '../store/useAppStore';
import type { AcceptedChapter, Chapter, ChapterDraft, Scene } from '../types';

export function Reading() {
  const { project } = useProjectCtx();

  const devMode = useAppStore((s) => s.devMode);
  const adapter = getAdapter();
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [acceptedChapters, setAcceptedChapters] = useState<AcceptedChapter[]>([]);
  const [pendingDraft, setPendingDraft] = useState<ChapterDraft | null>(null);
  const [finalizing, setFinalizing] = useState(false);
  const [busy, setBusy] = useState<'accept' | 'reject' | 'rewrite' | ''>('');
  const refs = useRef<Record<string, HTMLElement | null>>({});

  const refresh = async () => {
    const [sceneRows, chapterRows, acceptedRows, draftRows] = await Promise.all([
      adapter.getScenes(project.id).catch(() => []),
      adapter.getChapters(project.id).catch(() => []),
      adapter.getAcceptedChapters(project.id).catch(() => []),
      adapter.getChapterDrafts(project.id).catch(() => []),
    ]);
    setScenes(sceneRows);
    setChapters(chapterRows);
    setAcceptedChapters(acceptedRows);
    setPendingDraft(draftRows.find((row) => row.status === 'pending_acceptance' || row.status === 'blocked') ?? null);
  };

  useEffect(() => {
    if (project.status === 'seeding') return;
    let alive = true;
    const pull = () => {
      Promise.all([
        adapter.getScenes(project.id).catch(() => []),
        adapter.getChapters(project.id).catch(() => []),
        adapter.getAcceptedChapters(project.id).catch(() => []),
        adapter.getChapterDrafts(project.id).catch(() => []),
      ]).then(([sceneRows, chapterRows, acceptedRows, draftRows]) => {
        if (!alive) return;
        setScenes(sceneRows);
        setChapters(chapterRows);
        setAcceptedChapters(acceptedRows);
        setPendingDraft(draftRows.find((row) => row.status === 'pending_acceptance' || row.status === 'blocked') ?? null);
      }).catch(() => {});
    };
    pull();
    const id = window.setInterval(pull, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [project.id, project.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const sceneById = useMemo(() => Object.fromEntries(scenes.map((s) => [s.sceneId, s])), [scenes]);

  const doFinalize = async () => {
    setFinalizing(true);
    try {
      await adapter.finalizeProject(project.id);
      await refresh();
    } catch {
      /* ignore */
    } finally {
      setFinalizing(false);
    }
  };

  const acceptDraft = async () => {
    if (!pendingDraft) return;
    setBusy('accept');
    try {
      await adapter.acceptChapterDraft(project.id, pendingDraft.id);
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const rejectDraft = async () => {
    if (!pendingDraft) return;
    setBusy('reject');
    try {
      await adapter.rejectChapterDraft(project.id, pendingDraft.id);
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const rewriteDraft = async () => {
    if (!pendingDraft || pendingDraft.status !== 'blocked') return;
    setBusy('rewrite');
    try {
      const snapshot = pendingDraft.contextSnapshot as {
        combinedAudit?: { rewriteTargets?: string[] };
        audit?: { rewriteAdvice?: string };
        scopeAudit?: { rewriteAdvice?: string };
      };
      const guidance = [
        snapshot.audit?.rewriteAdvice,
        snapshot.scopeAudit?.rewriteAdvice,
        ...(snapshot.combinedAudit?.rewriteTargets ?? []),
      ].filter((item): item is string => Boolean(item?.trim())).join('\n');
      await adapter.rejectChapterDraft(project.id, pendingDraft.id);
      await adapter.createChapterDraft(project.id, {
        guidance: guidance || '人工确认重写：严格按当前章纲修复全部审计问题，不得提前展开后续章节。',
        targetWords: pendingDraft.targetWords,
        mode: 'manual',
      });
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const buildChapterText = (chapterNo: number, title: string | undefined, body: string) => {
    const header = title ? `第${chapterNo}章 ${title}` : `第${chapterNo}章`;
    return `${header}\n\n${body}`;
  };

  if (project.status === 'seeding') return <SeedingGate />;

  if (acceptedChapters.length > 0) {
    return (
      <div className="mx-auto flex max-w-6xl gap-6">
        <nav className="sticky top-16 hidden h-fit w-52 shrink-0 lg:block">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-zinc-500">
            <List className="h-3.5 w-3.5" /> 目录 <span className="text-zinc-400">· {acceptedChapters.length} 章</span>
          </div>
          <ol className="space-y-1">
            {acceptedChapters.map((chapter) => (
              <li key={chapter.id}>
                <a
                  href={`#accepted-${chapter.id}`}
                  className="block rounded px-2 py-1 text-sm text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
                >
                  第{chapter.chapterNo}章 {chapter.title || ''}
                </a>
              </li>
            ))}
            {pendingDraft && (
              <li>
                <a
                  href="#pending-draft"
                  className="block rounded px-2 py-1 text-sm text-amber-700 hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-950/40"
                >
                  {pendingDraft.status === 'blocked' ? '审计拦截' : '待验收'}：第{pendingDraft.chapterNo}章
                </a>
              </li>
            )}
          </ol>
        </nav>

        <article className="mx-auto max-w-2xl flex-1">
          {pendingDraft && (
            <PendingDraftCard draft={pendingDraft} busy={busy} onAccept={acceptDraft} onReject={rejectDraft} onRewrite={rewriteDraft} />
          )}
          {acceptedChapters.map((chapter) => (
            <section key={chapter.id} id={`accepted-${chapter.id}`} className="mb-14">
              <header className="mb-8 text-center">
                <div className="font-serif text-sm tracking-[0.3em] text-zinc-500">第{chapter.chapterNo}章</div>
                {chapter.title && <div className="mt-2 font-serif text-lg text-zinc-800 dark:text-zinc-100">{chapter.title}</div>}
                <div className="mx-auto mt-4 h-px w-16 bg-zinc-300 dark:bg-zinc-700" />
                <div className="mt-3 flex justify-center">
                  <CopyButton text={buildChapterText(chapter.chapterNo, chapter.title, chapter.prose)} />
                </div>
              </header>
              <div className="whitespace-pre-wrap font-serif text-[17px] leading-[2] text-zinc-800 dark:text-zinc-200">
                {chapter.prose}
              </div>
            </section>
          ))}
        </article>
      </div>
    );
  }

  if (!scenes.length && !pendingDraft) {
    return <Empty>还没有可读正文，也没有待验收草稿。新的章节一生成，就会直接在这个阅读页出现供你确认。</Empty>;
  }

  const goto = (sid: string) => refs.current[sid]?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  return (
    <div className="flex gap-6">
      <nav className="sticky top-16 hidden h-fit w-48 shrink-0 lg:block">
        <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-zinc-500">
          <List className="h-3.5 w-3.5" /> 目录 <span className="text-zinc-400">· {chapters.length} 章</span>
        </div>
        <ol className="max-h-[calc(100vh-7rem)] space-y-1 overflow-y-auto pr-1">
          {pendingDraft && (
            <li>
              <a
                href="#pending-draft"
                className="block rounded px-2 py-1 text-sm text-amber-700 hover:bg-amber-100 dark:text-amber-300 dark:hover:bg-amber-950/40"
              >
                {pendingDraft.status === 'blocked' ? '审计拦截' : '待验收'}：第{pendingDraft.chapterNo}章
              </a>
            </li>
          )}
          {chapters.map((ch) => (
            <li key={ch.index}>
              <button
                onClick={() => goto(ch.sceneIds[0])}
                className="flex w-full items-baseline gap-1.5 rounded px-2 py-1 text-left text-sm text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                <span className="shrink-0 text-xs tabular-nums text-zinc-400">第{ch.index}章</span>
                <span className="truncate">{ch.title}</span>
                {ch.status === 'ongoing' && <span className="ml-auto shrink-0 text-xs text-amber-500">未完</span>}
              </button>
            </li>
          ))}
        </ol>
      </nav>

      <article className="mx-auto max-w-2xl flex-1">
        {pendingDraft && (
          <PendingDraftCard draft={pendingDraft} busy={busy} onAccept={acceptDraft} onReject={rejectDraft} onRewrite={rewriteDraft} />
        )}

        {scenes.length > 0 && (
          <div className="mb-6 flex items-center justify-end gap-3">
            {project.status === 'completed' ? (
              <span className="chip bg-emerald-500/15 text-emerald-400">已定稿 · in medias res 重排</span>
            ) : (
              <button
                className="btn-ghost border border-zinc-200 text-xs dark:border-zinc-800"
                onClick={doFinalize}
                disabled={finalizing}
                title="完结后做一次电影感重排，把最高张力场作为序章钩子前置。"
              >
                {finalizing ? '定稿中…' : '定稿重排（杀青）'}
              </button>
            )}
          </div>
        )}

        {chapters.map((ch) => {
          const chapterBody = ch.sceneIds
            .map((sid) => sceneById[sid]?.proseText ?? '')
            .filter((text) => text.trim().length > 0)
            .join('\n\n');
          return (
          <div key={ch.index}>
            <header className="mb-10 mt-4 text-center">
              <div className="font-serif text-sm tracking-[0.3em] text-zinc-500">第{ch.index}章</div>
              {ch.title && <div className="mt-1.5 font-serif text-xs tracking-[0.4em] text-zinc-400">{ch.title}</div>}
              {ch.cast && ch.cast.length > 0 && (
                <div className="mt-1.5 text-[11px] text-zinc-400">本章人物 · {ch.cast.join(' / ')}</div>
              )}
              {ch.status === 'ongoing' && <div className="mt-1 text-[11px] text-amber-500">（未完待续）</div>}
              <div className="mx-auto mt-4 h-px w-16 bg-zinc-300 dark:bg-zinc-700" />
              {chapterBody && (
                <div className="mt-3 flex justify-center">
                  <CopyButton text={buildChapterText(ch.index, ch.title, chapterBody)} />
                </div>
              )}
            </header>
            {ch.sceneIds.map((sid) => {
              const s = sceneById[sid];
              if (!s) return null;
              return (
                <section key={sid} ref={(el) => { refs.current[sid] = el; }} className="mb-14">
                  {devMode && (
                    <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-zinc-300 px-3 py-1.5 text-xs text-zinc-400 dark:border-zinc-700">
                      <span className="chip bg-indigo-500/15 text-indigo-400">POV {s.pov}</span>
                      <span className="font-mono">话语#{s.discourseOrder} · 张力 {s.targetTension}</span>
                      <span className="font-mono">由 {s.sourceEvents.join(', ')} 渲染</span>
                      {sid === ch.climaxSceneId && <span className="chip bg-rose-500/15 text-rose-400">本章高潮</span>}
                    </div>
                  )}
                  <div className="whitespace-pre-wrap font-serif text-[17px] leading-[2] text-zinc-800 dark:text-zinc-200">{s.proseText}</div>
                  {devMode && s.newlyRevealed.length > 0 && (
                    <div className="mt-3 flex items-center gap-1.5 text-xs text-emerald-500/80">
                      <Eye className="h-3.5 w-3.5" /> 本场向读者揭示了 {s.newlyRevealed.length} 条新真相
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        );
        })}
      </article>
    </div>
  );
}

function PendingDraftCard({
  draft,
  busy,
  onAccept,
  onReject,
  onRewrite,
}: {
  draft: ChapterDraft;
  busy: 'accept' | 'reject' | 'rewrite' | '';
  onAccept: () => void;
  onReject: () => void;
  onRewrite: () => void;
}) {
  const blocked = draft.status === 'blocked';
  return (
    <section
      id="pending-draft"
      className={`mb-12 rounded-2xl border p-5 ${
        blocked
          ? 'border-rose-300/60 bg-rose-50/80 dark:border-rose-900/70 dark:bg-rose-950/20'
          : 'border-amber-300/50 bg-amber-50/80 dark:border-amber-800/60 dark:bg-amber-950/20'
      }`}
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className={`mt-0.5 h-5 w-5 shrink-0 ${blocked ? 'text-rose-500' : 'text-amber-500'}`} />
        <div className="min-w-0 flex-1">
          <div className={`text-sm font-semibold ${blocked ? 'text-rose-800 dark:text-rose-200' : 'text-amber-800 dark:text-amber-200'}`}>
            {blocked ? '审计拦截章节' : '待验收章节'}：第 {draft.chapterNo} 章{draft.title ? ` · ${draft.title}` : ''}
          </div>
          <p className={`mt-1 text-xs leading-6 ${blocked ? 'text-rose-700/90 dark:text-rose-200/80' : 'text-amber-700/90 dark:text-amber-200/80'}`}>
            {blocked
              ? '正文已经生成，但未通过剧情范围或章节质量审计，因此不会进入正式目录。请根据下方问题丢弃重写。'
              : '系统已经写完这一章，并因为开启了“每章都需要人工验收”而自动暂停。你可以直接在阅读页决定是否接受。'}
          </p>
          <ChapterAuditPanel draft={draft} />
          {draft.outline && (
            <pre className="mt-4 whitespace-pre-wrap rounded-xl bg-white/80 p-3 text-xs leading-6 text-zinc-700 dark:bg-zinc-900/70 dark:text-zinc-200">
              {draft.outline}
            </pre>
          )}
          {draft.prose && (
            <>
              <div className="mt-4 flex">
                <CopyButton
                  text={`${draft.title ? `第${draft.chapterNo}章 ${draft.title}` : `第${draft.chapterNo}章`}\n\n${draft.prose}`}
                />
              </div>
              <div className="mt-3 whitespace-pre-wrap font-serif text-[17px] leading-[2] text-zinc-800 dark:text-zinc-100">
                {draft.prose}
              </div>
            </>
          )}
          <div className="mt-4 flex gap-2">
            <button className="btn-primary" onClick={onAccept} disabled={busy !== '' || blocked}>
              接受这一章
            </button>
            {blocked && (
              <button className="btn-primary" onClick={onRewrite} disabled={busy !== ''}>
                {busy === 'rewrite' ? '自动重写中…' : '确认并重写'}
              </button>
            )}
            <button className="btn-ghost border border-zinc-200 dark:border-zinc-700" onClick={onReject} disabled={busy !== ''}>
              {blocked ? '丢弃草稿' : '丢弃重写'}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

type AuditViolation = {
  type?: string;
  text?: string;
  belongs_to_chapter?: number;
};

function ChapterAuditPanel({ draft }: { draft: ChapterDraft }) {
  const snapshot = draft.contextSnapshot as {
    combinedAudit?: {
      decision?: string;
      violations?: AuditViolation[];
      rewriteTargets?: string[];
    };
    scopeAudit?: {
      severity?: string;
      violations?: AuditViolation[];
      rewriteAdvice?: string;
    };
    audit?: {
      severity?: string;
      rewriteAdvice?: string;
      checks?: Record<string, { ok?: boolean; feedback?: string }>;
    };
  };
  const combined = snapshot.combinedAudit ?? {};
  const scope = snapshot.scopeAudit ?? {};
  const audit = snapshot.audit ?? {};
  const rawViolations = [
    ...(combined.violations ?? []),
    ...(scope.violations ?? []),
  ];
  const seen = new Set<string>();
  const violations = rawViolations.filter((item) => {
    const key = `${item.type ?? ''}|${item.text ?? ''}|${item.belongs_to_chapter ?? ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const failedChecks = Object.entries(audit.checks ?? {}).filter(([, value]) => value?.ok === false);
  const advice = [
    audit.rewriteAdvice,
    scope.rewriteAdvice,
    ...(combined.rewriteTargets ?? []),
  ].filter((item): item is string => Boolean(item?.trim()));
  const isBlocked = draft.status === 'blocked'
    || combined.decision === 'rewrite'
    || scope.severity === 'blocker'
    || audit.severity === 'blocker';

  if (!isBlocked && violations.length === 0 && failedChecks.length === 0) return null;

  return (
    <details open className="mt-3 rounded-xl border border-rose-300 bg-white/80 p-3 text-xs leading-6 text-rose-900 dark:border-rose-900 dark:bg-zinc-950/50 dark:text-rose-100">
      <summary className="cursor-pointer list-none font-semibold">
        未通过审计 · {violations.length + failedChecks.length} 项问题
      </summary>
      {violations.length > 0 && (
        <div className="mt-2 space-y-1">
          {violations.slice(0, 16).map((item, index) => (
            <div key={`${item.type}-${item.text}-${index}`}>
              · {auditViolationLabel(item.type)}：{item.text || '未提供详情'}
              {item.belongs_to_chapter ? `（属于第 ${item.belongs_to_chapter} 章）` : ''}
            </div>
          ))}
        </div>
      )}
      {failedChecks.length > 0 && (
        <div className="mt-2 space-y-1">
          {failedChecks.map(([name, check]) => (
            <div key={name}>· {auditCheckLabel(name)}：{check.feedback || '未通过'}</div>
          ))}
        </div>
      )}
      {advice.length > 0 && (
        <div className="mt-3 rounded-lg bg-rose-100/80 px-3 py-2 text-rose-800 dark:bg-rose-950/50 dark:text-rose-200">
          <div className="font-medium">重写建议</div>
          {[...new Set(advice)].map((item, index) => <div key={index}>- {item}</div>)}
        </div>
      )}
    </details>
  );
}

function CopyButton({ text, label = '复制全章' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      /* ignore */
    }
  };
  return (
    <button
      type="button"
      onClick={onCopy}
      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 px-2.5 py-1 text-xs text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
      title="复制本章正文（保留分行）"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? '已复制' : label}
    </button>
  );
}

function auditViolationLabel(type?: string): string {
  return {
    chapter_audit: '章节质量',
    unauthorized_item: '越界道具',
    unauthorized_character: '越界人物',
    unauthorized_location: '越界地点',
    unauthorized_truth_reveal: '越权真相',
    future_event_leak: '提前泄露后续剧情',
    invented_exact_date: '擅自添加精确日期',
    new_investigation_result: '擅自增加调查结论',
    text_encoding_corruption: '文本编码异常',
  }[type ?? ''] ?? (type || '审计问题');
}

function auditCheckLabel(name: string): string {
  return {
    hook_continuity: '钩子连续性',
    scene_transition: '场景衔接',
    cast_item_compliance: '人物与道具权限',
    forward_progress: '剧情推进',
    anti_grandstanding: '避免强行升华',
    title_quality: '标题质量',
    name_quality: '命名质量',
    bug_signals: '正文硬伤',
  }[name] ?? name;
}
