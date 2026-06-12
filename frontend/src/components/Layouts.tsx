import { AlertTriangle, BookMarked, BookOpen, FlaskConical, Library, Lock, Network, ScrollText, Settings2, Sparkles } from 'lucide-react';
import { ReactNode, useEffect, useState } from 'react';
import { NavLink, Outlet, useNavigate, useOutletContext, useParams, useRouteError } from 'react-router-dom';
import { cn } from '../lib/cn';
import { useAppStore } from '../store/useAppStore';
import type { Project } from '../types';
import { TopBar } from './TopBar';
import { BreatheDot, StatusBadge } from './ui';

export function RootLayout() {
  const applyTheme = useAppStore((s) => s.applyTheme);
  const loadConfig = useAppStore((s) => s.loadConfig);
  useEffect(() => {
    applyTheme();
    loadConfig();
  }, [applyTheme, loadConfig]);
  return (
    <div className="min-h-screen">
      <TopBar />
      <Outlet />
    </div>
  );
}

interface ProjectCtx {
  project: Project;
}
export function useProjectCtx() {
  return useOutletContext<ProjectCtx>();
}

const TABS = [
  { to: 'seed', label: '种子工坊', icon: Sparkles, lockInSeeding: false, dev: false },
  { to: 'world', label: '世界配置', icon: Settings2, lockInSeeding: false, dev: false },
  { to: 'outline', label: '大纲', icon: BookMarked, lockInSeeding: true, dev: false },
  { to: 'sim', label: '模拟·上帝视角', icon: FlaskConical, lockInSeeding: true, dev: false },
  { to: 'read', label: '阅读', icon: BookOpen, lockInSeeding: true, dev: false },
  { to: 'inspect', label: '检视台', icon: Network, lockInSeeding: true, dev: false },
  { to: 'ledger', label: '账本检查器', icon: ScrollText, lockInSeeding: true, dev: true },
] as const;

export function ProjectLayout() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { projects, refreshProjects, devMode } = useAppStore();
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    setLoaded(false);
    Promise.resolve(refreshProjects()).finally(() => setLoaded(true));
  }, [refreshProjects, projectId]);

  const project = projects.find((p) => p.id === projectId);
  if (!project) {
    // 区分"加载中"与"确实不存在"，后者给明确出口（避免看起来像空白页）
    return (
      <div className="mx-auto max-w-md panel mt-16 p-8 text-center">
        <Library className="mx-auto mb-3 h-8 w-8 text-zinc-400" />
        {!loaded ? (
          <p className="text-sm text-zinc-400">载入项目中…</p>
        ) : (
          <>
            <p className="text-sm text-zinc-500">这部小说不存在（可能已被删除，或来自旧链接）。</p>
            <button className="btn-primary mt-4" onClick={() => navigate('/')}>
              返回我的小说
            </button>
          </>
        )}
      </div>
    );
  }
  const seeding = project.status === 'seeding';

  return (
    <div className="mx-auto flex max-w-[1400px] gap-4 p-4">
      <nav className="sticky top-16 h-fit w-52 shrink-0 space-y-1">
        <div className="px-2 pb-2">
          <div className="truncate text-sm font-semibold">{project.title}</div>
          <div className="mt-1 flex items-center gap-2">
            <StatusBadge status={project.status} />
            {project.runningSim && <BreatheDot />}
          </div>
        </div>
        {TABS.filter((t) => !t.dev || devMode).map((t) => {
          const locked = seeding && t.lockInSeeding;
          return (
            <NavLink
              key={t.to}
              to={locked ? '#' : t.to}
              onClick={(e) => locked && e.preventDefault()}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 rounded-lg px-3 py-2 text-sm',
                  locked ? 'cursor-not-allowed text-zinc-400 dark:text-zinc-600' : 'hover:bg-zinc-100 dark:hover:bg-zinc-800',
                  isActive && !locked && 'bg-indigo-500/10 text-indigo-400',
                )
              }
            >
              <t.icon className="h-4 w-4" />
              <span className="flex-1">{t.label}</span>
              {locked && <Lock className="h-3.5 w-3.5" />}
              {t.dev && <span className="chip bg-zinc-500/15 text-zinc-400">dev</span>}
            </NavLink>
          );
        })}
      </nav>

      <main className="min-w-0 flex-1">
        <Outlet context={{ project } satisfies ProjectCtx} />
      </main>
    </div>
  );
}

// 路由级错误边界：任何视图渲染抛错时显示友好页面，而非整页白屏。
export function ErrorPage() {
  const err = useRouteError() as Error | undefined;
  const navigate = useNavigate();
  return (
    <div className="mx-auto mt-16 max-w-lg panel p-8 text-center">
      <AlertTriangle className="mx-auto mb-3 h-8 w-8 text-amber-500" />
      <h2 className="text-lg font-semibold">这个页面出了点问题</h2>
      <p className="mt-2 break-all text-sm text-zinc-500">{err?.message || '未知错误'}</p>
      <div className="mt-5 flex justify-center gap-2">
        <button className="btn-primary" onClick={() => window.location.reload()}>
          重新加载
        </button>
        <button className="btn-ghost border border-zinc-200 dark:border-zinc-800" onClick={() => navigate('/')}>
          回到我的小说
        </button>
      </div>
    </div>
  );
}

// 通用门禁提示：seeding 状态下访问需写作的视图时显示。
export function SeedingGate({ children }: { children?: ReactNode }) {
  return (
    <div className="panel mx-auto mt-10 max-w-lg p-8 text-center">
      <Lock className="mx-auto mb-3 h-7 w-7 text-amber-500" />
      <p className="text-sm text-zinc-500">先在「种子工坊」完成种子，达标后即可解锁模拟与阅读。</p>
      {children}
    </div>
  );
}
