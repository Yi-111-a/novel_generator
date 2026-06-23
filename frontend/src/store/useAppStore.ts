import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { getAdapter } from '../adapters';
import type { ApiConfig, GenreTemplateCard, Project, ProjectType } from '../types';

interface AppState {
  // 全局偏好（持久化）
  devMode: boolean;
  theme: 'dark' | 'light';
  toggleDevMode: () => void;
  toggleTheme: () => void;
  applyTheme: () => void;

  // 配置
  config: ApiConfig | null;
  loadConfig: () => Promise<void>;
  saveConfig: (c: ApiConfig) => Promise<void>;

  // 项目列表
  projects: Project[];
  refreshProjects: () => Promise<void>;
  createProject: (title: string, type?: ProjectType, templateId?: string) => Promise<Project>;
  renameProject: (id: string, title: string) => Promise<void>;
  deleteProject: (id: string) => Promise<void>;

  // 题材模板
  templates: GenreTemplateCard[];
  refreshTemplates: () => Promise<void>;
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      devMode: false,
      theme: 'dark',
      toggleDevMode: () => set({ devMode: !get().devMode }),
      toggleTheme: () => {
        const theme = get().theme === 'dark' ? 'light' : 'dark';
        set({ theme });
        get().applyTheme();
      },
      applyTheme: () => {
        const root = document.documentElement;
        if (get().theme === 'dark') root.classList.add('dark');
        else root.classList.remove('dark');
      },

      config: null,
      loadConfig: async () => {
        set({ config: await getAdapter().getConfig() });
      },
      saveConfig: async (c) => {
        await getAdapter().saveConfig(c);
        set({ config: c });
      },

      projects: [],
      refreshProjects: async () => {
        set({ projects: await getAdapter().listProjects() });
      },
      createProject: async (title, type = 'original', templateId = '') => {
        const p = await getAdapter().createProject(title, type, templateId);
        await get().refreshProjects();
        return p;
      },

      templates: [],
      refreshTemplates: async () => {
        try {
          set({ templates: await getAdapter().listTemplates() });
        } catch {
          set({ templates: [] });
        }
      },
      renameProject: async (id, title) => {
        await getAdapter().renameProject(id, title);
        await get().refreshProjects();
      },
      deleteProject: async (id) => {
        await getAdapter().deleteProject(id);
        await get().refreshProjects();
      },
    }),
    { name: 'novel-engine.app', partialize: (s) => ({ devMode: s.devMode, theme: s.theme }) },
  ),
);
