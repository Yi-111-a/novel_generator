import { BookText, Check, ChevronsUpDown } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAppStore } from '../store/useAppStore';
import { BreatheDot, StatusBadge } from './ui';

// 随时切到另一部小说，不中断其它项目正在跑的模拟（模拟由适配器层独立维持）。
export function ProjectSwitcher() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { projects, refreshProjects } = useAppStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    refreshProjects();
  }, [refreshProjects]);
  useEffect(() => {
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const current = projects.find((p) => p.id === projectId);

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((o) => !o)} className="btn-ghost min-w-[180px] justify-between border border-zinc-200 dark:border-zinc-800">
        <span className="flex items-center gap-2 truncate">
          <BookText className="h-4 w-4 shrink-0" />
          <span className="truncate">{current ? current.title : '我的小说'}</span>
        </span>
        <ChevronsUpDown className="h-3.5 w-3.5 opacity-60" />
      </button>
      {open && (
        <div className="absolute z-30 mt-1 w-72 panel p-1 shadow-xl">
          <button
            onClick={() => {
              setOpen(false);
              navigate('/');
            }}
            className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <BookText className="h-4 w-4" /> 全部小说（仪表盘）
          </button>
          <div className="my-1 h-px bg-zinc-200 dark:bg-zinc-800" />
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => {
                setOpen(false);
                navigate(`/p/${p.id}/${p.status === 'seeding' ? 'seed' : 'sim'}`);
              }}
              className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              <span className="flex items-center gap-2 truncate">
                {p.id === projectId ? <Check className="h-4 w-4 text-indigo-400" /> : <span className="w-4" />}
                <span className="truncate">{p.title}</span>
                {p.runningSim && <BreatheDot />}
              </span>
              <StatusBadge status={p.status} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
