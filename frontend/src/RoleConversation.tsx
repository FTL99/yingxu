import { useEffect, useRef, useState } from "react";
import MarkdownView from "./MarkdownView";
import { writerSceneTables } from "./writerSceneTables";
import StoryboardMedia from "./StoryboardMedia";
import ImageBatch from "./ImageBatch";

type Data = Record<string, any>;
type Props = {
  pid: string;
  task: Data;
  name: string;
  ready: boolean;
  balance: Data;
  capabilities: Data;
  compactAudienceAvailable: boolean;
  onAdvanced: (result: Data) => Promise<any>;
  onReopened: (result: Data) => Promise<any>;
  api: (path: string, body?: Data, method?: string) => Promise<any>;
  onChange: () => Promise<any>;
  onReview: () => void;
  onGate?: () => void;
  onNext?: () => void;
};

export default function RoleConversation({
  pid,
  task,
  name,
  ready,
  balance,
  capabilities,
  compactAudienceAvailable,
  onAdvanced,
  onReopened,
  api,
  onChange,
  onReview,
  onGate,
  onNext,
}: Props) {
  const [conversation, setConversation] = useState<Data>({
    messages: [],
    jobs: [],
  });
  const [text, setText] = useState(""),
    [cost, setCost] = useState(1),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [mergeAudience, setMergeAudience] = useState(true);
  const [mediaBudget, setMediaBudget] = useState(10);
  const [acknowledged, setAcknowledged] = useState(false);
  const [handoff, setHandoff] = useState<Data | null>(null);
  const transcript = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const last = conversation.messages.at(-1);
    if (last?.job_id && transcript.current)
      transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [conversation.messages.at(-1)?.id,
    (conversation.media_calls || []).map((c: Data) => `${c.id}:${c.status}:${c.result?.asset?.id || ""}`).join("|")]);
  const url = `/projects/${pid}/tasks/${task.id}/chat`;
  const running =
    conversation.running || ["running", "input_changed"].includes(task.status);
  const polling = running || conversation.media_running;
  const batches = (conversation.media_calls || []).filter((c: Data) => c.result?.batch).map((c: Data) => c.result.batch);
  const queueActive = batches.some((b: Data) => ["queued", "running", "paused"].includes(b.status));
  const readOnly =
    !!task.superseded_by ||
    ["accepted", "needs_review", "input_changed", "merged"].includes(
      task.status,
    );
  const revisionMode = !!(task.revision_open || task.sync_pending);
  const canReopen = !task.superseded_by && ["accepted", "needs_review", "merged"].includes(task.status);
  async function load() {
    setConversation(await api(url));
  }
  async function reopen() {
    setBusy(true); setError("");
    try {
      const result = await api(`/projects/${pid}/tasks/${task.id}/reopen`, {
        expected_brief_revision: task.brief_revision,
        artifact_version: conversation.artifact_version || 0,
      }, "POST");
      await load(); await onReopened(result);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  useEffect(() => {
    let active = true;
    api(url)
      .then((v) => {
        if (active) setConversation(v);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [url]);
  useEffect(() => {
    let active = true;
    if (!task.blocker && !task.merged_into)
      api(`/projects/${pid}/tasks/${task.id}/handoff`)
        .then((v) => {
          if (active) setHandoff(v);
        })
        .catch(() => {
          if (active) setHandoff(null);
        });
    return () => {
      active = false;
    };
  }, [url, task.blocker, task.status]);
  useEffect(() => {
    setAcknowledged(false);
  }, [conversation.messages.at(-1)?.id, conversation.artifact_version]);
  useEffect(() => {
    if (!polling) return;
    let active = true;
    const timer = setInterval(
      () =>
        api(url)
          .then(async (v) => {
            if (!active) return;
            setConversation(v);
            if (!v.running) await onChange();
          })
          .catch((e) => {
            if (active) setError(e.message);
          }),
      2000,
    );
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [url, polling]);
  async function send(message: string, mode = "discussion") {
    if (!message.trim() || busy || running || readOnly) return;
    setBusy(true);
    setError("");
    try {
      await api(url, { message, max_cost: cost, mode,
        ...(task.role === "storyboard" ? { media_budget: mode === "discussion" ? mediaBudget : 0 } : {}) }, "POST");
      if (mode === "discussion") setText("");
      await load();
      await onChange();
    } catch (e) {
      setError((e as Error).message);
      await load().catch(() => {});
      await onChange().catch(() => {});
    } finally {
      setBusy(false);
    }
  }
  async function finalize() {
    setBusy(true);
    setError("");
    try {
      await api(url + "/finalize", { message_id: last.id }, "POST");
      await onChange();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const last = conversation.messages.at(-1);
  const canFinalize =
    !queueActive &&
    task.status === "discussing" &&
    last?.role === "assistant" &&
    last.complete &&
    !!last.result;
  const canAdvance =
    !queueActive &&
    (task.role !== "post" || revisionMode) &&
    !task.merged_into &&
    (canFinalize || ["pending_review", "accepted"].includes(task.status)) &&
    (!revisionMode || !task.revision_message_id || last?.id !== task.revision_message_id);
  const hasRisks = !!(
    conversation.advance?.risks?.length ||
    conversation.advance?.assumptions?.length ||
    conversation.advance?.unverified_hypotheses
  );
  async function advance() {
    if (busy || running) return;
    setBusy(true);
    setError("");
    try {
      const result = await api(
        `/projects/${pid}/tasks/${task.id}/advance`,
        {
          confirmed: true,
          expected_brief_revision: task.brief_revision,
          message_id: last?.id,
          artifact_version: conversation.artifact_version || 0,
          merge_audience: !revisionMode &&
            task.role === "director" &&
            compactAudienceAvailable &&
            mergeAudience,
          acknowledged_uncertainties: acknowledged,
          start_next: ready && !revisionMode,
          max_cost: cost,
        },
        "POST",
      );
      await onAdvanced(result);
    } catch (e) {
      setError((e as Error).message);
      await load().catch(() => {});
      await onChange().catch(() => {});
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="role-conversation">
      <header className="conversation-heading">
        <div>
          <span className="eyebrow">一起打磨这一阶段</span>
          <h2>与{name}讨论</h2>
        </div>
        <span>
          {running
            ? "正在回复…"
            : task.status === "accepted"
              ? "此阶段已验收"
              : "可以继续追问和修改"}
        </span>
      </header>
      <p className="conversation-intro">
        {revisionMode ? "沿用原讨论修改设定，确认后同步其他 Agent。每个受影响环节会承接新设定，再由你确认更新后的方案。" : "先聊清楚，确认无异议后自动交给下一位 Agent。决定、讨论和资料版本会一起传递。"}
      </p>
      {canReopen && <aside className="role-revision-notice">
        <strong>可以回来修改这一阶段</strong>
        <p>继续沿用原讨论，修改后确认同步给其他 Agent。原成果和素材保留，相关旧方案会提示更新。</p>
        <button className="button primary" disabled={busy || running} onClick={reopen}>
          {task.merged_into ? "修改受众设定（由总控承接）" : "修改本阶段设定"}
        </button>
      </aside>}
      {revisionMode && <aside className="role-revision-notice" role="status">
        <strong>{task.sync_pending ? "已收到其他 Agent 的修改" : "正在修改原设定"}</strong>
        <p>可以继续讨论并调整本环节。得到新方案后点击“确认修改并同步其他 Agent”；同步不产生模型费用，不重新生成图片或视频。</p>
        {task.blocker && <p>上游尚未确认时，可以先讨论；确认同步前需完成上游更新。</p>}
      </aside>}
      {!!conversation.sync_changes?.length && <details className="role-sync-updates" open={!!task.sync_pending}>
        <summary>最新同步的设定（{conversation.sync_changes.length} 项）</summary>
        {conversation.sync_changes.slice(0, 6).map((change: Data) => <div key={change.artifact_ref.id}>
          <strong>{{director:"总控",audience:"观众策略",writer:"编剧",assets:"一致性资产",storyboard:"分镜",post:"后期"}[change.role as string] || change.role} · V{change.artifact_ref.version}</strong>
          <p>{change.summary}</p>
          <details><summary>查看完整新版设定</summary><MarkdownView text={change.body} /></details>
          {!change.confirmed && <small>已保存文字方案，成片仍待人工验收。</small>}
        </div>)}
      </details>}
      {task.role === "storyboard" && capabilities.storyboard_media_tools?.enabled && <aside className="capability-notice">
        分镜可以直接生成图片或视频。说“把需要的图片加入队列，全部生成后给我总览”，就会在这里逐张生成，每批最多50张。整批共用本轮素材额度；设为0元时只讨论。
        <button className="button" disabled={running || busy || queueActive} onClick={() => setText("沿用我们已确认的分镜和资产，把本阶段需要的图片一次加入队列，逐张生成直到完成，在当前窗口展示总览。已生成图片不重复生成；如果整批超过本轮素材额度，先告诉我需要多少费用。")}>准备批量出图</button>
      </aside>}
      {task.role === "director" && (
        <p className="conversation-intro">
          总控负责收敛方向、明确约束和交接；流程由工作台落实，聊天里的同意不会自行改写任务状态。
        </p>
      )}
      {task.merged_into && (
        <aside className="capability-notice">
          受众说明已由总控承接，随交接包传给编剧；此任务保留记录，未执行观众校验。
        </aside>
      )}
      {!!handoff?.upstream?.length && (
        <details className="handoff-package">
          <summary>
            已收到交接包 · {handoff!.upstream.length} 个已确认成果
          </summary>
          <p>
            Brief 第 {handoff!.brief_revision}{" "}
            版；以下资料已作为本角色输入，未验证事项仍保留。
          </p>
          {handoff!.upstream.map((item: Data) => (
            <div key={item.task_id}>
              <strong>
                {item.title} · v{item.artifact_ref.version}
              </strong>
              <p>{item.summary}</p>
              <small>
                含 {item.discussion.length} 条讨论记录、
                {item.uncertainties.length} 项未验证事项。
              </small>
            </div>
          ))}
        </details>
      )}
      {task.role === "audience" &&
        capabilities?.audience_video_validation?.enabled !== true && (
          <aside className="capability-notice" role="note">
            <strong>观众视频校验已禁用 · 当前仅文字策划</strong>
            <p>
              可以继续讨论受众、创意和测试方案。当前 Agent 不会观看视频，
              也不会验证真实观众反应；进入下一阶段只表示你认可策略方向。
            </p>
          </aside>
        )}
      {batches.map((batch: Data) => <ImageBatch key={batch.id} batch={batch} pid={pid} api={api}
        onChange={async () => { await load(); await onChange(); }} />)}
      <div
        className="conversation-transcript"
        aria-live="polite"
        ref={transcript}
      >
        {conversation.messages.length === 0 && !task.merged_into && (
          <div className="chat-empty">
            <h3>Brief 已就位，从你最想解决的问题开始</h3>
            <p>
              {task.role === "storyboard" ? "例如：沿用已确认主线，先生成开场风格帧给我看，然后根据我的反馈调整。" :
                "例如：“先帮我比较三个方向，每个用两句话说明，然后告诉我你最需要确认什么。”"}
            </p>
            <button
              className="button"
              onClick={() =>
                setText(
                  task.role === "storyboard" ? "沿用已确认主线和资产，先生成开场风格帧给我看。如果缺少必要参考，先说明。" :
                    "先帮我比较 Brief 中的几个方向，每个用两句话说明。告诉我你推荐哪一个，以及你最需要我确认的问题。",
                )
              }
            >
              用这个问题开始
            </button>
          </div>
        )}
        {conversation.messages.map((message: Data) => {
          const longInitial = message.initial && message.text.length > 2000;
          return (
            <article
              className={"chat-message " + (message.system_notice ? "system-notice" : message.role)}
              key={message.id}
            >
              <div className="chat-author">
                {message.system_notice ? "工作台 · 更新通知" : message.role === "user" ? "你" : name}
                {message.initial && !message.system_notice ? task.sync_pending ? " · 旧版回答，待更新" : " · 已有回答" : ""}
                {!message.complete ? " · 未完成草稿" : ""}
              </div>
              {message.warnings?.length > 0 && (
                <p className="chat-warning">{message.warnings.join(" ")}</p>
              )}
              {longInitial && !expanded ? (
                <>
                  <MarkdownView
                    text={
                      message.result?.summary ||
                      "之前的回答已带入上下文。可以直接提出修改意见，或展开查看完整内容。"
                    }
                  />
                  <button className="button" onClick={() => setExpanded(true)}>
                    展开已有回答（含表格）
                  </button>
                </>
              ) : (
                <MarkdownView text={task.role === "writer" && message.role === "assistant" ? writerSceneTables(message.text) : message.text} />
              )}
              {longInitial && expanded && (
                <button className="button" onClick={() => setExpanded(false)}>
                  收起已有回答
                </button>
              )}
              {message.role === "assistant" && <StoryboardMedia
                calls={(conversation.media_calls || []).filter((c: Data) => c.chat_job_id === message.job_id && !c.result?.batch)}
                pid={pid} api={api} onChange={async () => { await load(); await onChange(); }} />}
              {message.role === "assistant" &&
                message.result?.uncertainties?.length > 0 && (
                  <details>
                    <summary>
                      未验证事项（{message.result.uncertainties.length}）
                    </summary>
                    <ul>
                      {message.result.uncertainties.map(
                        (s: string, i: number) => (
                          <li key={i}>{s}</li>
                        ),
                      )}
                    </ul>
                  </details>
                )}
            </article>
          );
        })}
        {running && (
          <div className="chat-thinking" role="status">
            Agent 正在结合之前的讨论回复。你可以离开页面，回答会保留。
          </div>
        )}
      </div>
      {error && (
        <p className="blocker" role="alert">
          {error}
        </p>
      )}
      {task.error && <p className="chat-warning">{task.error}</p>}
      {task.blocker && <p className="blocker">{task.blocker}</p>}
      {!readOnly && (
        <form
          className="chat-composer"
          onSubmit={(e) => {
            e.preventDefault();
            send(text);
          }}
        >
          <label htmlFor={"chat-" + task.id}>告诉 Agent 你的想法</label>
          <textarea
            id={"chat-" + task.id}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={task.role === "storyboard" ? "例如：先生成开场风格帧给我看；确认后再用它生成带环境声和音效的视频。" :
              "例如：我更喜欢海盗方向，但不要说教。先帮我调整开场，我们再讨论结尾。"}
            rows={3}
            maxLength={20000}
            disabled={running || busy}
          />
          <div className="chat-send-row">
            <label>
              对话预留（元）
              <input
                aria-label="对话每轮预留额度"
                type="number"
                min="0.01"
                step="0.01"
                value={cost}
                onChange={(e) => setCost(Number(e.target.value))}
              />
            </label>
            {task.role === "storyboard" && <label>
              本轮素材预留上限（元）
              <input aria-label="本轮素材预留上限" type="number" min="0" max="1000000" step="0.01"
                value={mediaBudget} disabled={running || busy} onChange={e => setMediaBudget(Number(e.target.value))} />
            </label>}
            <button
              className="button primary"
              disabled={
                busy || running || !ready || (!!task.blocker && !revisionMode) || !text.trim()
              }
            >
              发送
            </button>
          </div>
          <small>
            发送会调用已配置的模型。剩余额度{" "}
            {(balance.budget - balance.actual - balance.reserved).toFixed(2)}{" "}
            元；预留金额用于核账，实际费用以供应商账单为准。
          </small>
          {task.role === "storyboard" && <small>
            素材额度只在实际提交生成任务时占用，未使用额度不扣留；一批最多50张图片，逐张执行，无需逐张回复“继续”。
            按已配置报价估算并自动提交，实际费用以供应商账单为准。
          </small>}
        </form>
      )}
      {canAdvance && (
        <div className="stage-confirmation">
          {task.role === "director" && compactAudienceAvailable && (
            <label className="checkbox">
              <input
                type="checkbox"
                checked={mergeAudience}
                onChange={(e) => setMergeAudience(e.target.checked)}
                disabled={busy || running}
              />
              由总控承接受众说明，确认后直接交给编剧
            </label>
          )}
          {!!conversation.advance?.assumptions?.length && (
            <div className="soft-note">
              <strong>随确认登记为“未验证”的关键假设</strong>
              {conversation.advance.assumptions.map((h: Data, i: number) => (
                <p key={i}>{h.claim}</p>
              ))}
            </div>
          )}
          {!!conversation.advance?.risks?.length && (
            <div className="soft-note">
              <strong>确认时仍保留的未验证事项</strong>
              <ul>
                {conversation.advance.risks.map((risk: string, i: number) => (
                  <li key={i}>{risk}</li>
                ))}
              </ul>
            </div>
          )}
          {hasRisks && (
            <label className="checkbox">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                disabled={busy || running}
              />
              我理解以上未验证事项，愿意带着这些风险继续制作
            </label>
          )}
          <button
            className="button primary"
            onClick={advance}
            disabled={
              busy || running || !!task.blocker || (hasRisks && !acknowledged)
            }
          >
            {revisionMode ? "确认修改并同步其他 Agent" : task.status === "accepted" ? "继续自动交接" : "无异议，确认并交接"}
          </button>
          <small>
            {revisionMode ? "确认后把新版设定同步给其他角色，受影响的旧成果待更新；保留原版本，不自动发起任何模型或媒体调用。" : ready
              ? `确认会保存并验收当前方案、记录阶段决定，自动切到下一位并发起一轮接棒回复，预留 ${cost} 元；后续阶段等你再次确认。`
              : "确认会保存当前方案并自动切到下一位；模型未就绪时不发起调用。"}
          </small>
        </div>
      )}
      <details className="legacy-stage-actions">
        <summary>单独整理、保存与验收</summary>
        <div className="chat-stage-actions">
          {!readOnly && (
            <button
              className="button"
              disabled={
                busy ||
                running ||
                !ready ||
                (!!task.blocker && !revisionMode) ||
                !conversation.messages.length
              }
              onClick={() =>
                send(
                  "请根据我们已有的讨论，整理一版可以交给下一位 Agent 的阶段成果：已确认决定、仍待验证的假设、具体交接要点。不要把尚未确认的建议当作已决定。",
                  "finalize",
                )
              }
            >
              整理阶段成果
            </button>
          )}
          {canFinalize && (
            <button
              className="button primary"
              disabled={busy || running || !!task.blocker}
              onClick={finalize}
            >
              把最新回复定为阶段成果
            </button>
          )}
          {task.status === "pending_review" && (
            <button className="button primary" onClick={onReview}>
              验收这版成果
            </button>
          )}
          {task.status === "accepted" && onNext && (
            <button className="button primary" onClick={onNext}>
              与下一位 Agent 继续
            </button>
          )}
          {task.status === "accepted" && !onNext && onGate && (
            <button className="button primary" onClick={onGate}>
              确认进入下一阶段
            </button>
          )}
        </div>
      </details>
      <details className="chat-billing">
        <summary>本阶段对话记录与核账（{conversation.jobs.length} 轮）</summary>
        {conversation.jobs.map((job: Data) => (
          <div className="chat-charge" key={job.id}>
            <span>
              {job.id.slice(-6)} ·{" "}
              {job.status === "running"
                ? "回复中"
                : job.status === "completed"
                  ? "已回复"
                  : "未完成"}{" "}
              ·{" "}
              {job.actual_cost === null
                ? `预留 ${job.reservation} 元待核账`
                : `实际 ${job.actual_cost} 元`}
            </span>
            {job.actual_cost === null && job.status !== "running" && (
              <ChatReconcile
                job={job}
                api={api}
                pid={pid}
                onSaved={async () => {
                  await load();
                  await onChange();
                }}
              />
            )}
          </div>
        ))}
      </details>
    </section>
  );
}

function ChatReconcile({
  job,
  api,
  pid,
  onSaved,
}: {
  job: Data;
  api: Props["api"];
  pid: string;
  onSaved: () => Promise<any>;
}) {
  const [open, setOpen] = useState(false),
    [cost, setCost] = useState(""),
    [evidence, setEvidence] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  if (!open)
    return (
      <button className="button" onClick={() => setOpen(true)}>
        核账
      </button>
    );
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        try {
          await api(
            `/projects/${pid}/chat/${job.id}/reconcile`,
            { actual_cost: Number(cost), evidence },
            "POST",
          );
          await onSaved();
          setOpen(false);
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <label>
        实际费用（元）
        <input
          required
          type="number"
          min="0"
          step="0.0001"
          value={cost}
          onChange={(e) => setCost(e.target.value)}
        />
      </label>
      <label>
        账单依据
        <input
          required
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
        />
      </label>
      <button className="button" disabled={busy}>
        保存核账
      </button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
