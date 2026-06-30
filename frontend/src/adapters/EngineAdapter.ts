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
  GenreTemplateCard,
} from '../types';

/** 数据访问抽象。所有项目内方法带 projectId，保证项目间隔离。 */
export interface EngineAdapter {
  // 全局设置
  getConfig(): Promise<ApiConfig>;
  saveConfig(c: ApiConfig): Promise<void>;
  testConnection(c: ApiConfig): Promise<boolean>;

  // 题材模板（新建项目时可选）
  listTemplates(): Promise<GenreTemplateCard[]>;

  // 项目（小说）
  listProjects(): Promise<Project[]>;
  createProject(title: string, type?: ProjectType, templateId?: string): Promise<Project>;
  renameProject(id: string, title: string): Promise<void>;
  deleteProject(id: string): Promise<void>;

  // 种子工坊
  getSeedChat(projectId: string): Promise<SeedChatMessage[]>;
  sendSeedMessage(projectId: string, content: string, onToken: (t: string) => void): Promise<SeedDraft>;
  getSeedDraft(projectId: string): Promise<SeedDraft>;
  updateSeedDraft(projectId: string, draft: SeedDraft): Promise<void>;
  lockSeedAndStart(projectId: string): Promise<void>;

  // 运行态
  getWorldState(projectId: string): Promise<WorldState>;
  getBeats(projectId: string): Promise<Beat[]>;
  getThreads(projectId: string): Promise<Thread[]>;
  getEndings(projectId: string): Promise<Ending[]>;
  getPersonas(projectId: string): Promise<Persona[]>;
  getAgentKnowledge(projectId: string, agentId: string): Promise<KnowledgeEntry[]>;
  getReaderKnowledge(projectId: string, uptoDiscoursePos?: number): Promise<ReaderKnowledge[]>;
  getForeshadows(projectId: string): Promise<Foreshadow[]>;
  getScenes(projectId: string): Promise<Scene[]>;
  getChapters(projectId: string): Promise<Chapter[]>;
  getPlan(projectId: string): Promise<ProjectPlan>;
  getDossier(projectId: string, agentId: string): Promise<Dossier>;
  finalizeProject(projectId: string): Promise<{ ok: boolean; reason?: string }>;
  updateTone(projectId: string, patch: Record<string, unknown>, confirm: boolean): Promise<ToneProfile>;
  // B0 文风模拟：上传作品/现成文风→蒸馏指纹；启停；删除（回落 tone 基线）
  getStyleSkill(projectId: string): Promise<StyleSkill | null>;
  ingestStyleSkill(projectId: string, mode: 'works' | 'skill', text: string, name?: string, source?: string): Promise<{ ok: boolean; styleSkill?: StyleSkill }>;
  setStyleEnabled(projectId: string, enabled: boolean): Promise<{ ok: boolean; styleSkill?: StyleSkill | null }>;
  deleteStyleSkill(projectId: string): Promise<{ ok: boolean }>;
  // 大纲编辑/删除（已写完的章后端会拒绝编辑；删除会清正文，删后位置可重写）
  editChapter(projectId: string, chapterId: string, fields: Record<string, unknown>): Promise<{ ok: boolean }>;
  replanChapter(projectId: string, chapterId: string): Promise<{ ok: boolean }>;
  updateDisclosure(projectId: string, entityId: string, fields: Record<string, unknown>): Promise<{ ok: boolean }>;
  deleteChapter(projectId: string, chapterId: string): Promise<{ ok: boolean }>;
  // W1-b 世界观渐进深化（可多次；hint 为用户指定的深化方向）
  deepenBibleSection(projectId: string, section: string, context?: string, hint?: string): Promise<{ ok: boolean; section?: string }>;
  // S1 Author Writing Sheet
  distillAuthorSheet(projectId: string, text: string, name?: string, genre?: string): Promise<{ ok: boolean; sheetId?: number; sheet?: AuthorWritingSheet; styleSkill?: StyleSkill }>;
  listAuthorSheets(projectId: string): Promise<AuthorSheetListItem[]>;
  getAuthorSheet(projectId: string, sheetId: number): Promise<AuthorWritingSheet>;
  deleteAuthorSheet(projectId: string, sheetId: number): Promise<{ ok: boolean }>;
  // 续写

  // 控制
  getWritingSettings(projectId: string): Promise<WritingSettings>;
  saveWritingSettings(projectId: string, settings: Partial<WritingSettings>): Promise<WritingSettings>;
  buildStoryBible(projectId: string): Promise<{ ok: boolean; status: string }>;
  getStoryBibleStatus(projectId: string): Promise<StoryBibleStatus>;
  getStoryBible(projectId: string): Promise<StoryBibleV2 | null>;
  importSourceText(projectId: string, body: { text: string; filename?: string }): Promise<{ ok: boolean; chapters: number; filename: string }>;
  importContinuationSources(projectId: string, body: { text: string; filename?: string }): Promise<{ ok: boolean; chapters: number; filename: string }>;
  uploadContinuationSources(projectId: string, files: File[]): Promise<{ ok: boolean; chapters: number; filename: string; documents?: number }>;
  getSourceChapters(projectId: string): Promise<SourceChapter[]>;
  updateSourceChapter(projectId: string, chapterId: number, body: { title?: string; text?: string; summary?: string }): Promise<{ ok: boolean }>;
  resplitSource(projectId: string): Promise<{ ok: boolean; chapters?: number }>;
  getContinuationSettings(projectId: string): Promise<ContinuationSettings>;
  saveContinuationSettings(projectId: string, body: Partial<ContinuationSettings>): Promise<ContinuationSettings & { ok?: boolean }>;
  startContinuationDistill(projectId: string, body: ContinuationDistillConfig): Promise<{ ok: boolean; jobId?: string; error?: string }>;
  getContinuationJob(projectId: string): Promise<ContinuationJobStatus>;
  getContinuationStyleDiagnostics(projectId: string): Promise<ContinuationStyleDiagnostics>;
  getContinuationKnowledgePackage(projectId: string): Promise<DistilledKnowledgePackage>;
  lockContinuation(projectId: string): Promise<ContinuationSettings & { ok?: boolean }>;
  createChapterDraft(projectId: string, body: { guidance?: string; targetWords?: number; outlineOnly?: boolean; mode?: 'manual' | 'auto' }): Promise<ChapterDraft>;
  getChapterDrafts(projectId: string): Promise<ChapterDraft[]>;
  acceptChapterDraft(projectId: string, draftId: number): Promise<{ ok: boolean; acceptedChapterId: number; chapterNo: number; title: string }>;
  forceAcceptChapterDraft(projectId: string, draftId: number, reason: string): Promise<{ ok: boolean; acceptedChapterId: number; chapterNo: number; title: string; forced: boolean }>;
  rejectChapterDraft(projectId: string, draftId: number): Promise<{ ok: boolean }>;
  autoWriteChapters(projectId: string, body: { chapters?: number; targetWords?: number; guidance?: string }): Promise<{ ok: boolean; draftIds: number[] }>;
  getAcceptedChapters(projectId: string): Promise<AcceptedChapter[]>;
  control(projectId: string, action: ControlAction): Promise<SimulationControlResult>;
  injectGodAction(projectId: string, action: GodAction): Promise<void>;

  // 实时：每个项目独立订阅，返回取消订阅函数
  subscribe(
    projectId: string,
    onEvent: (e: SimEvent) => void,
    onStateDelta: (d: unknown) => void,
  ): () => void;
}
