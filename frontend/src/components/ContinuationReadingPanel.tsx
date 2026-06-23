import { AlertTriangle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { getAdapter } from '../adapters';
import type { AcceptedChapter, ChapterDraft } from '../types';
import { Empty } from './ui';

export function ContinuationReadingPanel({ projectId }: { projectId: string }) {
  const adapter = getAdapter();
  const [chapters, setChapters] = useState<AcceptedChapter[]>([]);
  const [pendingDraft, setPendingDraft] = useState<ChapterDraft | null>(null);
  const [busy, setBusy] = useState<'accept' | 'reject' | 'force' | ''>('');

  const refresh = async () => {
    const [acceptedRows, draftRows] = await Promise.all([
      adapter.getAcceptedChapters(projectId),
      adapter.getChapterDrafts(projectId).catch(() => []),
    ]);
    setChapters(acceptedRows);
    setPendingDraft(draftRows.find((row) => row.status === 'pending_acceptance' || row.status === 'blocked') ?? null);
  };

  useEffect(() => {
    let alive = true;
    const pull = () => {
      Promise.all([
        adapter.getAcceptedChapters(projectId),
        adapter.getChapterDrafts(projectId).catch(() => []),
      ]).then(([acceptedRows, draftRows]) => {
        if (!alive) return;
        setChapters(acceptedRows);
        setPendingDraft(draftRows.find((row) => row.status === 'pending_acceptance' || row.status === 'blocked') ?? null);
      }).catch(() => {});
    };
    pull();
    const id = window.setInterval(pull, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  const acceptDraft = async () => {
    if (!pendingDraft) return;
    setBusy('accept');
    try {
      await adapter.acceptChapterDraft(projectId, pendingDraft.id);
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const rejectDraft = async () => {
    if (!pendingDraft) return;
    setBusy('reject');
    try {
      await adapter.rejectChapterDraft(projectId, pendingDraft.id);
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const forceAcceptDraft = async () => {
    if (!pendingDraft) return;
    const reason = window.prompt('请说明为什么要绕过 blocker 审计。该原因会写入草稿记录：')?.trim();
    if (!reason) return;
    setBusy('force');
    try {
      await adapter.forceAcceptChapterDraft(projectId, pendingDraft.id, reason);
      await refresh();
    } finally {
      setBusy('');
    }
  };

  const rewriteDraft = async () => {
    if (!pendingDraft) return;
    setBusy('reject');
    try {
      const snapshot = pendingDraft.contextSnapshot as any;
      const audit = snapshot?.audit ?? {};
      const scope = snapshot?.scopeAudit ?? {};
      const combined = snapshot?.combinedAudit ?? {};
      const guidance = [
        audit.rewriteAdvice,
        scope.rewriteAdvice,
        ...(combined.rewriteTargets ?? []),
      ].filter(Boolean).join('\n');
      await adapter.rejectChapterDraft(projectId, pendingDraft.id);
      await adapter.createChapterDraft(projectId, {
        guidance: guidance || '严格按当前章纲重写，不得提前展开后续章节事件。',
        targetWords: pendingDraft.targetWords,
        mode: 'manual',
      });
      await refresh();
    } finally {
      setBusy('');
    }
  };

  if (!chapters.length && !pendingDraft) {
    return <Empty>还没有已验收章节，也没有待验收草稿。后续生成出的章节会直接出现在这里供你阅读和决定是否采用。</Empty>;
  }

  return (
    <div className="mx-auto flex max-w-6xl gap-6">
      <nav className="sticky top-16 hidden h-fit w-52 shrink-0 lg:block">
        <div className="mb-2 text-xs font-medium text-zinc-500">目录</div>
        <ol className="space-y-1">
          {chapters.map((chapter) => (
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
                待验收：第{pendingDraft.chapterNo}章
              </a>
            </li>
          )}
        </ol>
      </nav>

      <article className="mx-auto max-w-2xl flex-1">
        {pendingDraft && (
          <section id="pending-draft" className="mb-12 rounded-2xl border border-amber-300/50 bg-amber-50/80 p-5 dark:border-amber-800/60 dark:bg-amber-950/20">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-semibold text-amber-800 dark:text-amber-200">
                  待验收章节：第 {pendingDraft.chapterNo} 章{pendingDraft.title ? ` · ${pendingDraft.title}` : ''}
                </div>
                <p className="mt-1 text-xs leading-6 text-amber-700/90 dark:text-amber-200/80">
                  {pendingDraft.status === 'blocked'
                    ? '本章未通过剧情权限或硬伤审计，不能直接接受。请先按问题重写。'
                    : '系统已经写完这一章，并因为开启了“每章都需要人工验收”而自动暂停。你可以直接在阅读页决定是否接受。'}
                </p>
                <AuditReport draft={pendingDraft} />
                {pendingDraft.outline && (
                  <pre className="mt-4 whitespace-pre-wrap rounded-xl bg-white/80 p-3 text-xs leading-6 text-zinc-700 dark:bg-zinc-900/70 dark:text-zinc-200">
                    {pendingDraft.outline}
                  </pre>
                )}
                {pendingDraft.prose && (
                  <div className="mt-4 whitespace-pre-wrap font-serif text-[17px] leading-[2] text-zinc-800 dark:text-zinc-100">
                    {pendingDraft.prose}
                  </div>
                )}
                <div className="mt-4 flex gap-2">
                  <button className="btn-primary" onClick={acceptDraft} disabled={busy !== '' || pendingDraft.status === 'blocked'}>
                    接受这一章
                  </button>
                  {pendingDraft.status === 'blocked' && (
                    <>
                      <button className="btn-primary" onClick={rewriteDraft} disabled={busy !== ''}>
                        确认并重写
                      </button>
                      <button className="btn-ghost border border-red-300 text-red-700 dark:border-red-900 dark:text-red-300" onClick={forceAcceptDraft} disabled={busy !== ''}>
                        强制接受…
                      </button>
                    </>
                  )}
                  <button className="btn-ghost border border-zinc-200 dark:border-zinc-700" onClick={rejectDraft} disabled={busy !== ''}>
                    丢弃重写
                  </button>
                </div>
              </div>
            </div>
          </section>
        )}

        {chapters.map((chapter) => (
          <section key={chapter.id} id={`accepted-${chapter.id}`} className="mb-14">
            <header className="mb-8 text-center">
              <div className="font-serif text-sm tracking-[0.3em] text-zinc-500">第{chapter.chapterNo}章</div>
              {chapter.title && <div className="mt-2 font-serif text-lg text-zinc-800 dark:text-zinc-100">{chapter.title}</div>}
              <div className="mx-auto mt-4 h-px w-16 bg-zinc-300 dark:bg-zinc-700" />
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

function AuditReport({ draft }: { draft: ChapterDraft }) {
  const snapshot = draft.contextSnapshot as any;
  const audit = snapshot?.audit ?? {};
  const scope = snapshot?.scopeAudit ?? {};
  const violations = Array.isArray(scope.violations) ? scope.violations : [];
  if (audit.severity !== 'blocker' && scope.severity !== 'blocker') return null;
  return (
    <div className="mt-3 rounded border border-red-300 bg-red-50 p-3 text-xs leading-6 text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
      <div className="font-semibold">本章未通过审计</div>
      {audit.rewriteAdvice && <div>{audit.rewriteAdvice}</div>}
      {scope.rewriteAdvice && <div>{scope.rewriteAdvice}</div>}
      {violations.slice(0, 10).map((item: any, idx: number) => (
        <div key={idx}>· {item.type}: {item.text}{item.belongs_to_chapter ? `（属于第${item.belongs_to_chapter}章）` : ''}</div>
      ))}
    </div>
  );
}
