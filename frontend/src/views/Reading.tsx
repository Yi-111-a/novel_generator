import { Eye, List } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { getAdapter } from '../adapters';
import { useProjectCtx, SeedingGate } from '../components/Layouts';
import { Empty } from '../components/ui';
import { useAppStore } from '../store/useAppStore';
import type { Chapter, Scene } from '../types';

export function Reading() {
  const { project } = useProjectCtx();
  const devMode = useAppStore((s) => s.devMode);
  const adapter = getAdapter();
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const refs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    if (project.status === 'seeding') return;
    let alive = true;
    const pull = () => {
      // 每次取场后端最多增量渲染少量新场 → 内容逐步流入，不会一次性久等
      adapter.getScenes(project.id).then((s) => alive && setScenes(s)).catch(() => {});
      adapter.getChapters(project.id).then((c) => alive && setChapters(c)).catch(() => {});
    };
    pull();
    const id = window.setInterval(pull, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [project.id, project.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const sceneById = useMemo(() => Object.fromEntries(scenes.map((s) => [s.sceneId, s])), [scenes]);
  const [finalizing, setFinalizing] = useState(false);
  const doFinalize = async () => {
    setFinalizing(true);
    try {
      await adapter.finalizeProject(project.id);
      const c = await adapter.getChapters(project.id);
      setChapters(c);
    } catch {
      /* ignore */
    } finally {
      setFinalizing(false);
    }
  };

  if (project.status === 'seeding') return <SeedingGate />;
  if (!scenes.length) return <Empty>还没有成稿场景。去「模拟」里推进剧情，剪辑层会把高潮渲染成小说（在「阅读」里逐场成稿、到高潮处分章）。</Empty>;

  const goto = (sid: string) => refs.current[sid]?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  return (
    <div className="flex gap-6">
      {/* 章节导航 */}
      <nav className="sticky top-16 hidden h-fit w-48 shrink-0 lg:block">
        <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-zinc-500">
          <List className="h-3.5 w-3.5" /> 目录 <span className="text-zinc-400">· {chapters.length} 章</span>
        </div>
        <ol className="max-h-[calc(100vh-7rem)] space-y-1 overflow-y-auto pr-1">
          {chapters.map((ch) => (
            <li key={ch.index}>
              <button onClick={() => goto(ch.sceneIds[0])} className="flex w-full items-baseline gap-1.5 rounded px-2 py-1 text-left text-sm text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800">
                <span className="shrink-0 text-xs tabular-nums text-zinc-400">第{ch.index}章</span>
                <span className="truncate">{ch.title}</span>
                {ch.status === 'ongoing' && <span className="ml-auto shrink-0 text-xs text-amber-500">未完</span>}
              </button>
            </li>
          ))}
        </ol>
      </nav>

      {/* 正文：按章分组、沉浸衬线排版 */}
      <article className="mx-auto max-w-2xl flex-1">
        <div className="mb-6 flex items-center justify-end gap-3">
          {project.status === 'completed' ? (
            <span className="chip bg-emerald-500/15 text-emerald-400">已定稿 · in medias res 重排</span>
          ) : (
            <button
              className="btn-ghost border border-zinc-200 text-xs dark:border-zinc-800"
              onClick={doFinalize}
              disabled={finalizing}
              title="完结后做一次电影感重排：把最高潮场作为序章钩子前置"
            >
              {finalizing ? '定稿中…' : '定稿重排（杀青）'}
            </button>
          )}
        </div>
        {chapters.map((ch) => (
          <div key={ch.index}>
            <header className="mb-10 mt-4 text-center">
              <div className="font-serif text-sm tracking-[0.3em] text-zinc-500">第{ch.index}章</div>
              {ch.title && <div className="mt-1.5 font-serif text-xs tracking-[0.4em] text-zinc-400">{ch.title}</div>}
              {ch.cast && ch.cast.length > 0 && (
                <div className="mt-1.5 text-[11px] text-zinc-400">本章人物 · {ch.cast.join(' / ')}</div>
              )}
              {ch.status === 'ongoing' && <div className="mt-1 text-[11px] text-amber-500">（未完待续）</div>}
              <div className="mx-auto mt-4 h-px w-16 bg-zinc-300 dark:bg-zinc-700" />
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
        ))}
      </article>
    </div>
  );
}
