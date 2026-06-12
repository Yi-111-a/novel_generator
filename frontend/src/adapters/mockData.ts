// MockAdapter 的内置数据：一个修真复仇小世界（主角沈砚），供无后端时预览全部视图。
import type {
  Beat,
  Ending,
  Fact,
  Foreshadow,
  KnowledgeEntry,
  Persona,
  ReaderKnowledge,
  Scene,
  SeedChatMessage,
  SeedCompleteness,
  SeedDraft,
  SimEvent,
  Thread,
} from '../types';

export interface MockRuntime {
  facts: Fact[];
  events: SimEvent[];
  tick: number;
  beats: Beat[];
  threads: Thread[];
  endings: Ending[];
  personas: Persona[];
  knowledge: Record<string, KnowledgeEntry[]>; // agentId -> entries
  reader: ReaderKnowledge[];
  foreshadows: Foreshadow[];
  scenes: Scene[];
}

export const EMPTY_CHECKLIST: SeedCompleteness = {
  ready: false,
  checklist: [
    { key: 'immutable', label: '不可变设定已定（世界/地理/文化/物理法则）', done: false },
    { key: 'theme', label: '主题已定', done: false },
    { key: 'endings', label: '≥2 个候选结局', done: false },
    { key: 'personas', label: '≥2 个角色且 persona 核心齐全', done: false },
    { key: 'asymmetry', label: '至少一个初始信息不对称', done: false },
  ],
};

export function emptyDraft(): SeedDraft {
  return {
    worldBible: { physicsRules: [], candidateEndings: [] },
    personas: [],
    completeness: structuredClone(EMPTY_CHECKLIST),
  };
}

/** 修真复仇种子：完整且 ready。 */
export function xianxiaDraft(): SeedDraft {
  return {
    worldBible: {
      settingCore: '青冥修真界。灵气为尊，剑修最盛，二十年前一场灭门旧案至今讳莫如深。',
      geography: '九峰环抱的青冥山脉；山下是依附宗门的市井村镇。',
      culture: '门第森严，弟子重道义、轻性命；私藏禁术者天下共诛。',
      physicsRules: ['修真世界没有手机、电、互联网', '凡人不能御剑飞行', '已死之人不可复生'],
      protagonistWant: '为灭门的师门查清真相、手刃真凶',
      theme: '复仇的代价',
      candidateEndings: [
        {
          id: 'end_revenge',
          summary: '沈砚揭穿玄霜真人，血债血偿，自身却也万劫不复。',
          themeExpression: '复仇得偿，代价是再无归处。',
          requiredConditions: ['真凶身份被公开', '沈砚付出不可逆代价'],
          activeWeight: 0.55,
        },
        {
          id: 'end_mercy',
          summary: '沈砚在最后一刻收剑，选择让真相而非血来终结仇恨。',
          themeExpression: '复仇的代价，是学会不被它吞没。',
          requiredConditions: ['真相被公开', '沈砚放弃亲手了结'],
          activeWeight: 0.45,
        },
      ],
    },
    personas: xianxiaPersonas(),
    completeness: { ready: true, checklist: EMPTY_CHECKLIST.checklist.map((c) => ({ ...c, done: true })) },
  };
}

function xianxiaPersonas(): Persona[] {
  return [
    {
      id: 'shen_yan',
      name: '沈砚',
      want: '为灭门的师门查清真相、手刃真凶',
      values: [
        { name: '对师门的道义', weight: 0.9 },
        { name: '苟全性命', weight: 0.3 },
      ],
      fatalFlaw: '偏执，认定的事不计后果',
      obstacles: ['仇家势大', '身负旧伤'],
      costThreshold: '可舍命，不可舍真相',
      voice: '冷峭少言，问到痛处才开口',
      mannerisms: ['摩挲那半截断剑', '垂眸不看人眼'],
      motifObjects: ['obj_broken_sword'],
      arcState: '复仇起点：尚未动摇',
      costLedger: [],
    },
    {
      id: 'chu_hongxiao',
      name: '楚红绡',
      want: '守住楚家、护住无辜',
      values: [
        { name: '亲情', weight: 0.8 },
        { name: '公义', weight: 0.55 },
      ],
      fatalFlaw: '心软，见不得人受苦',
      obstacles: ['身为仇家之女', '不知家族旧恶'],
      costThreshold: '可背家族，不可背良心',
      voice: '温言却有锋',
      mannerisms: ['以袖掩唇', '替人理衣角'],
      motifObjects: ['obj_jade_hairpin'],
      arcState: '尚不知家族真相',
      costLedger: [],
    },
    {
      id: 'xuan_shuang',
      name: '玄霜真人',
      want: '永远掩盖二十年前的真相',
      values: [{ name: '宗门权位', weight: 0.9 }],
      fatalFlaw: '多疑，宁错杀不放过',
      obstacles: ['沈砚步步紧逼'],
      costThreshold: '可弃旧部，不可弃权位',
      voice: '滴水不漏，惯用反问',
      mannerisms: ['以指节叩案'],
      motifObjects: [],
      arcState: '稳坐高位',
      costLedger: [],
    },
  ];
}

