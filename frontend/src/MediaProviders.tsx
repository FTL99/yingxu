import { useState } from "react";
import ReplaceImage from "./ReplaceImage";

type Data = Record<string, any>;
const names: Data = { seedream: "Seedream 生图", wan: "万相 图生视频", cosyvoice: "CosyVoice 配音", seedance: "Seedance 中转站" };

export function MediaProviderSettings({ initial, busy, onSave, onCheck }: {
  initial: Data; busy: boolean; onSave: (value: Data) => Promise<boolean>; onCheck: () => Promise<Data>;
}) {
  const [checking, setChecking] = useState(false);
  const [checkMessage, setCheckMessage] = useState("");
  const [value, setValue] = useState<Data>({
    seedream: { model: initial.seedream?.model || "doubao-seedream-5-0-flash-260915", api_key: "", clear_key: false },
    aliyun: { workspace_id: initial.aliyun?.workspace_id || "", video_model: "wan2.6-i2v-flash",
      tts_model: initial.aliyun?.tts_model || "cosyvoice-v3-plus", voice: initial.aliyun?.voice || "longanyang", api_key: "", clear_key: false },
    seedance: { base_url: initial.seedance?.base_url || "", model: initial.seedance?.model || "",
      protocol: initial.seedance?.protocol || "unconfigured", price_per_second: initial.seedance?.price_per_second ?? null,
      audio_price_per_second: initial.seedance?.audio_price_per_second ?? null,
      api_key: "", clear_key: false },
  });
  const update = (section: string, key: string, next: any) => setValue((old: Data) => ({ ...old, [section]: { ...old[section], [key]: next } }));
  const credential = (section: string, label: string) => <>
    <label className="media-field">{label}
      <input type="password" autoComplete="new-password" value={value[section].api_key}
        placeholder={initial[section]?.has_key ? "已保存 · 留空保留" : "粘贴此平台的 API Key"}
        onChange={e => update(section, "api_key", e.target.value)} />
    </label>
    <label className="checkbox"><input type="checkbox" checked={value[section].clear_key}
      onChange={e => update(section, "clear_key", e.target.checked)} />清除该平台已保存的 Key</label>
  </>;
  return <form className="modal-body media-settings" onSubmit={async e => {
    e.preventDefault();
    if (await onSave(value)) setValue((old: Data) => Object.fromEntries(Object.entries(old).map(([k, v]) =>
      [k, { ...(v as Data), api_key: "", clear_key: false }])));
  }}>
    <h3>图片、视频与配音</h3>
    <p className="muted">先填火山方舟和阿里云百炼的 Key。保存只写入本机，不验证 Key、不发送素材、不产生生成费用。</p>
    <details open><summary>Seedream · 官方生图 {initial.seedream?.has_key ? " · Key已保存" : " · 待填写"}</summary>
      <p>Seedream 5.0 Flash · 0.12元/张 · 支持角色与场景参考图</p>
      <p className="muted">首次使用还须在火山方舟控制台「开通管理」开通 Seedream 5.0 Flash。仅保存 Key 不代表已开通；未开通会返回 404 / ModelNotOpen。</p>
      <a href="https://ark.volcengine.com/region:cn-beijing/apikey" target="_blank" rel="noreferrer">获取火山方舟官方 Key ↗</a>
      {credential("seedream", "火山方舟 API Key")}
    </details>
    <details open><summary>万相＋CosyVoice · 阿里云官方 {initial.aliyun?.has_key ? " · Key已保存" : " · 待填写"}</summary>
      <p>两个模型共用北京地域百炼 Key。万相默认720P、无声，0.15元/秒。</p>
      <a href="https://help.aliyun.com/zh/model-studio/get-api-key" target="_blank" rel="noreferrer">获取阿里云百炼官方 Key ↗</a>
      {credential("aliyun", "阿里云百炼 API Key（北京）")}
      <label className="media-field">业务空间 ID（可选）<input value={value.aliyun.workspace_id}
        placeholder="留空使用 dashscope 官方兼容域名" onChange={e => update("aliyun", "workspace_id", e.target.value)} /></label>
      <details><summary>配音模型与音色</summary>
        <label className="media-field">配音模型<select value={value.aliyun.tts_model} onChange={e => update("aliyun", "tts_model", e.target.value)}>
          <option value="cosyvoice-v3-plus">3 Plus · 现成音色 · 2元/万字符</option>
          <option value="cosyvoice-v3-flash">3 Flash · 1元/万字符（填写匹配音色ID）</option>
          <option value="cosyvoice-v3.5-plus">3.5 Plus · 1.5元/万字符（需自定义音色）</option>
          <option value="cosyvoice-v3.5-flash">3.5 Flash · 0.8元/万字符（需自定义音色）</option>
        </select></label>
        <label className="media-field">音色 ID<input required value={value.aliyun.voice} onChange={e => update("aliyun", "voice", e.target.value)} /></label>
        <p className="muted">默认3 Plus / longanyang。3.5没有系统音色，需填为对应模型创建的设计或克隆音色ID；本次未加入音色创建。</p>
      </details>
    </details>
    <details><summary>Seedance · 中转站 {initial.seedance?.ready ? " · 已配置" : " · 待选定"}</summary>
      <p>OpenRouter或其他中转站使用独立Key。先保存配置，再测试OpenRouter连接。</p>
      <label className="media-field">API 根地址<input value={value.seedance.base_url} placeholder="例如 https://你的中转站/api/v3"
        onChange={e => update("seedance", "base_url", e.target.value)} /></label>
      {credential("seedance", "Seedance 中转站 API Key")}
      <label className="media-field">该站模型名<input value={value.seedance.model} placeholder="按中转站文档填写"
        onChange={e => update("seedance", "model", e.target.value)} /></label>
      <label className="media-field">接口协议<select value={value.seedance.protocol} onChange={e => {
        update("seedance", "protocol", e.target.value);
        setCheckMessage("");
        if (e.target.value === "openrouter") {
          update("seedance", "base_url", "https://openrouter.ai/api/v1");
          if (!value.seedance.model || value.seedance.model === "seedance2") update("seedance", "model", "bytedance/seedance-2.0");
        }
      }}>
        <option value="unconfigured">尚未确认（暂不能调用）</option>
        <option value="ark">方舟兼容（需中转站文档明确支持）</option>
        <option value="openrouter">OpenRouter 视频 API</option>
      </select></label>
      <label className="media-field">720P无声视频预算参考价（元/秒）<input type="number" min="0" step="any"
        value={value.seedance.price_per_second ?? ""} placeholder="按该站报价填写；其他计费方式需另行适配"
        onChange={e => update("seedance", "price_per_second", e.target.value === "" ? null : Number(e.target.value))} /></label>
      {value.seedance.protocol === "openrouter" && <label className="media-field">720P有声视频预算参考价（元/秒）<input type="number" min="0" step="any"
        value={value.seedance.audio_price_per_second ?? ""} placeholder="按有声报价填写；不自动沿用无声价"
        onChange={e => update("seedance", "audio_price_per_second", e.target.value === "" ? null : Number(e.target.value))} /></label>}
      <p className="muted">支持方舟兼容或OpenRouter的文生视频/单首帧、创建、查询和下载。OpenRouter实际按模型报价以美元计费，元/秒仅作预算参考；完成后按账单核对。更换站点需重填Key。</p>
      {value.seedance.protocol === "openrouter" && <>
        <button type="button" className="button" disabled={busy || checking} onClick={async () => {
          setChecking(true); setCheckMessage("");
          try { const result = await onCheck(); setCheckMessage(`${result.model}：${result.message}`); }
          catch (error) { setCheckMessage((error as Error).message); }
          finally { setChecking(false); }
        }}>{checking ? "查询中…" : "测试已保存的 OpenRouter 配置（不生成）"}</button>
        {checkMessage && <p role="status">{checkMessage}</p>}
      </>}
    </details>
    <p className="muted">Key保存在本机 data/media-settings.json，接口不会回传。显示“已保存”仅表示已填写，不代表账号权限或连接验证通过。</p>
    <div className="modal-actions"><button className="button primary" disabled={busy}>保存媒体 API 配置</button></div>
  </form>;
}

