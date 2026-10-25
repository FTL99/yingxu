import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Aperture,
  ArrowUpRight,
  ArrowRight,
  Plus,
  X,
  Check,
  ChevronRight,
  CircleHelp,
  Clapperboard,
  Film,
  FlaskConical,
  FolderOpen,
  LayoutDashboard,
  Library,
  Settings2,
  ShieldCheck,
  Sparkles,
  Users,
  Play,
  Upload,
  Search,
  RefreshCw,
  Clock3,
  AlertCircle,
  Download,
  FileText,
  CheckCircle2,
  GitBranch,
  Wallet,
  ExternalLink,
  Pencil,
} from "lucide-react";
import "./style.css";
import MarkdownView from "./MarkdownView";
import RoleConversation from "./RoleConversation";
import ReplaceImage from "./ReplaceImage";
import { MediaProviderSettings, MediaGenerationPanel } from "./MediaProviders";

type Data = Record<string, any>;
type RecordItem = {
  id: string;
  kind: string;
  version: number;
  data: Data;
  input_refs: Data[];
  stale: boolean;
  created_at: string;
};
type Snapshot = {
  capabilities: Data;
  project: Data;
  records: RecordItem[];
  tasks: Data[];
  balance: Data;
  exports: Data[];
  media_jobs: Data[];
  events: Data[];
};
type Role = {
  id: string;
  name: string;
  description: string;
  prompt_version: string;
};
type Tab =
  | "overview"
  | "hypotheses"
  | "agents"
  | "references"
  | "storyboard"
  | "reviews";
