import { Moon, Settings, Sun, Wrench } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { cn } from '../lib/cn';
import { useAppStore } from '../store/useAppStore';
import { ProjectSwitcher } from './ProjectSwitcher';

export function TopBar() {
  const navigate = useNavigate();
  const { devMode, toggleDevMode, theme, toggleTheme } = useAppStore();

  return (
    <header className="sticky top-0 z-20 flex items-center justify-between border-b border-zinc-200 bg-white/80 px-4 py-2.5 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
      <div className="flex items-center gap-3">
        <button onClick={() => navigate('/')} className="text-sm font-semibold tracking-tight">
          小说模拟引擎
        </button>
        <ProjectSwitcher />
      </div>

      <div className="flex items-center gap-1.5">
        {/* 开发者模式开关：默认成品流程，打开后显示调试面板 */}
        <button
          onClick={toggleDevMode}
          className={cn('btn border', devMode ? 'border-indigo-500/60 bg-indigo-500/10 text-indigo-400' : 'btn-ghost border-zinc-200 dark:border-zinc-800')}
          title="开发者模式"
        >
          <Wrench className="h-4 w-4" />
          <span className="hidden sm:inline">{devMode ? '开发者模式：开' : '开发者模式'}</span>
        </button>
        <button onClick={toggleTheme} className="btn-ghost" title="切换深浅色">
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
        <button onClick={() => navigate('/settings')} className="btn-ghost" title="全局设置">
          <Settings className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
}
