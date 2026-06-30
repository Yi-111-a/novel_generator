import { Layers, ChevronRight, Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { DistilledKnowledgePackage } from '../types';

// 蒸馏结果是程序+模型协作产物，字段较松散，这里统一按 any 读取并防御性渲染。
type Dict = Record<string, any>;

interface Props {
  knowledgePackage: DistilledKnowledgePackage | null;
}

const TABS = [
  { key: 'overview', label: '总览' },
  { key: 'characters', label: '人物' },
  { key: 'world', label: '世界设定' },
  { key: 'places', label: '地点/势力' },
  { key: 'events', label: '逐章事件' },
  { key: 'timeline', label: '时间线' },
  { key: 'relations', label: '关系' },
  { key: 'threads', label: '伏笔' },
  { key: 'style', label: '文风' },
  { key: 'uncertainties', label: '不确定项' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

function asArray(value: unknown): Dict[] {
  return Array.isArray(value) ? (value as Dict[]) : [];
}

function ChapterTags({ chapters }: { chapters?: unknown }) {
  const list = Array.isArray(chapters) ? chapters.filter((c) => Number(c) > 0) : [];
  if (!list.length) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {list.map((c, i) => (
        <span key={i} className="rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] text-indigo-600 dark:bg-indigo-950/40 dark:text-indigo-300">
          第{String(c)}章
        </span>
      ))}
    </div>
  );
}

function Confidence({ value }: { value?: unknown }) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  const pct = Math.round(Math.min(1, n) * 100);
  const tone = pct >= 80 ? 'text-emerald-600' : pct >= 50 ? 'text-amber-600' : 'text-rose-600';
  return <span className={`text-[11px] ${tone}`}>可信度 {pct}%</span>;
}

function Chips({ items, tone = 'zinc' }: { items?: unknown; tone?: string }) {
  const list = Array.isArray(items) ? items.filter((x) => x != null && String(x).trim()) : [];
  if (!list.length) return <span className="text-zinc-400">—</span>;
  const cls: Record<string, string> = {
    zinc: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300',
    indigo: 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300',
    rose: 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300',
  };
  return (
    <div className="flex flex-wrap gap-1">
      {list.map((x, i) => (
        <span key={i} className={`rounded-full px-2 py-0.5 text-xs ${cls[tone] ?? cls.zinc}`}>{String(x)}</span>
      ))}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  if (children == null || children === '' ) return null;
  return (
    <div className="grid grid-cols-[84px_1fr] gap-2 text-sm">
      <div className="text-zinc-400">{label}</div>
      <div className="text-zinc-700 dark:text-zinc-200">{children}</div>
    </div>
  );
}

function Accordion({ title, subtitle, badge, children, defaultOpen = false }: {
  title: ReactNode; subtitle?: ReactNode; badge?: ReactNode; children: ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronRight className={`h-4 w-4 shrink-0 text-zinc-400 transition-transform ${open ? 'rotate-90' : ''}`} />
        <span className="flex-1 text-sm font-medium text-zinc-800 dark:text-zinc-100">{title}</span>
        {badge}
        {subtitle ? <span className="text-xs text-zinc-400">{subtitle}</span> : null}
      </button>
      {open ? <div className="space-y-2 border-t border-zinc-100 px-3 py-2 dark:border-zinc-800">{children}</div> : null}
    </div>
  );
}

function ImportanceBadge({ value, tier }: { value?: unknown; tier?: unknown }) {
  const t = tier ? String(tier) : '';
  const n = Number(value);
  if (!t && !(Number.isFinite(n) && n > 0)) return null;
  const tierTone: Record<string, string> = {
    主角: 'bg-rose-600 text-white',
    主要: 'bg-amber-500 text-white',
    次要: 'bg-zinc-300 text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200',
    龙套: 'bg-zinc-200 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400',
  };
  return (
    <span className="flex items-center gap-1">
      {t ? <span className={`rounded px-1.5 py-0.5 text-[10px] ${tierTone[t] ?? 'bg-zinc-200 text-zinc-600'}`}>{t}</span> : null}
      {Number.isFinite(n) && n > 0 ? <span className="text-[10px] text-zinc-400">★{Math.round(n * 100)}</span> : null}
    </span>
  );
}

function PlaceCard({ place }: { place: Dict }) {
  return (
    <Accordion
      title={String(place.name ?? '条目')}
      subtitle={place.nature ? String(place.nature) : undefined}
      badge={<ImportanceBadge value={place.importance} />}
    >
      {place.summary ? <p className="text-sm italic text-zinc-600 dark:text-zinc-300">{String(place.summary)}</p> : null}
      <Field label="描述">{place.description ? String(place.description) : null}</Field>
      <Field label="性质">{place.nature ? String(place.nature) : null}</Field>
      <Field label="作用">{place.role ? String(place.role) : null}</Field>
      <Field label="别名"><Chips items={place.aliases} /></Field>
      <Field label="重要成员"><Chips items={place.key_members} tone="indigo" /></Field>
      <Field label="关联"><Chips items={place.related} /></Field>
      <div className="flex items-center justify-between pt-1">
        <ChapterTags chapters={place.evidence_chapters ?? [place.first_seen_chapter]} />
        <Confidence value={place.confidence} />
      </div>
    </Accordion>
  );
}

function StateDict({ data }: { data?: unknown }) {
  const entries = data && typeof data === 'object' && !Array.isArray(data) ? Object.entries(data as Dict) : [];
  if (!entries.length) return <span className="text-zinc-400">—</span>;
  return (
    <div className="space-y-0.5">
      {entries.map(([k, v]) => (
        <div key={k} className="text-sm">
          <span className="text-zinc-500">{k}：</span>
          <span className="text-zinc-700 dark:text-zinc-200">{Array.isArray(v) ? v.join('、') : String(v)}</span>
        </div>
      ))}
    </div>
  );
}

export function DistillationResultsPanel({ knowledgePackage }: Props) {
  const [tab, setTab] = useState<TabKey>('overview');
  const [query, setQuery] = useState('');
  const pkg = (knowledgePackage?.package ?? {}) as Dict;
  const stats = knowledgePackage?.stats ?? {};

  // 所有 hook 必须在任何条件 return 之前无条件调用，避免 hooks 顺序错乱。
  const characters = useMemo(() => asArray(pkg.characters), [pkg]);
  const filteredChars = useMemo(() => {
    const q = query.trim();
    if (!q) return characters;
    return characters.filter((c) =>
      String(c.name ?? '').includes(q) || asArray(c.aliases).some((a) => String(a).includes(q)));
  }, [characters, query]);
  const eventsByChapter = useMemo(() => {
    const map = new Map<number, Dict[]>();
    for (const e of asArray(pkg.chapter_events)) {
      const ch = Number(e.chapter_no) || 0;
      if (!map.has(ch)) map.set(ch, []);
      map.get(ch)!.push(e);
    }
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [pkg]);

  if (!knowledgePackage || !knowledgePackage.package) {
    return (
      <section className="panel p-4">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Layers className="h-4 w-4 text-violet-400" />
          蒸馏结果
        </div>
        <p className="mt-3 text-sm text-zinc-500">还没有知识包。先导入原文并运行叙事蒸馏（B1–B4），这里就会展示蒸馏出的正文内容。</p>
      </section>
    );
  }

  const events = asArray(pkg.chapter_events);
  const threads = asArray(pkg.plot_threads);
  const threadGroups = {
    resolved: threads.filter((t) => t.status === 'resolved'),
    advanced: threads.filter((t) => t.status === 'advanced'),
    open: threads.filter((t) => t.status === 'open' || !t.status),
  };

  const world = (pkg.world_setting ?? {}) as Dict;
  const style = (pkg.style_profile ?? {}) as Dict;

  return (
    <section className="panel p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Layers className="h-4 w-4 text-violet-400" />
          蒸馏结果
          {stats.usedFallback ? (
            <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">本地回退（未调用模型）</span>
          ) : (
            <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">已合成</span>
          )}
        </div>
        {knowledgePackage.updatedAt ? (
          <span className="text-xs text-zinc-400">更新于 {new Date(knowledgePackage.updatedAt).toLocaleString()}</span>
        ) : null}
      </div>

      <div className="mt-3 flex flex-wrap gap-1 border-b border-zinc-200 pb-2 dark:border-zinc-800">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`rounded-md px-2.5 py-1 text-xs ${tab === t.key ? 'bg-violet-600 text-white' : 'text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="mt-3 max-h-[640px] overflow-y-auto pr-1">
        {/* ===== 总览 ===== */}
        {tab === 'overview' && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                ['人物', characters.length],
                ['地点', asArray(pkg.locations).length],
                ['势力', asArray(pkg.factions).length],
                ['事件', events.length],
                ['时间线', asArray(pkg.timeline).length],
                ['关系', asArray(pkg.relationship_graph).length],
                ['伏笔', threads.length],
                ['不确定项', asArray(pkg.uncertainties).length],
              ].map(([label, n]) => (
                <div key={String(label)} className="rounded-lg border border-zinc-200 p-2 text-center dark:border-zinc-800">
                  <div className="text-lg font-semibold text-zinc-800 dark:text-zinc-100">{String(n)}</div>
                  <div className="text-xs text-zinc-500">{String(label)}</div>
                </div>
              ))}
            </div>
            <div className="rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
              <div className="text-zinc-400">伏笔进度</div>
              <div className="mt-1 text-zinc-700 dark:text-zinc-200">
                已回收 {threadGroups.resolved.length} · 推进中 {threadGroups.advanced.length} · 未解 {threadGroups.open.length}
              </div>
            </div>
            {world.summary ? (
              <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                <div className="text-xs text-zinc-400">世界观速览</div>
                <p className="mt-1 text-sm leading-6 text-zinc-700 dark:text-zinc-200">{String(world.summary)}</p>
              </div>
            ) : null}
            {style.overall_voice ? (
              <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                <div className="text-xs text-zinc-400">总体文风</div>
                <p className="mt-1 text-sm leading-6 text-zinc-700 dark:text-zinc-200">{String(style.overall_voice)}</p>
              </div>
            ) : null}
          </div>
        )}

        {/* ===== 人物 ===== */}
        {tab === 'characters' && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 rounded-lg border border-zinc-200 px-2 dark:border-zinc-800">
              <Search className="h-4 w-4 text-zinc-400" />
              <input
                className="w-full bg-transparent py-1.5 text-sm outline-none"
                placeholder="搜索人物名或别名…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            {filteredChars.map((c, i) => (
              <Accordion
                key={String(c.id ?? i)}
                title={String(c.name ?? '未命名')}
                subtitle={c.role ? String(c.role) : undefined}
                badge={<ImportanceBadge value={c.importance} tier={c.tier} />}
              >
                {c.one_liner ? <p className="text-sm italic text-zinc-600 dark:text-zinc-300">{String(c.one_liner)}</p> : null}
                <Field label="身份">{c.identity ? String(c.identity) : null}</Field>
                <Field label="外貌">{c.appearance ? String(c.appearance) : null}</Field>
                <Field label="别名"><Chips items={c.aliases} /></Field>
                <Field label="性格"><Chips items={c.personality} tone="indigo" /></Field>
                <Field label="核心欲望">{c.core_desire ? String(c.core_desire) : null}</Field>
                <Field label="目标"><Chips items={c.goals} /></Field>
                <Field label="恐惧"><Chips items={c.fears} /></Field>
                <Field label="缺陷"><Chips items={c.flaws} tone="rose" /></Field>
                <Field label="能力"><Chips items={c.abilities} tone="indigo" /></Field>
                <Field label="重要经历">{asArray(c.key_experiences).length ? (
                  <ul className="list-disc pl-4">{asArray(c.key_experiences).map((x, k) => <li key={k}>{String(x)}</li>)}</ul>
                ) : null}</Field>
                <Field label="关系">{asArray(c.relationships).length ? (
                  <ul className="space-y-0.5">{asArray(c.relationships).map((r, k) => (
                    <li key={k}>{String(r.with ?? '')} — {String(r.relation ?? '')}{r.note ? `（${String(r.note)}）` : ''}</li>
                  ))}</ul>
                ) : null}</Field>
                <Field label="说话特征">{c.speech_style ? String(c.speech_style) : null}</Field>
                <Field label="成长弧">{c.growth_arc ? String(c.growth_arc) : null}</Field>
                <Field label="书末状态">{c.book_end_state ? String(c.book_end_state) : null}</Field>
                {c.augmented ? (
                  <div className="text-[11px] text-emerald-600 dark:text-emerald-400">
                    ✦ 已回原文补全（来源：{asArray(c.augment_chapters).map((x) => `第${String(x)}章`).join('、')}）
                  </div>
                ) : null}
                <Field label="持久状态"><StateDict data={c.final_state} /></Field>
                {Object.keys((c.transient_state ?? {}) as Dict).length ? (
                  <Field label="临时状态"><StateDict data={c.transient_state} /></Field>
                ) : null}
                <div className="flex items-center justify-between pt-1">
                  <ChapterTags chapters={c.evidence_chapters ?? (c.first_seen_chapter ? [c.first_seen_chapter] : [])} />
                  <Confidence value={c.confidence} />
                </div>
              </Accordion>
            ))}
            {!filteredChars.length ? <p className="text-sm text-zinc-400">没有匹配的人物。</p> : null}
          </div>
        )}

        {/* ===== 世界设定 ===== */}
        {tab === 'world' && (
          <div className="space-y-3">
            {world.summary ? <p className="text-sm leading-6 text-zinc-700 dark:text-zinc-200">{String(world.summary)}</p> : null}
            {world.factions_overview ? (
              <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                <div className="text-xs text-zinc-400">势力格局</div>
                <p className="mt-1 text-sm leading-6 text-zinc-700 dark:text-zinc-200">{String(world.factions_overview)}</p>
              </div>
            ) : null}
            {asArray(world.rules).length ? (
              <div>
                <div className="mb-1 text-xs font-semibold text-zinc-500">世界规则（按重要性）</div>
                <div className="space-y-2">
                  {asArray(world.rules).map((r, i) => (
                    <Accordion key={i} title={String(r.name ?? r.detail ?? '规则')} badge={<ImportanceBadge value={r.importance} />}>
                      <p className="text-sm text-zinc-700 dark:text-zinc-200">{String(r.detail ?? '')}</p>
                      <ChapterTags chapters={r.chapters} />
                    </Accordion>
                  ))}
                </div>
              </div>
            ) : null}
            {asArray(world.history).length ? (
              <div>
                <div className="mb-1 text-xs font-semibold text-zinc-500">关键历史（按重要性）</div>
                <div className="space-y-2">
                  {asArray(world.history).map((h, i) => (
                    <Accordion
                      key={i}
                      title={String(h.event ?? '历史')}
                      subtitle={h.when ? `🕰 ${String(h.when)}` : undefined}
                      badge={<ImportanceBadge value={h.importance} />}
                    >
                      {h.when ? <div className="text-xs text-indigo-500">故事内时间：{String(h.when)}</div> : null}
                      <p className="text-sm text-zinc-700 dark:text-zinc-200">{String(h.detail ?? '')}</p>
                    </Accordion>
                  ))}
                </div>
              </div>
            ) : null}
            {asArray(world.assertions).length ? (
              <div>
                <div className="mb-1 text-xs font-semibold text-zinc-500">设定断言（含原文证据）</div>
                <div className="space-y-2">
                  {asArray(world.assertions).map((a, i) => (
                    <div key={i} className="rounded-lg border border-zinc-200 p-2 text-sm dark:border-zinc-800">
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">{String(a.category ?? '')}</span>
                        <span className="text-zinc-700 dark:text-zinc-200">{String(a.claim ?? '')}</span>
                      </div>
                      {(a.evidence as Dict)?.quote ? (
                        <blockquote className="mt-1 border-l-2 border-zinc-300 pl-2 text-xs text-zinc-500 dark:border-zinc-700">“{String((a.evidence as Dict).quote)}”</blockquote>
                      ) : null}
                      <ChapterTags chapters={[a.chapter]} />
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        )}

        {/* ===== 地点/势力 ===== */}
        {tab === 'places' && (
          <div className="space-y-3">
            <div>
              <div className="mb-1 text-xs font-semibold text-zinc-500">势力 / 机构（{asArray(pkg.factions).length}）· 按重要性排序</div>
              <div className="space-y-2">
                {asArray(pkg.factions).map((f, i) => <PlaceCard key={i} place={f} />)}
                {!asArray(pkg.factions).length ? <p className="text-sm text-zinc-400">无。</p> : null}
              </div>
            </div>
            <div>
              <div className="mb-1 text-xs font-semibold text-zinc-500">地点（{asArray(pkg.locations).length}）· 按重要性排序</div>
              <div className="space-y-2">
                {asArray(pkg.locations).map((l, i) => <PlaceCard key={i} place={l} />)}
                {!asArray(pkg.locations).length ? <p className="text-sm text-zinc-400">无。</p> : null}
              </div>
            </div>
          </div>
        )}

        {/* ===== 逐章事件 ===== */}
        {tab === 'events' && (
          <div className="space-y-3">
            {eventsByChapter.map(([ch, list]) => (
              <div key={ch}>
                <div className="mb-1 text-xs font-semibold text-zinc-500">第 {ch} 章（{list.length}）</div>
                <ol className="space-y-1">
                  {list.map((e, i) => (
                    <li key={i} className="rounded-lg border border-zinc-200 p-2 text-sm dark:border-zinc-800">
                      <div className="text-zinc-700 dark:text-zinc-200">{String(e.summary ?? '')}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                        {e.kind ? <span className="rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800">{String(e.kind)}</span> : null}
                        {e.location ? <span>📍{String(e.location)}</span> : null}
                        {e.time_marker ? <span>🕒{String(e.time_marker)}</span> : null}
                      </div>
                      {asArray(e.participants).length ? <div className="mt-1"><Chips items={e.participants} tone="indigo" /></div> : null}
                      {e.effects ? <div className="mt-1 text-xs text-zinc-500">结果：{String(e.effects)}</div> : null}
                    </li>
                  ))}
                </ol>
              </div>
            ))}
            {!eventsByChapter.length ? <p className="text-sm text-zinc-400">无事件。</p> : null}
          </div>
        )}

        {/* ===== 时间线 ===== */}
        {tab === 'timeline' && (
          <ol className="space-y-1">
            {asArray(pkg.timeline).map((t, i) => (
              <li key={i} className="flex gap-2 rounded-lg border border-zinc-200 p-2 text-sm dark:border-zinc-800">
                <span className="shrink-0 text-xs text-indigo-600 dark:text-indigo-300">第{String(t.chapter ?? '')}章{t.time_expression ? ` · ${String(t.time_expression)}` : ''}</span>
                <span className="text-zinc-700 dark:text-zinc-200">{String(t.summary ?? '')}</span>
              </li>
            ))}
            {!asArray(pkg.timeline).length ? <p className="text-sm text-zinc-400">无时间线条目。</p> : null}
          </ol>
        )}

        {/* ===== 关系 ===== */}
        {tab === 'relations' && (
          <div className="space-y-1">
            {asArray(pkg.relationship_graph).map((r, i) => (
              <div key={i} className="rounded-lg border border-zinc-200 p-2 text-sm dark:border-zinc-800">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-medium text-zinc-800 dark:text-zinc-100">{String(r.src_name ?? r.src ?? '')}</span>
                  <span className="rounded-full bg-violet-50 px-2 py-0.5 text-xs text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">{String(r.relation ?? '关联')}</span>
                  <span className="font-medium text-zinc-800 dark:text-zinc-100">{String(r.dst_name ?? r.dst ?? '')}</span>
                  {r.sentiment ? <span className="text-xs text-zinc-400">（{String(r.sentiment)}）</span> : null}
                  {r.co_occurrences ? <span className="text-xs text-zinc-400">共现{String(r.co_occurrences)}次</span> : null}
                </div>
                {r.detail ? <div className="mt-1 text-xs text-zinc-500">{String(r.detail)}</div> : null}
                <ChapterTags chapters={r.chapters} />
              </div>
            ))}
            {!asArray(pkg.relationship_graph).length ? <p className="text-sm text-zinc-400">未生成关系图谱。</p> : null}
          </div>
        )}

        {/* ===== 伏笔 ===== */}
        {tab === 'threads' && (
          <div className="space-y-3">
            {([
              ['已回收', threadGroups.resolved, 'text-emerald-600'],
              ['推进中', threadGroups.advanced, 'text-amber-600'],
              ['未解开', threadGroups.open, 'text-zinc-500'],
            ] as const).map(([label, list, tone]) => (
              <div key={label}>
                <div className={`mb-1 text-xs font-semibold ${tone}`}>{label}（{list.length}）</div>
                <div className="space-y-1">
                  {list.map((t, i) => (
                    <div key={i} className="rounded-lg border border-zinc-200 p-2 text-sm dark:border-zinc-800">
                      <div className="text-zinc-700 dark:text-zinc-200">{String(t.question ?? '')}</div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                        <span>第{String(t.opened_chapter ?? '?')}章提出</span>
                        {Number(t.resolved_chapter) > 0 ? <span>第{String(t.resolved_chapter)}章回收</span> : null}
                        {t.resolution_source === 'deterministic_link' ? <span className="rounded bg-zinc-100 px-1 dark:bg-zinc-800">程序回收</span> : null}
                        <Confidence value={t.confidence} />
                      </div>
                      {t.resolution ? <div className="mt-1 text-xs text-emerald-700 dark:text-emerald-300">结果：{String(t.resolution)}</div> : null}
                    </div>
                  ))}
                  {!list.length ? <p className="text-xs text-zinc-400">无。</p> : null}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* ===== 文风 ===== */}
        {tab === 'style' && (
          <div className="space-y-2">
            <Field label="总体声口">{style.overall_voice ? String(style.overall_voice) : null}</Field>
            <Field label="叙事视角">{style.pov ? String(style.pov) : null}</Field>
            <Field label="基调">{style.tone ? String(style.tone) : null}</Field>
            <Field label="句式节奏">{style.sentence_rhythm ? String(style.sentence_rhythm) : null}</Field>
            <Field label="对白特点">{style.dialogue_style ? String(style.dialogue_style) : null}</Field>
            <Field label="节奏控制">{style.pacing ? String(style.pacing) : null}</Field>
            <Field label="用词偏好">{style.vocabulary ? String(style.vocabulary) : null}</Field>
            <Field label="幽默/反讽">{style.humor ? String(style.humor) : null}</Field>
            <Field label="标志手法"><Chips items={style.signature_devices} tone="indigo" /></Field>
            <Field label="续写保持"><Chips items={style.continuation_dos} /></Field>
            <Field label="续写避免"><Chips items={style.continuation_donts} tone="rose" /></Field>
            <Field label="高频特征"><Chips items={style.features} /></Field>
            {asArray(style.samples).length ? (
              <div>
                <div className="mb-1 mt-2 text-xs font-semibold text-zinc-500">文风样本（{asArray(style.samples).length}）</div>
                <div className="space-y-1">
                  {asArray(style.samples).slice(0, 30).map((s, i) => (
                    <div key={i} className="rounded-lg border border-zinc-200 p-2 text-sm dark:border-zinc-800">
                      <div className="flex items-center gap-2 text-xs text-zinc-400">
                        <span className="rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800">{String(s.type ?? '')}</span>
                        {s.chapter_id ? <span>第{String(s.chapter_id)}章</span> : null}
                      </div>
                      <p className="mt-1 text-zinc-700 dark:text-zinc-200">{String(s.text ?? '')}</p>
                      <Chips items={s.features} />
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        )}

        {/* ===== 不确定项 ===== */}
        {tab === 'uncertainties' && (
          <div className="space-y-1">
            {asArray(pkg.uncertainties).map((u, i) => (
              <div key={i} className="rounded-lg border border-amber-200 bg-amber-50/40 p-2 text-sm dark:border-amber-900 dark:bg-amber-950/10">
                <div className="flex items-center gap-2">
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                    {u.category === 'entity_merge' ? '实体待裁决' : String(u.category ?? '存疑')}
                  </span>
                  {u.subject ? <span className="font-medium text-zinc-700 dark:text-zinc-200">{String(u.subject)}</span> : null}
                  <Confidence value={u.confidence} />
                </div>
                {u.claim ? <div className="mt-1 text-zinc-700 dark:text-zinc-200">{String(u.claim)}</div> : null}
                {asArray(u.versions).length ? (
                  <ul className="mt-1 list-disc pl-4 text-xs text-zinc-500">
                    {asArray(u.versions).map((v, k) => <li key={k}>第{String(v.chapter ?? '?')}章：{String(v.claim ?? '')}</li>)}
                  </ul>
                ) : null}
                {u.reason ? <div className="mt-1 text-xs text-zinc-500">原因：{String(u.reason)}</div> : null}
                {Number(u.chapter) > 0 ? <ChapterTags chapters={[u.chapter]} /> : null}
              </div>
            ))}
            {!asArray(pkg.uncertainties).length ? <p className="text-sm text-zinc-400">没有标记不确定项。</p> : null}
          </div>
        )}
      </div>
    </section>
  );
}