let studioToken = "";
async function api(
  path: string,
  body?: Data | FormData,
  method = "GET",
  requestId: string = crypto.randomUUID(),
): Promise<any> {
  const response = await fetch("/api" + path, {
    method,
    headers: {
      ...(body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      "X-Studio-Token": studioToken,
      "X-Request-ID": requestId,
    },
    ...(body
      ? { body: body instanceof FormData ? body : JSON.stringify(body) }
      : {}),
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : "输入格式有误，请检查必填项。",
    );
  return result;
}
const statusNames: Data = {
  draft: "待开始",
  discussing: "讨论中",
  running: "执行中",
  pending_review: "待验收",
  accepted: "已通过",
  changes_requested: "需修改",
  needs_review: "输入已变更",
  merged: "由总控承接",
  input_changed: "执行中输入变更",
  interrupted: "已中断",
  untested: "待验证",
  supported: "获得支持",
  contradicted: "被反证",
  inconclusive: "无法判断",
  completed: "已导出",
  failed: "失败",
};
const sourceNames: Data = {
  user_intuition: "用户直觉",
  model_inference: "模型推演",
  reference_observation: "参考观察",
  audience_feedback: "真实观众反馈",
  behavior_data: "行为数据",
  tool_test: "工具实测",
};
const navItems: { id: Tab; name: string; icon: typeof Aperture }[] = [
  { id: "overview", name: "项目概览", icon: LayoutDashboard },
  { id: "hypotheses", name: "假设与实验", icon: FlaskConical },
  { id: "agents", name: "Agent 工作流", icon: Users },
  { id: "references", name: "参考资料", icon: Library },
  { id: "storyboard", name: "分镜与素材", icon: Clapperboard },
  { id: "reviews", name: "验收与放行", icon: ShieldCheck },
];
const roleColors: Data = {
  director: "#dbedb6",
  audience: "#ecb8a2",
  writer: "#c6c2ed",
  assets: "#eddda1",
  storyboard: "#afd8dd",
  post: "#c6d3c0",
};
function Badge({ status, stale = false }: { status: string; stale?: boolean }) {
  return (
    <span className={"badge " + (stale ? "warning" : status)}>
      <span />
      {stale ? "需复查" : statusNames[status] || status}
    </span>
  );
}
function Empty({
  title,
  text,
  action,
}: {
  title: string;
  text: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Aperture size={30} />
      </div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}
function Button({
  children,
  onClick,
  primary = false,
  disabled = false,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  primary?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      className={primary ? "button primary" : "button"}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function Dialog({
  title,
  subtitle,
  children,
  onClose,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="modal-scrim" onClick={onClose}>
      <section
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <div>
            <span className="eyebrow">映序 / WORKSPACE</span>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          <button className="icon-button" aria-label="关闭" onClick={onClose}>
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
function App() {
  const [projects, setProjects] = useState<Data[]>([]),
    [pid, setPid] = useState(""),
    [snapshot, setSnapshot] = useState<Snapshot | null>(null),
    [roles, setRoles] = useState<Role[]>([]),
    [runtime, setRuntime] = useState<Data>({}),
    [gemini, setGemini] = useState<Data>({}),
    [mediaSettings, setMediaSettings] = useState<Data>({}),
    [tab, setTab] = useState<Tab>("overview"),
    [modal, setModal] = useState<Data | null>(null),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [busy, setBusy] = useState(false),
    [query, setQuery] = useState(""),
    [chatTaskId, setChatTaskId] = useState("");
  const [exportAudio, setExportAudio] = useState<Data>({ preserve_source_audio: true, source_gain: 1,
    narration_asset_id: null, narration_gain: 1, narration_start_seconds: 0 });
  useEffect(() => { setExportAudio({ preserve_source_audio: true, source_gain: 1,
    narration_asset_id: null, narration_gain: 1, narration_start_seconds: 0 }); }, [pid]);
  const selectedProject = useRef(pid);
  selectedProject.current = pid;
  async function refresh(id = selectedProject.current) {
    const p = await api("/projects");
    setProjects(p);
    if (id) {
      const fresh = await api("/projects/" + id);
      if (selectedProject.current === id) setSnapshot(fresh);
    }
    setRuntime(await api("/runtime"));
    setGemini(await api("/gemini"));
    setMediaSettings(await api("/media-settings"));
  }
  useEffect(() => {
    (async () => {
      studioToken = (await api("/health")).token;
      setRoles(await api("/roles"));
      await refresh();
      const p = await api("/projects");
      if (p.length) {
        setPid(p[0].id);
        setSnapshot(await api("/projects/" + p[0].id));
      }
    })().catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (pid) refresh(pid).catch((e) => setError(e.message));
  }, [pid]);
  useEffect(() => {
    if (
      !snapshot?.tasks.some(
        (t) => t.status === "running" || t.status === "input_changed",
      ) &&
      !snapshot?.exports.some((e) => e.status === "running") &&
      !snapshot?.media_jobs?.some((j) => j.status === "running")
    )
      return;
    const timer = setInterval(
      () => refresh().catch((e) => setError(e.message)),
      2500,
    );
    return () => clearInterval(timer);
  }, [snapshot, pid]);
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(""), 4500);
      return () => clearTimeout(t);
    }
  }, [toast]);
  const run = async (
    action: () => Promise<any>,
    message = "已保存",
    close = true,
  ) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await refresh();
      if (close) setModal(null);
      setToast(message);
      return true;
    } catch (e) {
      await refresh().catch(() => {});
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const records = (kind: string) =>
    snapshot?.records.filter((r) => r.kind === kind) || [];
  const hypotheses = records("hypothesis"),
    shots = records("shot").sort(
      (a, b) => (a.data.order || 0) - (b.data.order || 0),
    ),
    assets = records("asset"),
    artifacts = records("artifact"),
    tasks = snapshot?.tasks || [];
  const accepted = tasks.filter((t) => t.status === "accepted").length;
  const project = snapshot?.project;
  const chatTask =
    tasks.find((t) => t.id === chatTaskId && !t.superseded_by) ||
    tasks.find(
      (t) =>
        !t.superseded_by &&
        !t.merged_into &&
        t.status !== "accepted" &&
        !t.blocker,
    ) ||
    tasks.find((t) => !t.superseded_by);
  useEffect(() => {
    if (chatTask && chatTaskId !== chatTask.id) setChatTaskId(chatTask.id);
  }, [pid, chatTask?.id]);
  const nextChatTask =
    chatTask &&
    tasks.find(
      (t) =>
        !t.superseded_by &&
        t.status !== "accepted" &&
        !t.blocker &&
        t.dependencies.includes(chatTask.id),
    );

  const saveRecord = (
    kind: string,
    data: Data,
    record?: RecordItem,
    refs: Data[] = [],
  ) =>
    api(
      `/projects/${pid}/records/${kind}`,
      {
        data,
        id: record?.id,
        expected_version: record?.version || 0,
        input_refs: refs,
      },
      "POST",
    );
  async function createExample() {
    const p = await api(
      "/projects",
      {
        name: "最后一盏灯",
        brief:
          "一位在城市独居的年轻人，每晚回家都会看见对面窗户亮着。某晚那扇窗变暗，他第一次敲响邻居的门。结尾揭示：那盏灯也一直在等他。这个故事方向用于演示，尚未经过观众测试。",
        audience: "在城市独居、渴望被看见的年轻人（示例假设）",
        promise: "让观众感受到微小善意带来的连接",
        action: "关注",
        followup: "持续讲述城市里被忽略的温柔故事",
        duration: 15,
        aspect: "9:16",
        budget: 0,
        sample: true,
      },
      "POST",
    );
    const h = await api(
      `/projects/${p.id}/records/hypothesis`,
      {
        data: {
          claim: "独居观众会对「有人在等你回家」产生情绪共鸣",
          owner: "audience",
          risk: "高",
          scope: "城市独居的年轻人",
          failure_impact: "高潮无法形成共鸣，整片内容承诺失效",
          test_plan: "用粗分镜让目标观众复述故事并描述情绪原因",
          pass_criteria: "测试前与用户约定，当前未知",
          counter_criteria: "目标观众不能理解或不在乎结尾",
          source_type: "user_intuition",
          evidence_status: "untested",
          evidence: "",
        },
      },
      "POST",
    );
    await api(
      `/projects/${p.id}/records/hypothesis`,
      {
        data: {
          claim: "情绪共鸣能让观众期待同主题后续故事并按下关注",
          owner: "audience",
          risk: "高",
          scope: "看过高潮的目标观众",
          failure_impact: "有共鸣却没有关注理由",
          test_plan: "把共鸣与后续期待分开记录；真实关注行为另测",
          pass_criteria: "尚未约定",
          counter_criteria: "只喜欢这一个故事，缺乏后续内容期待",
          source_type: "model_inference",
          evidence_status: "untested",
          evidence: "",
        },
      },
      "POST",
    );
    const frames = [
      [
        "夜归",
        "青年走进安静的楼道，停下脚步，抬头看向一扇暖色窗。",
        "每天，总有一盏灯亮着。",
        5,
      ],
      [
        "暗下的窗",
        "对面的窗第一次没有亮，青年望向那个熟悉的位置。",
        "直到那个晚上。",
        4,
      ],
      [
        "有人在等",
        "敲门后的两个身影被同一盏灯照亮；动作留待关键镜头实测。",
        "原来，我们都在等一个人。",
        6,
      ],
    ];
    for (let i = 0; i < frames.length; i++)
      await api(
        `/projects/${p.id}/records/shot`,
        {
          data: {
            title: frames[i][0],
            description: frames[i][1],
            subtitle: frames[i][2],
            duration: frames[i][3],
            order: i + 1,
            camera: "固定机位",
            emotion: i === 2 ? "连接" : "孤独",
            status: "draft",
          },
          input_refs: [{ kind: "hypothesis", id: h.id, version: h.version }],
        },
        "POST",
      );
    await api(`/projects/${p.id}/pipeline`, {}, "POST");
    setPid(p.id);
    setSnapshot(await api("/projects/" + p.id));
    setToast("已建立示例 · 所有假设均待验证");
  }
  function recordDialog(kind: string, record?: RecordItem) {
    setModal({ type: "record", kind, record });
  }
  const nav = (next: Tab) => {
    setTab(next);
    setQuery("");
  };
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Aperture size={25} />
          </div>
          <span>
            映序<small>STORY, THEN STUDIO.</small>
          </span>
        </div>
        <div className="workspace-label">
          制作工作台 <span>V0.1</span>
        </div>
        <button
          className="project-switch"
          onClick={() => setModal({ type: "projects" })}
        >
          <div className="project-avatar">
            <Film size={18} />
          </div>
          <div>
            <strong>{project?.name || "选择你的项目"}</strong>
            <small>
              {project?.sample ? "示例项目 · 待验证" : "本地项目空间"}
            </small>
          </div>
          <ChevronRight size={15} />
        </button>
        <nav>
          {navItems.map((n) => (
            <button
              key={n.id}
              className={tab === n.id ? "active" : ""}
              onClick={() => nav(n.id)}
            >
              <n.icon size={18} />
              {n.name}
              {n.id === "hypotheses" && hypotheses.length > 0 && (
                <span>{hypotheses.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="status-dot" />
          <strong>先验证，再制作</strong>
          <p>把最昂贵的错误，留在最便宜的阶段。</p>
          <div className="note-orbit">
            <span />
            <span />
            <span />
          </div>
        </div>
        <div className="sidebar-bottom">
          <button onClick={() => setModal({ type: "settings" })}>
            <Settings2 size={17} />
            运行设置
            <span className={runtime.ready ? "online-dot" : "offline-dot"} />
          </button>
          <a href="http://127.0.0.1:8765/" target="_blank" rel="noreferrer">
            <GitBranch size={17} /> AP-Vibe 项目资料 <ExternalLink size={12} />
          </a>
          <small>素材与记录保存在本机</small>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            工作台 <ChevronRight size={12} />{" "}
            <span>{navItems.find((n) => n.id === tab)?.name}</span>
          </div>
          <div className="top-actions">
            <span className="runtime-chip">
              <span className={runtime.ready ? "online-dot" : "offline-dot"} />
              {runtime.ready ? "DeepSeek 已就绪" : "手动制作模式"}
            </span>
            <button
              className="icon-button"
              aria-label="刷新"
              onClick={() => run(() => refresh(), "已刷新", false)}
            >
              <RefreshCw size={16} />
            </button>
            <div className="user-avatar">导</div>
          </div>
        </header>
        {error && (
          <div className="error-banner">
            <AlertCircle size={17} />
            {error}
            <button aria-label="关闭错误" onClick={() => setError("")}>
              <X size={15} />
            </button>
          </div>
        )}
        {toast && (
          <div className="toast">
            <CheckCircle2 size={18} />
            {toast}
          </div>
        )}
        <main>
          <div className="page-heading">
            <div>
              <div className="eyebrow">SHORT FILM / CREATIVE WORKSPACE</div>
              <h1>{navItems.find((n) => n.id === tab)?.name}</h1>
              <p>
                {
                  {
                    overview: "让每个创意都有依据，让每次制作都有方向。",
                    hypotheses: "先找到一旦失败，损失最大的判断。",
                    agents: "六种专业职责，通过版本和验收协作。",
                    references: "理解参考为什么有效，再决定如何迁移。",
                    storyboard: "先看完整的情绪路径，再投入镜头生产。",
                    reviews: "完成不等于通过；证据结论与制作批准分开记录。",
                  }[tab]
                }
              </p>
            </div>
            {!project && (
              <Button primary onClick={() => setModal({ type: "project" })}>
                <Plus size={16} />
                新建项目
              </Button>
            )}
          </div>
          {!project ? (
            <div className="welcome">
              <div className="welcome-copy">
                <span className="eyebrow">FROM A QUESTION TO A FILM</span>
                <h2>
                  一个好故事，
                  <br />
                  从值得验证的
                  <br />
                  <em>假设</em>开始。
                </h2>
                <p>
                  定义观众，找到他们在乎的事。
                  <br />
                  让六位 Agent 把想法变成可评审的作品。
                </p>
                <div className="inline">
                  <Button primary onClick={() => setModal({ type: "project" })}>
                    开始你的第一部短片
                    <ArrowRight size={16} />
                  </Button>
                  <Button onClick={() => run(createExample, "示例已建立")}>
                    探索示例项目
                  </Button>
                </div>
                <small>无 API Key 也可管理项目、提交成果、导出动态分镜。</small>
              </div>
              <div className="welcome-art">
                <div className="film-frame f-one">
                  <span>01 / THE QUESTION</span>
                  <div className="moon" />
                  <div className="window-light" />
                  <p>他们为什么在乎？</p>
                </div>
                <div className="film-frame f-two">
                  <span>02 / THE CONNECTION</span>
                  <div className="arch" />
                  <p>先共鸣，再行动。</p>
                </div>
                <div className="art-caption">
                  <Sparkles size={15} />
                  每个镜头，都服务一个明确的目的。
                </div>
              </div>
            </div>
          ) : (
            <>
              {tab === "overview" && (
                <>
                  <section className="project-hero">
                    <div className="hero-content">
                      <div className="inline">
                        <span className="eyebrow">
                          PROJECT {project.id.slice(-4).toUpperCase()}
                        </span>
                        <span className="hero-tag">
                          {project.sample ? "示例 · 全部待验证" : "制作中"}
                        </span>
                      </div>
                      <h2>{project.name}</h2>
                      <p>
                        {project.promise ||
                          "先写下你想让观众获得的情绪或意义。"}
                      </p>
                      <div className="hero-meta">
                        <span>
                          <Clock3 size={14} />
                          {project.duration} 秒
                        </span>
                        <span>
                          <Clapperboard size={14} />
                          {project.aspect}
                        </span>
                        <span>
                          <Users size={14} />
                          {project.audience || "观众待定义"}
                        </span>
                      </div>
                      <Button
                        onClick={() => setModal({ type: "project", project })}
                      >
                        编辑项目 Brief
                        <Pencil size={13} />
                      </Button>
                    </div>
                    <div className="hero-art">
                      <div className="hero-aperture">
                        <Aperture size={116} strokeWidth={0.8} />
                      </div>
                      <div className="hero-art-label">
                        STORY
                        <br />
                        <em>with intent.</em>
                      </div>
                    </div>
                  </section>
                  <div className="stats-grid">
                    <div className="stat">
                      <span>
                        关键假设 <FlaskConical size={16} />
                      </span>
                      <strong>
                        {hypotheses.length}
                        <small>项</small>
                      </strong>
                      <p>
                        {
                          hypotheses.filter(
                            (h) =>
                              h.data.evidence_status === "supported" &&
                              !h.stale,
                          ).length
                        }{" "}
                        项获得证据支持
                      </p>
                    </div>
                    <div className="stat">
                      <span>
                        角色任务 <Users size={16} />
                      </span>
                      <strong>
                        {accepted}
                        <small>/ {tasks.length}</small>
                      </strong>
                      <p>已验收 / 总任务数</p>
                    </div>
                    <div className="stat">
                      <span>
                        分镜时长 <Film size={16} />
                      </span>
                      <strong>
                        {shots.reduce(
                          (s, r) => s + Number(r.data.duration || 0),
                          0,
                        )}
                        <small>秒</small>
                      </strong>
                      <p>
                        {shots.length} 个镜头 · 目标 {project.duration} 秒
                      </p>
                    </div>
                    <div className="stat">
                      <span>
                        费用管理 <Wallet size={16} />
                      </span>
                      <strong>
                        {snapshot?.balance.actual.toFixed(2)}
                        <small>元</small>
                      </strong>
                      <p>
                        预留 {snapshot?.balance.reserved.toFixed(2)} / 预算{" "}
                        {project.budget}
                      </p>
                    </div>
                  </div>
                  <div className="overview-grid">
                    <div>
                      <section className="panel">
                        <div className="section-title">
                          <h3>
                            制作路径 <span>THE PROCESS</span>
                          </h3>
                          <button onClick={() => nav("reviews")}>
                            查看放行
                            <ArrowUpRight size={14} />
                          </button>
                        </div>
                        <div className="process-path">
                          {[
                            {
                              title: "观众与概念",
                              desc: "G0 · 有人会在乎吗？",
                            },
                            {
                              title: "故事与资产",
                              desc: "G1 · 能理解和共鸣吗？",
                            },
                            {
                              title: "关键镜头",
                              desc: "G2 · 能够表达出来吗？",
                            },
                            {
                              title: "整片交付",
                              desc: "G3 · 真正值得关注吗？",
                            },
                          ].map((s, i) => {
                            const d = records("decision").find(
                              (d) =>
                                d.data.stage === "G" + i &&
                                !d.stale &&
                                d.data.allow,
                            );
                            return (
                              <div
                                className={"process-step " + (d ? "done" : "")}
                                key={s.title}
                              >
                                <div className="step-number">
                                  {d ? (
                                    <Check size={14} />
                                  ) : (
                                    String(i + 1).padStart(2, "0")
                                  )}
                                </div>
                                <strong>{s.title}</strong>
                                <small>{s.desc}</small>
                                <span>{d ? "已放行" : "待评审"}</span>
                              </div>
                            );
                          })}
                        </div>
                      </section>
                      <section className="panel">
                        <div className="section-title">
                          <h3>最值得先验证的事</h3>
                          <button onClick={() => nav("hypotheses")}>
                            全部假设
                            <ArrowUpRight size={14} />
                          </button>
                        </div>
                        {hypotheses.length ? (
                          hypotheses.slice(0, 3).map((h, i) => (
                            <button
                              className="hypothesis-row"
                              key={h.id}
                              onClick={() => recordDialog("hypothesis", h)}
                            >
                              <span className="row-number">
                                H{String(i + 1).padStart(2, "0")}
                              </span>
                              <div>
                                <strong>{h.data.claim}</strong>
                                <small>
                                  {h.data.failure_impact || "失败损失待定义"}
                                </small>
                              </div>
                              <Badge
                                status={h.data.evidence_status || "untested"}
                                stale={h.stale}
                              />
                              <ChevronRight size={16} />
                            </button>
                          ))
                        ) : (
                          <Empty
                            title="还没有关键假设"
                            text="把观众、情绪和关注动机，分别写成可以被反证的判断。"
                            action={
                              <Button
                                onClick={() => recordDialog("hypothesis")}
                              >
                                <Plus size={14} />
                                添加假设
                              </Button>
                            }
                          />
                        )}
                      </section>
                    </div>
                    <div>
                      <section className="panel next-panel">
                        <span className="eyebrow">YOUR NEXT MOVE</span>
                        <div className="next-icon">
                          <ArrowUpRight size={23} />
                        </div>
                        <h3>
                          {!hypotheses.length
                            ? "从观众开始"
                            : !tasks.length
                              ? "建立六角色流程"
                              : "继续，保持小步验证"}
                        </h3>
                        <p>
                          {!hypotheses.length
                            ? "描述观众在什么情境下观看，他们为什么会在乎这个故事。"
                            : !tasks.length
                              ? "先让导演梳理目标与风险，再把明确的输入交给下一位角色。"
                              : "先验收上游成果，再放行下游。未验证的风险需要明确保留。"}
                        </p>
                        <Button
                          primary
                          onClick={() =>
                            !hypotheses.length
                              ? recordDialog("hypothesis")
                              : !tasks.length
                                ? run(
                                    () =>
                                      api(
                                        `/projects/${pid}/pipeline`,
                                        {},
                                        "POST",
                                      ),
                                    "六角色任务已建立",
                                  )
                                : nav("agents")
                          }
                        >
                          {!tasks.length ? "建立工作流" : "进入工作流"}
                          <ArrowRight size={14} />
                        </Button>
                      </section>
                      <section className="panel brief-summary">
                        <div className="section-title">
                          <h3>观众与行为</h3>
                          <CircleHelp size={16} />
                        </div>
                        <label>希望观众做什么</label>
                        <p>{project.action}</p>
                        <label>为什么值得回来</label>
                        <p>{project.followup || "后续价值尚未定义"}</p>
                        <div className="soft-note">
                          共鸣与关注，需要分别验证。
                        </div>
                      </section>
                    </div>
                  </div>
                </>
              )}
              {tab === "hypotheses" && (
                <>
                  <div className="section-toolbar">
                    <span>
                      {hypotheses.length} 条假设 ·{" "}
                      {records("experiment").length} 个实验
                    </span>
                    <Button primary onClick={() => recordDialog("hypothesis")}>
                      <Plus size={15} />
                      添加关键假设
                    </Button>
                  </div>
                  <div className="hypothesis-grid">
                    {hypotheses.map((h, i) => (
                      <section
                        key={h.id}
                        className={
                          "panel hypothesis-card " + (h.stale ? "stale" : "")
                        }
                      >
                        <div className="inline spread">
                          <span className="eyebrow">
                            H{String(i + 1).padStart(2, "0")} · V{h.version}
                          </span>
                          <Badge
                            status={h.data.evidence_status || "untested"}
                            stale={h.stale}
                          />
                        </div>
                        <h3>{h.data.claim}</h3>
                        <span className="source-label">
                          {sourceNames[h.data.source_type] || "来源待注明"}
                        </span>
                        <dl>
                          <dt>失败时的损失</dt>
                          <dd>{h.data.failure_impact || "待定义"}</dd>
                          <dt>最小验证方法</dt>
                          <dd>{h.data.test_plan || "待设计"}</dd>
                        </dl>
                        <footer>
                          <span className="risk-label">
                            {h.data.risk || "高"}风险 ·{" "}
                            {roles.find((r) => r.id === h.data.owner)?.name ||
                              h.data.owner}
                          </span>
                          <button onClick={() => recordDialog("hypothesis", h)}>
                            查看 / 更新
                            <ArrowUpRight size={14} />
                          </button>
                        </footer>
                      </section>
                    ))}
                  </div>
                  {!hypotheses.length && (
                    <Empty
                      title="最先要验证什么？"
                      text="判断一旦失败，会让整部短片失效的关键前提。"
                    />
                  )}
                  <section className="panel">
                    <div className="section-title">
                      <h3>实验记录</h3>
                      <Button onClick={() => recordDialog("experiment")}>
                        <Plus size={15} />
                        记录实验
                      </Button>
                    </div>
                    {records("experiment").length ? (
                      records("experiment").map((e) => (
                        <button
                          className="list-row"
                          key={e.id}
                          onClick={() => recordDialog("experiment", e)}
                        >
                          <FlaskConical size={19} />
                          <div>
                            <strong>{e.data.title}</strong>
                            <small>
                              {e.data.method} ·{" "}
                              {e.data.participants || "参与者来源待说明"}
                            </small>
                          </div>
                          <Badge
                            status={e.data.status || "untested"}
                            stale={e.stale}
                          />
                          <ChevronRight size={15} />
                        </button>
                      ))
                    ) : (
                      <p className="muted">
                        先定义通过和反证条件，再记录原始反馈。模拟观众只能帮助设计问题。
                      </p>
                    )}
                  </section>
                </>
              )}
              {tab === "agents" && (
                <>
                  <div className="soft-note">
                    当前短片：{project.name}。与 Agent 讨论 → 确认无异议 →
                    自动交给下一位。
                  </div>
                  <div className="section-toolbar">
                    <span>独立角色上下文 · 依赖验收后推进</span>
                    <div className="inline">
                      <Button onClick={() => setModal({ type: "task" })}>
                        <Plus size={15} />
                        局部任务
                      </Button>
                      <Button
                        primary
                        disabled={tasks.length > 0 || busy}
                        onClick={() =>
                          run(
                            () => api(`/projects/${pid}/pipeline`, {}, "POST"),
                            "六角色流程已建立",
                          )
                        }
                      >
                        <GitBranch size={15} />
                        建立六角色流程
                      </Button>
                    </div>
                  </div>
                  <div className="conversation-roles">
                    {roles.map((r) => {
                      const t = tasks
                        .filter((t) => t.role === r.id && !t.superseded_by)
                        .at(-1);
                      return (
                        <button
                          key={r.id}
                          className={chatTask?.role === r.id ? "selected" : ""}
                          disabled={!t}
                          onClick={() => t && setChatTaskId(t.id)}
                        >
                          {r.name}
                          <small>
                            {t ? t.revision_open ? "修改中" : t.sync_pending ? "收到修改 · 待更新" : statusNames[t.status] : "尚未建立"}
                          </small>
                        </button>
                      );
                    })}
                  </div>
                  {chatTask && (
                    <RoleConversation
                      key={`${pid}-${chatTask.id}`}
                      pid={pid}
                      task={chatTask}
                      name={
                        roles.find((r) => r.id === chatTask.role)?.name ||
                        chatTask.role
                      }
                      ready={runtime.ready}
                      balance={snapshot!.balance}
                      capabilities={snapshot!.capabilities}
                      compactAudienceAvailable={tasks.some(
                        (t) =>
                          t.role === "audience" &&
                          t.status === "draft" &&
                          !t.merged_into &&
                          t.dependencies.includes(chatTask.id),
                      )}
                      onAdvanced={async (result) => {
                        await refresh();
                        if (result.settings_synced) {
                          if (result.next_task_id) setChatTaskId(result.next_task_id);
                          setToast(`已同步给 ${result.synced_agents.length} 位 Agent，相关旧成果待更新；没有重新生成素材。`);
                          return;
                        }
                        if (result.next_task_id) {
                          setChatTaskId(result.next_task_id);
                          setTab("agents");
                          setToast(
                            `已交接给${roles.find((r) => r.id === result.next_role)?.name || result.next_role}，已确认资料随交接包传递。`,
                          );
                        } else setToast("本阶段已确认，暂无下一项角色任务。");
                      }}
                      onReopened={async (result) => {
                        await refresh(); setChatTaskId(result.task_id);
                        setToast("已开启修改讨论，确认后再同步其他 Agent。");
                      }}
                      api={api}
                      onChange={() => refresh()}
                      onNext={
                        nextChatTask
                          ? () => setChatTaskId(nextChatTask.id)
                          : undefined
                      }
                      onReview={() => {
                        setChatTaskId(chatTask.id);
                        setModal({ type: "review", task: chatTask });
                      }}
                      onGate={
                        (
                          {
                            audience: "G0",
                            writer: "G1",
                            storyboard: "G2",
                          } as Data
                        )[chatTask.role]
                          ? () => {
                              const stage = (
                                {
                                  audience: "G0",
                                  writer: "G1",
                                  storyboard: "G2",
                                } as Data
                              )[chatTask.role];
                              setModal({
                                type: "gate",
                                stage,
                                record: records("decision").find(
                                  (r) => r.data.stage === stage,
                                ),
                              });
                            }
                          : undefined
                      }
                    />
                  )}
                  <details className="legacy-workflow">
                    <summary>任务详情、手动录入与旧调用记录</summary>
                    <div className="agent-grid">
                      {roles.map((r, i) => {
                        const rt = tasks.filter(
                          (t) => t.role === r.id && !t.superseded_by,
                        );
                        const history = tasks.filter(
                          (t) => t.role === r.id && t.superseded_by,
                        );
                        return (
                          <section className="panel agent-card" key={r.id}>
                            <header>
                              <div
                                className="role-avatar"
                                style={{ background: roleColors[r.id] }}
                              >
                                {String(i + 1).padStart(2, "0")}
                              </div>
                              <div>
                                <span className="eyebrow">
                                  {r.id.toUpperCase()}
                                </span>
                                <h3>{r.name}</h3>
                              </div>
                              <button
                                className="icon-button"
                                title="查看系统提示词"
                                onClick={() =>
                                  run(
                                    async () =>
                                      setModal({
                                        type: "prompt",
                                        prompt: await api("/prompts/" + r.id),
                                      }),
                                    "提示词已载入",
                                    false,
                                  )
                                }
                              >
                                <FileText size={17} />
                              </button>
                            </header>
                            <p>{r.description}</p>
                            {rt.length ? (
                              rt.map((t) => (
                                <div className="task-block" key={t.id}>
                                  <div className="inline spread">
                                    <small>
                                      {t.id.slice(-6)} · Brief V
                                      {t.brief_revision}
                                    </small>
                                    <Badge status={t.status} />
                                  </div>
                                  <strong>{t.goal}</strong>
                                  {t.blocker && (
                                    <div className="blocker">
                                      <AlertCircle size={13} />
                                      {t.blocker}
                                    </div>
                                  )}
                                  {t.error && (
                                    <div className="blocker">
                                      {t.error.includes("max-tokens")
                                        ? "回答达到长度上限，已中断。可查看未完成草稿，再决定补齐或重新生成；本次费用待核账。"
                                        : t.error}
                                    </div>
                                  )}
                                  <div className="task-actions">
                                    <Button
                                      onClick={() =>
                                        setModal({ type: "result", task: t })
                                      }
                                    >
                                      {t.result_id
                                        ? "查看成果"
                                        : "手动填写成果"}
                                      <ArrowRight size={13} />
                                    </Button>
                                    {t.status === "interrupted" && (
                                      <Button
                                        disabled={busy}
                                        onClick={() =>
                                          run(
                                            async () =>
                                              setModal({
                                                type: "partial",
                                                task: t,
                                                output: await api(
                                                  `/projects/${pid}/tasks/${t.id}/output`,
                                                ),
                                              }),
                                            "草稿已载入",
                                            false,
                                          )
                                        }
                                      >
                                        查看未完成草稿
                                      </Button>
                                    )}
                                    {[
                                      "interrupted",
                                      "changes_requested",
                                    ].includes(t.status) && (
                                      <Button
                                        disabled={
                                          busy || !runtime.ready || !!t.blocker
                                        }
                                        onClick={() =>
                                          setModal({
                                            type: "run",
                                            task: t,
                                            retry: true,
                                          })
                                        }
                                      >
                                        重新生成
                                      </Button>
                                    )}
                                    {t.status === "draft" && (
                                      <Button
                                        disabled={
                                          !!t.blocker || !runtime.ready || busy
                                        }
                                        onClick={() =>
                                          setModal({ type: "run", task: t })
                                        }
                                      >
                                        <Play size={12} />
                                        运行
                                      </Button>
                                    )}
                                    {t.reservation > 0 &&
                                      t.actual_cost === null && (
                                        <button
                                          onClick={() =>
                                            setModal({ type: "cost", task: t })
                                          }
                                        >
                                          核账
                                        </button>
                                      )}
                                  </div>
                                  {t.status === "pending_review" && (
                                    <button
                                      className="review-link"
                                      onClick={() =>
                                        setModal({ type: "review", task: t })
                                      }
                                    >
                                      验收这次成果
                                      <ArrowUpRight size={13} />
                                    </button>
                                  )}
                                </div>
                              ))
                            ) : (
                              <button
                                className="empty-task"
                                onClick={() =>
                                  setModal({ type: "task", role: r.id })
                                }
                              >
                                <Plus size={14} />
                                为角色建立任务
                              </button>
                            )}
                            {history.length > 0 && (
                              <details>
                                <summary>
                                  以前的尝试（{history.length}）
                                </summary>
                                {history.map((t) => (
                                  <div className="task-block" key={t.id}>
                                    <small>
                                      {t.id.slice(-6)} · 已被新任务替代
                                    </small>
                                    <p>原会话和费用记录仍保留。</p>
                                    <div className="task-actions">
                                      <Button
                                        onClick={() =>
                                          run(
                                            async () =>
                                              setModal({
                                                type: "partial",
                                                task: t,
                                                output: await api(
                                                  `/projects/${pid}/tasks/${t.id}/output`,
                                                ),
                                              }),
                                            "草稿已载入",
                                            false,
                                          )
                                        }
                                      >
                                        查看旧草稿
                                      </Button>
                                      {t.reservation > 0 &&
                                        t.actual_cost === null && (
                                          <Button
                                            onClick={() =>
                                              setModal({
                                                type: "cost",
                                                task: t,
                                              })
                                            }
                                          >
                                            核账
                                          </Button>
                                        )}
                                    </div>
                                  </div>
                                ))}
                              </details>
                            )}
                          </section>
                        );
                      })}
                    </div>
                  </details>
                  <div className="soft-note">
                    <ShieldCheck size={16} />
                    先保存执行意图，再调用模型。未知结果保留会话与费用预留，不自动重试。
                  </div>
                </>
              )}
              {tab === "references" && (
                <>
                  <div className="section-toolbar">
                    <div className="search">
                      <Search size={16} />
                      <input
                        aria-label="搜索参考"
                        placeholder="搜索受众、情绪、叙事机制…"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                    </div>
                    <Button primary onClick={() => recordDialog("reference")}>
                      <Plus size={15} />
                      添加参考
                    </Button>
                  </div>
                  <div className="reference-grid">
                    {records("reference")
                      .filter((r) =>
                        JSON.stringify(r.data)
                          .toLowerCase()
                          .includes(query.toLowerCase()),
                      )
                      .map((r, i) => (
                        <section className="panel reference-card" key={r.id}>
                          <div className={"reference-art ref-" + (i % 3)}>
                            <span>
                              REFERENCE / {String(i + 1).padStart(2, "0")}
                            </span>
                            <Library size={48} strokeWidth={0.8} />
                            <strong>{r.data.emotion || "情绪路径"}</strong>
                          </div>
                          <div className="reference-copy">
                            <div className="inline spread">
                              <span className="eyebrow">
                                V{r.version} · 参考观察
                              </span>
                              {r.stale && <Badge status="warning" stale />}
                            </div>
                            <h3>{r.data.title || "未命名参考"}</h3>
                            <p>{r.data.mechanism || "叙事机制待分析"}</p>
                            <div className="tags">
                              {[r.data.audience, r.data.emotion]
                                .filter(Boolean)
                                .map((t) => (
                                  <span key={t}>{t}</span>
                                ))}
                            </div>
                            <small>
                              使用范围：{r.data.license || "许可待核实"}
                            </small>
                            <button
                              onClick={() => recordDialog("reference", r)}
                            >
                              查看适用条件
                              <ArrowUpRight size={14} />
                            </button>
                          </div>
                        </section>
                      ))}
                  </div>
                  {!records("reference").length && (
                    <Empty
                      title="参考要相似，更要说得清"
                      text="记录受众、情绪路径与叙事机制。热度不能直接证明对你的观众有效。"
                      action={
                        <Button onClick={() => recordDialog("reference")}>
                          <Plus size={15} />
                          添加第一条参考
                        </Button>
                      }
                    />
                  )}
                </>
              )}
              {tab === "storyboard" && (
                <>
                  <MediaGenerationPanel key={pid} providers={snapshot?.capabilities.media_generation?.providers || []}
                    pid={pid} api={api} onChange={() => refresh()}
                    records={snapshot?.records || []} jobs={snapshot?.media_jobs || []} busy={busy}
                    onSettings={() => setModal({ type: "settings" })}
                    onEstimate={data => api(`/projects/${pid}/media-estimate`, data, "POST")}
                    onGenerate={(data, requestId) => run(() => api(`/projects/${pid}/media-jobs`, data, "POST", requestId), "生成任务已提交", false)}
                    onRetrieve={jid => run(() => api(`/projects/${pid}/media-jobs/${jid}/retrieve`, {}, "POST"), "正在取回原结果", false)}
                    onReconcile={(jid, data) => run(() => api(`/projects/${pid}/media-jobs/${jid}/reconcile`, data, "POST"), "实际费用已保存", false)} />
                  <div className="section-toolbar">
                    <div>
                      <strong>{shots.length} 个镜头</strong>
                      <span className="muted">
                        {" "}
                        /{" "}
                        {shots.reduce(
                          (a, s) => a + Number(s.data.duration),
                          0,
                        )}{" "}
                        秒
                      </span>
                    </div>
                    <div className="inline">
                      <Button onClick={() => recordDialog("shot")}>
                        <Plus size={15} />
                        添加镜头
                      </Button>
                      <Button
                        primary
                        disabled={
                          !shots.length || shots.some((s) => s.stale) || busy
                        }
                        onClick={() =>
                          run(
                            () =>
                              api(
                                `/projects/${pid}/exports`,
                                {
                                  shot_ids: shots.map((s) => s.id),
                                  name: project.name + " · 动态分镜",
                                  ...exportAudio,
                                },
                                "POST",
                              ),
                            "导出已开始",
                          )
                        }
                      >
                        <Film size={15} />
                        导出动态分镜
                      </Button>
                    </div>
                  </div>
                  <details className="panel"><summary>导出声音设置 · 默认保留视频原声</summary>
                    <label className="checkbox"><input type="checkbox" checked={exportAudio.preserve_source_audio}
                      onChange={e => setExportAudio(old => ({ ...old, preserve_source_audio: e.target.checked }))} />保留视频中的环境声、音效与对白</label>
                    <div className="form-grid">
                      <Field label="视频原声音量（100%为原始音量）"><input type="number" min="0" max="200" step="5"
                        value={Math.round(exportAudio.source_gain * 100)} onChange={e => setExportAudio(old => ({ ...old, source_gain: Number(e.target.value) / 100 }))} /></Field>
                      <Field label="叠加一条旁白音频（可选）"><select value={exportAudio.narration_asset_id || ""}
                        onChange={e => setExportAudio(old => ({ ...old, narration_asset_id: e.target.value || null }))}>
                        <option value="">不叠加旁白</option>{assets.filter(a => a.data.media_type === "audio" && !a.stale).map(a =>
                          <option key={a.id} value={a.id}>{a.data.name}</option>)}
                      </select></Field>
                      {exportAudio.narration_asset_id && <>
                        <Field label="旁白音量（%）"><input type="number" min="0" max="200" step="5"
                          value={Math.round(exportAudio.narration_gain * 100)} onChange={e => setExportAudio(old => ({ ...old, narration_gain: Number(e.target.value) / 100 }))} /></Field>
                        <Field label="旁白开始位置（秒）"><input type="number" min="0" max="300" step="0.1"
                          value={exportAudio.narration_start_seconds} onChange={e => setExportAudio(old => ({ ...old, narration_start_seconds: Number(e.target.value) }))} /></Field>
                      </>}
                    </div>
                    <p className="muted">本地混音不调用模型。无音轨素材补静音；视频不足镜头时长时保持末帧，声音不循环。旁白超出成片部分截断，叠加旁白时建议把原声调至30%–50%。混音不能去除原视频中的对白或音乐。</p>
                  </details>
                  <div className="shot-grid">
                    {shots.map((s, i) => {
                      const a = assets.find((a) => a.id === s.data.asset_id);
                      return (
                        <section className="panel shot-card" key={s.id}>
                          <div className="shot-frame">
                            {a?.data.media_type === "image" ? (
                              <img src={a.data.url} alt={s.data.description} />
                            ) : a?.data.media_type === "video" ? (
                              <video src={a.data.url} controls />
                            ) : (
                              <>
                                <div className="frame-guides" />
                                <span className="shot-scene">
                                  {s.data.title || "画面待生成"}
                                </span>
                                <small>分镜规划卡</small>
                              </>
                            )}
                            <span className="shot-counter">
                              {String(i + 1).padStart(2, "0")}
                            </span>
                            <span className="shot-duration">
                              {s.data.duration}s
                            </span>
                          </div>
                          <div className="shot-copy">
                            <div className="inline spread">
                              <h3>{s.data.title || "镜头 " + (i + 1)}</h3>
                              {s.stale && <Badge status="warning" stale />}
                              <button
                                className="icon-button"
                                title="编辑镜头"
                                onClick={() => recordDialog("shot", s)}
                              >
                                <Pencil size={14} />
                              </button>
                            </div>
                            <p>{s.data.description}</p>
                            <div className="tags">
                              <span>{s.data.camera || "固定机位"}</span>
                              <span>{s.data.emotion || "情绪待注明"}</span>
                            </div>
                            <small>{s.data.subtitle || "无字幕"}</small>
                          </div>
                        </section>
                      );
                    })}
                  </div>
                  {!shots.length && (
                    <Empty
                      title="先排出故事，再制作镜头"
                      text="用描述卡或导入的素材组织时序，输出真实可播放的动态分镜。"
                    />
                  )}
                  <section className="panel">
                    <div className="section-title">
                      <h3>
                        本地素材 <span>{assets.length} ASSETS</span>
                      </h3>
                      <label className="button">
                        <Upload size={15} />
                        导入素材
                        <input
                          type="file"
                          accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.wav,.mp3"
                          hidden
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file) {
                              const body = new FormData();
                              body.append("file", file);
                              run(
                                () =>
                                  api(`/projects/${pid}/assets`, body, "POST"),
                                "素材已导入",
                              );
                            }
                            e.target.value = "";
                          }}
                        />
                      </label>
                    </div>
                    <div className="asset-list">
                      {assets.map((a) => (
                        <div key={a.id}>
                        <a
                          href={a.data.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {a.data.media_type === "image" ? <img className="asset-thumbnail" src={a.data.url} alt={a.data.name} loading="lazy" /> : <FolderOpen size={16} />}
                          <span>{a.data.name}</span>
                          <small>{a.data.media_type} · 许可待核实</small>
                          <ExternalLink size={13} />
                        </a>
                        {a.data.media_type === "image" && <ReplaceImage asset={a} pid={pid} api={api} onChange={() => refresh()} />}
                        </div>
                      ))}
                    </div>
                    {assets.map((a) => (
                      <button
                        className="asset-meta-link"
                        key={a.id}
                        onClick={() => recordDialog("asset", a)}
                      >
                        {a.data.name} · V{a.version} · 编辑来源与许可
                        <Pencil size={12} />
                      </button>
                    ))}
                    {!assets.length && (
                      <p className="muted">
                        可导入图片、视频、音频。每个素材会得到项目内的独立 ID。
                      </p>
                    )}
                  </section>
                  <section className="panel">
                    <div className="section-title">
                      <h3>一致性资产 Bible</h3>
                      <Button onClick={() => recordDialog("bible")}>
                        <Plus size={15} />
                        建立资产 Bible
                      </Button>
                    </div>
                    {records("bible").map((b) => (
                      <button
                        className="list-row"
                        key={b.id}
                        onClick={() => recordDialog("bible", b)}
                      >
                        <Aperture size={18} />
                        <div>
                          <strong>{b.data.title}</strong>
                          <small>
                            {b.data.bible_type} · V{b.version} ·{" "}
                            {b.data.fixed_description}
                          </small>
                        </div>
                        {b.stale && <Badge status="warning" stale />}
                        <Pencil size={14} />
                      </button>
                    ))}
                    {!records("bible").length && (
                      <p className="muted">
                        固定角色、场景和风格描述；参考素材通过版本引用。Seed
                        和模型 ID 仅在工具支持时填写。
                      </p>
                    )}
                  </section>
                  <section className="panel">
                    <div className="section-title">
                      <h3>动态分镜导出</h3>
                      <span className="muted">
                      规划小样 · 可保留原声与叠加旁白 · 独立 SRT
                      </span>
                    </div>
                    {snapshot?.exports.map((e) => (
                      <div className="export-item" key={e.id}>
                        <div className="inline spread">
                          <strong>
                            {e.data.name || "动态分镜"} · {e.id.slice(-6)}
                          </strong>
                          <Badge status={e.status} stale={e.stale} />
                        </div>
                        {e.error && <p>{e.error}</p>}
                        {e.status === "completed" && (
                          <>
                            <video controls src={e.data.video_url} />
                            <div className="inline">
                              <a
                                className="button"
                                href={e.data.video_url}
                                download
                              >
                                <Download size={14} />
                                MP4
                              </a>
                              <a
                                className="button"
                                href={e.data.subtitles_url}
                                download
                              >
                                字幕 SRT
                              </a>
                              <a
                                className="button"
                                href={e.data.manifest_url}
                                download
                              >
                                工程清单
                              </a>
                              <span className="muted">
                                {e.data.duration}s · {e.data.width}×
                                {e.data.height}
                              </span>
                            </div>
                          </>
                        )}
                      </div>
                    ))}
                    {!snapshot?.exports.length && (
                      <p className="muted">
                        导出会使用当前有效的镜头和素材版本。描述卡用于判断节奏，不能替代最终画面验收。
                      </p>
                    )}
                  </section>
                </>
              )}
              {tab === "reviews" && (
                <>
                  <div className="gate-grid">
                    {["G0", "G1", "G2", "G3"].map((stage, i) => {
                      const d = records("decision").find(
                        (d) => d.data.stage === stage,
                      );
                      return (
                        <section className="panel gate-card" key={stage}>
                          <div className="inline spread">
                            <span className="eyebrow">
                              {stage} / STAGE GATE
                            </span>
                            <Badge
                              status={
                                d?.data.allow && !d.stale
                                  ? "accepted"
                                  : "untested"
                              }
                              stale={d?.stale}
                            />
                          </div>
                          <h3>
                            {
                              [
                                "观众与概念",
                                "故事与资产",
                                "关键镜头可行性",
                                "最终整片交付",
                              ][i]
                            }
                          </h3>
                          <p>
                            {
                              [
                                "受众、内容承诺、后续价值与高风险假设是否清楚？",
                                "主线能否被理解，高潮是否有充分铺垫？",
                                "粗分镜、关键镜头实测与预算是否支持生产？",
                                "真实成片、声音、字幕与目标行为是否完成评审？",
                              ][i]
                            }
                          </p>
                          {d && <small>{d.data.note}</small>}
                          <Button
                            onClick={() =>
                              setModal({ type: "gate", stage, record: d })
                            }
                          >
                            {d ? "更新阶段决定" : "记录阶段决定"}
                            <ArrowUpRight size={14} />
                          </Button>
                        </section>
                      );
                    })}
                  </div>
                  <section className="panel">
                    <div className="section-title">
                      <h3>成果与验收</h3>
                      <span className="muted">
                        {
                          tasks.filter((t) => t.status === "pending_review")
                            .length
                        }{" "}
                        项待验收
                      </span>
                    </div>
                    {tasks
                      .filter((t) => t.result_id)
                      .map((t) => {
                        const a = artifacts.find((a) => a.id === t.result_id);
                        return (
                          <div className="list-row" key={t.id}>
                            <FileText size={20} />
                            <div>
                              <strong>{a?.data.title}</strong>
                              <small>
                                {roles.find((r) => r.id === t.role)?.name} ·
                                成果 V{a?.version}
                              </small>
                            </div>
                            <Badge status={t.status} stale={a?.stale} />
                            <Button
                              onClick={() =>
                                setModal({ type: "result", task: t })
                              }
                            >
                              查看
                            </Button>
                            {t.status === "pending_review" && (
                              <Button
                                primary
                                onClick={() =>
                                  setModal({ type: "review", task: t })
                                }
                              >
                                验收
                              </Button>
                            )}
                          </div>
                        );
                      })}
                    {!artifacts.length && (
                      <p className="muted">
                        角色提交成果后会进入待验收。每项检查都引用实际成果版本。
                      </p>
                    )}
                  </section>
                  <section className="panel">
                    <div className="section-title">
                      <h3>版本与操作记录</h3>
                      <span className="muted">最近 80 条</span>
                    </div>
                    <div className="event-list">
                      {snapshot?.events.map((e) => (
                        <div key={e.seq}>
                          <span className="event-dot" />
                          <strong>
                            {{
                              "project.created": "创建项目",
                              "task.created": "建立角色任务",
                              "record.saved": "保存记录版本",
                              "task.submitted": "提交成果",
                              "task.reviewed": "完成人工验收",
                              "task.started": "开始模型执行",
                              "brief.updated": "更新 Brief",
                              "inputs.invalidated": "标记下游复查",
                              "animatic.started": "开始动态分镜导出",
                              "animatic.exported": "动态分镜已导出",
                              "task.interrupted": "执行中断",
                              "cost.reconciled": "费用已核账",
                            }[e.type as string] || e.type}
                          </strong>
                          <small>
                            {new Date(e.created_at).toLocaleString("zh-CN")}
                          </small>
                          <code>{JSON.stringify(e.data)}</code>
                        </div>
                      ))}
                    </div>
                  </section>
                </>
              )}
            </>
          )}
        </main>
        <footer className="workspace-footer">
          <span>
            <Aperture size={13} />
            映序 · 让创意有方向
          </span>
          <span>本地工作台 / v0.1</span>
        </footer>
      </div>
      {modal && (
        <Dialog
          title={modalTitle(modal)}
          subtitle={modalSubtitle(modal)}
          onClose={() => setModal(null)}
        >
          {modal.type === "project" && (
            <ProjectForm
              initial={modal.project}
              busy={busy}
              onSave={(data) =>
                run(
                  async () => {
                    const p = modal.project
                      ? await api(
                          "/projects/" + pid,
                          {
                            ...data,
                            expected_revision: modal.project.revision,
                          },
                          "PUT",
                        )
                      : await api("/projects", data, "POST");
                    setPid(p.id);
                    setSnapshot(await api("/projects/" + p.id));
                  },
                  modal.project
                    ? "Brief 已更新，受影响任务已标记复查"
                    : "项目已建立",
                )
              }
            />
          )}
          {modal.type === "projects" && (
            <div className="modal-body">
              {projects.map((p) => (
                <button
                  className="list-row"
                  key={p.id}
                  onClick={() => {
                    setPid(p.id);
                    setModal(null);
                  }}
                >
                  <Film size={18} />
                  <div>
                    <strong>{p.name}</strong>
                    <small>
                      {p.sample ? "示例项目" : "制作项目"} · Brief V{p.revision}
                    </small>
                  </div>
                  {pid === p.id && <Check size={17} />}
                </button>
              ))}
              <div className="inline modal-actions">
                <Button primary onClick={() => setModal({ type: "project" })}>
                  <Plus size={15} />
                  新建项目
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => run(createExample, "示例已建立")}
                >
                  建立示例项目
                </Button>
              </div>
            </div>
          )}
          {modal.type === "record" && (
            <RecordForm
              key={modal.record?.id || modal.kind}
              kind={modal.kind}
              initial={modal.record?.data}
              roles={roles}
              assets={assets}
              records={snapshot?.records || []}
              initialRefs={modal.record?.input_refs || []}
              busy={busy}
              onSave={(data, refs) =>
                run(
                  () =>
                    modal.kind === "asset"
                      ? api(
                          `/projects/${pid}/assets/${modal.record.id}`,
                          {
                            name: data.name,
                            license: data.license,
                            source_note: data.source_note || "",
                            expected_version: modal.record.version,
                          },
                          "PUT",
                        )
                      : saveRecord(modal.kind, data, modal.record, refs),
                  "记录版本已保存",
                )
              }
            />
          )}
          {modal.type === "settings" && (
            <>
              <MediaProviderSettings initial={mediaSettings} busy={busy}
                onCheck={() => api("/media-settings/seedance/check", {}, "POST")}
                onSave={data => run(() => api("/media-settings", data, "PUT"), "媒体配置已保存；未调用模型", false)} />
              <GeminiSettingsForm
                initial={gemini}
                busy={busy}
                onSave={(data) =>
                  run(
                    () => api("/gemini", data, "PUT"),
                    "Gemini 配置已保存；功能仍禁用",
                  )
                }
              />
              <SettingsForm
                initial={runtime}
                busy={busy}
                onSave={(data) =>
                  run(() => api("/runtime", data, "PUT"), "运行配置已保存")
                }
              />
            </>
          )}
          {modal.type === "task" && (
            <TaskForm
              roles={roles}
              initialRole={modal.role}
              tasks={tasks}
              records={snapshot?.records || []}
              busy={busy}
              onSave={(data) =>
                run(
                  () => api(`/projects/${pid}/tasks`, data, "POST"),
                  "任务已建立",
                )
              }
            />
          )}
          {modal.type === "prompt" && (
            <div className="modal-body">
              <div className="soft-note">
                生效版本 {modal.prompt.version} · 已合并六项职责和假设驱动规则
              </div>
              <pre className="prompt-text">{modal.prompt.text}</pre>
            </div>
          )}
          {modal.type === "result" && (
            <ResultPanel
              draft={modal.draft}
              assets={assets}
              task={modal.task}
              artifact={artifacts.find((a) => a.id === modal.task.result_id)}
              busy={busy}
              onSubmit={(data) =>
                run(
                  () =>
                    api(
                      `/projects/${pid}/tasks/${modal.task.id}/submit`,
                      data,
                      "POST",
                    ),
                  "成果已提交，等待验收",
                )
              }
              onReview={() => setModal({ type: "review", task: modal.task })}
              onImport={(index) =>
                run(
                  () =>
                    api(
                      `/projects/${pid}/tasks/${modal.task.id}/proposals/${index}`,
                      {},
                      "POST",
                    ),
                  "建议已导入；假设仍待验证",
                  false,
                )
              }
            />
          )}
          {modal.type === "partial" && (
            <div className="modal-body">
              <div className="soft-note">
                这是模型实际写出的未完成回答，尚未成为可验收成果。查看不会重新调用模型。
              </div>
              {modal.output.warnings.map((warning: string, i: number) => (
                <div className="blocker" key={i}>
                  {warning}
                </div>
              ))}
              {!modal.task.superseded_by && (
                <div className="modal-actions">
                  <Button
                    disabled={busy || !modal.output.available}
                    onClick={() =>
                      setModal({
                        type: "result",
                        task: modal.task,
                        draft: modal.output,
                      })
                    }
                  >
                    手动补齐这份草稿
                  </Button>
                  <Button
                    primary
                    disabled={busy || !runtime.ready || !!modal.task.blocker}
                    onClick={() =>
                      setModal({ type: "run", task: modal.task, retry: true })
                    }
                  >
                    重新生成
                  </Button>
                </div>
              )}
              {modal.output.available ? (
                <>
                  <h3>{modal.output.draft_fields.title || "未完成草稿"}</h3>
                  <p>{modal.output.draft_fields.summary}</p>
                  <MarkdownView
                    text={modal.output.draft_fields.body || modal.output.text}
                  />
                </>
              ) : (
                <p>这次会话没有可恢复的可见回答。</p>
              )}
            </div>
          )}
          {modal.type === "review" && (
            <ReviewForm
              artifact={artifacts.find((a) => a.id === modal.task.result_id)!}
              busy={busy}
              onSave={(data) =>
                run(
                  () =>
                    api(
                      `/projects/${pid}/tasks/${modal.task.id}/review`,
                      data,
                      "POST",
                    ),
                  "验收记录已保存",
                )
              }
            />
          )}
          {modal.type === "gate" && (
            <GateForm
              stage={modal.stage}
              record={modal.record}
              artifacts={artifacts}
              tasks={tasks}
              busy={busy}
              onSave={(data, refs) =>
                run(
                  () => saveRecord("decision", data, modal.record, refs),
                  "阶段决定已记录",
                )
              }
            />
          )}
          {modal.type === "run" && (
            <RunForm
              retry={modal.retry}
              task={modal.task}
              balance={snapshot!.balance}
              busy={busy}
              onRun={(cost) =>
                run(async () => {
                  const target = modal.retry
                    ? await api(
                        `/projects/${pid}/tasks/${modal.task.id}/retry`,
                        {},
                        "POST",
                      )
                    : modal.task;
                  return api(
                    `/projects/${pid}/tasks/${target.id}/run`,
                    { max_cost: cost },
                    "POST",
                  );
                }, "模型任务已派发")
              }
            />
          )}
          {modal.type === "cost" && (
            <CostForm
              task={modal.task}
              busy={busy}
              onSave={(data) =>
                run(
                  () =>
                    api(
                      `/projects/${pid}/tasks/${modal.task.id}/reconcile`,
                      data,
                      "POST",
                    ),
                  "已按实际账单核账",
                )
              }
            />
          )}
        </Dialog>
      )}
    </div>
  );
}

function modalTitle(m: Data) {
  return (
    {
      project: m.project ? "编辑项目 Brief" : "开始一部新短片",
      projects: "你的项目",
      record:
        {
          hypothesis: "关键假设",
          reference: "参考资料",
          experiment: "实验记录",
          shot: "分镜镜头",
          bible: "一致性资产 Bible",
          asset: "素材来源与许可",
        }[m.kind as string] || "记录",
      settings: "运行设置",
      task: "建立角色任务",
      prompt: "系统提示词",
      result: "角色成果",
      partial: "未完成的模型草稿",
      review: "验收成果",
      gate: m.stage + " · 阶段决定",
      run: m.retry ? "重新生成角色成果" : "运行 DeepSeek 任务",
      cost: "核对实际费用",
    }[m.type as string] || "工作台"
  );
}
function modalSubtitle(m: Data) {
  return {
    project: "观众、内容承诺与后续价值，是制作的共同起点。",
    record: "记录来源、适用条件和证据，保留每次修改的版本。",
    settings: "发送消息或确认交接时调用模型；配置完成前可使用手动制作模式。",
    review: "检查当前成果版本，再决定是否允许下游继续。",
    gate: "批准制作不等于假设已经得到证据支持。",
  }[m.type as string];
}
function ProjectForm({
  initial,
  busy,
  onSave,
}: {
  initial?: Data;
  busy: boolean;
  onSave: (data: Data) => void;
}) {
  const [d, set] = useState<Data>({
    name: "",
    brief: "",
    audience: "",
    promise: "",
    action: "关注",
    followup: "",
    duration: 30,
    aspect: "9:16",
    budget: 0,
    sample: false,
    ...initial,
  });
  const update = (k: string, v: any) => set({ ...d, [k]: v });
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const { id, revision, created_at, ...data } = d;
        onSave(data);
      }}
      className="modal-body"
    >
      <Field label="项目名称">
        <input
          required
          value={d.name}
          placeholder="例如：最后一盏灯"
          onChange={(e) => update("name", e.target.value)}
        />
      </Field>
      <Field label="故事方向 / Brief">
        <textarea
          value={d.brief}
          rows={3}
          placeholder="你希望讲什么？现在还不知道什么？"
          onChange={(e) => update("brief", e.target.value)}
        />
      </Field>
      <Field label="谁会观看？">
        <textarea
          value={d.audience}
          rows={2}
          placeholder="具体的人群、处境与观看场景"
          onChange={(e) => update("audience", e.target.value)}
        />
      </Field>
      <Field label="观众能获得什么？">
        <input
          value={d.promise}
          placeholder="情绪、意义、信息或体验"
          onChange={(e) => update("promise", e.target.value)}
        />
      </Field>
      <div className="form-grid">
        <Field label="目标行为">
          <input
            value={d.action}
            onChange={(e) => update("action", e.target.value)}
          />
        </Field>
        <Field label="为什么值得回来？">
          <input
            value={d.followup}
            placeholder="后续内容承诺"
            onChange={(e) => update("followup", e.target.value)}
          />
        </Field>
      </div>
      <div className="form-grid three">
        <Field label="目标片长（秒）">
          <input
            required
            type="number"
            min="1"
            max="300"
            value={d.duration}
            onChange={(e) => update("duration", Number(e.target.value))}
          />
        </Field>
        <Field label="画幅">
          <select
            value={d.aspect}
            onChange={(e) => update("aspect", e.target.value)}
          >
            <option>9:16</option>
            <option>16:9</option>
            <option>1:1</option>
          </select>
        </Field>
        <Field label="项目预算（元）">
          <input
            required
            type="number"
            min="0"
            step="0.01"
            value={d.budget}
            onChange={(e) => update("budget", Number(e.target.value))}
          />
        </Field>
      </div>
      <div className="modal-actions">
        <button className="button primary" disabled={busy} type="submit">
          {busy ? "保存中…" : "保存项目"}
          <ArrowRight size={15} />
        </button>
      </div>
    </form>
  );
}

function RefPicker({
  records,
  value,
  onChange,
}: {
  records: RecordItem[];
  value: Data[];
  onChange: (v: Data[]) => void;
}) {
  const outdated = value.some((q) => {
    const current = records.find((r) => r.kind === q.kind && r.id === q.id);
    return !current || current.stale || current.version !== q.version;
  });
  return (
    <Field
      label="依赖的输入版本"
      hint="选择相关记录。上游更新时，这份记录会被标记复查。"
    >
      {outdated && (
        <div className="soft-note">
          已选输入包含旧版本。核对后可明确更新；仍需复查的源记录应先修复。
          <button
            type="button"
            className="button"
            onClick={() =>
              onChange(
                value.flatMap((q) => {
                  const current = records.find(
                    (r) => r.kind === q.kind && r.id === q.id && !r.stale,
                  );
                  return current
                    ? [
                        {
                          kind: current.kind,
                          id: current.id,
                          version: current.version,
                        },
                      ]
                    : [];
                }),
              )
            }
          >
            选用当前有效版本
          </button>
        </div>
      )}
      <select
        multiple
        className="multi-select"
        value={value.map((r) => r.kind + ":" + r.id)}
        onChange={(e) =>
          onChange(
            Array.from(e.target.selectedOptions).map((o) => {
              const r = records.find((r) => r.kind + ":" + r.id === o.value)!;
              return { kind: r.kind, id: r.id, version: r.version };
            }),
          )
        }
      >
        {records
          .filter((r) => !r.stale)
          .map((r) => (
            <option key={r.kind + ":" + r.id} value={r.kind + ":" + r.id}>
              {r.kind} · {r.data.title || r.data.claim || r.data.name || r.id} ·
              V{r.version}
            </option>
          ))}
      </select>
    </Field>
  );
}
function RecordForm({
  kind,
  initial,
  roles,
  assets,
  records,
  initialRefs,
  busy,
  onSave,
}: {
  kind: string;
  initial?: Data;
  roles: Role[];
  assets: RecordItem[];
  records: RecordItem[];
  initialRefs: Data[];
  busy: boolean;
  onSave: (d: Data, refs: Data[]) => void;
}) {
  const defaults: Data = {
    hypothesis: {
      claim: "",
      owner: "audience",
      risk: "高",
      scope: "",
      failure_impact: "",
      test_plan: "",
      pass_criteria: "",
      counter_criteria: "",
      source_type: "user_intuition",
      evidence_status: "untested",
      evidence: "",
    },
    reference: {
      title: "",
      source: "",
      audience: "",
      emotion: "",
      mechanism: "",
      observations: "",
      transfer_conditions: "",
      counter_examples: "",
      license: "待核实",
    },
    experiment: {
      title: "",
      method: "",
      participants: "",
      material_version: "",
      pass_criteria: "",
      counter_criteria: "",
      raw_results: "",
      limits: "",
      status: "untested",
    },
    bible: {
      title: "",
      bible_type: "character",
      fixed_description: "",
      negative_prompt: "",
      seed: "",
      model_id: "",
      invariants: "",
      license: "待核实",
    },
    asset: { name: "", license: "待核实", source_note: "" },
    shot: {
      title: "",
      order: records.filter((r) => r.kind === "shot").length + 1,
      description: "",
      duration: 3,
      camera: "固定机位",
      emotion: "",
      subtitle: "",
      audio_direction: "",
      asset_id: "",
    },
  };
  const [d, set] = useState<Data>({ ...defaults[kind], ...initial }),
    [refs, setRefs] = useState<Data[]>(
      initialRefs.filter((r) => kind !== "shot" || r.kind !== "asset"),
    );
  const update = (k: string, v: any) => set({ ...d, [k]: v });
  const fields: Data = {
    hypothesis: [
      ["claim", "假设陈述", true],
      ["scope", "适用人群 / 场景"],
      ["failure_impact", "如果失败，损失什么？", true],
      ["test_plan", "最小验证方法", true],
      ["pass_criteria", "预先约定的通过条件"],
      ["counter_criteria", "反证 / 无法判断条件"],
      ["evidence", "证据与原始记录", true],
    ],
    reference: [
      ["title", "参考标题"],
      ["source", "来源链接 / 本地出处"],
      ["audience", "受众与观看场景"],
      ["emotion", "情绪路径"],
      ["observations", "直接观察到的特征", true],
      ["mechanism", "可能有效的叙事机制（推测）", true],
      ["transfer_conditions", "适用条件", true],
      ["counter_examples", "反例 / 不适用条件"],
      ["license", "使用范围 / 许可证据"],
    ],
    experiment: [
      ["title", "实验标题"],
      ["method", "测试方法", true],
      ["participants", "参与者来源与适配性"],
      ["material_version", "测试素材 ID / 版本"],
      ["pass_criteria", "测试前的通过条件"],
      ["counter_criteria", "反证 / 无法判断条件"],
      ["raw_results", "原始结果与证据位置", true],
      ["limits", "实验限制", true],
    ],
    bible: [
      ["title", "资产名称"],
      ["fixed_description", "固定描述 / 标准提示词", true],
      ["negative_prompt", "负面描述（按工具支持情况使用）", true],
      ["invariants", "跨镜头必须保持的特征", true],
      ["seed", "Seed（未知可留空）"],
      ["model_id", "模型 / LoRA / 风格 ID（可选）"],
      ["license", "使用范围 / 许可"],
    ],
    asset: [
      ["name", "素材名称"],
      ["source_note", "来源与用途", true],
      ["license", "许可证据 / 使用范围", true],
    ],
    shot: [
      ["title", "镜头名称"],
      ["description", "画面描述与叙事目的", true],
      ["camera", "景别 / 运镜"],
      ["emotion", "情绪"],
      ["subtitle", "字幕 / 旁白"],
      ["audio_direction", "声音设计（环境声、音效、时间点、静默；单独配音请注明）", true],
    ],
  };
  return (
    <form
      className="modal-body"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(d, refs);
      }}
    >
      {kind === "bible" && (
        <Field label="资产类型">
          <select
            value={d.bible_type}
            onChange={(e) => update("bible_type", e.target.value)}
          >
            <option value="character">角色 Bible</option>
            <option value="scene">场景 Bible</option>
            <option value="style">风格 Bible</option>
          </select>
        </Field>
      )}
      {kind === "hypothesis" && (
        <div className="form-grid">
          <Field label="负责人">
            <select
              value={d.owner}
              onChange={(e) => update("owner", e.target.value)}
            >
              {roles.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="失败风险">
            <select
              value={d.risk}
              onChange={(e) => update("risk", e.target.value)}
            >
              <option>高</option>
              <option>中</option>
              <option>低</option>
            </select>
          </Field>
        </div>
      )}
      {fields[kind].map(([key, label, multi]: any[]) => (
        <Field key={key} label={label}>
          {multi ? (
            <textarea
              rows={2}
              value={d[key] || ""}
              required={
                key === "claim" ||
                key === "description" ||
                key === "fixed_description"
              }
              onChange={(e) => update(key, e.target.value)}
            />
          ) : (
            <input
              value={d[key] || ""}
              required={key === "title" || key === "source"}
              onChange={(e) => update(key, e.target.value)}
            />
          )}
        </Field>
      ))}
      {kind === "hypothesis" && (
        <div className="form-grid">
          <Field label="来源性质">
            <select
              value={d.source_type}
              onChange={(e) => update("source_type", e.target.value)}
            >
              {Object.entries(sourceNames).map(([k, v]) => (
                <option value={k} key={k}>
                  {v}
                </option>
              ))}
            </select>
          </Field>
          <Field label="证据状态">
            <select
              value={d.evidence_status}
              onChange={(e) => update("evidence_status", e.target.value)}
            >
              {["untested", "supported", "contradicted", "inconclusive"].map(
                (k) => (
                  <option value={k} key={k}>
                    {statusNames[k]}
                  </option>
                ),
              )}
            </select>
          </Field>
        </div>
      )}
      {kind === "shot" && (
        <>
          <div className="form-grid">
            <Field label="镜头顺序">
              <input
                type="number"
                min="1"
                required
                value={d.order}
                onChange={(e) => update("order", Number(e.target.value))}
              />
            </Field>
            <Field label="时长（秒）">
              <input
                type="number"
                min="0.1"
                step="0.1"
                max="60"
                required
                value={d.duration}
                onChange={(e) => update("duration", Number(e.target.value))}
              />
            </Field>
          </div>
          <Field label="画面素材">
            <select
              value={d.asset_id || ""}
              onChange={(e) => update("asset_id", e.target.value)}
            >
              <option value="">使用分镜描述卡</option>
              {assets
                .filter((a) => ["image", "video"].includes(a.data.media_type))
                .map((a) => (
                  <option value={a.id} key={a.id}>
                    {a.data.name}
                  </option>
                ))}
            </select>
          </Field>
        </>
      )}
      {kind === "experiment" && (
        <div className="soft-note">
          记录实验不会自动改变假设结论。请根据真实结果更新对应假设。
        </div>
      )}
      <RefPicker
        records={records.filter(
          (r) => !["review", "decision", "asset"].includes(r.kind),
        )}
        value={refs}
        onChange={setRefs}
      />
      <div className="modal-actions">
        <button className="button primary" type="submit" disabled={busy}>
          保存新版本
          <Check size={15} />
        </button>
      </div>
    </form>
  );
}
function GeminiSettingsForm({
  initial,
  busy,
  onSave,
}: {
  initial: Data;
  busy: boolean;
  onSave: (d: Data) => void;
}) {
  const [d, set] = useState<Data>({
    api_key: "",
    clear_key: false,
    model: initial.model || "gemini-3.8-flash",
    fps: initial.fps || 1,
    max_output_tokens: initial.max_output_tokens || 3072,
    request_timeout_seconds: initial.request_timeout_seconds || 120,
  });
  return (
    <form
      className="modal-body gemini-settings"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(d);
      }}
    >
      <div className="settings-status">
        <Film size={23} />
        <div>
          <strong>Gemini · 视频内容评审</strong>
          <small>
            功能已禁用 · {initial.has_key ? "Key 已保存" : "Key 待配置"}
          </small>
        </div>
      </div>
      <p className="soft-note">
        可以先填写 Gemini
        Key。保存后仍不会分析视频、上传素材或产生模型调用；文字讨论继续使用
        DeepSeek。
      </p>
      <Field
        label="Gemini API Key"
        hint="仅保存在本机 data/gemini-settings.json，独立于 DeepSeek；留空保留已保存的 Key。"
      >
        <input
          type="password"
          autoComplete="new-password"
          value={d.api_key}
          placeholder={
            initial.has_key ? "已配置 · 留空保留" : "请输入你的 Gemini API Key"
          }
          onChange={(e) => set({ ...d, api_key: e.target.value })}
        />
      </Field>
      <details>
        <summary>Gemini 高级配置（保存参数，不启用功能）</summary>
        <Field label="Gemini 模型 ID">
          <input
            required
            value={d.model}
            onChange={(e) => set({ ...d, model: e.target.value })}
          />
        </Field>
        <div className="form-grid">
          <Field label="Gemini 每秒抽帧数">
            <input
              required
              type="number"
              min="0.2"
              max="5"
              step="0.1"
              value={d.fps}
              onChange={(e) => set({ ...d, fps: Number(e.target.value) })}
            />
          </Field>
          <Field label="Gemini 最大输出 token 数">
            <input
              required
              type="number"
              min="512"
              max="8192"
              step="1"
              value={d.max_output_tokens}
              onChange={(e) =>
                set({ ...d, max_output_tokens: Number(e.target.value) })
              }
            />
          </Field>
        </div>
        <Field label="Gemini 请求超时（秒）">
          <input
            required
            type="number"
            min="30"
            max="300"
            value={d.request_timeout_seconds}
            onChange={(e) =>
              set({ ...d, request_timeout_seconds: Number(e.target.value) })
            }
          />
        </Field>
      </details>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={d.clear_key}
          onChange={(e) => set({ ...d, clear_key: e.target.checked })}
        />
        清除本地保存的 Gemini API Key
      </label>
      <div className="modal-actions">
        <button className="button primary" disabled={busy}>
          保存 Gemini 配置
          <Check size={15} />
        </button>
      </div>
    </form>
  );
}

