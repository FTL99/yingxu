import { useRef, useState } from "react";
import { Upload } from "lucide-react";

type Data = Record<string, any>;

export default function ReplaceImage({ asset, pid, api, onChange }: {
  asset: Data; pid: string;
  api: (path: string, body?: Data | FormData, method?: string) => Promise<any>;
  onChange: () => Promise<any>;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [history, setHistory] = useState<Data[] | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  if (asset.data.media_type !== "image") return null;
  return <div className="image-replacement">
    <button type="button" className="button" disabled={busy} onClick={() => input.current?.click()}>
      <Upload size={14} />{busy ? "正在上传…" : "上传替换"}
    </button>
      <input ref={input} type="file" accept=".png,.jpg,.jpeg,.webp" disabled={busy} hidden
        aria-label={`上传图片替换 ${asset.data.name || "这张图片"}`}
        onChange={async e => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (!file || busy) return;
          setError(""); setNotice("");
          if (file.size > 10 * 1024 * 1024) { setError("请选择10MB以内的图片。"); return; }
          setBusy(true);
          try {
            const form = new FormData();
            form.append("file", file);
            form.append("expected_version", String(asset.version));
            await api(`/projects/${pid}/assets/${asset.id}/replace-image`, form, "POST");
            setHistory(null);
            setNotice("已替换，原图已保留。引用原图的成果需要复查。");
            try { await onChange(); }
            catch { setError("图片已保存，页面刷新失败，请刷新查看新图。"); }
          } catch (e) { setError((e as Error).message); }
          finally { setBusy(false); }
        }} />
    {asset.version > 1 && <button type="button" className="button" disabled={loadingHistory} onClick={async () => {
      if (history) { setHistory(null); return; }
      setLoadingHistory(true); setError("");
      try { setHistory(await api(`/projects/${pid}/records/asset/${asset.id}/history`)); }
      catch (e) { setError((e as Error).message); }
      finally { setLoadingHistory(false); }
    }}>{loadingHistory ? "读取旧图…" : history ? "收起旧图" : "查看旧图"}</button>}
    <small className="muted">PNG / JPG / WebP · ≤10MB · 保留原图</small>
    {asset.data.source === "human_replacement" && <small className="muted">已上传替换 · V{asset.version} · {asset.data.uploaded_filename}</small>}
    {notice && <small role="status">{notice}</small>}
    {error && <small role="alert" className="chat-warning">{error}</small>}
    {history && <div className="image-replacement-history">{history.filter(a => a.version < asset.version).map(a =>
      <a key={a.version} href={a.data.url} target="_blank" rel="noreferrer">
        <img src={a.data.url} alt={`${asset.data.name} 原图 V${a.version}`} loading="lazy" />
        <small>V{a.version} · 点击查看原图</small>
      </a>)}{!history.some(a => a.version < asset.version) && <small>暂无旧版图片。</small>}</div>}
  </div>;
}
