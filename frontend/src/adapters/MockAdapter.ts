// 离线兜底实现：内置修真复仇世界，模拟流式聊天与假 SSE 事件流。
// 设计取舍：运行态/聊天/草稿保存在「模块级单例」内存里——这样切换项目时，
// 其它项目的模拟计时器不会被 React 卸载打断（满足"多项目并行不中断"）。
// 仅 ApiConfig 持久化到 localStorage（便于刷新后保留 key）。
import type { EngineAdapter } from './EngineAdapter';
import type {
  ApiConfig,
  Beat,
  Chapter,
  ControlAction,
  ContinuationStyleDiagnostics,
  Dossier,
  Ending,
  Fact,
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
  SimulationControlResult,
  StyleSkill,
  Thread,
  WorldState,
} from '../types';
import { MockRuntime, SEED_CHAT_INTRO, emptyDraft, xianxiaDraft, xianxiaRuntime } from './mockData';

interface SimState {
  playing: boolean;
  timer: number | null;
  subscribers: Set<{ onEvent: (e: SimEvent) => void; onDelta: (d: unknown) => void }>;
}

interface ProjectSlice {
  project: Project;
  chat: SeedChatMessage[];
  draft: SeedDraft;
  runtime?: MockRuntime;
  sim: SimState;
}

