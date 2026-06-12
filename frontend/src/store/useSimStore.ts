import { create } from 'zustand';
import { getAdapter } from '../adapters';
import type { SimEvent } from '../types';

// 按项目分片的实时缓冲：每个项目独立的事件流与订阅计数。
// 订阅/退订只影响"是否往缓冲里追加"，不影响后端/Mock 的模拟计时器本身——
// 因此切换项目不会中断其它项目正在跑的模拟。
const CAP = 400; // 事件缓冲上限

interface ProjectLive {
  events: SimEvent[];
  tick: number;
  unsub?: () => void;
  loaded?: boolean; // 是否已一次性加载过历史事件
}

interface SimState {
  byProject: Record<string, ProjectLive>;
  subscribe: (projectId: string) => void;
  unsubscribe: (projectId: string) => void;
  clear: (projectId: string) => void;
}

function mergeEvents(prev: SimEvent[], incoming: SimEvent[]): SimEvent[] {
  const m = new Map<string, SimEvent>();
  for (const e of prev) m.set(e.eventId, e);
  for (const e of incoming) m.set(e.eventId, e); // 去重（按 eventId）
  return [...m.values()].sort((a, b) => a.storyTime - b.storyTime).slice(-CAP);
}

export const useSimStore = create<SimState>((set, get) => ({
  byProject: {},
  subscribe: (projectId) => {
    const existing = get().byProject[projectId];
    if (existing?.unsub) return; // 已订阅则不重复（切换标签回来不会重订阅/重刷）

    const unsub = getAdapter().subscribe(
      projectId,
      (e) =>
        set((s) => {
          const live = s.byProject[projectId] ?? { events: [], tick: 0 };
          if (live.events.some((x) => x.eventId === e.eventId)) return {} as Partial<SimState>; // 去重，避免重复刷新
          const events = [...live.events, e].slice(-CAP);
          return { byProject: { ...s.byProject, [projectId]: { ...live, events, tick: Math.max(live.tick, e.storyTime) } } };
        }),
      (d) =>
        set((s) => {
          const live = s.byProject[projectId] ?? { events: [], tick: 0 };
          const tick = Math.max(live.tick, (d as { tick?: number })?.tick ?? 0);
          return { byProject: { ...s.byProject, [projectId]: { ...live, tick } } };
        }),
    );
    set((s) => ({
      byProject: { ...s.byProject, [projectId]: { ...(s.byProject[projectId] ?? { events: [], tick: 0 }), unsub } },
    }));

    // 仅首次：一次性拉取历史事件灌入缓冲，之后全靠 SSE 增量（不再反复全量拉取）
    if (!existing?.loaded) {
      getAdapter()
        .getWorldState(projectId)
        .then((w) =>
          set((s) => {
            const live = s.byProject[projectId] ?? { events: [], tick: 0 };
            const events = mergeEvents(live.events, w.events);
            const tick = Math.max(live.tick, events.length ? events[events.length - 1].storyTime : 0);
            return { byProject: { ...s.byProject, [projectId]: { ...live, events, tick, loaded: true } } };
          }),
        )
        .catch(() => {});
    }
  },
  unsubscribe: (projectId) => {
    const live = get().byProject[projectId];
    live?.unsub?.();
    if (live) set((s) => ({ byProject: { ...s.byProject, [projectId]: { ...live, unsub: undefined } } }));
  },
  clear: (projectId) =>
    set((s) => ({ byProject: { ...s.byProject, [projectId]: { events: [], tick: 0, loaded: false, unsub: s.byProject[projectId]?.unsub } } })),
}));
