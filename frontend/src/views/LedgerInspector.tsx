import { AlertTriangle, GitCompareArrows } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { getAdapter } from '../adapters';
import { useProjectCtx, SeedingGate } from '../components/Layouts';
import { Empty, Panel } from '../components/ui';
import { cn } from '../lib/cn';
import type { Fact, Foreshadow, KnowledgeEntry, Persona, ReaderKnowledge } from '../types';

export function LedgerInspector() {
  const { project } = useProjectCtx();
  const adapter = getAdapter();

  const [facts, setFacts] = useState<Fact[]>([]);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [agentId, setAgentId] = useState('');
  const [agentLedger, setAgentLedger] = useState<KnowledgeEntry[]>([]);
  const [allKnowledge, setAllKnowledge] = useState<Record<string, KnowledgeEntry[]>>({});
  const [reader, setReader] = useState<ReaderKnowledge[]>([]);
  const [maxPos, setMaxPos] = useState(1);
  const [pos, setPos] = useState(1);
  const [foreshadows, setForeshadows] = useState<Foreshadow[]>([]);

  const factMap = useMemo(() => Object.fromEntries(facts.map((f) => [f.factId, f])), [facts]);

  useEffect(() => {
    if (project.status === 'seeding') return;
    (async () => {
      const ws = await adapter.getWorldState(project.id);
      setFacts(ws.facts);
      const ps = await adapter.getPersonas(project.id);
      setPersonas(ps);
      if (ps[0] && !agentId) setAgentId(ps[0].id);
      const all: Record<string, KnowledgeEntry[]> = {};
      for (const p of ps) all[p.id] = await adapter.getAgentKnowledge(project.id, p.id);
      setAllKnowledge(all);
      const rk = await adapter.getReaderKnowledge(project.id);
      const mp = Math.max(1, ...rk.map((r) => r.revealedDiscoursePos));
      setMaxPos(mp);
      setPos(mp);
      setForeshadows(await adapter.getForeshadows(project.id));
    })();
  }, [project.id, project.status]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (agentId) adapter.getAgentKnowledge(project.id, agentId).then(setAgentLedger);
  }, [agentId, project.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (project.status !== 'seeding') adapter.getReaderKnowledge(project.id, pos).then(setReader);
  }, [pos, project.id, project.status]); // eslint-disable-line react-hooks/exhaustive-deps

  if (project.status === 'seeding') return <SeedingGate />;

  // 落差计算（客户端）
  const readerFactIds = new Set(reader.map((r) => r.factId));
  const mysterySet = facts.filter((f) => !readerFactIds.has(f.factId));
  const povKnown = new Set((allKnowledge[agentId] ?? []).map((k) => k.factId));
  const ironySet = reader.filter((r) => !povKnown.has(r.factId));
  const conflictPairs = facts
    .map((f) => {
      const versions = Object.entries(allKnowledge)
        .map(([aid, ks]) => ({ aid, v: ks.find((k) => k.factId === f.factId)?.versionContent }))
        .filter((x) => x.v);
      const distinct = new Set(versions.map((x) => x.v));
      return distinct.size > 1 ? { fact: f, versions } : null;
    })
    .filter(Boolean) as { fact: Fact; versions: { aid: string; v?: string }[] }[];

  const openMustResolve = foreshadows.filter((f) => f.mustResolve && f.status === 'open');

  return (
    <div className="space-y-4 font-mono text-[13px]">
      {/* 三本账本并排 */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <Panel title="世界账本（全部 facts · 真相）">
          <div className="max-h-72 space-y-1.5 overflow-y-auto">
            {facts.map((f) => (
              <div key={f.factId} className="rounded border border-zinc-200 p-1.5 dark:border-zinc-800">
                <span className="text-truth">{f.canonicalContent}</span>
                <div className="text-[11px] text-zinc-400">{f.factId} · t{f.storyTime}</div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel
          title="角色账本"
          right={
            <select className="input w-32 py-1 text-xs" value={agentId} onChange={(e) => setAgentId(e.target.value)}>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          }
        >
          <div className="max-h-72 space-y-1.5 overflow-y-auto">
            {agentLedger.map((k) => {
              const canon = factMap[k.factId]?.canonicalContent;
              const distorted = canon != null && canon !== k.versionContent;
              return (
                <div key={k.factId} className={cn('rounded border p-1.5', distorted ? 'border-distort/50 bg-distort/5' : 'border-zinc-200 dark:border-zinc-800')}>
                  <span className={distorted ? 'text-distort' : ''}>{k.versionContent}</span>
                  {distorted && <div className="text-[11px] text-zinc-400">真相：{canon}</div>}
                  <div className="text-[11px] text-zinc-400">conf {k.confidence} · 学于 t{k.learnedTick}{distorted ? ' · ⚠扭曲' : ''}</div>
                </div>
              );
            })}
            {!agentLedger.length && <Empty>该角色账本为空。</Empty>}
          </div>
        </Panel>

        <Panel title="读者账本">
          <div className="mb-2 flex items-center gap-2 text-xs">
            <span className="text-zinc-500">回看至话语#</span>
            <input type="range" min={1} max={maxPos} value={pos} onChange={(e) => setPos(Number(e.target.value))} className="flex-1" />
            <span className="w-6 text-right">{pos}</span>
          </div>
          <div className="max-h-60 space-y-1.5 overflow-y-auto">
            {reader.map((r) => (
              <div key={r.factId} className="rounded border border-zinc-200 p-1.5 dark:border-zinc-800">
                <span className="text-ironic">{r.revealedVersion}</span>
                <div className="text-[11px] text-zinc-400">#{r.revealedDiscoursePos} · via {r.viaPov}</div>
              </div>
            ))}
            {!reader.length && <Empty>此话语位置时，读者还一无所知。</Empty>}
          </div>
        </Panel>
      </div>

      {/* 落差视图 */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <Panel title={`mystery_set（读者未知真相 · ${mysterySet.length}）`}>
          <div className="max-h-48 space-y-1 overflow-y-auto">
            {mysterySet.map((f) => (
              <div key={f.factId} className="truncate text-zinc-500">
                · {f.canonicalContent}
              </div>
            ))}
            {!mysterySet.length && <Empty>读者已知晓全部真相。</Empty>}
          </div>
        </Panel>
        <Panel title={`irony_set（读者知而「${personas.find((p) => p.id === agentId)?.name ?? agentId}」不知 · ${ironySet.length}）`}>
          <div className="max-h-48 space-y-1 overflow-y-auto">
            {ironySet.map((r) => (
              <div key={r.factId} className="truncate text-ironic">
                · {r.revealedVersion}
              </div>
            ))}
            {!ironySet.length && <Empty>无戏剧反讽落差。</Empty>}
          </div>
        </Panel>
        <Panel title={`conflict_pairs（同 fact 不同版本 · ${conflictPairs.length}）`} right={<GitCompareArrows className="h-4 w-4 text-zinc-400" />}>
          <div className="max-h-48 space-y-2 overflow-y-auto">
            {conflictPairs.map(({ fact, versions }) => (
              <div key={fact.factId} className="rounded border border-zinc-200 p-1.5 dark:border-zinc-800">
                <div className="text-[11px] text-zinc-400">{fact.factId}</div>
                {versions.map((v) => (
                  <div key={v.aid} className="truncate">
                    <span className="text-zinc-400">{v.aid}:</span> {v.v}
                  </div>
                ))}
              </div>
            ))}
            {!conflictPairs.length && <Empty>暂无版本冲突。</Empty>}
          </div>
        </Panel>
      </div>

      {/* 伏笔台账 + 诚实性闸门 */}
      <Panel
        title="伏笔台账"
        right={
          openMustResolve.length > 0 ? (
            <span className="chip bg-danger/15 text-danger">
              <AlertTriangle className="h-3.5 w-3.5" /> 诚实性闸门：{openMustResolve.length} 条 must_resolve 未回收
            </span>
          ) : (
            <span className="chip bg-truth/15 text-truth">伏笔诚实 ✓</span>
          )
        }
      >
        <table className="w-full text-left text-xs">
          <thead className="text-zinc-400">
            <tr>
              <th className="py-1">问题</th>
              <th>关联 fact</th>
              <th>mustResolve</th>
              <th>回收节拍</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {foreshadows.map((f) => (
              <tr key={f.foreshadowId} className={cn('border-t border-zinc-200 dark:border-zinc-800', f.mustResolve && f.status === 'open' && 'bg-danger/5')}>
                <td className="py-1.5 pr-2">{f.question}</td>
                <td className="pr-2 text-zinc-500">{f.linkedFactId}</td>
                <td>{f.mustResolve ? '是' : '否'}</td>
                <td className="text-zinc-500">{f.targetPayoffBeat || <span className="text-danger">未排</span>}</td>
                <td>
                  <span className={cn('chip', f.status === 'paid_off' ? 'bg-truth/15 text-truth' : f.status === 'abandoned' ? 'bg-zinc-500/15 text-zinc-400' : 'bg-distort/15 text-distort')}>{f.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!foreshadows.length && <Empty>暂无伏笔。</Empty>}
      </Panel>
    </div>
  );
}