function SettingsForm({
  initial,
  busy,
  onSave,
}: {
  initial: Data;
  busy: boolean;
  onSave: (d: Data) => void;
}) {
  const [d, set] = useState<Data>({
    enabled: !!initial.enabled,
    provider: initial.provider || "deepseek-official",
    model: initial.model || "deepseek-v4-flash",
    base_url: initial.base_url || "",
    api_key: "",
    clear_key: false,
    request_timeout_seconds: initial.request_timeout_seconds || 300,
    max_output_tokens: initial.max_output_tokens || 16384,
    reasoning_effort: initial.reasoning_effort || "low",
  });
  return (
    <form
      className="modal-body"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(d);
      }}
    >
      <div className="settings-status">
        <CheckCircle2 size={23} />
        <div>
          <strong>官方 DeepSeek Harness SDK</strong>
          <small>
            {initial.sdk_version
              ? "已安装 v" + initial.sdk_version
              : "尚未安装"}{" "}
            · {initial.has_key ? "凭证已配置" : "凭证待配置"}
          </small>
        </div>
      </div>
      <Field
        label="DeepSeek API Key"
        hint="仅保存在本机 data/runtime-settings.json；输入留空会保留已有配置。"
      >
        <input
          type="password"
          autoComplete="new-password"
          value={d.api_key}
          placeholder={
            initial.has_key ? "已配置 · 留空保留" : "输入你的 DeepSeek API Key"
          }
          onChange={(e) => set({ ...d, api_key: e.target.value })}
        />
      </Field>
      <div className="form-grid">
        <Field label="模型 ID">
          <input
            required
            value={d.model}
            onChange={(e) => set({ ...d, model: e.target.value })}
          />
        </Field>
        <Field label="请求超时（秒）">
          <input
            required
            type="number"
            min="30"
            max="3600"
            value={d.request_timeout_seconds}
            onChange={(e) =>
              set({ ...d, request_timeout_seconds: Number(e.target.value) })
            }
          />
        </Field>
      </div>
      <Field label="API 地址（可选）" hint="留空使用官方默认地址。">
        <input
          value={d.base_url}
          placeholder="https://…"
          onChange={(e) => set({ ...d, base_url: e.target.value })}
        />
      </Field>
      <details>
        <summary>高级设置：回答长度与思考强度</summary>
        <Field
          label="最大输出 token 数"
          hint="包含模型思考和回答；更高上限可能增加费用，仍可能截断。"
        >
          <select
            value={d.max_output_tokens}
            onChange={(e) =>
              set({ ...d, max_output_tokens: Number(e.target.value) })
            }
          >
            {[8192, 16384, 32768, 65536].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </Field>
        <Field label="思考强度">
          <select
            value={d.reasoning_effort}
            onChange={(e) => set({ ...d, reasoning_effort: e.target.value })}
          >
            <option value="low">低（默认）</option>
            <option value="high">高</option>
          </select>
        </Field>
      </details>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={d.enabled}
          onChange={(e) => set({ ...d, enabled: e.target.checked })}
        />
        启用模型任务（发送消息或确认交接时调用）
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={d.clear_key}
          onChange={(e) => set({ ...d, clear_key: e.target.checked })}
        />
        清除本地保存的 DeepSeek API Key
      </label>
      <div className="soft-note">
        媒体API在上方独立配置。分镜讨论可按本轮素材额度直接生成或批量出图，
        “分镜与素材”保留手动估价确认入口；Gemini观众校验保持禁用。
      </div>
      <div className="modal-actions">
        <button className="button primary" disabled={busy}>
          保存 DeepSeek 配置
          <Check size={15} />
        </button>
      </div>
    </form>
  );
}
function TaskForm({
  roles,
  initialRole,
  tasks,
  records,
  busy,
  onSave,
}: {
  roles: Role[];
  initialRole?: string;
  tasks: Data[];
  records: RecordItem[];
  busy: boolean;
  onSave: (d: Data) => void;
}) {
  const [role, setRole] = useState(initialRole || "director"),
    [goal, setGoal] = useState(""),
    [deps, setDeps] = useState<string[]>([]),
    [refs, setRefs] = useState<Data[]>([]);
  return (
    <form
      className="modal-body"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ role, goal, dependencies: deps, input_refs: refs });
      }}
    >
      <Field label="负责角色">
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {roles.map((r) => (
            <option value={r.id} key={r.id}>
              {r.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="具体目标 / 返工范围">
        <textarea
          required
          rows={4}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="描述交付物、需要修复的问题和验收标准"
        />
      </Field>
      <Field label="依赖的上游任务">
        <select
          multiple
          value={deps}
          className="multi-select"
          onChange={(e) =>
            setDeps(Array.from(e.target.selectedOptions).map((o) => o.value))
          }
        >
          {tasks.map((t) => (
            <option key={t.id} value={t.id}>
              {roles.find((r) => r.id === t.role)?.name} · {t.goal} ·{" "}
              {statusNames[t.status]}
            </option>
          ))}
        </select>
      </Field>
      <RefPicker records={records} value={refs} onChange={setRefs} />
      <div className="modal-actions">
        <button className="button primary" disabled={busy}>
          建立任务
          <Plus size={15} />
        </button>
      </div>
    </form>
  );
}
function ResultPanel({
  task,
  artifact,
  draft,
  assets,
  busy,
  onSubmit,
  onReview,
  onImport,
}: {
  assets: RecordItem[];
  task: Data;
  artifact?: RecordItem;
  draft?: Data;
  busy: boolean;
  onSubmit: (data: Data) => void;
  onReview: () => void;
  onImport: (index: number) => void;
}) {
  const [editing, setEditing] = useState(!artifact),
    [finalAsset, setFinalAsset] = useState(artifact?.data.final_asset_id || ""),
    [d, set] = useState<Data>({
      title: artifact?.data.title || draft?.draft_fields.title || "",
      summary: artifact?.data.summary || draft?.draft_fields.summary || "",
      body: artifact?.data.body || draft?.draft_fields.body || "",
      uncertainties: (
        artifact?.data.uncertainties ||
        draft?.warnings ||
        []
      ).join("\n"),
    });
  if (editing)
    return (
      <form
        className="modal-body"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit({
            ...d,
            uncertainties: d.uncertainties.split("\n").filter(Boolean),
            proposed_records: artifact?.data.proposed_records || [],
            expected_version: artifact?.version || 0,
            final_asset_id: finalAsset || null,
          });
        }}
      >
        <div className="soft-note">
          手动填写用于导入或补充答案。请核对完整性与预算单位；保存后仍需验收。通常先运行
          Agent，无需在这里自己写一遍。
        </div>
        <Field label="成果标题">
          <input
            required
            value={d.title}
            onChange={(e) => set({ ...d, title: e.target.value })}
          />
        </Field>
        <Field label="摘要">
          <textarea
            required
            rows={2}
            value={d.summary}
            onChange={(e) => set({ ...d, summary: e.target.value })}
          />
        </Field>
        <Field label="成果正文（支持 Markdown 文本）">
          <textarea
            required
            className="result-editor"
            rows={12}
            value={d.body}
            onChange={(e) => set({ ...d, body: e.target.value })}
          />
        </Field>
        <Field label="不确定 / 未验证项（每行一项）">
          <textarea
            rows={3}
            value={d.uncertainties}
            onChange={(e) => set({ ...d, uncertainties: e.target.value })}
          />
        </Field>
        {task.role === "post" && (
          <Field
            label="最终成片素材（可选）"
            hint="仅实际成片可选择；动态分镜不能作为最终成片放行。"
          >
            <select
              value={finalAsset}
              onChange={(e) => setFinalAsset(e.target.value)}
            >
              <option value="">仅提交文本计划 / 尚未完成成片</option>
              {assets
                .filter((a) => a.data.media_type === "video")
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.data.name}
                  </option>
                ))}
            </select>
          </Field>
        )}
        <div className="modal-actions">
          <button className="button primary" disabled={busy || !!task.blocker}>
            提交待验收
            <ArrowRight size={15} />
          </button>
        </div>
        {task.blocker && <div className="blocker">{task.blocker}</div>}
      </form>
    );
  return (
    <div className="modal-body">
      <div className="inline spread">
        <Badge status={task.status} stale={artifact?.stale} />
        <small>
          成果 V{artifact?.version} ·{" "}
          {artifact?.data.source === "deepseek_harness"
            ? "DeepSeek 生成"
            : "人工提交"}
        </small>
      </div>
      <h3>{artifact?.data.title}</h3>
      <p>{artifact?.data.summary}</p>
      <MarkdownView text={artifact?.data.body || ""} />
      {artifact?.data.video_url && (
        <video
          controls
          className="final-preview"
          src={artifact.data.video_url}
        />
      )}{" "}
      {!!artifact?.data.uncertainties?.length && (
        <div className="soft-note">
          未验证：{artifact.data.uncertainties.join("；")}
        </div>
      )}
      {artifact?.data.proposed_records?.map((p: Data, i: number) => (
        <div className="proposal" key={i}>
          <small>候选建议 · {p.kind}</small>
          <pre>{JSON.stringify(p.data, null, 2)}</pre>
          <Button
            disabled={task.status !== "accepted" || busy}
            onClick={() => onImport(i)}
          >
            审核后导入建议
          </Button>
        </div>
      ))}
      <div className="modal-actions">
        {!["running", "input_changed"].includes(task.status) && (
          <Button onClick={() => setEditing(true)}>编辑并提交新版本</Button>
        )}
        {task.status === "pending_review" && (
          <Button primary onClick={onReview}>
            验收成果
            <ShieldCheck size={15} />
          </Button>
        )}
      </div>
    </div>
  );
}
function ReviewForm({
  artifact,
  busy,
  onSave,
}: {
  artifact: RecordItem;
  busy: boolean;
  onSave: (d: Data) => void;
}) {
  const [rows, setRows] = useState<Data[]>(
      artifact.data.final_movie
        ? [
            "画面与一致性",
            "声音与节奏",
            "字幕与信息",
            "时长与画幅",
            "目标行为与后续价值",
          ].map((criterion) => ({ criterion, status: "unknown", note: "" }))
        : [
            {
              criterion: "成果符合任务目标与已确认约束",
              status: "unknown",
              note: "",
            },
            {
              criterion: "事实、假设和未验证事项已明确区分",
              status: "unknown",
              note: "",
            },
          ],
    ),
    [note, setNote] = useState(""),
    [ack, setAck] = useState(false);
  return (
    <form className="modal-body" onSubmit={(e) => e.preventDefault()}>
      <div className="soft-note">
        当前验收：{artifact.data.title} · V{artifact.version}
        。请阅读实际成果，再记录每项观察。
      </div>
      {rows.map((row, i) => (
        <div className="check-row" key={i}>
          <Field label={"检查 " + (i + 1)}>
            <input
              required
              value={row.criterion}
              onChange={(e) =>
                setRows(
                  rows.map((r, n) =>
                    n === i ? { ...r, criterion: e.target.value } : r,
                  ),
                )
              }
            />
          </Field>
          <div className="form-grid">
            <Field label="结果">
              <select
                value={row.status}
                onChange={(e) =>
                  setRows(
                    rows.map((r, n) =>
                      n === i ? { ...r, status: e.target.value } : r,
                    ),
                  )
                }
              >
                <option value="unknown">未验证</option>
                <option value="passed">通过</option>
                <option value="failed">失败</option>
              </select>
            </Field>
            <Field label="观察证据 / 对应位置">
              <input
                value={row.note}
                required
                placeholder="具体段落、画面或文件"
                onChange={(e) =>
                  setRows(
                    rows.map((r, n) =>
                      n === i ? { ...r, note: e.target.value } : r,
                    ),
                  )
                }
              />
            </Field>
          </div>
        </div>
      ))}
      <Button
        onClick={() =>
          setRows([...rows, { criterion: "", status: "unknown", note: "" }])
        }
      >
        <Plus size={14} />
        增加检查项
      </Button>
      <Field label="评审意见">
        <textarea
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </Field>
      {artifact.data.uncertainties?.length > 0 && (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={ack}
            onChange={(e) => setAck(e.target.checked)}
          />
          已阅读并接受当前未验证项：{artifact.data.uncertainties.join("；")}
        </label>
      )}
      <div className="modal-actions">
        <Button
          disabled={busy}
          onClick={() =>
            onSave({
              outcome: "changes_requested",
              note,
              acknowledged_uncertainties: ack,
              checks: rows.map((r) => ({
                ...r,
                evidence_refs: [
                  {
                    kind: "artifact",
                    id: artifact.id,
                    version: artifact.version,
                  },
                ],
              })),
            })
          }
        >
          退回修改
        </Button>
        <Button
          primary
          disabled={
            busy ||
            rows.some(
              (r) => r.status !== "passed" || !r.criterion || !r.note,
            ) ||
            (artifact.data.uncertainties?.length > 0 && !ack)
          }
          onClick={() =>
            onSave({
              outcome: "accepted",
              note,
              acknowledged_uncertainties: ack,
              checks: rows.map((r) => ({
                ...r,
                evidence_refs: [
                  {
                    kind: "artifact",
                    id: artifact.id,
                    version: artifact.version,
                  },
                ],
              })),
            })
          }
        >
          <Check size={15} />
          验收通过
        </Button>
      </div>
    </form>
  );
}
function GateForm({
  stage,
  record,
  artifacts,
  tasks,
  busy,
  onSave,
}: {
  stage: string;
  record?: RecordItem;
  artifacts: RecordItem[];
  tasks: Data[];
  busy: boolean;
  onSave: (d: Data, refs: Data[]) => void;
}) {
  const [note, setNote] = useState(record?.data.note || ""),
    [allow, setAllow] = useState(record?.data.allow || false),
    [risk, setRisk] = useState(record?.data.risks_acknowledged || false),
    [refs, setRefs] = useState<Data[]>(record?.input_refs || []);
  return (
    <form
      className="modal-body"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(
          { stage, note, allow, risks_acknowledged: risk, approver: "human" },
          refs,
        );
      }}
    >
      <RefPicker
        records={artifacts.filter((a) =>
          tasks.some((t) => t.result_id === a.id && t.status === "accepted"),
        )}
        value={refs}
        onChange={setRefs}
      />
      <Field label="决定理由 / 允许动作 / 预算边界">
        <textarea
          required
          rows={4}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="写下本阶段允许继续什么，以及保留哪些风险"
        />
      </Field>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={risk}
          onChange={(e) => setRisk(e.target.checked)}
        />
        明确接受仍未获得支持的假设风险（不改变证据结论）
      </label>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={allow}
          onChange={(e) => setAllow(e.target.checked)}
        />
        允许进入下一阶段
      </label>
      <div className="soft-note">
        G0 引用观众策略成果；G1 引用编剧成果；G2 引用分镜成果；G3
        需要真实成片与最终评审，当前动态分镜不能替代成片。
      </div>
      <div className="modal-actions">
        <button className="button primary" disabled={busy}>
          保存决定
          <ShieldCheck size={15} />
        </button>
      </div>
    </form>
  );
}
function RunForm({
  task,
  retry,
  balance,
  busy,
  onRun,
}: {
  task: Data;
  retry?: boolean;
  balance: Data;
  busy: boolean;
  onRun: (cost: number) => void;
}) {
  const [cost, setCost] = useState(1);
  return (
    <form
      className="modal-body"
      onSubmit={(e) => {
        e.preventDefault();
        onRun(cost);
      }}
    >
      <h3>{task.goal}</h3>
      {retry && (
        <div className="soft-note">
          这会建立替代任务并发出一次新的模型请求，可能产生新费用。上次会话与{" "}
          {task.reservation}{" "}
          元费用预留会保留，直到按实际账单核账；尚未运行的下游任务将连接到新结果。
        </div>
      )}
      <p className="muted">
        将发送当前 Brief、版本化输入和参考记录给已配置的 DeepSeek 服务。
      </p>
      <Field
        label="本次预留额度（元）"
        hint={`项目剩余额度 ${(balance.budget - balance.actual - balance.reserved).toFixed(2)} 元。`}
      >
        <input
          type="number"
          min="0.01"
          step="0.01"
          required
          value={cost}
          onChange={(e) => setCost(Number(e.target.value))}
        />
      </Field>
      <div className="soft-note">
        预留额度用于项目核账，无法替代供应商的硬性收费上限。结束后需按账单记录真实费用；中断不会自动重新请求。
      </div>
      <div className="modal-actions">
        <button className="button primary" disabled={busy}>
          确认发送并运行
          <Play size={14} />
        </button>
      </div>
    </form>
  );
}
function CostForm({
  task,
  busy,
  onSave,
}: {
  task: Data;
  busy: boolean;
  onSave: (data: Data) => void;
}) {
  const [cost, setCost] = useState(0),
    [evidence, setEvidence] = useState("");
  return (
    <form
      className="modal-body"
      onSubmit={(e) => {
        e.preventDefault();
        onSave({ actual_cost: cost, evidence });
      }}
    >
      <div className="soft-note">
        已预留 {task.reservation} 元。请先核对供应商账单和会话 {task.session_id}
        ，不要把未知费用记为零。
      </div>
      <Field label="实际费用（元）">
        <input
          required
          type="number"
          min="0"
          step="0.0001"
          value={cost}
          onChange={(e) => setCost(Number(e.target.value))}
        />
      </Field>
      <Field label="账单 / 核对证据">
        <textarea
          required
          rows={3}
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
        />
      </Field>
      <div className="modal-actions">
        <button className="button primary" disabled={busy}>
          保存核账记录
          <Check size={15} />
        </button>
      </div>
    </form>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
