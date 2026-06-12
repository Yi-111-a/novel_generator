import { CheckCircle2, Circle, Lock, Send, Sparkles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getAdapter } from '../adapters';
import { Collapsible, Empty } from '../components/ui';
import { StyleSkillPanel } from '../components/StyleSkillPanel';
import { useProjectCtx } from '../components/Layouts';
import { cn } from '../lib/cn';
import { useAppStore } from '../store/useAppStore';
import type { SeedChatMessage, SeedDraft } from '../types';

export function SeedWorkshop() {
  const { project } = useProjectCtx();
  const navigate = useNavigate();
  const refreshProjects = useAppStore((s) => s.refreshProjects);
  const adapter = getAdapter();

  const [messages, setMessages] = useState<SeedChatMessage[]>([]);
  const [draft, setDraft] = useState<SeedDraft | null>(null);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [locking, setLocking] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    adapter.getSeedChat(project.id).then(setMessages);
    adapter.getSeedDraft(project.id).then(setDraft);
  }, [project.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  const locked = project.status !== 'seeding';

  const send = async () => {
    const content = input.trim();
    if (!content || streaming || locked) return;
    setInput('');
    setMessages((m) => [...m, { role: 'user', content, at: new Date().toISOString() }, { role: 'assistant', content: '', at: new Date().toISOString() }]);
    setStreaming(true);
    try {
      const newDraft = await adapter.sendSeedMessage(project.id, content, (tok) => {
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1] = { ...copy[copy.length - 1], content: copy[copy.length - 1].content + tok };
          return copy;
        });
      });
      setDraft(newDraft);
    } catch (err) {
      // 不做离线兜底：把后端报错直接显示在对话里
      const msg = err instanceof Error ? err.message : String(err);
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { ...copy[copy.length - 1], content: `⚠ 对话失败：${msg}` };
        return copy;
      });
    } finally {
      setStreaming(false);
    }
  };

  const lockAndStart = async () => {
    setLocking(true);
    try {
      await adapter.lockSeedAndStart(project.id);
      await refreshProjects();
      navigate(`/p/${project.id}/sim`);
    } finally {
      setLocking(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.1fr_1fr]">
      {/* 左：对话区（温暖、留白） */}
      <div className="panel flex h-[calc(100vh-8rem)] flex-col">
        <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <Sparkles className="h-4 w-4 text-amber-500" />
          <span className="text-sm font-semibold">种子工坊 · 与 AI 共创</span>
        </div>
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-5">
          {messages.map((m, i) => (
            <div key={i} className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}>
              <div
                className={cn(
                  'max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed',
                  m.role === 'user' ? 'bg-indigo-600 text-white' : 'bg-zinc-100 font-serif dark:bg-zinc-800',
                )}
              >
                {m.content || (streaming && i === messages.length - 1 ? '…' : '')}
              </div>
            </div>
          ))}
        </div>
        <div className="border-t border-zinc-200 p-3 dark:border-zinc-800">
          {locked ? (
            <div className="flex items-center justify-center gap-2 py-2 text-sm text-zinc-400">
              <Lock className="h-4 w-4" /> 种子已锁定，不可再改世界观（可在世界配置查看意图层）。
            </div>
          ) : (
            <div className="flex items-end gap-2">
              <textarea
                className="input max-h-32 min-h-[44px] resize-none"
                placeholder="聊聊你想要的世界、主题、主角、冲突，或心里的结局…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
              />
              <button className="btn-primary h-[44px]" onClick={send} disabled={streaming || !input.trim()}>
                <Send className="h-4 w-4" />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 右：种子草稿 + 完成度（内容区独立滚动，按钮固定底部） */}
      <div className="flex h-[calc(100vh-8rem)] flex-col gap-3">
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
          <Checklist draft={draft} />
          <SeedDraftView draft={draft} />
          <StyleSkillPanel projectId={project.id} />
        </div>
        <button className="btn-primary w-full shrink-0 justify-center py-2.5 shadow-lg" disabled={!draft?.completeness?.ready || locked || locking} onClick={lockAndStart}>
          {locked ? '已开始写作' : locking ? '正在锁定并启动…' : '完成种子 → 开始写作'}
        </button>
      </div>
    </div>
  );
}

