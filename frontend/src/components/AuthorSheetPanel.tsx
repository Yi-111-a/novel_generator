// S1 Author Writing Sheet 面板：AWS 蒸馏（上传原文→四维Claim-Evidence）+ 画像查看 + 列表管理。
import { BookOpen, FileText, Sparkles, Trash2, Upload } from 'lucide-react';
import { useEffect, useState } from 'react';
import { getAdapter } from '../adapters';
import { Panel } from './ui';
import type { AuthorSheetListItem, AuthorWritingSheet, StyleClaim } from '../types';

const DIM_META: { key: keyof Pick<AuthorWritingSheet, 'plot' | 'creativity' | 'development' | 'language'>; label: string; color: string }[] = [
  { key: 'language', label: '语言运用', color: 'text-blue-400' },
  { key: 'creativity', label: '创意手法', color: 'text-amber-400' },
  { key: 'plot', label: '情节结构', color: 'text-emerald-400' },
  { key: 'development', label: '发展节奏', color: 'text-purple-400' },
];

function ClaimList({ claims, color }: { claims: StyleClaim[]; color: string }) {
  if (!claims.length) return <span className="text-zinc-500 italic">（无）</span>;
  return (
    <ul className="space-y-1">
      {claims.map((c, i) => (
        <li key={i} className="rounded bg-zinc-50 p-1.5 dark:bg-zinc-900/60">
          <span className={`font-medium ${color}`}>{c.claim}</span>
          {c.evidence && (
            <span className="ml-1.5 text-zinc-400 italic">「{c.evidence}」</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function SheetViewer({ sheet }: { sheet: AuthorWritingSheet }) {
  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center gap-2">
        <BookOpen className="h-4 w-4 text-fuchsia-400" />
        <span className="font-medium text-zinc-700 dark:text-zinc-200">
          {sheet.name || '未命名'}{sheet.sourceGenre && <span className="text-zinc-400">（{sheet.sourceGenre}）</span>}
        </span>
        <span className="chip bg-fuchsia-500/15 text-fuchsia-400">{sheet.nSegments} 片段</span>
      </div>
      {DIM_META.map((d) => {
        const claims = sheet[d.key] as StyleClaim[];
        if (!claims?.length) return null;
        return (
          <details key={d.key} open={d.key === 'language' || d.key === 'creativity'}>
            <summary className={`cursor-pointer select-none font-medium ${d.color}`}>
              {d.label}（{claims.length}）
            </summary>
            <div className="mt-1">
              <ClaimList claims={claims} color={d.color} />
            </div>
          </details>
        );
      })}
      {sheet.personaMd && (
        <details>
          <summary className="cursor-pointer select-none text-zinc-400">Persona（注入 prompt）</summary>
          <pre className="mt-1 whitespace-pre-wrap rounded bg-zinc-50 p-2 text-[11px] dark:bg-zinc-900/60">{sheet.personaMd}</pre>
        </details>
      )}
    </div>
  );
}

export function AuthorSheetPanel({ projectId }: { projectId: string }) {
  const adapter = getAdapter();
  const [sheets, setSheets] = useState<AuthorSheetListItem[]>([]);
  const [activeSheet, setActiveSheet] = useState<AuthorWritingSheet | null>(null);
  const [text, setText] = useState('');
  const [name, setName] = useState('');
  const [genre, setGenre] = useState('');
  const [busy, setBusy] = useState(false);
  const [showUpload, setShowUpload] = useState(false);

  const pullList = () => adapter.listAuthorSheets(projectId).then(setSheets).catch(() => {});
  useEffect(() => { pullList(); }, [projectId]); // eslint-disable-line

  const viewSheet = async (id: number) => {
    try {
      const s = await adapter.getAuthorSheet(projectId, id);
      setActiveSheet(s);
    } catch { /* ignore */ }
  };

  const distill = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const res = await adapter.distillAuthorSheet(projectId, text, name, genre);
      if (res.ok && res.sheet) {
        setActiveSheet(res.sheet);
        setText('');
        setName('');
        setShowUpload(false);
        pullList();
      }
    } catch { /* ignore */ } finally {
      setBusy(false);
    }
  };

  const deleteSheet = async (id: number) => {
    if (!window.confirm('删除此文风画像？')) return;
    await adapter.deleteAuthorSheet(projectId, id);
    if (activeSheet) setActiveSheet(null);
    pullList();
  };

  return (
    <Panel
      title={
        <span className="flex items-center gap-1.5">
          <FileText className="h-4 w-4 text-fuchsia-400" /> 作者文风画像
          {sheets.length > 0 && (
            <span className="chip bg-fuchsia-500/15 text-fuchsia-400">{sheets.length}</span>
          )}
        </span>
      }
    >
      <div className="space-y-3 text-xs">
        {/* 已有画像列表 */}
        {sheets.length > 0 && !showUpload && (
          <div className="space-y-1">
            {sheets.map((s) => (
              <div key={s.id} className="flex items-center justify-between rounded border border-zinc-200 p-1.5 dark:border-zinc-700">
                <button onClick={() => viewSheet(s.id)} className="text-left hover:text-fuchsia-500">
                  {s.name || '未命名'}<span className="ml-1 text-zinc-400">{s.sourceGenre}</span>
                </button>
                <button onClick={() => deleteSheet(s.id)} className="text-rose-400 hover:text-rose-600">
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* 查看选中画像 */}
        {activeSheet && <SheetViewer sheet={activeSheet} />}

        {/* 上传区 */}
        {showUpload ? (
          <div className="space-y-2">
            <div className="flex gap-1.5">
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="风格名（选填）"
                className="w-1/2 rounded border border-zinc-300 px-2 py-1 dark:border-zinc-700 dark:bg-zinc-900" />
              <select value={genre} onChange={(e) => setGenre(e.target.value)}
                className="w-1/2 rounded border border-zinc-300 px-2 py-1 dark:border-zinc-700 dark:bg-zinc-900">
                <option value="">体裁（自动）</option>
                <option value="严肃文学">严肃文学</option>
                <option value="起点爽文">起点爽文</option>
                <option value="网文言情">网文言情</option>
                <option value="悬疑推理">悬疑推理</option>
              </select>
            </div>
            <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6}
              placeholder="粘贴作品原文（几千字效果更好——会自动切片、对照提取、增量合并）…"
              className="w-full resize-y rounded border border-zinc-300 p-2 dark:border-zinc-700 dark:bg-zinc-900" />
            <div className="flex gap-1.5">
              <button onClick={distill} disabled={!text.trim() || busy}
                className="flex flex-1 items-center justify-center gap-1.5 rounded bg-fuchsia-600 px-3 py-1.5 font-medium text-white hover:bg-fuchsia-500 disabled:opacity-50">
                {busy ? <><Sparkles className="h-3.5 w-3.5 animate-pulse" /> AWS 蒸馏中…</> : <><Upload className="h-3.5 w-3.5" /> 蒸馏文风画像</>}
              </button>
              <button onClick={() => setShowUpload(false)}
                className="rounded border border-zinc-300 px-3 py-1.5 dark:border-zinc-700">取消</button>
            </div>
          </div>
        ) : (
          <button onClick={() => setShowUpload(true)}
            className="flex w-full items-center justify-center gap-1.5 rounded border border-dashed border-fuchsia-400 px-3 py-2 text-fuchsia-500 hover:bg-fuchsia-500/5">
            <Upload className="h-3.5 w-3.5" /> 上传作品蒸馏文风画像
          </button>
        )}
      </div>
    </Panel>
  );
}
