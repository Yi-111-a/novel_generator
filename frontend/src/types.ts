// 前端数据契约（与后端 REST/SSE 对齐；camelCase）。

export interface ApiConfig {
  llmApiKey: string;
  baseUrl: string;
  modelName: string;
  memoryKey?: string;
  autoResume?: boolean; // 重启恢复写作中项目时是否自动继续播放
}

export type ProjectStatus = 'seeding' | 'writing' | 'completed';

export interface Project {
  id: string;
  title: string;
  status: ProjectStatus;
  createdAt: string;
  updatedAt: string;
  runningSim?: boolean;
  sceneCount?: number; // 仪表盘进度提示
  chapterCount?: number; // 已完成章数
}

export interface Chapter {
  index: number;
  title: string;
  sceneIds: string[];
  status: 'done' | 'ongoing';
  climaxSceneId?: string | null;
  cast?: string[]; // 计划模式：本章出场人物名
  beatGoals?: string[]; // 计划模式：本章节拍目标
  isPrologue?: boolean; // §5 final cut 的 in medias res 序章钩子
}

// —— 规划层（大纲驱动；仅新机制项目有内容） ——
export interface PlanPart {
  partId: string;
  sequenceOrder: number;
  title: string;
  goal: string;
  region: string;
  revealNodeIds: string[];
  status: 'planned' | 'active' | 'done';
}

export interface PlanArc {
  arcId: string;
  partId: string;
  sequenceOrder: number;
  title: string;
  summary: string;
  targetChapters: number;
  focusAgents: { agentId: string; weight: number }[];
  status: 'planned' | 'active' | 'done';
}

export interface PlanChapter {
  chapterId: string;
  arcId: string;
  sequenceOrder: number;
  title: string;
  cast: string[];
  castNames: string[];
  locationIds: string[];
  availableItems: string[];
  beatGoals: string[];
  beatPovNames?: string[];     // 每个 beat 的视角人物名（POV 跟着节拍走）
  revealGate: string[];
  targetScenes: number;
  role: 'setup' | 'rising' | 'twist' | 'climax' | 'resolution';
  targetTension: number;
  dramaticQuestion?: string;
  itemsPresent?: string[];
  itemsIntroduced?: string[];
  itemsConsumed?: string[];
  targetWords?: number;
  endingHook?: string;
  hookType?: string;
  provisional?: boolean;
  conflictType?: string;       // S1 冲突类型
  exitState?: string;          // 问题4 推进目标
  written?: boolean;           // 已写完（有正文/已收束）→ 不可编辑，只能删除
  itemsPresentNames?: string[]; // 在场道具名（编辑用）
  locationName?: string;       // 本章地点名（编辑用）
  status: 'planned' | 'active' | 'done';
}

export interface EraLogic {
  enabled?: boolean;
  moral_index?: number;
  religiosity?: number;
  science_level?: number;
  banned_modern_words?: string[];
  forced_attribution?: string;
}

export interface ToneProfile {
  genre: string;
  primaryEffect: string;
  register: string;
  sentenceRhythm: string;
  dictionDo: string[];
  dictionDont: string[];
  deviceKit: string[];
  pacing: string;
  tensionCurveBias: string;
  revealCadence: string;
  complexity: string;
  toneReference: string;
  confirmed: boolean;
  eraLogic?: EraLogic;
}

export interface StyleMetrics {
  avg_sentence_len?: number;
  sentence_len_variance?: number;
  punct_density?: number;
  ttr?: number;
  content_function_ratio?: number;
  semantic_leaping?: number;
  sensory_e?: number;
}

export interface StyleSkill {
  name: string;
  source: string;
  register: string;
  rhythm: string;
  devices: string[];
  dictionDo: string[];
  dictionDont: string[];
  motifs: string[];
  samples: string[];
  metrics: StyleMetrics;
  enabled: boolean;
}

export interface BibleSection {
  id: number;
  section: string;
  title: string;
  body_full: string;
  summary: string;
  source: string;
  created_at: number;
}

export interface RevealNode {
  nodeId: string;
  factId?: string | null;
  kind: 'clue' | 'truth';
  sequenceOrder: number;
  prereqNodeIds: string[];
  partId?: string | null;
  description: string;
  discovered: boolean;
  discoveredChapter?: number | null;
}

export interface PlanLocation {
  locId: string;
  partId?: string | null;
  name: string;
  geoFull: string;
  connectsTo: string[];
  controllingFaction: string;
  notableItems: string[];
  // W2 地理层：两级 + 风土人情 + 层级（旧项目可能空）
  level?: string;
  parent?: string;
  cultureLocal?: string;
  summary?: string;
  detail?: string;
}

export interface CharacterCard {
  cardId: string;
  agentId?: string | null;
  tier: 'lead' | 'supporting' | 'extra' | string;
  slotKey?: string | null;
  name: string;
  oneLiner: string;
  voiceRegister: string;
  definingTrait: string;
  coreDesire: string;
  verbalHabits: string;
  keyRelation: string;
  backstory: string;
  fatalFlaw: string;
  arc: string;
  // W4 三维度
  appearance: string;
  socialRole: string;
  psychology: string;
}

export interface FactionMember {
  name: string;
  role?: string;
  agent_id?: string;
  note?: string;
}

export interface FactionRelation {
  target_faction_id: string;
  kind: 'allied' | 'hostile' | 'infiltrates' | 'neutral' | 'tributary' | string;
  intensity: number;
  note?: string;
}

