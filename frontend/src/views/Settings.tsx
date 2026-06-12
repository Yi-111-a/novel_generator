import { CheckCircle2, KeyRound, Loader2, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Panel } from '../components/ui';
import { getAdapter } from '../adapters';
import { useAppStore } from '../store/useAppStore';
import type { ApiConfig } from '../types';

export function Settings() {
  const { config, loadConfig, saveConfig } = useAppStore();
  const [form, setForm] = useState<ApiConfig>({ llmApiKey: '', baseUrl: 'https://api.deepseek.com', modelName: 'deepseek-v4-flash', memoryKey: '', autoResume: false });
  const [test, setTest] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);
  useEffect(() => {
    if (config) setForm({ memoryKey: '', ...config });
  }, [config]);

  const set = (k: keyof ApiConfig) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  const onSave = async () => {
    await saveConfig(form);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };
  const onTest = async () => {
    setTest('testing');
    try {
      setTest((await getAdapter().testConnection(form)) ? 'ok' : 'fail');
    } catch {
      setTest('fail');
    }
  };

  return (
    <div className="mx-auto max-w-2xl p-6">
      <h1 className="mb-1 flex items-center gap-2 text-2xl font-semibold">
        <KeyRound className="h-6 w-6" /> 设置
      </h1>
      <p className="mb-6 text-sm text-zinc-500">配置驱动模拟与渲染的 LLM。Key 仅提交给后端在服务端保管，不在其它请求里回传明文。</p>

      <Panel title="LLM API">
        <div className="space-y-4">
          <Field label="API Key">
            <input type="password" className="input font-mono" placeholder="sk-..." value={form.llmApiKey} onChange={set('llmApiKey')} />
          </Field>
          <Field label="Base URL">
            <input className="input font-mono" placeholder="https://api.deepseek.com" value={form.baseUrl} onChange={set('baseUrl')} />
          </Field>
          <Field label="Model Name">
            <input className="input font-mono" placeholder="deepseek-v4-flash" value={form.modelName} onChange={set('modelName')} />
          </Field>
          <Field label="记忆服务 Key（可选）">
            <input type="password" className="input font-mono" placeholder="留空即可——默认用内置 Mem0 式本地记忆" value={form.memoryKey ?? ''} onChange={set('memoryKey')} />
            <span className="mt-1 block text-[11px] leading-relaxed text-zinc-400">
              角色记忆默认走<strong>内置 Mem0 式本地实现</strong>：基于每个角色的账本，按相关度检索 top-k 注入决策，
              并对新记忆做 ADD/UPDATE/NOOP/DELETE 巩固，无需任何外部服务。此处仅为将来接入外部嵌入/记忆服务预留——留空即可。
            </span>
          </Field>

          <label className="flex cursor-pointer items-center gap-2 pt-1 text-sm">
            <input type="checkbox" className="h-4 w-4 accent-indigo-600" checked={!!form.autoResume} onChange={(e) => setForm({ ...form, autoResume: e.target.checked })} />
            <span>恢复后自动继续播放</span>
            <span className="text-[11px] text-zinc-400">（重启服务后，写作中的小说自动继续模拟；默认关，恢复后暂停等你按播放）</span>
          </label>

          <div className="flex items-center gap-2 pt-2">
            <button className="btn-primary" onClick={onSave}>
              {saved ? '已保存 ✓' : '保存'}
            </button>
            <button className="btn-ghost border border-zinc-200 dark:border-zinc-800" onClick={onTest} disabled={test === 'testing'}>
              {test === 'testing' && <Loader2 className="h-4 w-4 animate-spin" />}
              测试api连接
            </button>
            {test === 'ok' && <span className="flex items-center gap-1 text-sm text-emerald-500"><CheckCircle2 className="h-4 w-4" /> 连接正常</span>}
            {test === 'fail' && <span className="flex items-center gap-1 text-sm text-rose-500"><XCircle className="h-4 w-4" /> 连接失败</span>}
          </div>
        </div>
      </Panel>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-zinc-500">{label}</span>
      {children}
    </label>
  );
}
