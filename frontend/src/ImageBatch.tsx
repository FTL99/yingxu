import { useState } from "react";
import ReplaceImage from "./ReplaceImage";

type Data = Record<string, any>;

export default function ImageBatch({ batch, pid, api, onChange }: {
  batch: Data; pid: string; api: (path: string, body?: Data, method?: string) => Promise<any>;
  onChange: () => Promise<any>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<Data | null>(null);
  const labels: Record<string, string> = {
    queued: "等待执行", running: "逐张生成中", paused: "已暂停", completed: "队列完成", cancelled: "已取消",
    pending: "等待生成", submitting: "正在提交", failed: "生成失败",
  };
  const live = ["queued", "running"].includes(batch.status);
  async function command(action: string) {
    setBusy(true); setError("");
    try { await api(`/projects/${pid}/image-batches/${batch.id}/${action}`, {}, "POST"); await onChange(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="image-batch-overview" aria-label="图片队列总览">
    <header className="inline spread"><h3>{batch.title}</h3><strong>{labels[batch.status] || batch.status}</strong></header>
    <p className="muted" role="status">已完成 {batch.counts.completed} / {batch.items.length} 张
      {batch.counts.failed > 0 ? ` · 失败 ${batch.counts.failed} 张` : ""}
      {batch.counts.cancelled > 0 ? ` · 取消 ${batch.counts.cancelled} 张` : ""}
      。结果会自动汇集在这里，可以离开页面，回来继续查看。</p>
    <div className="inline">
      {live && <button className="button" disabled={busy} onClick={() => command("pause")}>暂停队列</button>}
      {batch.status === "paused" && <button className="button primary" disabled={busy || batch.counts.running > 0} onClick={() => command("resume")}>继续剩余图片</button>}
      {["queued", "running", "paused"].includes(batch.status) && <button className="button" disabled={busy} onClick={() => command("cancel")}>取消剩余图片</button>}
    </div>
    {(batch.error || error) && <p className="chat-warning" role="alert">{error || batch.error}</p>}
    <div className="image-batch-grid">{batch.items.map((item: Data, i: number) => {
      const asset = item.result?.asset;
      const job = item.result?.job;
      return <article className="image-batch-item" key={i}>
        <div className="image-batch-image">{asset?.data.url
          ? <button onClick={() => setPreview({ name: item.name, url: asset.data.url })} aria-label={`放大 ${item.name}`}>
              <img src={asset.data.url} alt={item.name} loading="lazy" /></button>
          : <span>{labels[item.status] || item.status}</span>}</div>
        <strong>{i + 1}. {item.name}</strong>
        <small>{asset?.data.source === "human_replacement" ? "已换成你的图片 · 待你查看" : item.status === "completed" ? "已生成 · 待你查看" : labels[item.status] || item.status}</small>
        {asset?.data.media_type === "image" && <ReplaceImage asset={asset} pid={pid} api={api} onChange={onChange} />}
        {(asset?.stale || job?.stale) && <small className="chat-warning">输入已变化，需复查</small>}
        {item.error && <details><summary>失败原因</summary><p>{item.error}</p></details>}
        {job && <small>{asset?.data.source === "human_replacement" ? "原生成费用：" : ""}{job.actual_cost === null ? `预留 ¥${job.reservation}，待核账` : `实际 ¥${job.actual_cost}`}</small>}
      </article>;
    })}</div>
    {batch.counts.failed > 0 && <p className="muted">失败图片不会自动重试；继续队列只生成未提交项。费用可在“分镜与素材”中核对。</p>}
    {preview && <div className="image-batch-lightbox" role="dialog" aria-modal="true" aria-label={preview.name} onClick={() => setPreview(null)}>
      <div onClick={e => e.stopPropagation()}><div className="inline spread"><strong>{preview.name}</strong>
        <button className="button" autoFocus onClick={() => setPreview(null)} onKeyDown={e => { if (e.key === "Escape") setPreview(null); }}>关闭预览</button></div>
        <img src={preview.url} alt={preview.name} /></div>
    </div>}
  </section>;
}
