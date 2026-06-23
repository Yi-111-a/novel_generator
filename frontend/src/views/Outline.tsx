import { BookMarked, Boxes, KeyRound, Lock, Palette, Pencil, Play, RotateCcw, Settings2, Trash2, Unlock, UserRound, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { getAdapter } from '../adapters';
import { SeedingGate, useProjectCtx } from '../components/Layouts';
import { AuthorSheetPanel } from '../components/AuthorSheetPanel';
import { StyleSkillPanel } from '../components/StyleSkillPanel';
import { Empty } from '../components/ui';
import type { Persona, PlanChapter, ProjectPlan, WritingSettings } from '../types';


const STATUS_CHIP: Record<string, string> = {
  done: 'bg-emerald-500/15 text-emerald-400',
  active: 'bg-indigo-500/15 text-indigo-400',
  planned: 'bg-zinc-500/15 text-zinc-400',
};
const STATUS_LABEL: Record<string, string> = { done: '已写', active: '进行中', planned: '待写' };
const ROLE_LABEL: Record<string, string> = { setup: '起', rising: '承', twist: '转', climax: '合·高潮', resolution: '余波' };
const ROLE_CHIP: Record<string, string> = {
  setup: 'bg-zinc-500/15 text-zinc-400',
  rising: 'bg-sky-500/15 text-sky-400',
  twist: 'bg-amber-500/15 text-amber-500',
  climax: 'bg-rose-500/15 text-rose-400',
  resolution: 'bg-emerald-500/15 text-emerald-400',
};
const THREAD_DECISION_LABEL: Record<string, string> = { reveal: '揭', hint: '暗', hide: '藏' };
const THREAD_DECISION_CHIP: Record<string, string> = {
  reveal: 'bg-emerald-500/15 text-emerald-400',
  hint: 'bg-amber-500/15 text-amber-500',
  hide: 'bg-zinc-500/15 text-zinc-400',
};

export function Outline() {
  const { project } = useProjectCtx();
  const adapter = getAdapter();
  const [plan, setPlan] = useState<ProjectPlan | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [dossierFor, setDossierFor] = useState<string | null>(null);
  const [dossierMd, setDossierMd] = useState('');
  const [writingSettings, setWritingSettings] = useState<WritingSettings | null>(null);
  const [writingBusy, setWritingBusy] = useState<'save' | 'auto' | ''>('');
  const [writingMessage, setWritingMessage] = useState('');

  useEffect(() => {
    if (project.status === 'seeding') return;
    let alive = true;
    const pull = () => {
      adapter.getPlan(project.id).then((p) => alive && setPlan(p)).catch(() => {});
      adapter.getPersonas(project.id).then((p) => alive && setPersonas(p)).catch(() => {});
    };
    adapter.getWritingSettings(project.id).then((settings) => alive && setWritingSettings(settings)).catch(() => {});
    pull();
    const id = window.setInterval(pull, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [project.id, project.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const nameOf = useMemo(() => {
    const m = Object.fromEntries(personas.map((p) => [p.id, p.displayName || p.name]));
    return (id?: string | null) => (id ? m[id] ?? id : '—');
  }, [personas]);

  const [toneBusy, setToneBusy] = useState(false);
  const confirmTone = () => {
    setToneBusy(true);
    adapter
      .updateTone(project.id, {}, true)
      .then(() => adapter.getPlan(project.id).then(setPlan))
      .catch(() => {})
      .finally(() => setToneBusy(false));
  };
  // B0.6 时代隔离墙：启停（仅在基调未确认时可改）
  const toggleEra = (enabled: boolean) => {
    const era = { ...(plan?.toneProfile?.eraLogic || {}), enabled };
    setToneBusy(true);
    adapter
      .updateTone(project.id, { eraLogic: era }, false)
      .then(() => adapter.getPlan(project.id).then(setPlan))
      .catch(() => {})
      .finally(() => setToneBusy(false));
  };

  // 大纲编辑/删除
  const [editId, setEditId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const refreshPlan = () => adapter.getPlan(project.id).then(setPlan).catch(() => {});
  const startEdit = (c: PlanChapter) => {
    setEditId(c.chapterId);
    setDraft({
      title: c.title || '',
      conflictType: c.conflictType || '',
      dramaticQuestion: c.dramaticQuestion || '',
      exitState: c.exitState || '',
      beatGoals: (c.beatGoals || []).join('\n'),
      castNames: (c.castNames || []).join('、'),
      itemNames: (c.itemsPresentNames || []).join('、'),
      locationName: c.locationName || '',
    });
  };
  const splitNames = (s: string) => s.split(/[、,，\s]+/).map((x) => x.trim()).filter(Boolean);
  const saveEdit = (chapterId: string) => {
    setBusy(true);
    adapter
      .editChapter(project.id, chapterId, {
        title: draft.title,
        conflictType: draft.conflictType,
        dramaticQuestion: draft.dramaticQuestion,
        exitState: draft.exitState,
        beatGoals: draft.beatGoals.split('\n').map((x) => x.trim()).filter(Boolean),
        castNames: splitNames(draft.castNames),
        itemNames: splitNames(draft.itemNames),
        locationName: draft.locationName.trim(),
      })
      .then(() => { setEditId(null); return refreshPlan(); })
      .catch(() => alert('保存失败（已写完的章不能改）'))
      .finally(() => setBusy(false));
  };
  const delChapter = (c: PlanChapter) => {
    const warn = c.written
      ? `第${c.sequenceOrder}章已写完。删除会一并清掉它的正文，之后该位置可重写。确定删除？`
      : `确定删除第${c.sequenceOrder}章的大纲？`;
    if (!window.confirm(warn)) return;
    setBusy(true);
    adapter.deleteChapter(project.id, c.chapterId)
      .then(() => { if (editId === c.chapterId) setEditId(null); return refreshPlan(); })
      .catch(() => alert('删除失败'))
      .finally(() => setBusy(false));
  };
  const replanChapter = (c: PlanChapter) => {
    if (!window.confirm(`重新规划第${c.sequenceOrder}章？当前未写大纲会被刷新。`)) return;
    setBusy(true);
    adapter.replanChapter(project.id, c.chapterId)
      .then(refreshPlan)
      .catch(() => alert('重新规划失败；续写运行时临时章可能不支持此入口'))
      .finally(() => setBusy(false));
  };

  const openDossier = (agentId: string) => {
    setDossierFor(agentId);
    setDossierMd('载入中…');
    adapter.getDossier(project.id, agentId).then((d) => setDossierMd(d.markdown || '（暂无档案）')).catch(() => setDossierMd('载入失败'));
  };

  const saveWritingSettings = async () => {
    if (!writingSettings) return;
    setWritingBusy('save');
    setWritingMessage('');
    try {
      const next = await adapter.saveWritingSettings(project.id, writingSettings);
      setWritingSettings(next);
      setWritingMessage('写作设置已保存');
    } catch {
      setWritingMessage('写作设置保存失败');
    } finally {
      setWritingBusy('');
    }
  };

  const startAutoWriting = async () => {
    if (!writingSettings) return;
    setWritingBusy('auto');
    setWritingMessage('');
    try {
      const drafts = await adapter.getChapterDrafts(project.id);
      const activeDraft = drafts.find((draftRow) =>
        draftRow.status === 'pending_acceptance' || draftRow.status === 'blocked'
      );
      if (activeDraft) {
        setWritingMessage(`第 ${activeDraft.chapterNo} 章仍待处理，请先到“阅读”页审批或重写`);
        return;
      }
      const saved = await adapter.saveWritingSettings(project.id, writingSettings);
      setWritingSettings(saved);
      const result = await adapter.autoWriteChapters(project.id, {
        chapters: saved.autoChapterCount,
        targetWords: saved.targetWords,
      });
      const generated = result.draftIds.length;
      setWritingMessage(
        saved.requireHumanAcceptance
          ? `已生成 ${generated} 章并暂停，前往“阅读”页审批`
          : `自动写作完成，共生成并确认 ${generated} 章`,
      );
      await refreshPlan();
    } catch {
      setWritingMessage('自动写作启动失败，请检查是否已有待审批草稿');
    } finally {
      setWritingBusy('');
    }
  };

  if (project.status === 'seeding') return <SeedingGate />;
  if (!plan) return <Empty>载入大纲中…</Empty>;
  if (!plan.planned)
    return (
      <Empty>
        此项目使用旧机制，没有大纲层。<br />
        新建项目即可启用「大纲驱动」：三层大纲（部分→小部分→逐章计划）、探索驱动揭示链、按章出场人物与物品库存。
      </Empty>
    );

  // 续写项目：多级大纲（Parts → Arcs → Chapters，C3 预生成）
  if ((plan as any).continuation) {
    const parts = ((plan as any).parts ?? []).slice().sort((a: any, b: any) => a.sequenceOrder - b.sequenceOrder);
    const arcsAll = ((plan as any).arcs ?? []) as any[];
    const chs = (plan.chapters ?? []).slice().sort((a: any, b: any) => a.sequenceOrder - b.sequenceOrder);
    const chaptersByArc = (arcId: string) => chs.filter((c: any) => c.arcId === arcId || c.arc_id === arcId);
    const arcsByPart = (partId: string) => arcsAll
      .filter((a: any) => a.partId === partId)
      .sort((a: any, b: any) => a.sequenceOrder - b.sequenceOrder);

    // 计数
    const totalChs = chs.length;
    const writtenChs = chs.filter((c: any) => c.written).length;

    return (
      <div className="space-y-4">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <BookMarked className="h-5 w-5 text-indigo-400" /> 续写全书大纲
          <span className="ml-2 text-xs font-normal text-zinc-500">
            {parts.length} 部 · {arcsAll.length} 小部 · {totalChs} 章（已写 {writtenChs}）
          </span>
        </h2>
        {parts.length === 0 && totalChs === 0 && (
          <Empty>暂无预生成大纲。请先完成蒸馏锁定（蒸馏 B5 会写入大纲）。</Empty>
        )}
        {parts.length === 0 && totalChs > 0 && (
          /* 兼容旧扁平模式 */
          <div className="space-y-2">
            {chs.map((c: any) => (
              <ContinuationChapterCard key={c.chapterId} c={c} />
            ))}
          </div>
        )}
        {parts.map((part: any) => {
          const partArcs = arcsByPart(part.partId);
          return (
            <section key={part.partId} className="panel p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">第{part.sequenceOrder}部 · {part.title}</span>
                {part.region && <span className="chip bg-sky-500/15 text-sky-400">地域：{part.region}</span>}
                <span className="chip bg-zinc-500/15 text-zinc-400">{partArcs.length} 小部</span>
              </div>
              {part.goal && <p className="mt-1 text-xs text-zinc-500">目标：{part.goal}</p>}
              {partArcs.map((arc: any) => {
                const arcChs = chaptersByArc(arc.arcId).sort((a: any, b: any) => a.sequenceOrder - b.sequenceOrder);
                return (
                  <div key={arc.arcId} className="mt-3 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{arc.title || `小部分 ${arc.sequenceOrder}`}</span>
                      <span className="chip bg-zinc-500/15 text-zinc-400">{arcChs.length}/{arc.targetChapters} 章</span>
                      {(arc.focusAgents ?? []).length > 0 && (
                        <span className="text-xs text-zinc-500">
                          焦点：{arc.focusAgents.map((f: any) => f.name).filter(Boolean).join('、')}
                        </span>
                      )}
                    </div>
                    {arc.summary && <p className="mt-1 text-xs text-zinc-500">{arc.summary}</p>}
                    <ol className="mt-2 space-y-1.5">
                      {arcChs.map((c: any) => (
                        <li key={c.chapterId}><ContinuationChapterCard c={c} compact /></li>
                      ))}
                    </ol>
                  </div>
                );
              })}
            </section>
          );
        })}
        {((plan as any).storyArcsOriginal ?? plan.storyArcs ?? []).length > 0 && (
          <div className="panel p-3">
            <div className="mb-1 text-sm font-semibold">原作剧情主线（蒸馏结果·供参考）</div>
            <ul className="space-y-1 text-xs">
              {(((plan as any).storyArcsOriginal ?? plan.storyArcs) ?? []).map((a: any) => (
                <li key={a.arc_id}>
                  <span className="font-medium">{a.name}</span>
                  <span className="ml-2 text-zinc-500">[{a.resolution_status ?? a.resolution}]</span>
                  {(a.journey_summary || a.journey) && <span className="ml-2 text-zinc-600 dark:text-zinc-400">{a.journey_summary ?? a.journey}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  const chaptersByArc = (arcId: string): PlanChapter[] =>
    plan.chapters.filter((c: any) => c.arcId === arcId).sort((a: any, b: any) => a.sequenceOrder - b.sequenceOrder);

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_320px]">
      {/* 左：大纲树 */}
      <div className="space-y-5">
        <h2 className="flex items-center gap-2 text-lg font-semibold">
          <BookMarked className="h-5 w-5 text-indigo-400" /> 故事大纲
        </h2>
        {plan.parts.map((part) => {
          const arcs = plan.arcs.filter((a) => a.partId === part.partId).sort((a, b) => a.sequenceOrder - b.sequenceOrder);
          return (
            <section key={part.partId} className="panel p-4">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold">第{part.sequenceOrder}部 · {part.title}</span>
                <span className={`chip ${STATUS_CHIP[part.status]}`}>{STATUS_LABEL[part.status]}</span>
                {part.region && <span className="chip bg-sky-500/15 text-sky-400">地域：{part.region}</span>}
              </div>
              {part.goal && <p className="mt-1 text-xs text-zinc-500">目标：{part.goal}</p>}
              {(plan.locations ?? []).filter((l) => l.partId === part.partId).length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {(plan.locations ?? [])
                    .filter((l) => l.partId === part.partId)
                    .map((l) => (
                      <span
                        key={l.locId}
                        title={(l.summary || l.geoFull) + (l.controllingFaction ? `（${l.controllingFaction}）` : '')}
                        className="chip bg-teal-500/15 text-teal-400"
                      >
                        {l.name}
                      </span>
                    ))}
                </div>
              )}

              {arcs.length === 0 && <p className="mt-3 text-xs text-zinc-400">（本部分细纲尚未滚动生成）</p>}
              {arcs.map((arc) => (
                <div key={arc.arcId} className="mt-3 rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{arc.title || `小部分 ${arc.sequenceOrder}`}</span>
                    <span className={`chip ${STATUS_CHIP[arc.status]}`}>{STATUS_LABEL[arc.status]}</span>
                    <span className="chip bg-zinc-500/15 text-zinc-400">目标 {arc.targetChapters} 章</span>
                    {arc.focusAgents?.length > 0 && (
                      <span className="text-xs text-zinc-500">
                        焦点：{arc.focusAgents.map((f: any) => `${nameOf(f.agentId)}(${f.weight})`).join('、')}
                      </span>
                    )}
                  </div>
                  {arc.summary && <p className="mt-1 text-xs text-zinc-500">{arc.summary}</p>}
                  <ol className="mt-2 space-y-1.5">
                    {chaptersByArc(arc.arcId).map((c) => (
                      <li key={c.chapterId} className="rounded-md bg-zinc-50 px-2.5 py-1.5 text-xs dark:bg-zinc-900/60">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium text-zinc-700 dark:text-zinc-200">
                            第{c.sequenceOrder}章{c.title ? ` · ${c.title}` : ''}
                          </span>
                          {c.role && <span className={`chip ${ROLE_CHIP[c.role]}`}>{ROLE_LABEL[c.role]}</span>}
                          <span className={`chip ${STATUS_CHIP[c.status]}`}>{STATUS_LABEL[c.status]}</span>
                          {c.conflictType && <span className="chip bg-fuchsia-500/15 text-fuchsia-400">{c.conflictType}</span>}
                          {c.castNames.length > 0 && (
                            <span className="text-zinc-500">出场：{c.castNames.join('、')}</span>
                          )}
                          {c.revealGate.length > 0 && (
                            <span className="chip bg-amber-500/15 text-amber-500">含揭示</span>
                          )}
                          {(c.threadDecisions ?? []).slice(0, 4).map((d: any) => (
                            <span
                              key={`${c.chapterId}-${d.threadId}-${d.decision}`}
                              className={`chip ${THREAD_DECISION_CHIP[d.decision] ?? THREAD_DECISION_CHIP.hide}`}
                              title={`${d.question || ''}${d.reason ? `｜${d.reason}` : ''}`}
                            >
                              {THREAD_DECISION_LABEL[d.decision] ?? d.decision}
                            </span>
                          ))}
                          {c.provisional && (
                            <span className="chip bg-zinc-500/15 text-zinc-400" title="尚未演到，演到时会按已发生的事实复核">预演稿</span>
                          )}
                          {/* 编辑/删除：已写完的不能改（只能删），未写的可改可删 */}
                          <span className="ml-auto flex items-center gap-1">
                            {c.written ? (
                              <span className="chip bg-zinc-500/15 text-zinc-400" title="已写完，不能修改；如不满意可删除后重写">
                                <Lock className="mr-0.5 inline h-3 w-3" />已锁定
                              </span>
                            ) : (
                              <>
                                <button onClick={() => replanChapter(c)} disabled={busy}
                                  className="rounded p-1 text-zinc-400 hover:bg-zinc-200 hover:text-amber-500 disabled:opacity-40 dark:hover:bg-zinc-800"
                                  title="按当前安全上下文重新规划本章">
                                  <RotateCcw className="h-3.5 w-3.5" />
                                </button>
                                <button onClick={() => (editId === c.chapterId ? setEditId(null) : startEdit(c))}
                                  className="rounded p-1 text-zinc-400 hover:bg-zinc-200 hover:text-indigo-500 dark:hover:bg-zinc-800"
                                  title="编辑本章大纲">
                                  <Pencil className="h-3.5 w-3.5" />
                                </button>
                              </>
                            )}
                            <button onClick={() => delChapter(c)} disabled={busy}
                              className="rounded p-1 text-zinc-400 hover:bg-zinc-200 hover:text-rose-500 disabled:opacity-40 dark:hover:bg-zinc-800"
                              title="删除本章（含正文）">
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </span>
                        </div>
                        {editId === c.chapterId ? (
                          <div className="mt-2 space-y-1.5 rounded-md border border-indigo-300/40 bg-white p-2 dark:bg-zinc-900">
                            {([
                              ['title', '标题', false], ['conflictType', '冲突类型', false],
                              ['dramaticQuestion', '戏剧问题', false], ['exitState', '推进目标', false],
                              ['castNames', '出场人物（、分隔，没有的会自动新建）', false],
                              ['itemNames', '在场道具（、分隔，没有的会自动新建）', false],
                              ['locationName', '地点', false], ['beatGoals', '节拍（每行一个）', true],
                            ] as [string, string, boolean][]).map(([k, label, multi]) => (
                              <label key={k} className="block">
                                <span className="text-[11px] text-zinc-500">{label}</span>
                                {multi ? (
                                  <textarea value={draft[k] ?? ''} rows={3}
                                    onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                                    className="w-full rounded border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700" />
                                ) : (
                                  <input value={draft[k] ?? ''}
                                    onChange={(e) => setDraft({ ...draft, [k]: e.target.value })}
                                    className="w-full rounded border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700" />
                                )}
                              </label>
                            ))}
                            <div className="flex gap-2 pt-0.5">
                              <button onClick={() => saveEdit(c.chapterId)} disabled={busy}
                                className="rounded bg-indigo-500 px-3 py-1 text-xs text-white disabled:opacity-50">保存</button>
                              <button onClick={() => setEditId(null)}
                                className="rounded border border-zinc-300 px-3 py-1 text-xs dark:border-zinc-700">取消</button>
                            </div>
                          </div>
                        ) : (
                          <div className="mt-1 space-y-1">
                            {c.dramaticQuestion && (
                              <div className="text-indigo-400">戏剧问题：{c.dramaticQuestion}</div>
                            )}
                            {c.exitState && (
                              <div className="text-emerald-500/90">推进目标：{c.exitState}</div>
                            )}
                            {(c.locationName || c.itemsPresentNames?.length) ? (
                              <div className="text-zinc-500">
                                {c.locationName && <span>地点：{c.locationName}　</span>}
                                {c.itemsPresentNames?.length ? <span>道具：{c.itemsPresentNames.join('、')}</span> : null}
                              </div>
                            ) : null}
                            {c.beatGoals.length > 0 && (
                              <div>
                                <div className="text-zinc-500">节拍（{c.beatGoals.length}）：</div>
                                <ol className="ml-3 list-decimal space-y-0.5 text-zinc-600 dark:text-zinc-300">
                                  {c.beatGoals.map((b, i) => (
                                    <li key={i} className="leading-relaxed">
                                      {c.beatPovNames?.[i] && (
                                        <span className="mr-1 rounded bg-sky-500/15 px-1 text-[10px] text-sky-500">视角:{c.beatPovNames[i]}</span>
                                      )}
                                      {b}
                                    </li>
                                  ))}
                                </ol>
                              </div>
                            )}
                            {c.endingHook && (
                              <div className="text-amber-500/90">钩子：{c.endingHook}</div>
                            )}
                          </div>
                        )}
                      </li>
                    ))}
                  </ol>
                  {chaptersByArc(arc.arcId).length < arc.targetChapters && (
                    <div className="mt-1.5 text-[11px] text-zinc-400">
                      其余 {arc.targetChapters - chaptersByArc(arc.arcId).length} 章将随剧情展开
                    </div>
                  )}
                </div>
              ))}
            </section>
          );
        })}
      </div>

      {/* 右：文风契约 + 揭示链 + 库存 + 人物档案 */}
      <div className="space-y-5">
        {writingSettings && (
          <section className="panel p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <Settings2 className="h-4 w-4 text-cyan-400" /> 写作设置
            </h3>
            <div className="mt-3 space-y-3 text-sm">
              <label className="block">
                <div className="mb-1 text-zinc-500">每章目标字数</div>
                <input
                  className="input"
                  type="number"
                  min={500}
                  step={100}
                  value={writingSettings.targetWords}
                  onChange={(e) => setWritingSettings({
                    ...writingSettings,
                    targetWords: Math.max(500, Number(e.target.value) || 500),
                  })}
                />
              </label>
              <label className="block">
                <div className="mb-1 text-zinc-500">自动连写章数</div>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={50}
                  value={writingSettings.autoChapterCount}
                  onChange={(e) => setWritingSettings({
                    ...writingSettings,
                    autoChapterCount: Math.min(50, Math.max(1, Number(e.target.value) || 1)),
                  })}
                />
              </label>
              <label className="flex items-start gap-2 text-zinc-600 dark:text-zinc-300">
                <input
                  className="mt-0.5"
                  type="checkbox"
                  checked={writingSettings.requireHumanAcceptance}
                  onChange={(e) => setWritingSettings({
                    ...writingSettings,
                    requireHumanAcceptance: e.target.checked,
                  })}
                />
                <span>
                  每章都需要人工审批
                  <span className="mt-0.5 block text-xs text-zinc-500">
                    开启后每写一章就暂停；关闭后按设定章数连续生成并自动确认。
                  </span>
                </span>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  className="btn-ghost border border-zinc-200 dark:border-zinc-800"
                  onClick={saveWritingSettings}
                  disabled={writingBusy !== ''}
                >
                  {writingBusy === 'save' ? '保存中…' : '保存设置'}
                </button>
                <button
                  className="btn-primary"
                  onClick={startAutoWriting}
                  disabled={writingBusy !== ''}
                >
                  <Play className="h-4 w-4" />
                  {writingBusy === 'auto' ? '写作中…' : '开始自动写'}
                </button>
              </div>
              {writingMessage && (
                <p className="text-xs leading-5 text-zinc-500">{writingMessage}</p>
              )}
            </div>
          </section>
        )}
        {plan.toneProfile && (
          <section className="panel p-4">
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <Palette className="h-4 w-4 text-fuchsia-400" /> 文风契约
              {plan.toneProfile.confirmed ? (
                <span className="chip bg-emerald-500/15 text-emerald-400">已确认</span>
              ) : (
                <span className="chip bg-amber-500/15 text-amber-500">待确认</span>
              )}
            </h3>
            <dl className="mt-3 space-y-1 text-xs">
              <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">类型</dt><dd>{plan.toneProfile.genre || '—'}</dd></div>
              <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">主效果</dt><dd className="text-fuchsia-400">{plan.toneProfile.primaryEffect || '—'}</dd></div>
              <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">语域节奏</dt><dd>{[plan.toneProfile.register, plan.toneProfile.sentenceRhythm].filter(Boolean).join('，') || '—'}</dd></div>
              {plan.toneProfile.dictionDont.length > 0 && (
                <div className="flex gap-2"><dt className="w-16 shrink-0 text-zinc-400">禁忌</dt><dd className="text-rose-400">{plan.toneProfile.dictionDont.join('、')}</dd></div>
              )}
              {plan.toneProfile.toneReference && (
                <div className="mt-1 rounded bg-zinc-50 p-2 italic text-zinc-500 dark:bg-zinc-900/60">{plan.toneProfile.toneReference}</div>
              )}
            </dl>
            {/* B0.6 时代隔离墙 */}
            {(() => {
              const era = plan.toneProfile.eraLogic || {};
              const confirmed = plan.toneProfile.confirmed;
              return (
                <div className="mt-3 rounded border border-zinc-200 p-2 text-xs dark:border-zinc-800">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-zinc-600 dark:text-zinc-300">时代隔离墙</span>
                    {confirmed ? (
                      <span className={`chip ${era.enabled ? 'bg-amber-500/15 text-amber-500' : 'bg-zinc-500/15 text-zinc-400'}`}>
                        {era.enabled ? '已开启' : '关闭'}
                      </span>
                    ) : (
                      <label className="flex cursor-pointer items-center gap-1 text-zinc-500">
                        <input type="checkbox" checked={!!era.enabled} disabled={toneBusy} onChange={(e) => toggleEra(e.target.checked)} />
                        开启（古代/奇幻设定防现代腔）
                      </label>
                    )}
                  </div>
                  {era.enabled && (
                    <div className="mt-1.5 space-y-1 text-zinc-500">
                      <div>道德 {era.moral_index ?? '—'} · 宗教狂热 {era.religiosity ?? '—'} · 科学认知 {era.science_level ?? '—'}</div>
                      {!!era.banned_modern_words?.length && (
                        <div>禁用现代词：<span className="text-rose-400">{era.banned_modern_words.slice(0, 12).join('、')}</span></div>
                      )}
                      {era.forced_attribution && <div className="italic">归因逻辑：{era.forced_attribution}</div>}
                    </div>
                  )}
                </div>
              );
            })()}
            {!plan.toneProfile.confirmed && (
              <button
                onClick={confirmTone}
                disabled={toneBusy}
                className="mt-3 w-full rounded-md bg-fuchsia-500/15 py-1.5 text-xs font-medium text-fuchsia-400 hover:bg-fuchsia-500/25 disabled:opacity-50"
              >
                {toneBusy ? '处理中…' : '确认基调（锁定，全程不变）'}
              </button>
            )}
          </section>
        )}

        <StyleSkillPanel projectId={project.id} />
        <AuthorSheetPanel projectId={project.id} />


        <section className="panel p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <KeyRound className="h-4 w-4 text-amber-400" /> 揭示链（探索驱动）
          </h3>
          <ul className="mt-3 space-y-2">
            {plan.revealChain.map((n) => (
              <li key={n.nodeId} className="flex items-start gap-2 text-xs">
                {n.discovered ? (
                  <Unlock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                ) : (
                  <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-zinc-400" />
                )}
                <div>
                  <span className={n.kind === 'truth' ? 'font-medium text-rose-400' : 'text-zinc-600 dark:text-zinc-300'}>
                    {n.kind === 'truth' ? '【真相】' : '【线索】'}
                  </span>
                  <span className="text-zinc-600 dark:text-zinc-300"> {n.description}</span>
                  {n.discovered && n.discoveredChapter != null && (
                    <span className="ml-1 text-emerald-500">（第{n.discoveredChapter}章揭开）</span>
                  )}
                </div>
              </li>
            ))}
            {plan.revealChain.length === 0 && <li className="text-xs text-zinc-400">（无）</li>}
          </ul>
        </section>

        <section className="panel p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <Boxes className="h-4 w-4 text-sky-400" /> 物品库存
          </h3>
          <ul className="mt-3 space-y-1.5">
            {plan.inventory.map((it) => (
              <li key={it.objectId} className="flex items-center justify-between text-xs">
                <span className="text-zinc-700 dark:text-zinc-200">{it.name}</span>
                <span className="text-zinc-500">
                  {it.status === 'lost' ? '已消失' : `${nameOf(it.holderAgentId)} 持有`}
                </span>
              </li>
            ))}
            {plan.inventory.length === 0 && <li className="text-xs text-zinc-400">（无）</li>}
          </ul>
        </section>

        <section className="panel p-4">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <UserRound className="h-4 w-4 text-violet-400" /> 人物档案
          </h3>
          <div className="mt-3 flex flex-wrap gap-2">
            {personas.map((p) => (
              <button
                key={p.id}
                onClick={() => openDossier(p.id)}
                className="chip bg-zinc-500/15 text-zinc-500 hover:bg-violet-500/15 hover:text-violet-400"
              >
                {p.displayName || p.name}
              </button>
            ))}
          </div>
        </section>
      </div>

      {/* 人物档案抽屉 */}
      {dossierFor && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={() => setDossierFor(null)}>
          <div
            className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-xl dark:bg-zinc-950"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <span className="flex items-center gap-2 text-sm font-semibold">
                <UserRound className="h-4 w-4 text-violet-400" /> 角色档案 · {nameOf(dossierFor)}
              </span>
              <button onClick={() => setDossierFor(null)} className="rounded p-1 hover:bg-zinc-100 dark:hover:bg-zinc-800">
                <X className="h-4 w-4" />
              </button>
            </div>
            <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
              {dossierMd}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function ContinuationChapterCard({ c, compact }: { c: any; compact?: boolean }) {
  return (
    <div className={compact ? '' : 'panel p-3'}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold">第{c.sequenceOrder}章{c.title ? ` · ${c.title}` : ''}</span>
        {c.written && <span className="chip bg-emerald-500/20 text-emerald-500">已写</span>}
        {c.povName && <span className="chip bg-indigo-500/15 text-indigo-400">POV：{c.povName}</span>}
        {c.locationName && <span className="chip bg-teal-500/15 text-teal-400">{c.locationName}</span>}
        {(c.castNames ?? []).length > 0 && (
          <span className="chip bg-zinc-500/15 text-zinc-400">{(c.castNames ?? []).length} 人</span>
        )}
      </div>
      {c.dramaticQuestion && <p className="mt-1 text-xs text-amber-500">戏剧问题：{c.dramaticQuestion}</p>}
      {c.exitState && <p className="mt-1 text-xs text-zinc-500">章末出口：{c.exitState}</p>}
      <ThreadDecisionStrip decisions={c.threadDecisions ?? []} compact={compact} />
      {(c.beatGoals ?? []).length > 0 && (
        <ol className="mt-1.5 space-y-0.5 pl-4 text-xs text-zinc-600 dark:text-zinc-400" style={{ listStyle: 'decimal' }}>
          {c.beatGoals.map((b: string, i: number) => <li key={i}>{b}</li>)}
        </ol>
      )}
      {(c.castNames ?? []).length > 0 && (
        <p className="mt-1 text-[11px] text-zinc-500">出场：{c.castNames.join('、')}</p>
      )}
    </div>
  );
}

function ThreadDecisionStrip({ decisions, compact }: { decisions: any[]; compact?: boolean }) {
  const list = (decisions ?? []).filter(Boolean);
  if (list.length === 0) return null;
  const shown = list.slice(0, compact ? 3 : 5);
  const count = (kind: string) => list.filter((d) => d.decision === kind).length;

  return (
    <div className={`${compact ? 'mt-1' : 'mt-2'} space-y-1 text-[11px]`}>
      <div className="flex flex-wrap items-center gap-1.5 text-zinc-500">
        <span className="font-medium text-zinc-600 dark:text-zinc-300">悬念调度</span>
        {(['reveal', 'hint', 'hide'] as const).map((kind) => {
          const n = count(kind);
          if (!n) return null;
          return (
            <span key={kind} className={`chip ${THREAD_DECISION_CHIP[kind]}`}>
              {THREAD_DECISION_LABEL[kind]} {n}
            </span>
          );
        })}
      </div>
      <ul className="space-y-0.5 text-zinc-600 dark:text-zinc-400">
        {shown.map((d, i) => (
          <li key={`${d.threadId ?? i}-${d.decision ?? 'hide'}`} className="flex gap-1.5 leading-relaxed">
            <span className={`mt-0.5 h-4 shrink-0 rounded px-1 text-[10px] leading-4 ${THREAD_DECISION_CHIP[d.decision] ?? THREAD_DECISION_CHIP.hide}`}>
              {THREAD_DECISION_LABEL[d.decision] ?? d.decision ?? '藏'}
            </span>
            <span className="min-w-0">
              <span className="text-zinc-700 dark:text-zinc-300">{d.question || '未命名悬念'}</span>
              {d.reason && <span className="text-zinc-500">｜{d.reason}</span>}
            </span>
          </li>
        ))}
        {list.length > shown.length && (
          <li className="text-zinc-400">另有 {list.length - shown.length} 条隐藏调度</li>
        )}
      </ul>
    </div>
  );
}
