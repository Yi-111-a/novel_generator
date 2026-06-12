// B0 文风模拟面板（可复用）：上传作品原文 or 现成文风 → 蒸馏指纹；展示 6 维指纹；启停/删除。
// 自取数（getStyleSkill），可塞进任意带 projectId 的页面（种子工坊=锁定前；大纲页=锁定后）。
import { Palette, Sparkles, Trash2, Upload } from 'lucide-react';
import { useEffect, useState } from 'react';
import { getAdapter } from '../adapters';
import { Panel } from './ui';
import type { StyleSkill } from '../types';

const METRIC_LABELS: { key: keyof NonNullable<StyleSkill['metrics']>; label: string; hint: string }[] = [
  { key: 'avg_sentence_len', label: '平均句长', hint: '越小越简练（海明威低）' },
  { key: 'sentence_len_variance', label: '句长方差', hint: '长短句错落程度' },
  { key: 'punct_density', label: '标点密度', hint: '每千字标点数' },
  { key: 'ttr', label: '词汇丰富度', hint: 'TTR，越高用词越不重复' },
  { key: 'content_function_ratio', label: '实虚词比', hint: '越高信息越密' },
  { key: 'semantic_leaping', label: '语义跳跃', hint: '相邻句跳跃度（意识流高）' },
  { key: 'sensory_e', label: '感官比 E', hint: '感官/抽象情感，>3.5 易共鸣' },
];

export function StyleSkillPanel({ projectId }: { projectId: string }) {
  const adapter = getAdapter();
  const [skill, setSkill] = useState<StyleSkill | null>(null);
  const [mode, setMode] = useState<'works' | 'skill'>('works');
  const [text, setText] = useState('');
  const [name, setName] = useState('');
  const [source, setSource] = useState('');
  const [busy, setBusy] = useState(false);

  const pull = () => adapter.getStyleSkill(projectId).then(setSkill).catch(() => {});
  useEffect(() => {
    pull();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const ingest = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const res = await adapter.ingestStyleSkill(projectId, mode, text, name, source);
      if (res.styleSkill) setSkill(res.styleSkill);
      setText('');
    } catch {
      /* 失败保持原状 */
    } finally {
      setBusy(false);
    }
  };
  const toggle = async () => {
    if (!skill) return;
    const res = await adapter.setStyleEnabled(projectId, !skill.enabled);
    setSkill(res.styleSkill ?? null);
  };
  const remove = async () => {
    if (!skill || !window.confirm('删除文风模拟？将回落到题材基线文风。')) return;
    await adapter.deleteStyleSkill(projectId);
    setSkill(null);
  };

  return (
    <Panel
      title={
        <span className="flex items-center gap-1.5">
          <Palette className="h-4 w-4 text-fuchsia-400" /> 文风模拟
          {skill && (
            <span className={`chip ${skill.enabled ? 'bg-fuchsia-500/15 text-fuchsia-400' : 'bg-zinc-500/15 text-zinc-400'}`}>
              {skill.enabled ? '已启用' : '已停用'}
            </span>
          )}
        </span>
      }
    >
      {skill ? (
        <div className="space-y-3 text-xs">
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="font-medium text-zinc-700 dark:text-zinc-200">{skill.name || '未命名文风'}{skill.source && <span className="text-zinc-400">（{skill.source}）</span>}</div>
              <div className="text-zinc-500">{[skill.register, skill.rhythm].filter(Boolean).join('，') || '—'}</div>
            </div>
            <div className="flex shrink-0 gap-1.5">
              <button onClick={toggle} className="rounded border border-zinc-300 px-2 py-1 text-[11px] hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800">
                {skill.enabled ? '停用' : '启用'}
              </button>
              <button onClick={remove} className="rounded border border-rose-300 px-2 py-1 text-[11px] text-rose-500 hover:bg-rose-50 dark:border-rose-900 dark:hover:bg-rose-950/40">
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          </div>
          {skill.devices.length > 0 && (
            <div className="text-zinc-500">手法：{skill.devices.slice(0, 6).join('、')}</div>
          )}
          {/* 6 维指纹 */}
          <div className="grid grid-cols-2 gap-x-3 gap-y-1">
            {METRIC_LABELS.map((m) => {
              const v = skill.metrics?.[m.key];
              return (
                <div key={m.key} className="flex justify-between gap-2" title={m.hint}>
                  <span className="text-zinc-400">{m.label}</span>
                  <span className="font-mono text-fuchsia-500">{v == null ? '—' : v}</span>
                </div>
              );
            })}
          </div>
          {skill.samples.length > 0 && (
            <details className="text-zinc-500">
              <summary className="cursor-pointer select-none">风格样例（仅供模仿腔调）</summary>
              <ul className="mt-1 space-y-1">
                {skill.samples.slice(0, 3).map((s, i) => (
                  <li key={i} className="rounded bg-zinc-50 p-1.5 italic dark:bg-zinc-900/60">{s}</li>
                ))}
              </ul>
            </details>
          )}
          <button onClick={() => setSkill(null)} className="text-[11px] text-zinc-400 underline hover:text-zinc-600">换一个文风…</button>
        </div>
      ) : (
        <div className="space-y-2 text-xs">
          <p className="text-zinc-500">让正文按某种腔调写：粘贴一段<b>作品原文</b>自动蒸馏，或粘贴<b>现成文风描述</b>（SKILL.md）。只学腔调，不照搬内容。</p>
          <div className="flex gap-1.5">
            <button onClick={() => setMode('works')} className={`flex-1 rounded border px-2 py-1 ${mode === 'works' ? 'border-fuchsia-400 bg-fuchsia-500/10 text-fuchsia-500' : 'border-zinc-300 dark:border-zinc-700'}`}>上传作品原文</button>
            <button onClick={() => setMode('skill')} className={`flex-1 rounded border px-2 py-1 ${mode === 'skill' ? 'border-fuchsia-400 bg-fuchsia-500/10 text-fuchsia-500' : 'border-zinc-300 dark:border-zinc-700'}`}>现成文风</button>
          </div>
          {mode === 'works' && (
            <div className="flex gap-1.5">
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="风格名（选填）" className="w-1/2 rounded border border-zinc-300 px-2 py-1 dark:border-zinc-700 dark:bg-zinc-900" />
              <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="来源作品/作者（选填）" className="w-1/2 rounded border border-zinc-300 px-2 py-1 dark:border-zinc-700 dark:bg-zinc-900" />
            </div>
          )}
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            placeholder={mode === 'works' ? '粘贴一段你喜欢的作品原文（几百字即可）…' : '粘贴文风描述 / SKILL.md（语域、句法、手法、样例…）'}
            className="w-full resize-y rounded border border-zinc-300 p-2 dark:border-zinc-700 dark:bg-zinc-900"
          />
          <button
            onClick={ingest}
            disabled={!text.trim() || busy}
            className="flex w-full items-center justify-center gap-1.5 rounded bg-fuchsia-600 px-3 py-1.5 font-medium text-white hover:bg-fuchsia-500 disabled:opacity-50"
          >
            {busy ? <><Sparkles className="h-3.5 w-3.5 animate-pulse" /> 蒸馏中…</> : <><Upload className="h-3.5 w-3.5" /> 蒸馏文风</>}
          </button>
        </div>
      )}
    </Panel>
  );
}
