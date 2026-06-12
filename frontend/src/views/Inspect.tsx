import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getAdapter } from '../adapters';
import { KnowledgeGraph } from '../components/KnowledgeGraph';
import type { LLMLog, GraphData } from '../types';

/* ================================================================
   LLM 日志查看器
   ================================================================ */
function LogViewer({ projectId }: { projectId: string }) {
  const [logs, setLogs] = useState<LLMLog[]>([]);
  const [selected, setSelected] = useState<LLMLog | null>(null);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    const adapter = getAdapter() as any;
    if (!adapter?.getLLMLogs) return;
    adapter.getLLMLogs(projectId, 500, filter || undefined).then(setLogs).catch(() => setLogs([]));
  }, [projectId, filter]);

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <span style={{ fontWeight: 600 }}>LLM 对话日志</span>
        <select value={filter} onChange={e => setFilter(e.target.value)}
          style={{ padding: '4px 8px', borderRadius: 4, border: '1px solid #d4d4d4', fontSize: 13 }}>
          <option value="">全部 caller</option>
          <option value="default">default</option>
          <option value="seed">seed</option>
        </select>
        <span style={{ color: '#888', fontSize: 12 }}>{logs.length} 条</span>
      </div>
      <div style={{ display: 'flex', gap: 12 }}>
        <div style={{ width: 340, maxHeight: 500, overflowY: 'auto', border: '1px solid #e5e7eb', borderRadius: 8 }}>
          {logs.length === 0 && <div style={{ padding: 16, color: '#888', fontSize: 13 }}>暂无日志（需重新锁定项目后产生）</div>}
          {logs.map(l => (
            <div key={l.id}
              onClick={() => setSelected(l)}
              style={{
                padding: '8px 12px', borderBottom: '1px solid #f3f4f6', cursor: 'pointer',
                background: selected?.id === l.id ? '#eff6ff' : undefined,
                fontSize: 12,
              }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ fontWeight: 500 }}>#{l.id} {l.caller}</span>
                <span style={{ color: '#888' }}>{l.elapsed_ms}ms</span>
              </div>
              <div style={{ color: '#666', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 300 }}>
                {l.user_msg.slice(0, 80)}
              </div>
            </div>
          ))}
        </div>
        <div style={{ flex: 1, maxHeight: 500, overflowY: 'auto' }}>
          {selected ? (
            <div style={{ fontSize: 13 }}>
              <h4 style={{ margin: '0 0 8px' }}>#{selected.id} — {selected.caller} — {selected.elapsed_ms}ms — T={selected.temperature ?? 'default'}</h4>
              <details open>
                <summary style={{ cursor: 'pointer', fontWeight: 600, color: '#3b82f6' }}>System Prompt ({selected.system_msg.length} 字)</summary>
                <pre style={{ whiteSpace: 'pre-wrap', background: '#f8fafc', padding: 12, borderRadius: 6, maxHeight: 300, overflowY: 'auto', fontSize: 12, lineHeight: 1.5 }}>
                  {selected.system_msg}
                </pre>
              </details>
              <details open style={{ marginTop: 8 }}>
                <summary style={{ cursor: 'pointer', fontWeight: 600, color: '#10b981' }}>User Prompt ({selected.user_msg.length} 字)</summary>
                <pre style={{ whiteSpace: 'pre-wrap', background: '#f0fdf4', padding: 12, borderRadius: 6, maxHeight: 300, overflowY: 'auto', fontSize: 12, lineHeight: 1.5 }}>
                  {selected.user_msg}
                </pre>
              </details>
              <details open style={{ marginTop: 8 }}>
                <summary style={{ cursor: 'pointer', fontWeight: 600, color: '#f59e0b' }}>Response ({selected.response.length} 字)</summary>
                <pre style={{ whiteSpace: 'pre-wrap', background: '#fffbeb', padding: 12, borderRadius: 6, maxHeight: 300, overflowY: 'auto', fontSize: 12, lineHeight: 1.5 }}>
                  {selected.response}
                </pre>
              </details>
            </div>
          ) : (
            <div style={{ padding: 24, color: '#888', textAlign: 'center' }}>点击左侧日志条目查看详情</div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ================================================================
   主页面
   ================================================================ */
export function Inspect() {
  const { projectId } = useParams<{ projectId: string }>();
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [tab, setTab] = useState<'graph' | 'logs'>('graph');

  useEffect(() => {
    if (!projectId) return;
    const adapter = getAdapter() as any;
    if (!adapter?.getGraph) return;
    adapter.getGraph(projectId).then(setGraph).catch(() => setGraph(null));
  }, [projectId]);

  if (!projectId) return null;

  return (
    <div style={{ padding: 24 }}>
      <h2 style={{ marginTop: 0 }}>检视台</h2>
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        <button onClick={() => setTab('graph')}
          style={{ padding: '6px 16px', borderRadius: 6, border: 'none', cursor: 'pointer',
            background: tab === 'graph' ? '#3b82f6' : '#f3f4f6', color: tab === 'graph' ? '#fff' : '#1a1a1a', fontWeight: 500 }}>
          知识图谱
        </button>
        <button onClick={() => setTab('logs')}
          style={{ padding: '6px 16px', borderRadius: 6, border: 'none', cursor: 'pointer',
            background: tab === 'logs' ? '#3b82f6' : '#f3f4f6', color: tab === 'logs' ? '#fff' : '#1a1a1a', fontWeight: 500 }}>
          LLM 日志
        </button>
      </div>

      {tab === 'graph' && (
        graph && graph.nodes.length > 0 ? (
          <KnowledgeGraph data={graph} />
        ) : (
          <div style={{ padding: 24, color: '#888', border: '1px dashed #d4d4d4', borderRadius: 8 }}>
            暂无图谱数据（需锁定项目后生成）
          </div>
        )
      )}

      {tab === 'logs' && <LogViewer projectId={projectId} />}
    </div>
  );
}
