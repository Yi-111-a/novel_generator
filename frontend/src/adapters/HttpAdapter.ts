// 主实现：对接真后端 REST + SSE。
// 端点约定（不确定处的合理假设，后端可按此实现或微调本文件）：
//   GET    /api/config                     PUT /api/config            POST /api/config/test
//   GET    /api/projects                   POST /api/projects
//   PATCH  /api/projects/:id               DELETE /api/projects/:id
//   GET    /api/projects/:id/seed/chat
//   POST   /api/projects/:id/seed/chat     → SSE 流：data: {"token": "..."} 多条，末条 {"draft": {...}}
//   GET    /api/projects/:id/seed/draft    PUT /api/projects/:id/seed/draft
//   POST   /api/projects/:id/seed/lock
//   GET    /api/projects/:id/world | beats | threads | endings | personas
//   GET    /api/projects/:id/knowledge/:agentId
//   GET    /api/projects/:id/reader-knowledge?upto=N
//   GET    /api/projects/:id/foreshadows | scenes
//   POST   /api/projects/:id/control  body {action}
//   POST   /api/projects/:id/god      body GodAction
//   GET    /api/projects/:id/stream            → SSE：event:sim data:{SimEvent} / event:delta data:{...}
// Key 处理：API Key 由后端在服务端保管；前端只通过 /api/config 提交，不在请求里回传明文。
import type { EngineAdapter } from './EngineAdapter';
import type {
  ApiConfig,
  AuthorSheetListItem,
  AuthorWritingSheet,
  Beat,
  Chapter,
  ChapterDraft,
  ContinuationDistillConfig,
  ContinuationJobStatus,
  ContinuationSettings,
  ContinuationStyleDiagnostics,
  DistilledKnowledgePackage,
  ControlAction,
  Dossier,
  Ending,
  Foreshadow,
  GodAction,
  KnowledgeEntry,
  Persona,
  Project,
  ProjectPlan,
  ProjectType,
  ReaderKnowledge,
  Scene,
  SeedChatMessage,
  SeedDraft,
  SimEvent,
  SimulationControlResult,
  SourceChapter,
  StoryBibleV2,
  StoryBibleStatus,
  Thread,
  ToneProfile,
  StyleSkill,
  WritingSettings,
  WorldState,
  AcceptedChapter,
  LLMLog,
  GraphData,
} from '../types';

export class HttpAdapter implements EngineAdapter {
  constructor(private base = '/api') {}

