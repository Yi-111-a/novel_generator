import type {
  ApiConfig,
  Beat,
  Chapter,
  ControlAction,
  Dossier,
  Ending,
  Foreshadow,
  GodAction,
  KnowledgeEntry,
  Persona,
  Project,
  ProjectPlan,
  ReaderKnowledge,
  Scene,
  SeedChatMessage,
  SeedDraft,
  SimEvent,
  Thread,
  ToneProfile,
  StyleSkill,
  WorldState,
} from '../types';

/** 数据访问抽象。所有项目内方法带 projectId，保证项目间隔离。 */
export interface EngineAdapter {
  // 全局设置
  getConfig(): Promise<ApiConfig>;
  saveConfig(c: ApiConfig): Promise<void>;
  testConnection(c: ApiConfig): Promise<boolean>;

  // 项目（小说）
  listProjects(): Promise<Project[]>;
  createProject(title: string): Promise<Project>;
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
  deleteChapter(projectId: string, chapterId: string): Promise<{ ok: boolean }>;
  // W1-b 世界观渐进深化（可多次；hint 为用户指定的深化方向）
  deepenBibleSection(projectId: string, section: string, context?: string, hint?: string): Promise<{ ok: boolean; section?: string }>;

  // 控制
  control(projectId: string, action: ControlAction): Promise<void>;
  injectGodAction(projectId: string, action: GodAction): Promise<void>;

  // 实时：每个项目独立订阅，返回取消订阅函数
  subscribe(
    projectId: string,
    onEvent: (e: SimEvent) => void,
    onStateDelta: (d: unknown) => void,
  ): () => void;
}