export interface Faction {
  factionId: string;
  name: string;
  ideology: string;
  goals: string;
  methods: string;
  territory: string[];           // canon loc_id 列表
  structure: string;
  keyMembers: FactionMember[];
  history: string;
  relations: FactionRelation[];
  secret: string;
  summary: string;
  detail: string;
  source: string;
}

export interface InventoryItem {
  objectId: string;
  name: string;
  holderAgentId?: string | null;
  status: 'held' | 'transferred' | 'lost';
  acquiredChapter: number;
  note: string;
}

export interface ProjectPlan {
  planned: boolean;
  toneProfile?: ToneProfile | null;
  styleSkill?: StyleSkill | null;
  parts: PlanPart[];
  arcs: PlanArc[];
  chapters: PlanChapter[];
  revealChain: RevealNode[];
  inventory: InventoryItem[];
  locations?: PlanLocation[];
  factions?: Faction[];
  characterCards?: CharacterCard[];
  bibleSections?: BibleSection[];
}

export interface Dossier {
  agentId: string;
  markdown: string;
}

// —— 种子 ——
export interface WorldBible {
  // 不可变层（锁定后不可改）
  settingCore: string;
  geography: string;
  culture: string;
  physicsRules: string[];
  // 意图层（可弯曲）
  protagonistWant: string;
  theme: string;
  candidateEndings: Ending[];
}

export interface Ending {
  id: string;
  summary: string;
  themeExpression: string;
  requiredConditions: string[];
  activeWeight: number;
}

export interface Persona {
  id: string;
  name: string;
  want: string;
  values: { name: string; weight: number }[];
  fatalFlaw: string;
  obstacles: string[];
  costThreshold: string;
  voice: string;
  mannerisms: string[];
  motifObjects: string[];
  arcState: string;
  costLedger: string[];
}

export interface SeedChatMessage {
  role: 'user' | 'assistant';
  content: string;
  at: string;
}

export interface SeedCompletenessItem {
  key: string;
  label: string;
  done: boolean;
}

export interface SeedCompleteness {
  ready: boolean;
  checklist: SeedCompletenessItem[];
}

export interface SeedDraft {
  worldBible: Partial<WorldBible>;
  personas: Persona[];
  completeness: SeedCompleteness;
}

// —— 运行态 ——
export interface Fact {
  factId: string;
  factType: 'event' | 'state' | 'relationship';
  canonicalContent: string;
  storyTime: number;
  locationId?: string;
  involvedEntities: string[];
}

export interface SimEvent {
  eventId: string;
  storyTime: number;
  actors: string[];
  actionType: string;
  payload: string;
  locationId?: string;
  perceivers: string[];
  dramaScore?: number;
  beatId?: string;
}

export interface KnowledgeEntry {
  agentId: string;
  factId: string;
  versionContent: string; // ≠ canonical 即"扭曲"
  confidence: number;
  learnedTick: number;
}

export interface ReaderKnowledge {
  factId: string;
  revealedVersion: string;
  revealedDiscoursePos: number;
  viaPov: string;
}

export interface Beat {
  beatId: string;
  sequenceOrder: number;
  type: 'structural' | 'decision';
  goal: string;
  threads: string[];
  targetTension: number;
  targetEndingLink: string;
  status: 'planned' | 'active' | 'done';
}

export interface Thread {
  threadId: string;
  centralQuestion: string;
  involvedAgents: string[];
  priorityWeight: number;
  currentTension: number;
  lastAdvancedTick: number;
  status: 'open' | 'converging' | 'resolved';
}

export interface Foreshadow {
  foreshadowId: string;
  plantedDiscoursePos: number;
  question: string;
  linkedFactId: string;
  mustResolve: boolean;
  targetPayoffBeat: string;
  status: 'open' | 'paid_off' | 'abandoned';
}

export interface Scene {
  sceneId: string;
  discourseOrder: number;
  sourceEvents: string[];
  pov: string;
  targetTension: number;
  proseText: string;
  newlyRevealed: string[];
}

export type GodAction =
  | { kind: 'add_event'; payload: Partial<SimEvent> }
  | { kind: 'edit_fact'; factId: string; newContent: string }
  | { kind: 'reveal_to_reader'; factId: string }
  | { kind: 'hide_from_reader'; factId: string }
  | { kind: 'set_thread_priority'; threadId: string; weight: number }
  | { kind: 'add_entity'; entityType: 'character' | 'object'; name?: string; note?: string };

export type ControlAction = 'play' | 'pause' | 'step';

export interface WorldState {
  facts: Fact[];
  events: SimEvent[];
  tick: number;
}

// LLM 对话日志
export interface LLMLog {
  id: number;
  ts: number;
  caller: string;
  system_msg: string;
  user_msg: string;
  response: string;
  temperature: number | null;
  elapsed_ms: number;
  meta: string;
}

// 知识图谱
export interface GraphNode {
  id: string;
  name: string;
  type: string; // character | location | faction | item
  attributes?: Record<string, any>;
  factionId?: string;
  parentLoc?: string;
  territory?: string[];
}

export interface GraphEdgeViz {
  src: string;
  dst: string;
  rel: string;
  intensity: number;
  sinceChapter?: number;
  lastActiveChapter?: number;
  srcName: string;
  dstName: string;
  note?: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdgeViz[];
}
