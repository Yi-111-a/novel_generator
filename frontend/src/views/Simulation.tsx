import { Pause, Play, SkipForward, Sparkles, Wand2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { getAdapter } from '../adapters';
import { useProjectCtx, SeedingGate } from '../components/Layouts';
import { Panel, Empty } from '../components/ui';
import { cn } from '../lib/cn';
import { useAppStore } from '../store/useAppStore';
import { useSimStore } from '../store/useSimStore';
import type { Beat, Ending, Persona, Thread } from '../types';

export function Simulation() {
  const { project } = useProjectCtx();
  const devMode = useAppStore((s) => s.devMode);
  const adapter = getAdapter();
  const { byProject, subscribe } = useSimStore();

  const [beats, setBeats] = useState<Beat[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [endings, setEndings] = useState<Ending[]>([]);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [playing, setPlaying] = useState(project.runningSim ?? false);

  const live = byProject[project.id];
  // 事件流全部来自持久化的 sim store（首次加载历史 + SSE 增量），切换标签不重拉、不刷新
  const allEvents = live?.events ?? [];
  const tick = live?.tick ?? 0;

  // 订阅实时流：幂等；切走不退订（SSE 持续在后台收事件，切回即时可见，无需重新加载）
  useEffect(() => {
    if (project.status === 'seeding') return;
    subscribe(project.id);
  }, [project.id, project.status, subscribe]);

  // 播放按钮跟随后端真实状态：刷新/切回后 runningSim 异步到达时同步，避免"仍在跑却显示播放"。
  useEffect(() => {
    setPlaying(project.runningSim ?? false);
  }, [project.runningSim]);

  // 只轮询"变化较慢的聚合数据"（节拍/线/结局/角色），事件流不在此列；失败不清空
  const refetchAggregates = () => {
    adapter.getBeats(project.id).then(setBeats).catch(() => {});
    adapter.getThreads(project.id).then(setThreads).catch(() => {});
    adapter.getEndings(project.id).then(setEndings).catch(() => {});
    adapter.getPersonas(project.id).then(setPersonas).catch(() => {});
  };
  useEffect(() => {
    if (project.status === 'seeding') return;
    refetchAggregates();
    const id = window.setInterval(refetchAggregates, 8000);
    return () => window.clearInterval(id);
  }, [project.id, project.status]); // eslint-disable-line react-hooks/exhaustive-deps

  if (project.status === 'seeding') return <SeedingGate />;

  const control = async (a: 'play' | 'pause' | 'step') => {
    await adapter.control(project.id, a);
    if (a === 'play') setPlaying(true);
    if (a === 'pause') setPlaying(false);
  };

  const tensionData = allEvents.slice(-30).map((e) => ({ t: e.storyTime, drama: e.dramaScore ?? 0 }));

  return (
    <div className="space-y-4">
      {/* 控制台 */}
      <div className="panel flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="flex items-center gap-2">
          <button className="btn-primary" onClick={() => control(playing ? 'pause' : 'play')}>
            {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            {playing ? '暂停' : '播放'}
          </button>
          <button className="btn-ghost border border-zinc-200 dark:border-zinc-800" onClick={() => control('step')}>
            <SkipForward className="h-4 w-4" /> 单步
          </button>
          <span className="ml-2 text-sm text-zinc-500">故事时间 tick = {tick}</span>
        </div>
        <GodConsole projectId={project.id} threads={threads} facts={[]} devMode={devMode} onDone={refetchAggregates} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* 实时事件流 */}
        <Panel title="实时事件流" className="xl:col-span-1">
          <div className="max-h-[420px] space-y-2 overflow-y-auto">
            {allEvents.slice(-60).reverse().map((e) => (
              <div key={e.eventId} className="rounded-lg border border-zinc-200 p-2 text-sm dark:border-zinc-800">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{e.actionType}</span>
                  <span className="font-mono text-xs text-zinc-400">t{e.storyTime}</span>
                </div>
                <div className="text-zinc-500">{e.payload}</div>
                {devMode && (
                  <div className="mt-1 font-mono text-[11px] text-zinc-400">
                    {e.eventId} · drama {e.dramaScore?.toFixed(2)} · 感知 [{e.perceivers.join(',')}]
                  </div>
                )}
              </div>
            ))}
            {!allEvents.length && <Empty>还没有事件。点「播放」或「单步」让导演推进剧情。</Empty>}
          </div>
        </Panel>

        {/* 张力曲线 + 结局权重 */}
        <Panel title="张力曲线（近 30 个事件 drama）" className="xl:col-span-2">
          <div className="h-44">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={tensionData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" opacity={0.3} />
                <XAxis dataKey="t" tick={{ fontSize: 11 }} stroke="#71717a" />
                <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} stroke="#71717a" />
                <Tooltip contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', borderRadius: 8, fontSize: 12 }} />
                <Line type="monotone" dataKey="drama" stroke="#818cf8" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3">
            <div className="mb-1 text-xs font-medium text-zinc-500">候选结局 activeWeight</div>
            <div className="h-28">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={endings.map((e) => ({ name: e.summary.slice(0, 8), w: e.activeWeight }))}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" opacity={0.3} />
                  <XAxis dataKey="name" tick={{ fontSize: 10 }} stroke="#71717a" />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 11 }} stroke="#71717a" />
                  <Tooltip contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="w" fill="#34d399" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* 节拍时间线 */}
        <Panel title="节拍计划时间线">
          <div className="space-y-1.5">
            {beats.map((b) => (
              <div key={b.beatId} className={cn('flex items-center gap-2 rounded-lg border p-2 text-sm', b.status === 'active' ? 'border-indigo-500/60 bg-indigo-500/10' : 'border-zinc-200 dark:border-zinc-800', b.status === 'done' && 'opacity-50')}>
                <span className={cn('chip', b.type === 'structural' ? 'bg-sky-500/15 text-sky-400' : 'bg-fuchsia-500/15 text-fuchsia-400')}>{b.type === 'structural' ? '结构' : '抉择'}</span>
                <span className="flex-1">{b.goal}</span>
                <span className="font-mono text-xs text-zinc-400">↯{b.targetTension}</span>
                {b.status === 'active' && <Sparkles className="h-3.5 w-3.5 text-indigo-400" />}
              </div>
            ))}
            {!beats.length && <Empty>暂无节拍。</Empty>}
          </div>
        </Panel>

        {/* 故事线张力 + 角色卡 */}
        <Panel title="故事线张力">
          <div className="space-y-2">
            {threads.map((t) => (
              <div key={t.threadId}>
                <div className="flex items-center justify-between text-sm">
                  <span>{t.centralQuestion}</span>
                  <span className="font-mono text-xs text-zinc-400">{t.currentTension.toFixed(2)}</span>
                </div>
                <div className="mt-1 h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                  <div className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-rose-500" style={{ width: `${t.currentTension * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <Panel title="角色状态">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {personas.map((p) => (
            <div key={p.id} className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
              <div className="font-semibold">{p.name}</div>
              <div className="mt-1 text-xs text-zinc-500">弧线：{p.arcState || '—'}</div>
              <div className="mt-1.5 text-xs font-medium text-zinc-500">代价台账</div>
              {p.costLedger.length ? (
                <ul className="mt-0.5 list-disc pl-4 text-xs text-zinc-500">
                  {p.costLedger.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              ) : (
                <div className="text-xs text-zinc-400">（尚未付出可见代价）</div>
              )}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function GodConsole({ projectId, threads, devMode, onDone }: { projectId: string; threads: Thread[]; facts: unknown[]; devMode: boolean; onDone: () => void }) {
  const adapter = getAdapter();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [threadId, setThreadId] = useState('');
  const [weight, setWeight] = useState(0.5);
  const [entityType, setEntityType] = useState<'character' | 'object'>('character');
  const [entityName, setEntityName] = useState('');

  useMemo(() => {
    if (threads[0] && !threadId) setThreadId(threads[0].threadId);
  }, [threads, threadId]);

  return (
    <div className="relative">
      <button className={cn('btn border', open ? 'border-indigo-500/60 bg-indigo-500/10 text-indigo-400' : 'btn-ghost border-zinc-200 dark:border-zinc-800')} onClick={() => setOpen((o) => !o)}>
        <Wand2 className="h-4 w-4" /> 上帝控制台
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 w-80 panel space-y-3 p-3 shadow-xl">
          <div>
            <div className="mb-1 text-xs font-medium text-zinc-500">注入事件</div>
            <div className="flex gap-2">
              <input className="input" placeholder="发生了什么…" value={text} onChange={(e) => setText(e.target.value)} />
              <button
                className="btn-primary"
                onClick={async () => {
                  if (!text.trim()) return;
                  await adapter.injectGodAction(projectId, { kind: 'add_event', payload: { actionType: '神迹', payload: text.trim(), actors: [], perceivers: [] } });
                  setText('');
                  onDone();
                }}
              >
                注入
              </button>
            </div>
          </div>

          <div>
            <div className="mb-1 text-xs font-medium text-zinc-500">新增角色 / 物品（自动落地世界）</div>
            <div className="flex gap-2">
              <select className="input w-20" value={entityType} onChange={(e) => setEntityType(e.target.value as 'character' | 'object')}>
                <option value="character">角色</option>
                <option value="object">物品</option>
              </select>
              <input className="input" placeholder="名字（可留空，自动生成）" value={entityName} onChange={(e) => setEntityName(e.target.value)} />
              <button
                className="btn-ghost border border-zinc-200 dark:border-zinc-800"
                onClick={async () => {
                  await adapter.injectGodAction(projectId, { kind: 'add_entity', entityType, name: entityName.trim() || undefined });
                  setEntityName('');
                  onDone();
                }}
              >
                登场
              </button>
            </div>
          </div>

          <div>
            <div className="mb-1 text-xs font-medium text-zinc-500">调整故事线优先级</div>
            <div className="flex items-center gap-2">
              <select className="input" value={threadId} onChange={(e) => setThreadId(e.target.value)}>
                {threads.map((t) => (
                  <option key={t.threadId} value={t.threadId}>
                    {t.centralQuestion.slice(0, 14)}
                  </option>
                ))}
              </select>
              <input type="range" min={0} max={1} step={0.05} value={weight} onChange={(e) => setWeight(Number(e.target.value))} />
              <button className="btn-ghost border border-zinc-200 dark:border-zinc-800" onClick={async () => { await adapter.injectGodAction(projectId, { kind: 'set_thread_priority', threadId, weight }); onDone(); }}>
                应用
              </button>
            </div>
          </div>

          {devMode && (
            <p className="text-[11px] text-zinc-400">
              开发者模式下，揭示/隐藏 fact、改写 fact 等纯调试操作可在「账本检查器」中执行。
            </p>
          )}
        </div>
      )}
    </div>
  );
}
