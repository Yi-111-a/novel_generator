import { AlertTriangle, FileText, Plus, Save, Trash2, UserPlus } from 'lucide-react';
import { useEffect, useState } from 'react';
import { getAdapter } from '../adapters';
import { useProjectCtx } from '../components/Layouts';
import { Collapsible, Panel } from '../components/ui';
import { CharacterDrawer, CharactersPanel, FactionDrawer, FactionsPanel, LocationDrawer, LocationsPanel, WorldBiblePanel } from '../components/WorldPanels';
import type { CharacterCard, Ending, Faction, Persona, PlanLocation, ProjectPlan, SeedDraft } from '../types';

const uid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 7)}`;

export function WorldConfig() {
  const { project } = useProjectCtx();
  const adapter = getAdapter();
  const [draft, setDraft] = useState<SeedDraft | null>(null);
  const [plan, setPlan] = useState<ProjectPlan | null>(null);
  const [dirty, setDirty] = useState(false);
  const [savedAt, setSavedAt] = useState<string>('');
  // 抽屉态
  const [locFor, setLocFor] = useState<PlanLocation | null>(null);
  const [facFor, setFacFor] = useState<Faction | null>(null);
  const [charFor, setCharFor] = useState<CharacterCard | null>(null);

  useEffect(() => {
    adapter.getSeedDraft(project.id).then(setDraft);
    // 锁定后才有 plan（W1/W2/W3 都在锁定流程里跑）
    if (project.status !== 'seeding') {
      adapter.getPlan(project.id).then(setPlan).catch(() => {});
    }
  }, [project.id, project.status]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!draft) return <div className="p-6 text-sm text-zinc-400">载入中…</div>;
  const wb = draft.worldBible;

  const update = (mut: (d: SeedDraft) => void) => {
    const next = structuredClone(draft);
    mut(next);
    setDraft(next);
    setDirty(true);
  };
  const save = async () => {
    await adapter.updateSeedDraft(project.id, draft);
    setDirty(false);
    setSavedAt(new Date().toLocaleTimeString('zh-CN'));
  };

  // 校验：冲突需要信息差。种子层没有逐 agent 已知事实，这里用"角色数 < 2"作近似预警。
  const asymmetryWarning = draft.personas.length < 2;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">世界配置</h1>
          <p className="text-sm text-zinc-500">聊天用来发想，这里用来审阅与微调结构化种子。</p>
        </div>
        <button className="btn-primary" onClick={save} disabled={!dirty}>
          <Save className="h-4 w-4" /> {dirty ? '保存修改' : savedAt ? `已保存 ${savedAt}` : '已是最新'}
        </button>
      </div>

      {asymmetryWarning && !plan?.planned && (
        <div className="panel flex items-center gap-2 border-amber-500/40 p-3 text-sm text-amber-500">
          <AlertTriangle className="h-4 w-4" /> 冲突需要信息差：至少需要两个角色，且他们对关键事实持不同认知（或一方知、一方不知）。
        </div>
      )}

      {plan?.continuation && <ContinuationSummaryPanel plan={plan} />}

      {plan?.planned ? (
        <>
          {/* 锁定后：引擎权威设定是主体 */}
          <div className="space-y-4">
            <WorldBiblePanel projectId={project.id} sections={plan.bibleSections ?? []} />
            <LocationsPanel locations={plan.locations ?? []} onPickLocation={setLocFor} />
            <FactionsPanel factions={plan.factions ?? []} locations={plan.locations ?? []} onPickFaction={setFacFor} />
            <CharactersPanel cards={plan.characterCards ?? []} onPickCard={setCharFor} />
            <DisclosureTimelinePanel
              projectId={project.id}
              cards={plan.characterCards ?? []}
              locations={plan.locations ?? []}
              factions={plan.factions ?? []}
              onSaved={() => adapter.getPlan(project.id).then(setPlan)}
            />
            {plan?.continuation && <ForeshadowPanel foreshadows={plan.foreshadows ?? []} />}
            {plan?.continuation && <StoryArcsPanel arcs={plan.storyArcs ?? []} />}
            {plan?.continuation && <OpenThreadsPanel threads={plan.openThreads ?? []} />}
            {plan?.continuation && <SettingsCodexPanel codex={plan.codex ?? []} />}
          </div>

          {/* 折叠式原始种子回溯 */}
          <Collapsible title={<span className="flex items-center gap-2 text-zinc-400"><FileText className="h-3.5 w-3.5" /> 原始种子（锁定前的草稿）</span>}>
            <div className="space-y-3 text-xs text-zinc-500">
              {wb.settingCore && <div><span className="font-medium">世界设定：</span>{wb.settingCore}</div>}
              {wb.geography && <div><span className="font-medium">地理：</span>{wb.geography}</div>}
              {wb.culture && <div><span className="font-medium">文化/禁忌：</span>{wb.culture}</div>}
              {(wb.physicsRules ?? []).length > 0 && <div><span className="font-medium">物理法则：</span>{(wb.physicsRules ?? []).join('、')}</div>}
              {wb.theme && <div><span className="font-medium">主题：</span>{wb.theme}</div>}
              {wb.protagonistWant && <div><span className="font-medium">主角欲望：</span>{wb.protagonistWant}</div>}
              {(wb.candidateEndings ?? []).length > 0 && (
                <div><span className="font-medium">候选结局：</span>{(wb.candidateEndings ?? []).map((e) => e.summary).join('、')}</div>
              )}
              {draft.personas.length > 0 && (
                <div><span className="font-medium">角色种子：</span>{draft.personas.map((p) => p.name).join('、')}</div>
              )}
            </div>
          </Collapsible>
        </>
      ) : (
        <>
          {/* 未锁定：编辑阶段，显示完整种子编辑器 */}
          <Collapsible title={<span className="flex items-center gap-2">不可变层 · 世界设定</span>}>
            <Text label="世界设定" value={wb.settingCore ?? ''} onChange={(v) => update((d) => (d.worldBible.settingCore = v))} />
            <Text label="地理" value={wb.geography ?? ''} onChange={(v) => update((d) => (d.worldBible.geography = v))} />
            <Text label="文化 / 禁忌" value={wb.culture ?? ''} onChange={(v) => update((d) => (d.worldBible.culture = v))} />
            <ListEditor label="物理法则（硬约束）" items={wb.physicsRules ?? []} onChange={(items) => update((d) => (d.worldBible.physicsRules = items))} />
          </Collapsible>

          <Collapsible title="叙事意图 · 主题与结局">
            <Text label="主题" value={wb.theme ?? ''} onChange={(v) => update((d) => (d.worldBible.theme = v))} />
            <Text label="主角外在欲望" value={wb.protagonistWant ?? ''} onChange={(v) => update((d) => (d.worldBible.protagonistWant = v))} />
            <EndingsEditor endings={wb.candidateEndings ?? []} onChange={(e) => update((d) => (d.worldBible.candidateEndings = e))} />
          </Collapsible>

          <Panel
            title={`角色（${draft.personas.length}）`}
            right={
              <button className="btn-ghost" onClick={() => update((d) => d.personas.push(blankPersona()))}>
                <UserPlus className="h-4 w-4" /> 新增角色
              </button>
            }
          >
            <div className="space-y-3">
              {draft.personas.map((p, i) => (
                <PersonaEditor key={p.id} persona={p} onChange={(np) => update((d) => (d.personas[i] = np))} onRemove={() => update((d) => d.personas.splice(i, 1))} />
              ))}
            </div>
          </Panel>
        </>
      )}

      {/* W2-b 地点 / W3-b 势力 抽屉（共享） */}
      <LocationDrawer loc={locFor} locations={plan?.locations ?? []} onClose={() => setLocFor(null)} onPickLocation={setLocFor} />
      <CharacterDrawer card={charFor} onClose={() => setCharFor(null)} />
      <FactionDrawer
        fac={facFor}
        factions={plan?.factions ?? []}
        locations={plan?.locations ?? []}
        onPickFaction={setFacFor}
        onPickLocation={(l) => { setFacFor(null); setLocFor(l); }}
        onClose={() => setFacFor(null)}
      />
    </div>
  );
}

function blankPersona(): Persona {
  return { id: uid('persona'), name: '新角色', want: '', values: [], fatalFlaw: '', obstacles: [], costThreshold: '', voice: '', mannerisms: [], motifObjects: [], arcState: '', costLedger: [] };
}

function Text({ label, value, onChange, disabled }: { label: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <label className="mb-3 block">
      <span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>
      <input className="input disabled:opacity-60" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function ListEditor({ label, items, onChange, disabled }: { label: string; items: string[]; onChange: (i: string[]) => void; disabled?: boolean }) {
  const [val, setVal] = useState('');
  return (
    <div className="mb-3">
      <span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>
      <div className="mb-1.5 flex flex-wrap gap-1.5">
        {items.map((it, i) => (
          <span key={i} className="chip bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
            {it}
            {!disabled && (
              <button className="ml-1 text-zinc-400 hover:text-rose-500" onClick={() => onChange(items.filter((_, j) => j !== i))}>
                ×
              </button>
            )}
          </span>
        ))}
        {items.length === 0 && <span className="text-xs text-zinc-400">（空）</span>}
      </div>
      {!disabled && (
        <div className="flex gap-2">
          <input className="input" placeholder="添加一条…" value={val} onChange={(e) => setVal(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && val.trim()) { onChange([...items, val.trim()]); setVal(''); } }} />
          <button className="btn-ghost border border-zinc-200 dark:border-zinc-800" onClick={() => { if (val.trim()) { onChange([...items, val.trim()]); setVal(''); } }}>
            <Plus className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}

function EndingsEditor({ endings, onChange }: { endings: Ending[]; onChange: (e: Ending[]) => void }) {
  return (
    <div className="mt-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium text-zinc-500">候选结局（每个带 activeWeight）</span>
        <button className="btn-ghost" onClick={() => onChange([...endings, { id: uid('end'), summary: '新结局', themeExpression: '', requiredConditions: [], activeWeight: 0.3 }])}>
          <Plus className="h-4 w-4" /> 加结局
        </button>
      </div>
      {endings.map((e, i) => (
        <div key={e.id} className="mb-2 rounded-lg border border-zinc-200 p-2.5 dark:border-zinc-800">
          <div className="flex items-center gap-2">
            <input className="input" value={e.summary} onChange={(ev) => onChange(endings.map((x, j) => (j === i ? { ...x, summary: ev.target.value } : x)))} />
            <button className="btn-ghost text-rose-500" onClick={() => onChange(endings.filter((_, j) => j !== i))}>
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
          <input className="input mt-1.5 text-sm" placeholder="主题表达" value={e.themeExpression ?? ''} onChange={(ev) => onChange(endings.map((x, j) => (j === i ? { ...x, themeExpression: ev.target.value } : x)))} />
          <div className="mt-2 flex items-center gap-2">
            <span className="text-xs text-zinc-500">activeWeight</span>
            <input type="range" min={0} max={1} step={0.05} value={Number(e.activeWeight) || 0} className="flex-1" onChange={(ev) => onChange(endings.map((x, j) => (j === i ? { ...x, activeWeight: Number(ev.target.value) } : x)))} />
            <span className="w-10 text-right font-mono text-xs">{(Number(e.activeWeight) || 0).toFixed(2)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function PersonaEditor({ persona, onChange, onRemove }: { persona: Persona; onChange: (p: Persona) => void; onRemove: () => void }) {
  const set = <K extends keyof Persona>(k: K, v: Persona[K]) => onChange({ ...persona, [k]: v });
  const [vName, setVName] = useState('');
  return (
    <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <div className="mb-2 flex items-center gap-2">
        <input className="input font-semibold" value={persona.name} onChange={(e) => set('name', e.target.value)} />
        <button className="btn-ghost text-rose-500" onClick={onRemove}>
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Text label="外在欲望 want" value={persona.want} onChange={(v) => set('want', v)} />
        <Text label="致命弱点 fatalFlaw" value={persona.fatalFlaw} onChange={(v) => set('fatalFlaw', v)} />
        <Text label="说话方式 voice" value={persona.voice} onChange={(v) => set('voice', v)} />
        <Text label="代价阈值 costThreshold" value={persona.costThreshold} onChange={(v) => set('costThreshold', v)} />
      </div>

      <div className="mt-1">
        <span className="mb-1 block text-xs font-medium text-zinc-500">珍视之物 values（带权重，可加减）</span>
        <div className="mb-1.5 space-y-1">
          {persona.values.map((v, i) => (
            <div key={i} className="flex items-center gap-2">
              <input className="input flex-1" value={v.name ?? ''} onChange={(e) => set('values', persona.values.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <input type="range" min={0} max={1} step={0.05} value={Number(v.weight) || 0} onChange={(e) => set('values', persona.values.map((x, j) => (j === i ? { ...x, weight: Number(e.target.value) } : x)))} />
              <span className="w-10 text-right font-mono text-xs">{(Number(v.weight) || 0).toFixed(2)}</span>
              <button className="text-zinc-400 hover:text-rose-500" onClick={() => set('values', persona.values.filter((_, j) => j !== i))}>
                ×
              </button>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <input className="input" placeholder="新增珍视之物…" value={vName} onChange={(e) => setVName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && vName.trim()) { set('values', [...persona.values, { name: vName.trim(), weight: 0.5 }]); setVName(''); } }} />
          <button className="btn-ghost border border-zinc-200 dark:border-zinc-800" onClick={() => { if (vName.trim()) { set('values', [...persona.values, { name: vName.trim(), weight: 0.5 }]); setVName(''); } }}>
            <Plus className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <ListEditor label="阻碍 obstacles" items={persona.obstacles} onChange={(v) => set('obstacles', v)} />
        <ListEditor label="习惯动作 mannerisms" items={persona.mannerisms} onChange={(v) => set('mannerisms', v)} />
      </div>
    </div>
  );
}

// ===== 续写蒸馏面板 =====
function ContinuationSummaryPanel({ plan }: { plan: ProjectPlan }) {
  const ev = (plan.sourceEvents ?? []).length;
  const cd = (plan.codex ?? []).length;
  const fs = (plan.foreshadows ?? []);
  const open = fs.filter((f: any) => f.status === 'open').length;
  const paid = fs.filter((f: any) => f.status === 'paid').length;
  const arcs = (plan.storyArcs ?? []).length;
  const cards = (plan.characterCards ?? []).length;
  const locs = (plan.locations ?? []).length;
  const facs = (plan.factions ?? []).length;
  const chaps = (plan.chapters ?? []).length;
  return (
    <Panel title="续写·完全蒸馏摘要">
      <div className="grid grid-cols-3 gap-3 text-xs sm:grid-cols-4 md:grid-cols-5">
        <Stat label="原作事件" value={ev} />
        <Stat label="设定 Codex" value={cd} />
        <Stat label="伏笔(总)" value={fs.length} />
        <Stat label="未收伏笔" value={open} hint={open > 0 ? '续写优先回收' : ''} />
        <Stat label="已收伏笔" value={paid} />
        <Stat label="剧情主线" value={arcs} />
        <Stat label="角色卡" value={cards} />
        <Stat label="地点" value={locs} />
        <Stat label="势力" value={facs} />
        <Stat label="预生成章节" value={chaps} hint="下面写作页直接读这份大纲" />
      </div>
    </Panel>
  );
}

function Stat({ label, value, hint }: { label: string; value: number | string; hint?: string }) {
  return (
    <div className="rounded-md border border-zinc-200 p-2 dark:border-zinc-800">
      <div className="text-zinc-500">{label}</div>
      <div className="mt-0.5 text-lg font-mono">{value}</div>
      {hint && <div className="mt-0.5 text-[10px] text-amber-500">{hint}</div>}
    </div>
  );
}

function ForeshadowPanel({ foreshadows }: { foreshadows: any[] }) {
  if (!foreshadows || foreshadows.length === 0) return null;
  const open = foreshadows.filter((f) => f.status === 'open');
  const paid = foreshadows.filter((f) => f.status === 'paid');
  return (
    <Panel title={`伏笔（共 ${foreshadows.length}：未收 ${open.length} / 已收 ${paid.length}）`}>
      <div className="space-y-3 text-xs">
        {open.length > 0 && (
          <div>
            <div className="mb-1 font-medium text-amber-500">未回收（续写优先素材）</div>
            <ul className="space-y-1">
              {open.slice(0, 20).map((f) => (
                <li key={f.setup_id} className="rounded border border-amber-500/30 px-2 py-1">
                  <span className="text-zinc-400">第{f.chapter_no}章·</span>
                  <span className="font-medium">{f.what_planted}</span>
                  <span className="ml-2 text-zinc-500">「{f.excerpt}」</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {paid.length > 0 && (
          <Collapsible title={<span>已回收（不再重复使用） · {paid.length} 条</span>}>
            <ul className="space-y-1">
              {paid.slice(0, 12).map((f) => (
                <li key={f.setup_id} className="rounded border border-emerald-500/30 px-2 py-1">
                  <span className="text-zinc-400">第{f.chapter_no}章埋 → 第{f.payoff_chapter}章收 · </span>
                  <span>{f.what_planted}</span>
                  {f.reason && <span className="ml-2 text-zinc-500">（{f.reason}）</span>}
                </li>
              ))}
            </ul>
          </Collapsible>
        )}
      </div>
    </Panel>
  );
}

function StoryArcsPanel({ arcs }: { arcs: any[] }) {
  if (!arcs || arcs.length === 0) return null;
  return (
    <Panel title={`剧情主线（${arcs.length}）`}>
      <div className="space-y-2 text-xs">
        {arcs.map((a) => (
          <div key={a.arc_id} className="rounded border border-zinc-200 p-2 dark:border-zinc-800">
            <div className="flex items-center justify-between">
              <span className="font-medium">{a.name}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${
                a.resolution_status === 'open' ? 'bg-amber-500/20 text-amber-500' :
                a.resolution_status === 'partial' ? 'bg-yellow-500/20 text-yellow-500' :
                'bg-emerald-500/20 text-emerald-500'
              }`}>{a.resolution_status}</span>
            </div>
            {a.theme && <div className="mt-0.5 text-zinc-500">主题：{a.theme}</div>}
            {a.journey_summary && <div className="mt-0.5">{a.journey_summary}</div>}
            <div className="mt-0.5 text-zinc-400">关键事件 {(a.key_events ?? []).length} · 转折点 {(a.turning_points ?? []).length}</div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function OpenThreadsPanel({ threads }: { threads: any[] }) {
  if (!threads || threads.length === 0) return null;
  return (
    <Panel title={`未解线索（${threads.length}）`}>
      <ul className="space-y-1 text-xs">
        {threads.map((t) => (
          <li key={t.id} className="rounded border border-zinc-200 px-2 py-1 dark:border-zinc-800">
            <span className="font-medium">{t.question}</span>
            <span className="ml-2 text-zinc-500">[{t.status}·张力{(t.tension ?? 0).toFixed?.(2) ?? t.tension}]</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function SettingsCodexPanel({ codex }: { codex: any[] }) {
  if (!codex || codex.length === 0) return null;
  return (
    <Panel title={`设定 Codex（${codex.length}）`}>
      <div className="grid grid-cols-1 gap-1.5 text-xs sm:grid-cols-2">
        {codex.slice(0, 60).map((c) => (
          <div key={c.codex_id} className="rounded border border-zinc-200 p-1.5 dark:border-zinc-800">
            <div className="flex items-center gap-2">
              <span className="font-medium">{c.name}</span>
              <span className="rounded bg-zinc-200 px-1 text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">{c.kind}</span>
              <span className="text-[10px] text-zinc-400">第{c.first_appeared}章首现</span>
            </div>
            {c.summary && <div className="mt-0.5 text-zinc-500">{c.summary}</div>}
          </div>
        ))}
      </div>
    </Panel>
  );
}

type DisclosureEntity = {
  id: string;
  name: string;
  kind: string;
  foreshadowFrom?: number;
  revealChapter?: number;
  secretRevealChapter?: number;
  foreshadowHint?: string;
  secretTruth?: string;
};

function DisclosureTimelinePanel({ projectId, cards, locations, factions, onSaved }: {
  projectId: string;
  cards: CharacterCard[];
  locations: PlanLocation[];
  factions: Faction[];
  onSaved: () => void;
}) {
  const rows: DisclosureEntity[] = [
    ...cards.filter((card) => card.agentId).map((card) => ({
      ...card, id: card.agentId as string, name: card.displayName || card.name, kind: '人物',
    })),
    ...locations.map((loc) => ({ ...loc, id: loc.locId, name: loc.name, kind: '地点' })),
    ...factions.map((fac) => ({ ...fac, id: fac.factionId, name: fac.name, kind: '势力' })),
  ];
  if (!rows.length) return null;
  return (
    <Panel title="披露时间线">
      <p className="mb-3 text-xs text-zinc-500">
        0=沿用旧行为；伏笔章到登场章之间只允许注入不点名的提示，揭秘章之后才开放秘密面。
      </p>
      <div className="space-y-2">
        {rows.map((row) => (
          <DisclosureTimelineRow key={`${row.kind}-${row.id}`} projectId={projectId} row={row} onSaved={onSaved} />
        ))}
      </div>
    </Panel>
  );
}

function DisclosureTimelineRow({ projectId, row, onSaved }: {
  projectId: string;
  row: DisclosureEntity;
  onSaved: () => void;
}) {
  const adapter = getAdapter();
  const [value, setValue] = useState({
    foreshadowFrom: Number(row.foreshadowFrom || 0),
    revealChapter: Number(row.revealChapter || 0),
    secretRevealChapter: Number(row.secretRevealChapter || 0),
    foreshadowHint: row.foreshadowHint || '',
    secretTruth: row.secretTruth || '',
  });
  const [busy, setBusy] = useState(false);
  const save = () => {
    setBusy(true);
    adapter.updateDisclosure(projectId, row.id, value)
      .then(onSaved)
      .catch(() => alert(`保存「${row.name}」披露日程失败`))
      .finally(() => setBusy(false));
  };
  return (
    <div className="rounded-md border border-zinc-200 p-2 dark:border-zinc-800">
      <div className="mb-2 flex items-center gap-2 text-xs">
        <span className="chip bg-indigo-500/15 text-indigo-400">{row.kind}</span>
        <span className="font-medium">{row.name}</span>
        <button className="btn-ghost ml-auto" disabled={busy} onClick={save}>
          <Save className="h-3.5 w-3.5" />保存
        </button>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {([
          ['foreshadowFrom', '伏笔起始章'],
          ['revealChapter', '正式登场章'],
          ['secretRevealChapter', '秘密揭晓章'],
        ] as const).map(([key, label]) => (
          <label key={key} className="text-[11px] text-zinc-500">
            {label}
            <input className="input mt-1" type="number" min={0} value={value[key]}
              onChange={(event) => setValue({ ...value, [key]: Number(event.target.value || 0) })} />
          </label>
        ))}
      </div>
      <label className="mt-2 block text-[11px] text-zinc-500">
        伏笔提示（不得点名、不得解释）
        <input className="input mt-1" value={value.foreshadowHint}
          onChange={(event) => setValue({ ...value, foreshadowHint: event.target.value })} />
      </label>
      <label className="mt-2 block text-[11px] text-zinc-500">
        秘密面（只在揭秘章后进入 prompt）
        <textarea className="input mt-1 min-h-16" value={value.secretTruth}
          onChange={(event) => setValue({ ...value, secretTruth: event.target.value })} />
      </label>
    </div>
  );
}
