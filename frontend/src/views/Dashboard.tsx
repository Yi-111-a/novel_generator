import { MoreVertical, Pause, Pencil, Play, Plus, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getAdapter } from '../adapters';
import { BreatheDot, Empty, StatusBadge } from '../components/ui';
import { fmtTime } from '../lib/cn';
import { useAppStore } from '../store/useAppStore';
import type { Project } from '../types';

export function Dashboard() {
  const navigate = useNavigate();
  const { projects, refreshProjects, createProject, renameProject, deleteProject } = useAppStore();
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState('');

  useEffect(() => {
    refreshProjects();
  }, [refreshProjects]);

  const onCreate = async () => {
    const p = await createProject(title.trim() || '未命名小说');
    setTitle('');
    setCreating(false);
    navigate(`/p/${p.id}/seed`);
  };

  const toggleSim = async (p: Project) => {
    await getAdapter().control(p.id, p.runningSim ? 'pause' : 'play');
    await refreshProjects();
  };

  return (
    <div className="mx-auto max-w-6xl p-6">
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">我的小说</h1>
          <p className="mt-1 text-sm text-zinc-500">每部小说是一个独立项目——种子、世界、账本、成稿互不干扰，可同时开写。</p>
        </div>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" /> 新建小说
        </button>
      </div>

      {creating && (
        <div className="panel mb-4 flex items-center gap-2 p-3">
          <input autoFocus className="input" placeholder="给这部小说起个名字…" value={title} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && onCreate()} />
          <button className="btn-primary" onClick={onCreate}>
            创建并进入种子工坊
          </button>
          <button className="btn-ghost" onClick={() => setCreating(false)}>
            取消
          </button>
        </div>
      )}

      {projects.length === 0 ? (
        <Empty>还没有小说。点击「新建小说」开始播下第一颗种子。</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((p) => (
            <ProjectCard key={p.id} project={p} onOpen={() => navigate(`/p/${p.id}/${p.status === 'seeding' ? 'seed' : 'sim'}`)} onRename={renameProject} onDelete={deleteProject} onToggleSim={toggleSim} />
          ))}
        </div>
      )}
    </div>
  );
}

function ProjectCard({ project, onOpen, onRename, onDelete, onToggleSim }: { project: Project; onOpen: () => void; onRename: (id: string, t: string) => void; onDelete: (id: string) => void; onToggleSim: (p: Project) => void }) {
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(project.title);
  const [confirming, setConfirming] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setMenu(false);
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  return (
    <div className="panel group relative flex flex-col p-4 transition-shadow hover:shadow-lg">
      <div className="mb-2 flex items-start justify-between">
        <StatusBadge status={project.status} />
        <div className="relative" ref={ref}>
          <button className="btn-ghost opacity-0 group-hover:opacity-100" onClick={() => setMenu((m) => !m)}>
            <MoreVertical className="h-4 w-4" />
          </button>
          {menu && (
            <div className="absolute right-0 z-10 mt-1 w-36 panel p-1 shadow-xl">
              <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800" onClick={() => { setEditing(true); setMenu(false); }}>
                <Pencil className="h-3.5 w-3.5" /> 重命名
              </button>
              <button className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm text-rose-500 hover:bg-rose-500/10" onClick={() => { setConfirming(true); setMenu(false); }}>
                <Trash2 className="h-3.5 w-3.5" /> 删除
              </button>
            </div>
          )}
        </div>
      </div>

      {editing ? (
        <input
          autoFocus
          className="input mb-2"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => { onRename(project.id, title.trim() || project.title); setEditing(false); }}
          onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        />
      ) : (
        <button onClick={onOpen} className="mb-1 text-left text-lg font-semibold leading-snug hover:text-indigo-400">
          {project.title}
        </button>
      )}

      <div className="mt-auto flex items-center justify-between pt-3 text-xs text-zinc-500">
        <span className="flex items-center gap-1.5">
          {project.runningSim && <BreatheDot />}
          {project.status === 'seeding'
            ? '播种中'
            : `已写 ${project.sceneCount ?? 0} 场 · ${project.chapterCount ?? 0} 章`}
        </span>
        <span>{fmtTime(project.updatedAt)}</span>
      </div>

      {project.status === 'writing' && (
        <button
          className="btn-ghost mt-2 justify-center border border-zinc-200 dark:border-zinc-800"
          onClick={() => onToggleSim(project)}
        >
          {project.runningSim ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          {project.runningSim ? '暂停' : '继续写作'}
        </button>
      )}

      {confirming && (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-3 rounded-xl bg-zinc-950/90 p-4 text-center">
          <p className="text-sm">确认删除「{project.title}」？此操作不可撤销。</p>
          <div className="flex gap-2">
            <button className="btn bg-rose-600 text-white hover:bg-rose-500" onClick={() => onDelete(project.id)}>
              删除
            </button>
            <button className="btn-ghost border border-zinc-700" onClick={() => setConfirming(false)}>
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
