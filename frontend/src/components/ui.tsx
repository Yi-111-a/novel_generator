import { ChevronDown } from 'lucide-react';
import { ReactNode, useState } from 'react';
import { cn } from '../lib/cn';
import type { ProjectStatus } from '../types';

export function Panel({ title, right, children, className }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn('panel p-4', className)}>
      {(title || right) && (
        <header className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-zinc-700 dark:text-zinc-200">{title}</h3>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Collapsible({ title, defaultOpen = true, children }: { title: ReactNode; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="panel overflow-hidden">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold">
        <span>{title}</span>
        <ChevronDown className={cn('h-4 w-4 transition-transform', open && 'rotate-180')} />
      </button>
      {open && <div className="border-t border-zinc-200 px-4 py-3 dark:border-zinc-800">{children}</div>}
    </div>
  );
}

const STATUS_META: Record<ProjectStatus, { label: string; cls: string }> = {
  seeding: { label: '种子中', cls: 'bg-amber-500/15 text-amber-500' },
  writing: { label: '写作中', cls: 'bg-emerald-500/15 text-emerald-500' },
  completed: { label: '已完成', cls: 'bg-indigo-500/15 text-indigo-400' },
};

export function StatusBadge({ status }: { status: ProjectStatus }) {
  const m = STATUS_META[status];
  return <span className={cn('chip', m.cls)}>{m.label}</span>;
}

export function BreatheDot() {
  return <span className="inline-block h-2 w-2 animate-breathe rounded-full bg-emerald-400" title="模拟进行中" />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-8 text-center text-sm text-zinc-400">{children}</div>;
}