  private async j<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await fetch(this.base + path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText} @ ${path}`);
    return (res.status === 204 ? undefined : await res.json()) as T;
  }

  getConfig(): Promise<ApiConfig> {
    return this.j('/config');
  }
  saveConfig(c: ApiConfig): Promise<void> {
    return this.j('/config', { method: 'PUT', body: JSON.stringify(c) });
  }
  async testConnection(c: ApiConfig): Promise<boolean> {
    const r = await this.j<{ ok: boolean }>('/config/test', { method: 'POST', body: JSON.stringify(c) });
    return r.ok;
  }

  async listTemplates() {
    const r = await this.j<{ templates: { id: string; label: string; description: string; world_hints: string[] }[] }>('/templates');
    return r.templates;
  }
  listProjects(): Promise<Project[]> {
    return this.j('/projects');
  }
  createProject(title: string, type: ProjectType = 'original', templateId: string = ''): Promise<Project> {
    return this.j('/projects', { method: 'POST', body: JSON.stringify({ title, type, templateId }) });
  }
  renameProject(id: string, title: string): Promise<void> {
    return this.j(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify({ title }) });
  }
  deleteProject(id: string): Promise<void> {
    return this.j(`/projects/${id}`, { method: 'DELETE' });
  }

  getSeedChat(projectId: string): Promise<SeedChatMessage[]> {
    return this.j(`/projects/${projectId}/seed/chat`);
  }
  async sendSeedMessage(projectId: string, content: string, onToken: (t: string) => void): Promise<SeedDraft> {
    const res = await fetch(`${this.base}/projects/${projectId}/seed/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ content }),
    });
    if (!res.ok) {
      // 后端不做离线兜底：未配置 / API 未响应直接报错，这里抛出供 UI 显示
      let detail = `对话失败（HTTP ${res.status}）`;
      try {
        const j = await res.json();
        if (j?.detail) detail = j.detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    if (!res.body) throw new Error('无流式响应体');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let draft: SeedDraft | null = null;
    // 解析 SSE：以空行分隔事件，data: 后是 JSON。
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const chunks = buf.split('\n\n');
      buf = chunks.pop() ?? '';
      for (const chunk of chunks) {
        const line = chunk.split('\n').find((l) => l.startsWith('data:'));
        if (!line) continue;
        const payload = JSON.parse(line.slice(5).trim());
        if (typeof payload.token === 'string') onToken(payload.token);
        if (payload.draft) draft = payload.draft as SeedDraft;
      }
    }
    if (!draft) draft = await this.getSeedDraft(projectId);
    return draft;
  }
  getSeedDraft(projectId: string): Promise<SeedDraft> {
    return this.j(`/projects/${projectId}/seed/draft`);
  }
  updateSeedDraft(projectId: string, draft: SeedDraft): Promise<void> {
    return this.j(`/projects/${projectId}/seed/draft`, { method: 'PUT', body: JSON.stringify(draft) });
  }
  lockSeedAndStart(projectId: string): Promise<void> {
    return this.j(`/projects/${projectId}/seed/lock`, { method: 'POST' });
  }

  getWorldState(projectId: string): Promise<WorldState> {
    return this.j(`/projects/${projectId}/world`);
  }
  getBeats(projectId: string): Promise<Beat[]> {
    return this.j(`/projects/${projectId}/beats`);
  }
  getThreads(projectId: string): Promise<Thread[]> {
    return this.j(`/projects/${projectId}/threads`);
  }
  getEndings(projectId: string): Promise<Ending[]> {
    return this.j(`/projects/${projectId}/endings`);
  }
  getPersonas(projectId: string): Promise<Persona[]> {
    return this.j(`/projects/${projectId}/personas`);
  }
  getAgentKnowledge(projectId: string, agentId: string): Promise<KnowledgeEntry[]> {
    return this.j(`/projects/${projectId}/knowledge/${agentId}`);
  }
  getReaderKnowledge(projectId: string, uptoDiscoursePos?: number): Promise<ReaderKnowledge[]> {
    const q = uptoDiscoursePos == null ? '' : `?upto=${uptoDiscoursePos}`;
    return this.j(`/projects/${projectId}/reader-knowledge${q}`);
  }
  getForeshadows(projectId: string): Promise<Foreshadow[]> {
    return this.j(`/projects/${projectId}/foreshadows`);
  }
  getScenes(projectId: string): Promise<Scene[]> {
    return this.j(`/projects/${projectId}/scenes`);
  }
  getChapters(projectId: string): Promise<Chapter[]> {
    return this.j(`/projects/${projectId}/chapters`);
  }
  getPlan(projectId: string): Promise<ProjectPlan> {
    return this.j(`/projects/${projectId}/plan`);
  }
  getDossier(projectId: string, agentId: string): Promise<Dossier> {
    return this.j(`/projects/${projectId}/characters/${agentId}/dossier`);
  }
  finalizeProject(projectId: string): Promise<{ ok: boolean; reason?: string }> {
    return this.j(`/projects/${projectId}/finalize`, { method: 'POST' });
  }
  updateTone(projectId: string, patch: Record<string, unknown>, confirm: boolean): Promise<ToneProfile> {
    return this.j<ToneProfile>(`/projects/${projectId}/tone`, { method: 'POST', body: JSON.stringify({ patch, confirm }) });
  }
  getStyleSkill(projectId: string): Promise<StyleSkill | null> {
    return this.j<StyleSkill | null>(`/projects/${projectId}/style-skill`);
  }
  ingestStyleSkill(projectId: string, mode: 'works' | 'skill', text: string, name?: string, source?: string): Promise<{ ok: boolean; styleSkill?: StyleSkill }> {
    return this.j(`/projects/${projectId}/style-skill`, { method: 'POST', body: JSON.stringify({ mode, text, name, source }) });
  }
  setStyleEnabled(projectId: string, enabled: boolean): Promise<{ ok: boolean; styleSkill?: StyleSkill | null }> {
    return this.j(`/projects/${projectId}/style-skill`, { method: 'PATCH', body: JSON.stringify({ enabled }) });
  }
  deleteStyleSkill(projectId: string): Promise<{ ok: boolean }> {
    return this.j(`/projects/${projectId}/style-skill`, { method: 'DELETE' });
  }
  editChapter(projectId: string, chapterId: string, fields: Record<string, unknown>): Promise<{ ok: boolean }> {
    return this.j(`/projects/${projectId}/plan/chapter/${chapterId}`, { method: 'PATCH', body: JSON.stringify(fields) });
  }
  replanChapter(projectId: string, chapterId: string): Promise<{ ok: boolean }> {
    return this.j(`/projects/${projectId}/plan/chapter/${chapterId}/replan`, { method: 'POST' });
  }
  updateDisclosure(projectId: string, entityId: string, fields: Record<string, unknown>): Promise<{ ok: boolean }> {
    return this.j(`/projects/${projectId}/plan/disclosures/${entityId}`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    });
  }
  deleteChapter(projectId: string, chapterId: string): Promise<{ ok: boolean }> {
    return this.j(`/projects/${projectId}/plan/chapter/${chapterId}`, { method: 'DELETE' });
  }
  deepenBibleSection(projectId: string, section: string, context = '', hint = ''): Promise<{ ok: boolean; section?: string }> {
    return this.j(`/projects/${projectId}/world-bible/${section}/deepen`, {
      method: 'POST', body: JSON.stringify({ context, hint }),
    });
  }

  // S1 Author Writing Sheet
  distillAuthorSheet(projectId: string, text: string, name = '', genre = ''): Promise<{ ok: boolean; sheetId?: number; sheet?: AuthorWritingSheet; styleSkill?: StyleSkill }> {
    return this.j(`/projects/${projectId}/style/distill`, { method: 'POST', body: JSON.stringify({ text, name, genre }) });
  }
  listAuthorSheets(projectId: string): Promise<AuthorSheetListItem[]> {
    return this.j(`/projects/${projectId}/style/sheets`);
  }
  getAuthorSheet(projectId: string, sheetId: number): Promise<AuthorWritingSheet> {
    return this.j(`/projects/${projectId}/style/sheets/${sheetId}`);
  }
  deleteAuthorSheet(projectId: string, sheetId: number): Promise<{ ok: boolean }> {
    return this.j(`/projects/${projectId}/style/sheets/${sheetId}`, { method: 'DELETE' });
  }
  // 续写

  // LLM 日志
  getWritingSettings(projectId: string): Promise<WritingSettings> {
    return this.j(`/projects/${projectId}/writing/settings`);
  }
  saveWritingSettings(projectId: string, settings: Partial<WritingSettings>): Promise<WritingSettings> {
    return this.j(`/projects/${projectId}/writing/settings`, { method: 'PUT', body: JSON.stringify(settings) });
  }
  buildStoryBible(projectId: string): Promise<{ ok: boolean; status: string }> {
    return this.j(`/projects/${projectId}/story-bible/build`, { method: 'POST' });
  }
  getStoryBibleStatus(projectId: string): Promise<StoryBibleStatus> {
    return this.j(`/projects/${projectId}/story-bible/status`);
  }
  getStoryBible(projectId: string): Promise<StoryBibleV2 | null> {
    return this.j(`/projects/${projectId}/story-bible`);
  }
  importSourceText(projectId: string, body: { text: string; filename?: string }): Promise<{ ok: boolean; chapters: number; filename: string }> {
    return this.j(`/projects/${projectId}/source/import-text`, { method: 'POST', body: JSON.stringify(body) });
  }
  importContinuationSources(projectId: string, body: { text: string; filename?: string }): Promise<{ ok: boolean; chapters: number; filename: string }> {
    return this.j(`/projects/${projectId}/continuation/import`, { method: 'POST', body: JSON.stringify(body) });
  }
  async uploadContinuationSources(projectId: string, files: File[]): Promise<{ ok: boolean; chapters: number; filename: string; documents?: number }> {
    const body = new FormData();
    files.forEach((file) => body.append('files', file, file.name));
    const res = await fetch(`${this.base}/projects/${projectId}/continuation/upload`, {
      method: 'POST',
      body,
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText} @ continuation/upload`);
    return res.json();
  }
  getSourceChapters(projectId: string): Promise<SourceChapter[]> {
    return this.j(`/projects/${projectId}/source/chapters`);
  }
  updateSourceChapter(projectId: string, chapterId: number, body: { title?: string; text?: string; summary?: string }): Promise<{ ok: boolean }> {
    return this.j(`/projects/${projectId}/source/chapters/${chapterId}`, { method: 'PATCH', body: JSON.stringify(body) });
  }
  resplitSource(projectId: string): Promise<{ ok: boolean; chapters?: number }> {
    return this.j(`/projects/${projectId}/source/resplit`, { method: 'POST' });
  }
  getContinuationSettings(projectId: string): Promise<ContinuationSettings> {
    return this.j(`/projects/${projectId}/continuation/settings`);
  }
  saveContinuationSettings(projectId: string, body: Partial<ContinuationSettings>): Promise<ContinuationSettings & { ok?: boolean }> {
    return this.j(`/projects/${projectId}/continuation/settings`, { method: 'PUT', body: JSON.stringify(body) });
  }
  startContinuationDistill(projectId: string, body: ContinuationDistillConfig): Promise<{ ok: boolean; jobId?: string; error?: string }> {
    return this.j(`/projects/${projectId}/continuation/distill`, { method: 'POST', body: JSON.stringify(body) });
  }
  getContinuationJob(projectId: string): Promise<ContinuationJobStatus> {
    return this.j(`/projects/${projectId}/continuation/job`);
  }
  getContinuationStyleDiagnostics(projectId: string): Promise<ContinuationStyleDiagnostics> {
    return this.j(`/projects/${projectId}/continuation/style-diagnostics`);
  }
  getContinuationKnowledgePackage(projectId: string): Promise<DistilledKnowledgePackage> {
    return this.j(`/projects/${projectId}/continuation/knowledge-package`);
  }
  lockContinuation(projectId: string): Promise<ContinuationSettings & { ok?: boolean }> {
    return this.j(`/projects/${projectId}/continuation/lock`, { method: 'POST' });
  }
  createChapterDraft(projectId: string, body: { guidance?: string; targetWords?: number; outlineOnly?: boolean; mode?: 'manual' | 'auto' }): Promise<ChapterDraft> {
    return this.j(`/projects/${projectId}/chapters/drafts`, { method: 'POST', body: JSON.stringify(body) });
  }
  getChapterDrafts(projectId: string): Promise<ChapterDraft[]> {
    return this.j(`/projects/${projectId}/chapters/drafts`);
  }
  acceptChapterDraft(projectId: string, draftId: number): Promise<{ ok: boolean; acceptedChapterId: number; chapterNo: number; title: string }> {
    return this.j(`/projects/${projectId}/chapters/drafts/${draftId}/accept`, { method: 'POST' });
  }
  forceAcceptChapterDraft(projectId: string, draftId: number, reason: string): Promise<{ ok: boolean; acceptedChapterId: number; chapterNo: number; title: string; forced: boolean }> {
    return this.j(`/projects/${projectId}/chapters/drafts/${draftId}/force-accept`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    });
  }
  rejectChapterDraft(projectId: string, draftId: number): Promise<{ ok: boolean }> {
    return this.j(`/projects/${projectId}/chapters/drafts/${draftId}/reject`, { method: 'POST' });
  }
  autoWriteChapters(projectId: string, body: { chapters?: number; targetWords?: number; guidance?: string }): Promise<{ ok: boolean; draftIds: number[] }> {
    return this.j(`/projects/${projectId}/chapters/auto-write`, { method: 'POST', body: JSON.stringify(body) });
  }
  getAcceptedChapters(projectId: string): Promise<AcceptedChapter[]> {
    return this.j(`/projects/${projectId}/chapters/accepted`);
  }
  getLLMLogs(projectId: string, limit = 200, caller?: string): Promise<LLMLog[]> {
    const q = caller ? `?limit=${limit}&caller=${caller}` : `?limit=${limit}`;
    return this.j(`/projects/${projectId}/llm-logs${q}`);
  }
  getLLMLog(projectId: string, logId: number): Promise<LLMLog | null> {
    return this.j(`/projects/${projectId}/llm-logs/${logId}`);
  }
  // 知识图谱
  getGraph(projectId: string): Promise<GraphData> {
    return this.j(`/projects/${projectId}/graph`);
  }

  control(projectId: string, action: ControlAction): Promise<SimulationControlResult> {
    return this.j(`/projects/${projectId}/control`, { method: 'POST', body: JSON.stringify({ action }) });
  }
  injectGodAction(projectId: string, action: GodAction): Promise<void> {
    return this.j(`/projects/${projectId}/god`, { method: 'POST', body: JSON.stringify(action) });
  }

  subscribe(projectId: string, onEvent: (e: SimEvent) => void, onStateDelta: (d: unknown) => void): () => void {
    // 每个项目独立 EventSource，互不影响。
    const es = new EventSource(`${this.base}/projects/${projectId}/stream`);
    es.addEventListener('sim', (e) => onEvent(JSON.parse((e as MessageEvent).data)));
    es.addEventListener('delta', (e) => onStateDelta(JSON.parse((e as MessageEvent).data)));
    es.onerror = () => {
      /* 让浏览器自动重连；如需可在此上报状态 */
    };
    return () => es.close();
  }
}
