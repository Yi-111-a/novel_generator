import type { EngineAdapter } from './EngineAdapter';
import { HttpAdapter } from './HttpAdapter';
import { MockAdapter } from './MockAdapter';

// 通过环境变量一键切换：VITE_ADAPTER=http | mock。
// 默认以真后端为主（http）；显式设为 mock 时走离线兜底。
let _adapter: EngineAdapter | null = null;

export function getAdapter(): EngineAdapter {
  if (_adapter) return _adapter;
  const which = (import.meta.env.VITE_ADAPTER as string | undefined)?.toLowerCase();
  _adapter = which === 'mock' ? new MockAdapter() : new HttpAdapter('/api');
  return _adapter;
}

export type { EngineAdapter };