export function xianxiaRuntime(): MockRuntime {
  const facts: Fact[] = [
    { factId: 'fact_massacre', factType: 'event', canonicalContent: '二十年前青冥剑派一夜灭门，沈砚是唯一幸存弟子。', storyTime: 0, locationId: 'loc_qingming', involvedEntities: ['shen_yan'] },
    { factId: 'fact_true_killer', factType: 'event', canonicalContent: '真凶是玄霜真人，他嫁祸于已亡的客卿。', storyTime: 0, involvedEntities: ['xuan_shuang'] },
    { factId: 'fact_token', factType: 'state', canonicalContent: '沈砚怀中的半截断剑，是师父临终所托的信物。', storyTime: 0, involvedEntities: ['shen_yan', 'obj_broken_sword'] },
    { factId: 'fact_chu_kind', factType: 'event', canonicalContent: '楚红绡曾暗中接济灭门案的流落遗孤。', storyTime: 1, involvedEntities: ['chu_hongxiao'] },
  ];

  const knowledge: Record<string, KnowledgeEntry[]> = {
    shen_yan: [
      { agentId: 'shen_yan', factId: 'fact_massacre', versionContent: facts[0].canonicalContent, confidence: 1, learnedTick: 0 },
      { agentId: 'shen_yan', factId: 'fact_token', versionContent: facts[2].canonicalContent, confidence: 1, learnedTick: 0 },
    ],
    xuan_shuang: [
      { agentId: 'xuan_shuang', factId: 'fact_true_killer', versionContent: facts[1].canonicalContent, confidence: 1, learnedTick: 0 },
      { agentId: 'xuan_shuang', factId: 'fact_massacre', versionContent: facts[0].canonicalContent, confidence: 1, learnedTick: 0 },
    ],
    // 楚红绡持有被家族扭曲过的版本（versionContent ≠ canonical）→ 演示"误传"
    chu_hongxiao: [
      { agentId: 'chu_hongxiao', factId: 'fact_massacre', versionContent: '据家中长辈说，青冥剑派是私炼禁术、走火入魔自焚的。', confidence: 0.6, learnedTick: 0 },
      { agentId: 'chu_hongxiao', factId: 'fact_chu_kind', versionContent: facts[3].canonicalContent, confidence: 1, learnedTick: 1 },
    ],
  };

  const reader: ReaderKnowledge[] = [
    { factId: 'fact_massacre', revealedVersion: facts[0].canonicalContent, revealedDiscoursePos: 1, viaPov: 'shen_yan' },
    { factId: 'fact_chu_kind', revealedVersion: facts[3].canonicalContent, revealedDiscoursePos: 2, viaPov: 'chu_hongxiao' },
  ];

  const beats: Beat[] = [
    { beatId: 'beat_1', sequenceOrder: 1, type: 'structural', goal: '沈砚重返青冥山，旧案浮现', threads: ['thread_revenge'], targetTension: 0.6, targetEndingLink: 'end_revenge', status: 'done' },
    { beatId: 'beat_2', sequenceOrder: 2, type: 'decision', goal: '沈砚与楚红绡相遇，立场相撞', threads: ['thread_revenge', 'thread_doubt'], targetTension: 0.45, targetEndingLink: '', status: 'active' },
    { beatId: 'beat_3', sequenceOrder: 3, type: 'decision', goal: '一个逼沈砚在道义与性命间抉择的处境', threads: ['thread_revenge'], targetTension: 0.8, targetEndingLink: 'end_revenge', status: 'planned' },
    { beatId: 'beat_4', sequenceOrder: 4, type: 'structural', goal: '楚红绡撞见家族旧恶的线索', threads: ['thread_doubt'], targetTension: 0.7, targetEndingLink: '', status: 'planned' },
    { beatId: 'beat_5', sequenceOrder: 5, type: 'decision', goal: '真凶身份揭晓——回收主伏笔', threads: ['thread_revenge', 'thread_doubt'], targetTension: 0.95, targetEndingLink: 'end_revenge', status: 'planned' },
  ];

  const threads: Thread[] = [
    { threadId: 'thread_revenge', centralQuestion: '沈砚能否查明真凶并复仇？', involvedAgents: ['shen_yan', 'xuan_shuang'], priorityWeight: 0.9, currentTension: 0.55, lastAdvancedTick: 2, status: 'open' },
    { threadId: 'thread_doubt', centralQuestion: '楚红绡会站在家族还是真相一边？', involvedAgents: ['chu_hongxiao', 'shen_yan'], priorityWeight: 0.45, currentTension: 0.3, lastAdvancedTick: 1, status: 'open' },
  ];

  const foreshadows: Foreshadow[] = [
    { foreshadowId: 'fs_true_killer', plantedDiscoursePos: 1, question: '灭门那夜，真正的黑手是谁？', linkedFactId: 'fact_true_killer', mustResolve: true, targetPayoffBeat: 'beat_5', status: 'open' },
    { foreshadowId: 'fs_token', plantedDiscoursePos: 1, question: '半截断剑究竟开启什么？', linkedFactId: 'fact_token', mustResolve: true, targetPayoffBeat: '', status: 'open' },
    { foreshadowId: 'fs_chu', plantedDiscoursePos: 2, question: '楚红绡为何独独善待那些遗孤？', linkedFactId: 'fact_chu_kind', mustResolve: false, targetPayoffBeat: '', status: 'paid_off' },
  ];

  const scenes: Scene[] = [
    {
      sceneId: 'sc_1',
      discourseOrder: 1,
      sourceEvents: ['ev_1'],
      pov: 'shen_yan',
      targetTension: 0.9,
      newlyRevealed: ['fact_massacre'],
      proseText:
        '雨水顺着断碑的刻痕往下淌。沈砚在青冥山门前站了很久，指腹一寸寸碾过那半截断剑的缺口。\n二十年了。山门上的漆早已剥落，唯有当年那场火的焦痕，还咬在石阶深处。\n他没有抬头，只把剑收回袖中，像收起一句没说出口的话。',
    },
    {
      sceneId: 'sc_2',
      discourseOrder: 2,
      sourceEvents: ['ev_2'],
      pov: 'chu_hongxiao',
      targetTension: 0.3,
      newlyRevealed: ['fact_chu_kind'],
      proseText:
        '楚红绡把最后一包米塞进孩子怀里，又替他理了理破了口的衣角。\n檐外的雨小了些。她回头望了一眼那座灯火通明的家宅，没有进去，先沿着巷子把伞撑到了流民棚上。',
    },
  ];

  const events: SimEvent[] = [
    { eventId: 'ev_1', storyTime: 1, actors: ['shen_yan'], actionType: '重返故地', payload: '沈砚立于青冥山门，凭吊灭门旧址。', locationId: 'loc_qingming', perceivers: ['shen_yan'], dramaScore: 0.9, beatId: 'beat_1' },
    { eventId: 'ev_2', storyTime: 2, actors: ['chu_hongxiao'], actionType: '接济流民', payload: '楚红绡暗中接济灭门案遗孤。', perceivers: ['chu_hongxiao'], dramaScore: 0.35, beatId: 'beat_2' },
    { eventId: 'ev_3', storyTime: 3, actors: ['shen_yan', 'chu_hongxiao'], actionType: '相遇', payload: '两人在市井相遇，立场暗中相撞。', locationId: 'loc_town', perceivers: ['shen_yan', 'chu_hongxiao'], dramaScore: 0.7, beatId: 'beat_2' },
  ];

  return { facts, events, tick: 3, beats, threads, endings: xianxiaDraft().worldBible.candidateEndings!, personas: xianxiaPersonas(), knowledge, reader, foreshadows, scenes };
}

export const SEED_CHAT_INTRO: SeedChatMessage[] = [
  {
    role: 'assistant',
    content:
      '我们一起来播下这部小说的种子吧。先告诉我：你想要一个什么样的世界？它的主题、你心里的主角、以及你期待的那种冲突——任何一点都可以先聊起来。',
    at: new Date().toISOString(),
  },
];