export function MediaGenerationPanel({ providers, records, jobs, busy, onSettings, onEstimate, onGenerate, onRetrieve, onReconcile, pid, api, onChange }: {
  pid: string; api: (path: string, body?: Data | FormData, method?: string) => Promise<any>; onChange: () => Promise<any>;
  providers: Data[]; records: Data[]; jobs: Data[]; busy: boolean; onSettings: () => void;
  onEstimate: (value: Data) => Promise<Data>; onGenerate: (value: Data, requestId: string) => Promise<boolean>;
  onRetrieve: (id: string) => void; onReconcile: (id: string, value: Data) => Promise<boolean>;
}) {
  const [value, setValue] = useState<Data>({ provider: "seedream", prompt: "", reference_ids: [], input_refs: [], duration: 5,
    resolution: "720P", audio: false, audio_direction: "", seed: null, negative_prompt: "" });
  const [quotation, setQuotation] = useState<Data | null>(null);
  const [reserve, setReserve] = useState(0);
  const [estimating, setEstimating] = useState(false);
  const [localError, setLocalError] = useState("");
  const [settling, setSettling] = useState("");
  const [actual, setActual] = useState("");
  const [evidence, setEvidence] = useState("");
  const selected = providers.find(p => p.id === value.provider);
  const images = records.filter(r => r.kind === "asset" && r.data.media_type === "image" && !r.stale);
  const shots = records.filter(r => r.kind === "shot" && !r.stale);
  const change = (patch: Data) => { setValue(old => ({ ...old, ...patch })); setQuotation(null); setLocalError(""); };
  return <section className="panel media-generation">
    <div className="section-title"><h3>生成图片、视频和配音</h3><button className="button" onClick={onSettings}>配置 API Key</button></div>
    <p className="muted">先试最贵的两个镜头。每次只生成一个素材，结果进入本地素材库，再由你挑选采用。</p>
    <form onSubmit={async e => {
      e.preventDefault(); setLocalError("");
      if (quotation) {
        if (await onGenerate({ ...value, audio_direction: value.audio ? value.audio_direction : "", input_refs: quotation.input_refs, confirmed: true, max_cost: reserve,
          expected_config_version: quotation.config_version, expected_brief_revision: quotation.brief_revision }, quotation.request_id)) setQuotation(null);
      } else {
        setEstimating(true);
        try {
          const result = await onEstimate({ ...value, audio_direction: value.audio ? value.audio_direction : "" });
          setQuotation({ ...result, request_id: crypto.randomUUID() });
          setReserve(Math.max(.1, Math.ceil(result.estimated_cost * 1.2 * 100) / 100));
        } catch (error) { setLocalError((error as Error).message); }
        finally { setEstimating(false); }
      }
    }}>
      <div className="form-grid">
        <label className="media-field">生成类型<select value={value.provider} onChange={e => change({ provider: e.target.value,
          reference_ids: [], audio: false, audio_direction: "", resolution: "720P", seed: null, negative_prompt: "" })}>
          {Object.keys(names).map(id => <option key={id} value={id}>{names[id]}{providers.find(p => p.id === id)?.ready ? "" : " · 待配置"}</option>)}
        </select></label>
        <label className="media-field">从镜头填入描述（可选）<select value={value.input_refs[0]?.id || ""} onChange={e => {
          const shot = shots.find(s => s.id === e.target.value);
          change({ prompt: shot?.data.description || "", audio_direction: shot?.data.audio_direction || "",
            input_refs: shot ? [{ kind: "shot", id: shot.id, version: shot.version }] : [] });
        }}><option value="">自己填写</option>{shots.map(s => <option key={s.id} value={s.id}>{s.data.title || s.id}</option>)}</select></label>
      </div>
      <label className="media-field">{value.provider === "cosyvoice" ? "要朗读的文本" : "画面与动作描述"}
        <textarea required maxLength={10000} rows={4} value={value.prompt} onChange={e => change({ prompt: e.target.value })}
          placeholder={value.provider === "cosyvoice" ? "只填写实际要朗读的台词或旁白" : "描述主体、场景、构图；视频另写简单动作与运镜"} /></label>
      {value.provider !== "cosyvoice" && <div className="media-references">
        <strong>{value.provider === "seedream" ? "参考图片（可选，最多10张）" : "首帧图片（万相必选1张）"}</strong>
        {!images.length && <p className="muted">可以先用 Seedream 生图，或在下方导入参考图片。</p>}
        {images.map(a => <label className="checkbox" key={a.id}><input type="checkbox" checked={value.reference_ids.includes(a.id)}
          onChange={e => change({ reference_ids: e.target.checked
            ? value.provider === "seedream" ? [...value.reference_ids, a.id] : [a.id]
            : value.reference_ids.filter((id: string) => id !== a.id) })} />{a.data.name}</label>)}
      </div>}
      {["wan", "seedance"].includes(value.provider) && <div className="form-grid">
        <label className="media-field">生成时长（秒）<input type="number" min={value.provider === "seedance" ? 4 : 2} max="15" required value={value.duration}
          onChange={e => change({ duration: Number(e.target.value) })} /></label>
        <label className="media-field">分辨率<select value={value.resolution} onChange={e => change({ resolution: e.target.value })}>
          <option value="720P">720P</option>{value.provider === "wan" && <option value="1080P">1080P</option>}
        </select></label>
      </div>}
      {(value.provider === "wan" || (value.provider === "seedance" && selected?.audio_supported)) && <div className="soft-note">
        <label className="checkbox"><input type="checkbox" checked={value.audio} onChange={e => change({ audio: e.target.checked })} />
          视频同时生成声音（环境声、动作音效；默认关闭）</label>
        {value.audio && <label className="media-field">本镜头声音设计<textarea maxLength={3000} rows={3}
          value={value.audio_direction} onChange={e => change({ audio_direction: e.target.value })}
          placeholder="例：0–2秒低声海浪；2秒一声火枪响；随后静默半秒。无音乐、无对白，旁白后期单独配。" /></label>}
        <p className="muted">{value.provider === "wan" ? "有声单价720P 0.30元/秒、1080P 0.50元/秒。" :
          "OpenRouter先检查模型音频支持，有声预算单价在设置里单独填写，实际费用按美元账单核对。"}
          声音要求随画面提示词发送；时间和内容需人工试听确认。生成音轨通常无法单独分离，建议旁白另配。</p>
      </div>}
      {value.provider === "wan" && <details><summary>视频高级参数</summary>
        <label className="media-field">Seed（可选，不能保证角色一致）<input type="number" min="0" max="2147483647" value={value.seed ?? ""}
          onChange={e => change({ seed: e.target.value === "" ? null : Number(e.target.value) })} /></label>
        <label className="media-field">负面提示词（可选）<input maxLength={500} value={value.negative_prompt} onChange={e => change({ negative_prompt: e.target.value })} /></label>
      </details>}
      {value.provider === "cosyvoice" && <p className="muted">{selected?.model} · 音色 {selected?.voice || "待配置"}；音色在运行设置中修改。</p>}
      {localError && <p role="alert">{localError}</p>}
      {quotation && <div className="soft-note">
        预计本次生成 ¥{Number(quotation.estimated_cost).toFixed(4)} · {quotation.model}
        <p>{quotation.note}</p>
        <label className="media-field">本次费用预留（元）<input type="number" min={Math.max(.001, quotation.estimated_cost)} step="any"
          required value={reserve} onChange={e => setReserve(Number(e.target.value))} /></label>
        <p>确认后会把所选参考图或台词发送给该供应商。估算及预留不能限制供应商收费；未采用的素材也可能收费。完成后按账单核对，释放剩余预留。</p>
      </div>}
      <div className="modal-actions"><button className="button primary" disabled={busy || estimating || (!!quotation && !selected?.ready)}>
        {estimating ? "正在估算" : quotation ? "确认生成并预留费用" : "估算费用（不调用模型）"}</button></div>
      {!selected?.ready && <p className="muted">该供应商尚未配置，请先点“配置 API Key”。</p>}
    </form>
    {!!jobs.length && <div className="media-jobs"><h4>生成记录</h4>{jobs.map(j => {
      const asset = records.find(a => a.kind === "asset" && a.id === j.result.asset_id);
      return <article key={j.id} className="media-job">
        <div className="inline spread"><strong>{names[j.provider]} · {{ running: "生成中", completed: "已导入素材", interrupted: "待处理" }[j.status as string] || j.status}</strong>
          <small>{j.stale ? "输入已变化，需复查" : ""}</small></div>
        <p>{j.request.prompt.slice(0, 120)}</p>
        {["wan", "seedance"].includes(j.provider) && <p className="muted">{j.request.audio ? "已请求生成声音" : "未请求生成声音"}
          {asset ? (asset.data.has_audio_stream === true ? " · 文件包含音轨，需人工试听" : asset.data.has_audio_stream === false ? " · 文件未检测到音轨" : " · 音轨未检查") : ""}</p>}
        {asset?.data.media_type === "image" && <img className="generated-preview" src={asset.data.url} alt="生成图片，尚待人工采用" />}
        {asset?.data.media_type === "image" && <ReplaceImage asset={asset} pid={pid} api={api} onChange={onChange} />}
        {asset?.data.media_type === "video" && <video className="generated-preview" controls preload="metadata" src={asset.data.url} />}
        {asset?.data.media_type === "audio" && <audio controls preload="metadata" src={asset.data.url} />}
        {j.result.usage?.cost_usd !== undefined && <p className="muted">OpenRouter返回费用：${j.result.usage.cost_usd} USD；请按账单换算为元核账。</p>}
          <p className="muted">{asset?.data.source === "human_replacement" ? "原生成费用：" : ""}预计 ¥{j.result.estimated_cost} · {j.actual_cost === null ? `待核账，预留 ¥${j.reservation}` : `已核账 ¥${j.actual_cost}`}
          {j.remote_id ? ` · 供应商任务：${j.remote_id}` : ""}</p>
        {j.error && <p role="alert">{j.error}</p>}
        <div className="inline">
          {j.status === "interrupted" && j.remote_id && j.provider !== "seedream" && <button className="button" disabled={busy} onClick={() => onRetrieve(j.id)}>取回原结果（不重新生成）</button>}
          {j.status !== "running" && <button className="button" disabled={busy} onClick={() => { setSettling(j.id); setActual(""); setEvidence(""); }}>按供应商账单核对费用</button>}
        </div>
        {settling === j.id && <form className="media-settlement" onSubmit={async e => {
          e.preventDefault(); if (await onReconcile(j.id, { actual_cost: Number(actual), evidence })) setSettling("");
        }}>
          <label className="media-field">账单实际金额（元）<input type="number" required min="0" step="any" value={actual} onChange={e => setActual(e.target.value)} /></label>
          <label className="media-field">账单依据<input required maxLength={2000} value={evidence} placeholder="填写账单日期、请求ID或人工核对依据" onChange={e => setEvidence(e.target.value)} /></label>
          <button className="button" disabled={busy}>保存实际费用</button>
        </form>}
      </article>;
    })}</div>}
  </section>;
}