function Checklist({ draft }: { draft: SeedDraft | null }) {
  if (!draft) return null;
  // 历史项目(如 seed_scripted_persist.py 注册的)可能没 completeness 字段 → 默认空清单
  const checklist = draft.completeness?.checklist ?? [];
  const ready = draft.completeness?.ready ?? false;
  if (checklist.length === 0) return null;
  const done = checklist.filter((c) => c.done).length;
  return (
    <div className="panel p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold">种子完成度</h3>
        <span className={cn('chip', ready ? 'bg-emerald-500/15 text-emerald-500' : 'bg-amber-500/15 text-amber-500')}>
          {done}/{checklist.length}
        </span>
      </div>
      <ul className="space-y-1.5">
        {checklist.map((c) => (
          <li key={c.key} className="flex items-center gap-2 text-sm">
            {c.done ? <CheckCircle2 className="h-4 w-4 text-emerald-500" /> : <Circle className="h-4 w-4 text-zinc-400" />}
            <span className={c.done ? 'text-zinc-600 dark:text-zinc-300' : 'text-zinc-400'}>{c.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function SeedDraftView({ draft }: { draft: SeedDraft | null }) {
  if (!draft) return <Empty>草稿将随对话实时填充。</Empty>;
  const wb = draft.worldBible;
  return (
    <>
      <Collapsible title="不可变层（锁定后不可改）">
        <KV label="世界设定" v={wb.settingCore} />
        <KV label="地理" v={wb.geography} />
        <KV label="文化/禁忌" v={wb.culture} />
        <KV label="物理法则" v={wb.physicsRules?.join('；')} />
      </Collapsible>
      <Collapsible title="意图层（可弯曲）">
        <KV label="主题" v={wb.theme} />
        <KV label="主角欲望" v={wb.protagonistWant} />
        <div className="mt-2">
          <div className="mb-1 text-xs font-medium text-zinc-500">候选结局</div>
          {(wb.candidateEndings ?? []).map((e) => (
            <div key={e.id} className="mb-1 rounded-lg bg-zinc-100 p-2 text-sm dark:bg-zinc-800">
              <div className="flex items-center justify-between">
                <span className="font-medium">{e.summary}</span>
                <span className="chip bg-indigo-500/15 text-indigo-400">w {e.activeWeight}</span>
              </div>
              <div className="text-xs text-zinc-500">{e.themeExpression}</div>
            </div>
          ))}
        </div>
      </Collapsible>
      <Collapsible title={`角色（${draft.personas.length}）`}>
        {draft.personas.length === 0 ? (
          <Empty>还没有角色。</Empty>
        ) : (
          draft.personas.map((p) => (
            <div key={p.id} className="mb-2 rounded-lg bg-zinc-100 p-2.5 text-sm dark:bg-zinc-800">
              <div className="font-semibold">{p.name}</div>
              <div className="text-xs text-zinc-500">欲望：{p.want}</div>
              <div className="mt-1 flex flex-wrap gap-1">
                {p.values.map((v) => (
                  <span key={v.name} className="chip bg-zinc-200 text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300">
                    {v.name} · {v.weight}
                  </span>
                ))}
              </div>
              <div className="mt-1 text-xs text-rose-400">弱点：{p.fatalFlaw}</div>
            </div>
          ))
        )}
      </Collapsible>
    </>
  );
}

function KV({ label, v }: { label: string; v?: string }) {
  return (
    <div className="mb-1.5">
      <span className="text-xs font-medium text-zinc-500">{label}：</span>
      <span className="text-sm">{v || <span className="text-zinc-400">（待定）</span>}</span>
    </div>
  );
}
