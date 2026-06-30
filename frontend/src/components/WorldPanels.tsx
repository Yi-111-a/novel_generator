// W1+W2+W3 三联面板（世界观/地点/势力），含两个抽屉。供「世界配置」页装载。
import { ChevronDown, ChevronRight, Flag, Globe, MapPin, Sparkles, User, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import { getAdapter } from '../adapters';
import type { BibleSection, CharacterCard, Faction, PlanLocation } from '../types';

const TIER_LABEL: Record<string, string> = { lead: '主角', supporting: '主配', extra: '龙套' };
const TIER_CHIP: Record<string, string> = {
  lead: 'bg-amber-500/15 text-amber-500',
  supporting: 'bg-violet-500/15 text-violet-400',
  extra: 'bg-zinc-500/15 text-zinc-400',
};

const W1_SECTION_LABEL: Record<string, string> = {
  settingCore: '设定内核', cosmology: '世界体系', rules: '规则体系', geography: '地理',
  culture: '文化习俗', taboos: '禁忌', history: '历史', language: '语言', economy: '经济',
};

const REL_KIND_LABEL: Record<string, string> = {
  allied: '盟', hostile: '敌', infiltrates: '渗透', neutral: '中立', tributary: '附庸',
};
const REL_KIND_CHIP: Record<string, string> = {
  allied: 'bg-emerald-500/15 text-emerald-400',
  hostile: 'bg-rose-500/15 text-rose-400',
  infiltrates: 'bg-fuchsia-500/15 text-fuchsia-400',
  neutral: 'bg-zinc-500/15 text-zinc-400',
  tributary: 'bg-amber-500/15 text-amber-500',
};

// ---------------- 世界观面板（W1+W1-b）----------------
export function WorldBiblePanel({ projectId, sections }: { projectId: string; sections: BibleSection[] }) {
  const adapter = getAdapter();
  const bySection = useMemo(() => {
    const m = new Map<string, { w1?: BibleSection; raw?: BibleSection; deepened?: BibleSection }>();
    for (const s of sections) {
      const entry = m.get(s.section) ?? {};
      if (s.source === 'w1') entry.w1 = s;
      else if (s.source === 'w1_deepened') entry.deepened = s;
      else if (!entry.raw) entry.raw = s;
      m.set(s.section, entry);
    }
    return m;
  }, [sections]);
  const order = ['settingCore', 'geography', 'culture', 'rules', 'cosmology', 'taboos', 'history', 'language', 'economy'];
  const keys = [...order.filter((k) => bySection.has(k)),
    ...[...bySection.keys()].filter((k) => !order.includes(k))];

  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [deepening, setDeepening] = useState<string | null>(null);
  const [deepenCounts, setDeepenCounts] = useState<Record<string, number>>(() => {
    const m: Record<string, number> = {};
    for (const s of sections) if (s.source === 'w1_deepened') m[s.section] = (m[s.section] ?? 0) + 1;
    return m;
  });
  const [hintFor, setHintFor] = useState<string | null>(null);
  const [hintText, setHintText] = useState('');

  const toggle = (k: string) => setExpanded((p) => ({ ...p, [k]: !p[k] }));
  const doDeepen = async (k: string, hint: string = '') => {
    if (deepening) return;
    setDeepening(k);
    setHintFor(null);
    setHintText('');
    try {
      const res = await adapter.deepenBibleSection(projectId, k, '', hint);
      if (res.ok) setDeepenCounts((p) => ({ ...p, [k]: (p[k] ?? 0) + 1 }));
    } finally {
      setDeepening(null);
    }
  };

  if (keys.length === 0)
    return (
      <section className="panel p-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Globe className="h-4 w-4 text-teal-400" /> 世界观设定
        </h3>
        <p className="mt-2 text-xs text-zinc-400">（尚无世界观条目；旧项目重新锁定后由 W1 加厚）</p>
      </section>
    );

  return (
    <section className="panel p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Globe className="h-4 w-4 text-teal-400" /> 世界观设定（W1 权威两级）
        <span className="chip bg-teal-500/15 text-teal-400">{keys.length} 节已建档</span>
      </h3>
      <ul className="mt-3 space-y-1.5">
        {keys.map((k) => {
          const entry = bySection.get(k)!;
          const auth = entry.w1 ?? entry.raw;
          if (!auth) return null;
          const label = W1_SECTION_LABEL[k] ?? auth.title ?? k;
          const isW1 = !!entry.w1;
          const deepCount = deepenCounts[k] ?? 0;
          const deepEntries = sections.filter((s) => s.section === k && s.source === 'w1_deepened');
          const isOpen = expanded[k];
          return (
            <li key={k} className="rounded-md border border-zinc-200 dark:border-zinc-800">
              <button
                onClick={() => toggle(k)}
                className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-xs hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
              >
                {isOpen ? (
                  <ChevronDown className="h-3 w-3 shrink-0 text-zinc-400" />
                ) : (
                  <ChevronRight className="h-3 w-3 shrink-0 text-zinc-400" />
                )}
                <span className="font-medium text-zinc-700 dark:text-zinc-200">{label}</span>
                {isW1 && <span className="chip bg-teal-500/10 text-teal-400">W1</span>}
                {deepCount > 0 && <span className="chip bg-indigo-500/10 text-indigo-400">深化×{deepCount}</span>}
                {!isOpen && auth.summary && (
                  <span className="ml-1 truncate text-zinc-400">{auth.summary}</span>
                )}
              </button>
              {isOpen && (
                <div className="border-t border-zinc-100 px-3 py-2 dark:border-zinc-800">
                  {auth.summary && (
                    <p className="mb-1.5 text-xs font-medium text-teal-600 dark:text-teal-400">{auth.summary}</p>
                  )}
                  <p className="whitespace-pre-wrap text-xs leading-relaxed text-zinc-600 dark:text-zinc-300">
                    {auth.body_full}
                  </p>
                  {deepEntries.map((de, di) => (
                    <div key={di} className="mt-2 rounded bg-indigo-500/5 px-2 py-1.5">
                      <div className="mb-0.5 text-[10px] font-medium text-indigo-400">
                        {de.title || `深化追记${di + 1}`}
                      </div>
                      <p className="whitespace-pre-wrap text-xs leading-relaxed text-zinc-600 dark:text-zinc-300">
                        {de.body_full}
                      </p>
                    </div>
                  ))}
                  {isW1 && (
                    <div className="mt-2 space-y-1.5">
                      {hintFor === k ? (
                        <div className="flex gap-1.5">
                          <input
                            className="input flex-1 text-xs"
                            placeholder="深化方向（可留空，如：地下交易的具体规矩）"
                            value={hintText}
                            onChange={(e) => setHintText(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') doDeepen(k, hintText); if (e.key === 'Escape') { setHintFor(null); setHintText(''); } }}
                            autoFocus
                          />
                          <button
                            onClick={() => doDeepen(k, hintText)}
                            disabled={!!deepening}
                            className="rounded-md bg-indigo-500/10 px-2.5 py-1 text-[11px] font-medium text-indigo-400 hover:bg-indigo-500/20 disabled:opacity-50"
                          >
                            {deepening === k ? '深化中…' : '确认'}
                          </button>
                          <button
                            onClick={() => { setHintFor(null); setHintText(''); }}
                            className="rounded-md px-1.5 py-1 text-[11px] text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                          >
                            取消
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => setHintFor(k)}
                          disabled={!!deepening}
                          className="flex items-center gap-1 rounded-md bg-indigo-500/10 px-2.5 py-1 text-[11px] font-medium text-indigo-400 hover:bg-indigo-500/20 disabled:opacity-50"
                        >
                          <Sparkles className="h-3 w-3" />
                          {deepening === k ? '深化中…' : deepCount > 0 ? '继续深化' : '渐进深化'}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ---------------- 地点面板（W2+W2-b）----------------
export function LocationsPanel({ locations, onPickLocation }: {
  locations: PlanLocation[];
  onPickLocation: (l: PlanLocation) => void;
}) {
  if (!locations || locations.length === 0)
    return (
      <section className="panel p-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <MapPin className="h-4 w-4 text-teal-400" /> canon 地点
        </h3>
        <p className="mt-2 text-xs text-zinc-400">（尚无 canon 地点；项目锁定后由 W0/W2 固化加厚）</p>
      </section>
    );
  // 按 level 分组：大区域 / 街道 / 建筑 / 其他
  const groups: Record<string, PlanLocation[]> = { '大区域': [], '街道': [], '建筑': [], '其他': [] };
  for (const l of locations) {
    const lv = l.level || '其他';
    (groups[lv] ?? groups['其他']).push(l);
  }
  return (
    <section className="panel p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <MapPin className="h-4 w-4 text-teal-400" /> canon 地点（W2 两级 + 风土人情）
        <span className="chip bg-teal-500/15 text-teal-400">{locations.length}</span>
      </h3>
      <div className="mt-3 space-y-2">
        {Object.entries(groups).map(([lv, ls]) =>
          ls.length === 0 ? null : (
            <div key={lv}>
              <div className="mb-1 text-[11px] font-medium text-zinc-500">{lv}</div>
              <div className="flex flex-wrap gap-1.5">
                {ls.map((l) => (
                  <button
                    key={l.locId}
                    onClick={() => onPickLocation(l)}
                    title={l.summary || l.geoFull}
                    className="chip bg-teal-500/15 text-teal-400 hover:bg-teal-500/25"
                  >
                    {l.name}
                  </button>
                ))}
              </div>
            </div>
          ),
        )}
      </div>
    </section>
  );
}

// ---------------- 势力面板（W3+W3-b）----------------
export function FactionsPanel({ factions, locations, onPickFaction }: {
  factions: Faction[];
  locations: PlanLocation[];
  onPickFaction: (f: Faction) => void;
}) {
  if (!factions || factions.length === 0)
    return (
      <section className="panel p-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <Flag className="h-4 w-4 text-rose-400" /> 势力
        </h3>
        <p className="mt-2 text-xs text-zinc-400">（尚无势力；项目重新锁定后由 W3 据世界观+地理生成）</p>
      </section>
    );
  const locName = (lid: string) => locations.find((l) => l.locId === lid)?.name ?? lid;
  return (
    <section className="panel p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Flag className="h-4 w-4 text-rose-400" /> 势力（W3 一等实体 + 关系图）
        <span className="chip bg-rose-500/15 text-rose-400">{factions.length}</span>
      </h3>
      <ul className="mt-3 space-y-1.5">
        {factions.map((f) => (
          <li key={f.factionId} className="rounded-md border border-zinc-200 dark:border-zinc-800">
            <button
              onClick={() => onPickFaction(f)}
              className="flex w-full flex-col gap-0.5 px-2.5 py-1.5 text-left text-xs hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
            >
              <span className="flex items-center gap-1.5">
                <span className="font-medium text-zinc-700 dark:text-zinc-200">{f.name}</span>
                {f.territory.slice(0, 2).map((t) => (
                  <span key={t} className="chip bg-teal-500/10 text-teal-400">{locName(t)}</span>
                ))}
                {f.territory.length > 2 && (
                  <span className="chip bg-teal-500/10 text-teal-400">+{f.territory.length - 2}</span>
                )}
              </span>
              {f.summary && <span className="truncate text-zinc-500">{f.summary}</span>}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ---------------- 地点抽屉 ----------------
export function LocationDrawer({ loc, locations, onClose, onPickLocation }: {
  loc: PlanLocation | null;
  locations: PlanLocation[];
  onClose: () => void;
  onPickLocation?: (l: PlanLocation) => void;
}) {
  if (!loc) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-xl dark:bg-zinc-950"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-semibold">
            <MapPin className="h-4 w-4 text-teal-400" /> 地点 · {loc.name}
            {loc.level && <span className="chip bg-teal-500/15 text-teal-400">{loc.level}</span>}
          </span>
          <button onClick={onClose} className="rounded p-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
          {loc.summary && (
            <p className="font-medium text-teal-600 dark:text-teal-400">{loc.summary}</p>
          )}
          {(loc.controllingFaction || loc.parent) && (
            <dl className="space-y-1 text-xs">
              {loc.controllingFaction && (
                <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">掌控势力</dt><dd>{loc.controllingFaction}</dd></div>
              )}
              {loc.parent && (
                <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">上级</dt>
                  <dd>{locations.find((x) => x.locId === loc.parent)?.name ?? loc.parent}</dd></div>
              )}
            </dl>
          )}
          {(loc.detail || loc.geoFull) && (
            <div>
              <div className="mb-1 text-xs font-medium text-zinc-500">地貌·建筑·声光气味</div>
              <p className="whitespace-pre-wrap">{loc.detail || loc.geoFull}</p>
            </div>
          )}
          {loc.cultureLocal && (
            <div className="rounded bg-amber-500/5 px-3 py-2">
              <div className="mb-1 text-xs font-medium text-amber-500">本地风土</div>
              <p className="whitespace-pre-wrap text-zinc-600 dark:text-zinc-300">{loc.cultureLocal}</p>
            </div>
          )}
          {loc.connectsTo?.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-zinc-500">连通</div>
              <div className="flex flex-wrap gap-1.5">
                {loc.connectsTo.map((cid) => {
                  const c = locations.find((x) => x.locId === cid);
                  return c && onPickLocation ? (
                    <button key={cid} onClick={() => onPickLocation(c)}
                      className="chip bg-teal-500/15 text-teal-400 hover:bg-teal-500/25">{c.name}</button>
                  ) : <span key={cid} className="chip bg-zinc-500/15 text-zinc-400">{c?.name ?? cid}</span>;
                })}
              </div>
            </div>
          )}
          {loc.notableItems?.length > 0 && (
            <div className="text-xs text-zinc-500">固有道具：{loc.notableItems.join('、')}</div>
          )}
          {!loc.summary && !loc.detail && !loc.cultureLocal && (
            <p className="text-xs text-zinc-400">（此地点尚未跑 W2 加厚；旧项目重新锁定后会自动有两级 summary/detail/风土）</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------- 势力抽屉 ----------------
export function FactionDrawer({ fac, factions, locations, onPickFaction, onPickLocation, onClose }: {
  fac: Faction | null;
  factions: Faction[];
  locations: PlanLocation[];
  onPickFaction: (f: Faction) => void;
  onPickLocation: (l: PlanLocation) => void;
  onClose: () => void;
}) {
  if (!fac) return null;
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-xl dark:bg-zinc-950"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-semibold">
            <Flag className="h-4 w-4 text-rose-400" /> 势力 · {fac.name}
          </span>
          <button onClick={onClose} className="rounded p-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
          {fac.summary && (
            <p className="font-medium text-rose-500 dark:text-rose-400">{fac.summary}</p>
          )}
          {fac.ideology && (
            <div><span className="text-xs font-medium text-zinc-500">信条 </span>{fac.ideology}</div>
          )}
          {fac.detail && (
            <div>
              <div className="mb-1 text-xs font-medium text-zinc-500">综述</div>
              <p className="whitespace-pre-wrap">{fac.detail}</p>
            </div>
          )}
          {(fac.goals || fac.methods) && (
            <dl className="space-y-1 text-xs">
              {fac.goals && (
                <div className="flex gap-2"><dt className="w-12 shrink-0 text-zinc-400">目标</dt><dd className="whitespace-pre-wrap">{fac.goals}</dd></div>
              )}
              {fac.methods && (
                <div className="flex gap-2"><dt className="w-12 shrink-0 text-zinc-400">手段</dt><dd className="whitespace-pre-wrap">{fac.methods}</dd></div>
              )}
            </dl>
          )}
          {fac.territory?.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-zinc-500">地盘</div>
              <div className="flex flex-wrap gap-1.5">
                {fac.territory.map((tid) => {
                  const l = locations.find((x) => x.locId === tid);
                  return l ? (
                    <button key={tid} onClick={() => { onClose(); onPickLocation(l); }}
                      className="chip bg-teal-500/15 text-teal-400 hover:bg-teal-500/25">{l.name}</button>
                  ) : <span key={tid} className="chip bg-zinc-500/15 text-zinc-400">{tid}</span>;
                })}
              </div>
            </div>
          )}
          {fac.structure && (
            <div>
              <div className="mb-1 text-xs font-medium text-zinc-500">架构</div>
              <p className="whitespace-pre-wrap text-xs">{fac.structure}</p>
            </div>
          )}
          {fac.keyMembers?.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-zinc-500">核心成员</div>
              <ul className="space-y-0.5 text-xs">
                {fac.keyMembers.map((m, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium text-violet-500">{m.name}</span>
                    {m.role && <span className="chip bg-violet-500/10 text-violet-400">{m.role}</span>}
                    {m.note && <span className="text-zinc-500">{m.note}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {fac.history && (
            <div>
              <div className="mb-1 text-xs font-medium text-zinc-500">简史</div>
              <p className="whitespace-pre-wrap text-xs">{fac.history}</p>
            </div>
          )}
          {fac.relations?.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium text-zinc-500">关系</div>
              <ul className="space-y-1 text-xs">
                {fac.relations.map((r, i) => {
                  const target = factions.find((x) => x.factionId === r.target_faction_id);
                  const label = REL_KIND_LABEL[r.kind] ?? r.kind;
                  return (
                    <li key={i} className="flex items-center gap-1.5">
                      <span className={`chip ${REL_KIND_CHIP[r.kind] ?? 'bg-zinc-500/15 text-zinc-400'}`}>{label} {r.intensity}</span>
                      {target ? (
                        <button onClick={() => onPickFaction(target)}
                          className="font-medium text-rose-500 hover:underline">{target.name}</button>
                      ) : <span>{r.target_faction_id}</span>}
                      {r.note && <span className="text-zinc-500">— {r.note}</span>}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {fac.secret && (
            <div className="rounded bg-amber-500/5 px-3 py-2">
              <div className="mb-1 text-xs font-medium text-amber-500">秘密</div>
              <p className="whitespace-pre-wrap text-xs italic text-zinc-600 dark:text-zinc-300">{fac.secret}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------- 人物面板（W4+W4-b）：按 tier 分组列全部卡 ----------------
export function CharactersPanel({ cards, onPickCard }: {
  cards: CharacterCard[];
  onPickCard: (c: CharacterCard) => void;
}) {
  if (!cards || cards.length === 0)
    return (
      <section className="panel p-4">
        <h3 className="flex items-center gap-2 text-sm font-semibold">
          <User className="h-4 w-4 text-violet-400" /> 人物
        </h3>
        <p className="mt-2 text-xs text-zinc-400">（尚无人物卡）</p>
      </section>
    );
  const groups: Record<string, CharacterCard[]> = { lead: [], supporting: [], extra: [] };
  for (const c of cards) (groups[c.tier] ?? groups.extra).push(c);
  return (
    <section className="panel p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <User className="h-4 w-4 text-violet-400" /> 人物（W4 三维度 · 主角极详 / 主配加厚）
        <span className="chip bg-violet-500/15 text-violet-400">{cards.length}</span>
      </h3>
      <div className="mt-3 space-y-3">
        {(['lead', 'supporting', 'extra'] as const).map((tier) => {
          const list = groups[tier];
          if (!list || list.length === 0) return null;
          return (
            <div key={tier}>
              <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium">
                <span className={`chip ${TIER_CHIP[tier]}`}>{TIER_LABEL[tier]}</span>
                <span className="text-zinc-400">{list.length}</span>
              </div>
              <ul className="space-y-1">
                {list.map((c) => {
                  const enriched = !!(c.appearance || c.psychology);
                  return (
                    <li key={c.cardId} className="rounded-md border border-zinc-200 dark:border-zinc-800">
                      <button
                        onClick={() => onPickCard(c)}
                        className="flex w-full flex-col gap-0.5 px-2.5 py-1.5 text-left text-xs hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
                      >
                        <span className="flex items-center gap-1.5">
                          <span className="font-medium text-zinc-700 dark:text-zinc-200">{c.name}</span>
                          {enriched && <span className="chip bg-violet-500/10 text-violet-400">W4 已加厚</span>}
                          {c.definingTrait && <span className="text-zinc-400">· {c.definingTrait}</span>}
                        </span>
                        {c.oneLiner && <span className="truncate text-zinc-500">{c.oneLiner}</span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ---------------- 人物详情抽屉 ----------------
export function CharacterDrawer({ card, onClose }: {
  card: CharacterCard | null;
  onClose: () => void;
}) {
  if (!card) return null;
  const enriched = !!(card.appearance || card.psychology || card.socialRole);
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-xl dark:bg-zinc-950"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-semibold">
            <User className="h-4 w-4 text-violet-400" /> {card.name}
            <span className={`chip ${TIER_CHIP[card.tier]}`}>{TIER_LABEL[card.tier] ?? card.tier}</span>
          </span>
          <button onClick={onClose} className="rounded p-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-3 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
          {card.oneLiner && (
            <p className="font-medium text-violet-500 dark:text-violet-400">{card.oneLiner}</p>
          )}
          {(card.definingTrait || card.keyRelation || card.coreDesire || card.fatalFlaw) && (
            <dl className="space-y-1 text-xs">
              {card.definingTrait && (
                <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">特征</dt><dd>{card.definingTrait}</dd></div>
              )}
              {card.keyRelation && (
                <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">关键关系</dt><dd>{card.keyRelation}</dd></div>
              )}
              {card.coreDesire && (
                <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">欲望</dt><dd>{card.coreDesire}</dd></div>
              )}
              {card.fatalFlaw && (
                <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">致命弱点</dt><dd className="text-rose-400">{card.fatalFlaw}</dd></div>
              )}
            </dl>
          )}
          {enriched ? (
            <>
              {card.appearance && (
                <div>
                  <div className="mb-1 text-xs font-medium text-zinc-500">生理 · 外貌/身材/标志</div>
                  <p className="whitespace-pre-wrap text-xs">{card.appearance}</p>
                </div>
              )}
              {card.socialRole && (
                <div>
                  <div className="mb-1 text-xs font-medium text-zinc-500">社会 · 出身/家庭/阶层/隶属</div>
                  <p className="whitespace-pre-wrap text-xs">{card.socialRole}</p>
                </div>
              )}
              {card.psychology && (
                <div>
                  <div className="mb-1 text-xs font-medium text-zinc-500">心理 · 性格/三观/恐惧</div>
                  <p className="whitespace-pre-wrap text-xs">{card.psychology}</p>
                </div>
              )}
            </>
          ) : (
            <p className="text-xs text-zinc-400">（此卡尚未跑 W4 三维度加厚；项目重新锁定后会自动有 appearance/social_role/psychology）</p>
          )}
          {card.backstory && (
            <div>
              <div className="mb-1 text-xs font-medium text-zinc-500">小传</div>
              <p className="whitespace-pre-wrap text-xs">{card.backstory}</p>
            </div>
          )}
          {card.arc && (
            <div className="rounded bg-amber-500/5 px-3 py-2">
              <div className="mb-1 text-xs font-medium text-amber-500">角色弧线</div>
              <p className="whitespace-pre-wrap text-xs">{card.arc}</p>
            </div>
          )}
          {(card.voiceRegister || card.verbalHabits) && (
            <div className="text-xs text-zinc-500">
              {card.voiceRegister && <span>语域：{card.voiceRegister}　</span>}
              {card.verbalHabits && <span>口头禅：{card.verbalHabits}</span>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