const CONFIG_KEY = 'novel-engine.apiConfig';
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toISOString();
const uid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 8)}`;

class MockEngine {
  slices = new Map<string, ProjectSlice>();

  constructor() {
    // 内置一个已在「写作中」的修真复仇项目，开箱即可浏览全部视图。
    const id = 'proj_xianxia';
    const project: Project = {
      id,
      title: '断剑·青冥旧案',
      status: 'writing',
      createdAt: now(),
      updatedAt: now(),
      runningSim: true,
      sceneCount: 2,
    };
    const runtime = xianxiaRuntime();
    const slice: ProjectSlice = {
      project,
      chat: [...SEED_CHAT_INTRO],
      draft: xianxiaDraft(),
      runtime,
      sim: { playing: true, timer: null, subscribers: new Set() },
    };
    this.slices.set(id, slice);
    this.startTimer(slice);
  }

  get(projectId: string): ProjectSlice {
    const s = this.slices.get(projectId);
    if (!s) throw new Error(`未知项目：${projectId}`);
    return s;
  }

  // —— 假 SSE 计时器：与订阅者无关地推进，保证切走也不中断 ——
  startTimer(slice: ProjectSlice) {
    if (slice.sim.timer != null) return;
    slice.sim.playing = true;
    slice.project.runningSim = true;
    slice.sim.timer = window.setInterval(() => this.stepOnce(slice), 2600);
  }
  stopTimer(slice: ProjectSlice) {
    if (slice.sim.timer != null) window.clearInterval(slice.sim.timer);
    slice.sim.timer = null;
    slice.sim.playing = false;
    slice.project.runningSim = false;
  }

  stepOnce(slice: ProjectSlice) {
    const rt = slice.runtime;
    if (!rt) return;
    rt.tick += 1;
    const tick = rt.tick;
    const actorPool = rt.personas.map((p) => p.id);
    const actor = actorPool[tick % actorPool.length] || 'shen_yan';
    const drama = Math.round((0.35 + Math.random() * 0.6) * 100) / 100;
    const ev: SimEvent = {
      eventId: uid('ev'),
      storyTime: tick,
      actors: [actor],
      actionType: ['试探', '追问', '抉择', '隐忍', '交锋'][tick % 5],
      payload: `${nameOf(rt, actor)}在第 ${tick} 拍有所行动。`,
      perceivers: [actor],
      dramaScore: drama,
      beatId: rt.beats.find((b) => b.status === 'active')?.beatId,
    };
    rt.events.push(ev);

    // 落一条 canonical fact + 写入行动者账本
    const fact: Fact = {
      factId: uid('fact'),
      factType: 'event',
      canonicalContent: ev.payload,
      storyTime: tick,
      involvedEntities: [actor],
    };
    rt.facts.push(fact);
    (rt.knowledge[actor] ||= []).push({
      agentId: actor,
      factId: fact.factId,
      versionContent: fact.canonicalContent,
      confidence: 1,
      learnedTick: tick,
    });

    // 推进主线张力
    const main = rt.threads[0];
    if (main) {
      main.currentTension = Math.min(1, Math.round((main.currentTension + drama * 0.08) * 100) / 100);
      main.lastAdvancedTick = tick;
    }
    // 高潮事件偶尔成稿一场
    if (drama > 0.8) {
      const order = rt.scenes.length + 1;
      rt.scenes.push({
        sceneId: uid('sc'),
        discourseOrder: order,
        sourceEvents: [ev.eventId],
        pov: actor,
        targetTension: drama,
        newlyRevealed: [fact.factId],
        proseText: `${nameOf(rt, actor)}停在原地，良久，才把那句话咽了回去。\n（自动生成的占位场景，接真后端后由叙述层渲染。）`,
      });
      rt.reader.push({ factId: fact.factId, revealedVersion: fact.canonicalContent, revealedDiscoursePos: order, viaPov: actor });
      slice.project.sceneCount = rt.scenes.length;
    }

    slice.project.updatedAt = now();
    slice.sim.subscribers.forEach((s) => {
      s.onEvent(ev);
      s.onDelta({ tick });
    });
  }

  // —— 流式聊天：逐步把草稿填到 ready ——
  async fillNextSeedStep(slice: ProjectSlice, onToken: (t: string) => void): Promise<void> {
    const canned = xianxiaDraft();
    const cl = slice.draft.completeness.checklist;
    const next = cl.find((c) => !c.done);
    let reply = '我已经记下了。我们继续完善这部小说的种子。';
    if (next) {
      const wb = (slice.draft.worldBible ||= { physicsRules: [], candidateEndings: [] });
      switch (next.key) {
        case 'immutable':
          wb.settingCore = canned.worldBible.settingCore;
          wb.geography = canned.worldBible.geography;
          wb.culture = canned.worldBible.culture;
          wb.physicsRules = canned.worldBible.physicsRules;
          reply = '好——我把不可变层定下来了：一个剑修为尊、背着二十年灭门旧案的青冥修真界，并锁定了几条硬法则（没有手机电网、凡人不能御剑、死者不可复生）。';
          break;
        case 'theme':
          wb.theme = canned.worldBible.theme;
          wb.protagonistWant = canned.worldBible.protagonistWant;
          reply = '主题我提议定为「复仇的代价」，主角的外在欲望是查清真相、手刃真凶。你觉得这个张力够吗？';
          break;
        case 'endings':
          wb.candidateEndings = canned.worldBible.candidateEndings;
          reply = '我给了两个候选结局：一个血债血偿但自身万劫不复，一个最后收剑、以真相终结仇恨。导演会在模拟中动态调整它们的权重。';
          break;
        case 'personas':
          slice.draft.personas = canned.personas;
          reply = '角色卡来了：偏执的沈砚、心软的楚红绡（仇家之女）、多疑的玄霜真人（真凶）。每个人都有会相撞的珍视之物。';
          break;
        case 'asymmetry':
          reply = '我刻意制造了一处初始信息差：沈砚不知真凶是谁，楚红绡听到的却是被家族扭曲过的版本——冲突需要信息差，这一点齐了。';
          break;
      }
      next.done = true;
    }
    slice.draft.completeness.ready = slice.draft.completeness.checklist.every((c) => c.done);

    for (const ch of reply) {
      onToken(ch);
      await sleep(12);
    }
  }
}

const engine = new MockEngine();

function nameOf(rt: MockRuntime, id: string): string {
  return rt.personas.find((p) => p.id === id)?.name ?? id;
}

function buildRuntimeFromDraft(draft: SeedDraft): MockRuntime {
  // 用户自建项目锁定后，从草稿搭一个最小运行态。
  if (draft.personas.length >= 2 && draft.worldBible.theme) {
    // 与内置世界结构相同就直接用富数据，否则搭骨架
  }
  const personas = draft.personas.length ? draft.personas : xianxiaDraft().personas;
  const endings = draft.worldBible.candidateEndings?.length
    ? draft.worldBible.candidateEndings
    : xianxiaDraft().worldBible.candidateEndings!;
  const base = xianxiaRuntime();
  base.personas = personas;
  base.endings = endings;
  return base;
}

export class MockAdapter implements EngineAdapter {
  async getConfig(): Promise<ApiConfig> {
    const raw = localStorage.getItem(CONFIG_KEY);
    return raw ? JSON.parse(raw) : { llmApiKey: '', baseUrl: 'https://api.deepseek.com', modelName: 'deepseek-v4-flash' };
  }
  async saveConfig(c: ApiConfig): Promise<void> {
    localStorage.setItem(CONFIG_KEY, JSON.stringify(c));
  }
  async testConnection(c: ApiConfig): Promise<boolean> {
    await sleep(500);
    return Boolean(c.llmApiKey && c.baseUrl);
  }

  async listTemplates() {
    return [
      {
        id: 'shuangwen_zhuangbi',
        label: '爽文 · 装逼打脸系统',
        description: '《最强装逼打脸系统》一脉：数值系统 + 三声道反差 + 立 flag → 自爆闭环。',
        world_hints: ['建议给主角带一套数值化系统（黑化值/进度条）'],
      },
    ];
  }
  async listProjects(): Promise<Project[]> {
    return [...engine.slices.values()].map((s) => ({ ...s.project })).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }
  async createProject(title: string, _type = 'original', _templateId = ''): Promise<Project> {
    const id = uid('proj');
    const project: Project = { id, title: title || '未命名小说', status: 'seeding', createdAt: now(), updatedAt: now() };
    engine.slices.set(id, {
      project,
      chat: [{ role: 'assistant', content: SEED_CHAT_INTRO[0].content, at: now() }],
      draft: emptyDraft(),
      sim: { playing: false, timer: null, subscribers: new Set() },
    });
    return { ...project };
  }
  async renameProject(id: string, title: string): Promise<void> {
    engine.get(id).project.title = title;
    engine.get(id).project.updatedAt = now();
  }
  async deleteProject(id: string): Promise<void> {
    const s = engine.slices.get(id);
    if (s) engine.stopTimer(s);
    engine.slices.delete(id);
  }

  async getSeedChat(projectId: string): Promise<SeedChatMessage[]> {
    return [...engine.get(projectId).chat];
  }
  async sendSeedMessage(projectId: string, content: string, onToken: (t: string) => void): Promise<SeedDraft> {
    const slice = engine.get(projectId);
    slice.chat.push({ role: 'user', content, at: now() });
    let acc = '';
    await engine.fillNextSeedStep(slice, (t) => {
      acc += t;
      onToken(t);
    });
    slice.chat.push({ role: 'assistant', content: acc, at: now() });
    slice.project.updatedAt = now();
    return structuredClone(slice.draft);
  }
  async getSeedDraft(projectId: string): Promise<SeedDraft> {
    return structuredClone(engine.get(projectId).draft);
  }
  async updateSeedDraft(projectId: string, draft: SeedDraft): Promise<void> {
    engine.get(projectId).draft = structuredClone(draft);
    engine.get(projectId).project.updatedAt = now();
  }
  async lockSeedAndStart(projectId: string): Promise<void> {
    const slice = engine.get(projectId);
    slice.project.status = 'writing';
    slice.runtime = slice.runtime ?? buildRuntimeFromDraft(slice.draft);
    slice.project.sceneCount = slice.runtime.scenes.length;
    engine.startTimer(slice);
  }

  private rt(projectId: string): MockRuntime {
    const s = engine.get(projectId);
    if (!s.runtime) throw new Error('项目尚未开始写作');
    return s.runtime;
  }

  async getWorldState(projectId: string): Promise<WorldState> {
    const rt = this.rt(projectId);
    return { facts: [...rt.facts], events: [...rt.events], tick: rt.tick };
  }
  async getBeats(projectId: string): Promise<Beat[]> {
    return [...this.rt(projectId).beats];
  }
  async getThreads(projectId: string): Promise<Thread[]> {
    return [...this.rt(projectId).threads];
  }
  async getEndings(projectId: string): Promise<Ending[]> {
    return [...this.rt(projectId).endings];
  }
  async getPersonas(projectId: string): Promise<Persona[]> {
    return [...this.rt(projectId).personas];
  }
  async getAgentKnowledge(projectId: string, agentId: string): Promise<KnowledgeEntry[]> {
    return [...(this.rt(projectId).knowledge[agentId] ?? [])];
  }
  async getReaderKnowledge(projectId: string, uptoDiscoursePos?: number): Promise<ReaderKnowledge[]> {
    const all = this.rt(projectId).reader;
    return uptoDiscoursePos == null ? [...all] : all.filter((r) => r.revealedDiscoursePos <= uptoDiscoursePos);
  }
  async getForeshadows(projectId: string): Promise<Foreshadow[]> {
    return [...this.rt(projectId).foreshadows];
  }
  async getScenes(projectId: string): Promise<Scene[]> {
    return [...this.rt(projectId).scenes].sort((a, b) => a.discourseOrder - b.discourseOrder);
  }
  async getChapters(projectId: string): Promise<Chapter[]> {
    const scenes = [...this.rt(projectId).scenes].sort((a, b) => a.discourseOrder - b.discourseOrder);
    const cn = (n: number) => '零一二三四五六七八九十'[n] ?? String(n);
    const PEAK = 0.66, MIN = 2;
    const groups: string[][] = [];
    let cur: string[] = [];
    for (const s of scenes) {
      cur.push(s.sceneId);
      if (s.targetTension >= PEAK && cur.length >= MIN) {
        groups.push(cur);
        cur = [];
      }
    }
    const out: Chapter[] = groups.map((ids, i) => ({ index: i + 1, title: `第${cn(i + 1)}章`, sceneIds: ids, status: 'done', climaxSceneId: ids[ids.length - 1] }));
    if (cur.length) out.push({ index: out.length + 1, title: `第${cn(out.length + 1)}章`, sceneIds: cur, status: 'ongoing', climaxSceneId: null });
    return out;
  }

  async getPlan(projectId: string): Promise<ProjectPlan> {
    // 离线 Mock 走旧机制，无大纲；返回未规划，前端给友好提示。
    void projectId;
    return { planned: false, parts: [], arcs: [], chapters: [], revealChain: [], inventory: [] };
  }
  async getDossier(projectId: string, agentId: string): Promise<Dossier> {
    const p = this.rt(projectId).personas.find((x) => x.id === agentId);
    if (!p) return { agentId, markdown: '' };
    const vals = p.values.map((v) => `${v.name}(${v.weight})`).join('、');
    const md = [
      `# ${p.name}`,
      '> 角色档案（离线 Mock，按当前设定即时生成）',
      '',
      '## 设定核心',
      `- **欲望**：${p.want || '（未定）'}`,
      `- **珍视**：${vals || '（无）'}`,
      `- **致命弱点**：${p.fatalFlaw || '（未定）'}`,
      '',
      '## 表达层',
      `- **说话方式**：${p.voice || '（未定）'}`,
      `- **习惯动作**：${p.mannerisms.join('、') || '（无）'}`,
      '',
      '## 已付代价',
      ...(p.costLedger.length ? p.costLedger.map((c) => `- ${c}`) : ['- （尚无）']),
    ].join('\n');
    return { agentId, markdown: md };
  }

  async finalizeProject(projectId: string): Promise<{ ok: boolean; reason?: string }> {
    const s = engine.get(projectId);
    if (!s.runtime || !s.runtime.scenes.length) return { ok: false, reason: '尚无成稿场景' };
    engine.stopTimer(s);
    s.project.status = 'completed';
    return { ok: true };
  }

  async updateTone(projectId: string, patch: Record<string, unknown>, confirm: boolean) {
    // 离线 Mock 无文风契约后端，回声 patch 即可（前端类型完整）。
    void projectId;
    return {
      genre: String(patch.genre ?? ''), primaryEffect: String(patch.primaryEffect ?? ''),
      register: String(patch.register ?? ''), sentenceRhythm: String(patch.sentenceRhythm ?? ''),
      dictionDo: (patch.dictionDo as string[]) ?? [], dictionDont: (patch.dictionDont as string[]) ?? [],
      deviceKit: (patch.deviceKit as string[]) ?? [], pacing: String(patch.pacing ?? ''),
      tensionCurveBias: String(patch.tensionCurveBias ?? ''), revealCadence: String(patch.revealCadence ?? ''),
      complexity: String(patch.complexity ?? ''), toneReference: String(patch.toneReference ?? ''),
      confirmed: confirm,
    };
  }

  // B0 文风模拟（离线 Mock：模块级内存，回声指纹）
  private _style: Record<string, StyleSkill | null> = {};
  async getStyleSkill(projectId: string): Promise<StyleSkill | null> {
    return this._style[projectId] ?? null;
  }
  async ingestStyleSkill(projectId: string, mode: 'works' | 'skill', text: string, name?: string, source?: string) {
    void mode;
    const sk: StyleSkill = {
      name: name || '未命名文风', source: source || '', register: '（离线示例）',
      rhythm: '长短交替', devices: [], dictionDo: [], dictionDont: [], motifs: [],
      samples: [text.slice(0, 120)],
      metrics: { avg_sentence_len: 18, sentence_len_variance: 40, punct_density: 55, ttr: 0.8, content_function_ratio: 3.2, semantic_leaping: 0.7, sensory_e: 2.5 },
      enabled: true,
    };
    this._style[projectId] = sk;
    return { ok: true, styleSkill: sk };
  }
  async setStyleEnabled(projectId: string, enabled: boolean) {
    const cur = this._style[projectId];
    if (cur) cur.enabled = enabled;
    return { ok: true, styleSkill: this._style[projectId] ?? null };
  }
  async deleteStyleSkill(projectId: string) {
    this._style[projectId] = null;
    return { ok: true };
  }

  async editChapter(projectId: string, chapterId: string, fields: Record<string, unknown>): Promise<{ ok: boolean }> {
    void projectId; void chapterId; void fields; // 离线 Mock 不持久化大纲编辑
    return { ok: true };
  }
  async replanChapter(): Promise<{ ok: boolean }> {
    return { ok: true };
  }
  async updateDisclosure(): Promise<{ ok: boolean }> {
    return { ok: true };
  }
  async deleteChapter(projectId: string, chapterId: string): Promise<{ ok: boolean }> {
    void projectId; void chapterId;
    return { ok: true };
  }
  async deepenBibleSection(projectId: string, section: string): Promise<{ ok: boolean; section?: string }> {
    void projectId;
    return { ok: false, section }; // Mock 离线不调 LLM
  }
  async distillAuthorSheet(_projectId: string, _text: string, _name = '', _genre = '') {
    return { ok: false as const } as any;
  }
  async listAuthorSheets(_projectId: string) { return []; }
  async getAuthorSheet(_projectId: string, _sheetId: number) { return {} as any; }
  async deleteAuthorSheet(_projectId: string, _sheetId: number) { return { ok: true }; }
  async getWritingSettings() { return { targetWords: 6000, minWords: 5400, maxWords: 7200, outlineFirst: false, autoChapterCount: 5, requireHumanAcceptance: true, styleProfileId: null }; }
  async saveWritingSettings(_projectId: string, settings: any) { return { targetWords: 6000, minWords: 5400, maxWords: 7200, outlineFirst: false, autoChapterCount: 5, requireHumanAcceptance: true, styleProfileId: null, ...settings }; }
  async buildStoryBible() { return { ok: true, status: 'ready' }; }
  async getStoryBibleStatus() { return { status: 'ready', type: 'original' as const, pendingDraftId: null, pendingChapterNo: null }; }
  async getStoryBible() { return null; }
  async importSourceText(_projectId: string, body: { text: string; filename?: string }) { return { ok: true, chapters: body.text.trim() ? 1 : 0, filename: body.filename || 'source.txt' }; }
  async importContinuationSources(_projectId: string, body: { text: string; filename?: string }) { return { ok: true, chapters: body.text.trim() ? 1 : 0, filename: body.filename || 'source.txt' }; }
  async getSourceChapters() { return []; }
  async updateSourceChapter() { return { ok: true }; }
  async resplitSource() { return { ok: true, chapters: 0 }; }
  async getContinuationSettings() {
    return {
      sourceTextHash: '',
      continuationHint: '',
      seriesId: '',
      sourceBookTitle: '',
      currentBookTitle: '',
      bookIndex: 1,
      writeMode: 'continue_current_book' as const,
      chapterStartNo: 1,
      latestSourceChapterNo: 0,
      continuationReady: false,
      continuationPhase: '',
      timePosition: '',
      protagonistStrategy: '',
      inheritUnresolvedThreads: true,
      experienceLayerEnabled: false,
      experienceLayerMode: 'essay',
      experienceSourcePath: '',
      experienceStyleLevel: 'high',
      activeLifeModelId: '',
    };
  }
  async saveContinuationSettings(_projectId: string, body: any) { return { ...(await this.getContinuationSettings()), ...body, ok: true }; }
  async startContinuationDistill() { return { ok: true, jobId: 'mock-job' }; }
  async getContinuationJob() {
    return {
      status: 'done',
      progress: 7,
      total: 7,
      currentStep: 'B7',
      steps: [
        { code: 'B1', label: '导入分章', status: 'done' as const },
        { code: 'B2', label: '世界书', status: 'done' as const },
        { code: 'B3', label: '人物地点势力', status: 'done' as const },
        { code: 'B4', label: '系列状态', status: 'done' as const },
        { code: 'B5', label: '图谱', status: 'done' as const },
        { code: 'B6', label: '文风', status: 'done' as const },
        { code: 'B7', label: '快照', status: 'done' as const },
      ],
    };
  }
  async getContinuationStyleDiagnostics(): Promise<ContinuationStyleDiagnostics> {
    return {
      corpus: {
        segmentCount: 0,
        clusterCount: 0,
        negativeSampleCount: 0,
        discourseCoverage: {},
        voiceCoverage: {},
        sceneCoverage: {},
        lowConfidenceSegments: [],
        clusters: [],
      },
      latestDraft: null,
    };
  }
  async lockContinuation() { return { ...(await this.getContinuationSettings()), continuationReady: true, ok: true }; }
  async createChapterDraft() { return { id: 0, chapterNo: 1, title: '', outline: '', prose: '', guidance: '', targetWords: 6000, mode: 'manual' as const, status: 'draft' as const, contextSnapshot: {}, candidateGroupId: '', stylePacket: {}, scoreBreakdown: {}, retrievedSegmentIds: [], revisionHistory: [], createdAt: '' }; }
  async getChapterDrafts() { return []; }
  async acceptChapterDraft() { return { ok: true, acceptedChapterId: 0, chapterNo: 1, title: '' }; }
  async forceAcceptChapterDraft() { return { ok: true, acceptedChapterId: 0, chapterNo: 1, title: '', forced: true }; }
  async rejectChapterDraft() { return { ok: true }; }
  async autoWriteChapters() { return { ok: true, draftIds: [] }; }
  async getAcceptedChapters() { return []; }

  async control(projectId: string, action: ControlAction): Promise<SimulationControlResult> {
    const slice = engine.get(projectId);
    if (action === 'play') engine.startTimer(slice);
    else if (action === 'pause') engine.stopTimer(slice);
    else if (action === 'step') engine.stepOnce(slice);
    return {
      ok: true,
      runningSim: slice.project.runningSim ?? false,
      pendingDraftId: null,
      pendingChapterNo: null,
      autoPausedReason: null,
    };
  }
  async injectGodAction(projectId: string, action: GodAction): Promise<void> {
    const rt = this.rt(projectId);
    switch (action.kind) {
      case 'add_event': {
        const ev: SimEvent = {
          eventId: uid('ev'),
          storyTime: ++rt.tick,
          actors: action.payload.actors ?? [],
          actionType: action.payload.actionType ?? '神迹',
          payload: action.payload.payload ?? '导演注入的事件。',
          perceivers: action.payload.perceivers ?? [],
          dramaScore: action.payload.dramaScore ?? 0.5,
        };
        rt.events.push(ev);
        engine.get(projectId).sim.subscribers.forEach((s) => s.onEvent(ev));
        break;
      }
      case 'edit_fact': {
        const f = rt.facts.find((x) => x.factId === action.factId);
        if (f) f.canonicalContent = action.newContent;
        break;
      }
      case 'reveal_to_reader': {
        const f = rt.facts.find((x) => x.factId === action.factId);
        if (f && !rt.reader.some((r) => r.factId === f.factId))
          rt.reader.push({ factId: f.factId, revealedVersion: f.canonicalContent, revealedDiscoursePos: rt.scenes.length, viaPov: 'god' });
        break;
      }
      case 'hide_from_reader': {
        rt.reader = rt.reader.filter((r) => r.factId !== action.factId);
        break;
      }
      case 'set_thread_priority': {
        const t = rt.threads.find((x) => x.threadId === action.threadId);
        if (t) t.priorityWeight = action.weight;
        break;
      }
      case 'add_entity': {
        const tick = ++rt.tick;
        const name = action.name || (action.entityType === 'object' ? `无名遗物${rt.facts.length}` : `无名客${rt.personas.length + 1}`);
        if (action.entityType === 'character') {
          const id = uid('char_gen');
          rt.personas.push({
            id, name, want: '在乱局里谋一条生路', values: [{ name: '自保', weight: 0.6 }],
            fatalFlaw: '疑心过重', obstacles: ['来历不明'], costThreshold: '可弃旧名',
            voice: '言辞闪烁', mannerisms: ['压低斗笠'], motifObjects: [], arcState: '初登场', costLedger: [],
          });
          rt.threads[0]?.involvedAgents.push(id);
        }
        const fid = uid('fact');
        rt.facts.push({ factId: fid, factType: 'event', canonicalContent: `${name}登场。`, storyTime: tick, involvedEntities: [] });
        const ev: SimEvent = { eventId: uid('ev'), storyTime: tick, actors: [name], actionType: action.entityType === 'object' ? '现物' : '登场', payload: `${name}出现在故事里。`, perceivers: [], dramaScore: 0.5 };
        rt.events.push(ev);
        engine.get(projectId).sim.subscribers.forEach((s) => s.onEvent(ev));
        break;
      }
    }
    engine.get(projectId).sim.subscribers.forEach((s) => s.onDelta({ god: action.kind }));
  }

  subscribe(projectId: string, onEvent: (e: SimEvent) => void, onStateDelta: (d: unknown) => void): () => void {
    const slice = engine.get(projectId);
    const sub = { onEvent, onDelta: onStateDelta };
    slice.sim.subscribers.add(sub);
    return () => {
      slice.sim.subscribers.delete(sub); // 仅退订；计时器继续，不打断模拟
    };
  }
}
