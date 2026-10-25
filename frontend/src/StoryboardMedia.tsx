import { useState } from "react";
import ReplaceImage from "./ReplaceImage";

type Data = Record<string, any>;

export default function StoryboardMedia({ calls, pid, api, onChange }: {
  calls: Data[]; pid: string; api: (path: string, body?: Data, method?: string) => Promise<any>;
  onChange: () => Promise<any>;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [settling, setSettling] = useState("");
  const [amount, setAmount] = useState("");
  const [evidence, setEvidence] = useState("");
  if (!calls.length) return null;
  return <div className="storyboard-media-calls">{calls.map(call => {
    const job = call.result?.job;
    const asset = call.result?.asset;
    const status = { planned: "准备提交", running: "生成中", completed: "结果已取回", interrupted: "待处理", submitted: "已提交" }[call.status as string] || call.status;
    return <section key={call.id} className="storyboard-media-card">
      <div className="inline spread"><strong>{call.tool === "generate_image_batch" ? "图片队列" : call.tool === "generate_media" ? "分镜生成" : "取回生成结果"} · {status}</strong>
        {job && <small>{job.provider} · {job.result.model}</small>}</div>
      {call.arguments.request?.prompt && <p>{call.arguments.request.prompt}</p>}
      {asset?.data.media_type === "image" && <img src={asset.data.url} alt={asset.data.name || "生成画面，等待你检查"} />}
      {asset?.data.media_type === "image" && <ReplaceImage asset={asset} pid={pid} api={api} onChange={onChange} />}
      {asset?.data.media_type === "video" && <video src={asset.data.url} controls preload="metadata" />}
      {asset && <p className="muted">素材已保存，可继续说“调整这里”或“用这张图生成视频”。质量需你查看确认。</p>}
      {(asset?.stale || job?.stale) && <p className="chat-warning">输入已变化，这份结果需要复查。</p>}
      {(call.error || job?.error) && <p role="alert" className="chat-warning">{call.error || job.error}</p>}
      {job && <p className="muted">{asset?.data.source === "human_replacement" ? "原生成费用：" : ""}{job.actual_cost === null ? `预留 ¥${job.reservation}，待核账` : `实际 ¥${job.actual_cost}`}
        {job.result.usage?.cost_usd !== undefined ? ` · 供应商返回 $${job.result.usage.cost_usd} USD` : ""}</p>}
      <div className="inline">
        {job?.status === "interrupted" && job.remote_id && <button className="button" disabled={!!busy} onClick={async () => {
          setBusy(job.id); setError("");
          try { await api(`/projects/${pid}/media-jobs/${job.id}/retrieve`, {}, "POST"); await onChange(); }
          catch (e) { setError((e as Error).message); }
          finally { setBusy(""); }
        }}>取回原结果（不重新生成）</button>}
        {job && job.status !== "running" && job.actual_cost === null && <button className="button" onClick={() => {
          setSettling(job.id); setAmount(""); setEvidence(""); setError("");
        }}>核对素材费用</button>}
      </div>
      {job && settling === job.id && <form className="media-settlement" onSubmit={async e => {
        e.preventDefault(); setBusy(job.id); setError("");
        try {
          await api(`/projects/${pid}/media-jobs/${job.id}/reconcile`, { actual_cost: Number(amount), evidence }, "POST");
          await onChange(); setSettling("");
        } catch (e) { setError((e as Error).message); }
        finally { setBusy(""); }
      }}>
        <label className="media-field">账单实际金额（元）<input type="number" min="0" step="any" required value={amount} onChange={e => setAmount(e.target.value)} /></label>
        <label className="media-field">账单依据<input required maxLength={2000} value={evidence} onChange={e => setEvidence(e.target.value)} /></label>
        <button className="button" disabled={!!busy}>保存核账</button>
      </form>}
    </section>;
  })}{error && <p role="alert" className="chat-warning">{error}</p>}</div>;
}
