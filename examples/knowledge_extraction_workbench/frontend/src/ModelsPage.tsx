import { useCallback, useEffect, useState } from "react";

import { api, jsonBody } from "./api";
import { Button, EmptyState, Icon, Modal, Notice, StatusBadge, formatDate } from "./components";
import type { ModelConnection } from "./types";

const MODEL_PROVIDERS = [
  "DeepSeek",
  "OpenAI",
  "OpenRouter",
  "Anthropic",
  "SiliconFlow",
  "DashScope",
  "InferenceAffinity",
] as const;

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败。";
}

export function ModelsPage() {
  const [models, setModels] = useState<ModelConnection[]>([]);
  const [editing, setEditing] = useState<ModelConnection | "new" | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ModelConnection | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setModels((await api<{ items: ModelConnection[] }>("/models")).items);
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function test(model: ModelConnection) {
    setTesting(model.id);
    try {
      const result = await api<{ message: string; latency_ms: number }>(`/models/${model.id}/test`, { method: "POST" });
      setNotice({ tone: "success", text: `${result.message} · ${result.latency_ms} ms` });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setTesting(null);
    }
  }

  async function remove() {
    if (!deleteTarget) return;
    try {
      await api(`/models/${deleteTarget.id}`, { method: "DELETE" });
      setDeleteTarget(null);
      await load();
      setNotice({ tone: "success", text: "模型连接已删除。" });
    } catch (error) {
      setDeleteTarget(null);
      setNotice({ tone: "danger", text: messageOf(error) });
    }
  }

  return (
    <div className="page models-page">
      <header className="page-head compact"><div><p className="eyebrow">Provider connections</p><h1>模型<span>接入</span></h1><p>平台级连接供能力卡按需挂载；API Key 加密保存且接口永不回传明文。</p></div><Button kind="primary" icon="plus" onClick={() => setEditing("new")}>新增模型</Button></header>
      {notice && <Notice tone={notice.tone}>{notice.text}<button onClick={() => setNotice(null)}>关闭</button></Notice>}
      <Notice tone="info">完整流程使用已启用的真实模型连接。建议先执行最小调用测试，再到“智能体与 Skill”按场景挂载；API Key 仅加密保存在本机。</Notice>
      {models.length === 0 ? <EmptyState icon="model" title="尚无模型连接" detail="新增 Provider、API 地址、模型名称与密钥。" /> : (
        <section className="model-list">
          <div className="model-row model-header"><span>连接</span><span>Provider / 地址</span><span>密钥</span><span>状态</span><span>操作</span></div>
          {models.map((model) => (
            <article className="model-row" key={model.id}>
              <div className="model-identity"><span><Icon name="model" size={20} /></span><div><h3>{model.name}</h3><p>{model.model_name}</p></div></div>
              <div><b>{model.provider}</b><p className="endpoint">{model.api_base || "—"}</p></div>
              <div className="key-state"><span className={model.has_api_key ? "locked" : "local"}>{model.has_api_key ? "••••••••••••" : "未配置"}</span><small>{model.has_api_key ? "AES-GCM 加密" : ""}</small></div>
              <div><StatusBadge status={model.enabled ? "ENABLED" : "DISABLED"} /><small>更新 {formatDate(model.updated_at)}</small></div>
              <div className="model-actions"><Button icon="check" onClick={() => void test(model)} disabled={testing === model.id}>{testing === model.id ? "测试中…" : "测试"}</Button><button className="icon-button" aria-label={`编辑${model.name}`} onClick={() => setEditing(model)}><Icon name="edit" size={16} /></button><button className="icon-button danger" aria-label={`删除${model.name}`} onClick={() => setDeleteTarget(model)}><Icon name="archive" size={16} /></button></div>
            </article>
          ))}
        </section>
      )}
      <ModelModal model={editing} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); await load(); setNotice({ tone: "success", text: "模型连接已保存；密钥不会回显。" }); }} />
      <Modal open={Boolean(deleteTarget)} title="删除模型连接" subtitle="删除前会检查是否仍被能力卡挂载。" onClose={() => setDeleteTarget(null)} footer={<><Button onClick={() => setDeleteTarget(null)}>取消</Button><Button kind="danger" onClick={() => void remove()}>确认删除</Button></>}><p className="confirm-copy">确定删除“<strong>{deleteTarget?.name}</strong>”吗？此操作不会影响已冻结的历史任务快照。</p></Modal>
    </div>
  );
}

function ModelModal({ model, onClose, onSaved }: { model: ModelConnection | "new" | null; onClose: () => void; onSaved: () => Promise<void> }) {
  const editing = model && model !== "new" ? model : null;
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [modelName, setModelName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setName(editing?.name || "");
    setProvider(editing?.provider || "");
    setApiBase(editing?.api_base || "");
    setModelName(editing?.model_name || "");
    setApiKey("");
    setEnabled(editing?.enabled ?? true);
    setError("");
  }, [editing, model]);

  async function save() {
    setSaving(true);
    setError("");
    try {
      const payload = { name, provider, api_base: apiBase, model_name: modelName, api_key: apiKey || undefined, enabled };
      await api(editing ? `/models/${editing.id}` : "/models", { method: editing ? "PUT" : "POST", body: jsonBody(payload) });
      await onSaved();
    } catch (requestError) {
      setError(messageOf(requestError));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={Boolean(model)} title={editing ? "编辑模型连接" : "新增模型连接"} subtitle="密钥仅在本次保存时发送到本机后端；查询接口只返回 has_api_key。" onClose={onClose} footer={<><Button onClick={onClose}>取消</Button><Button kind="primary" onClick={() => void save()} disabled={saving || !name.trim() || !provider || !apiBase.trim() || !modelName.trim() || (!editing && !apiKey.trim())}>{saving ? "保存中…" : "保存连接"}</Button></>}>
      <div className="form-stack">
        {error && <Notice tone="danger">{error}</Notice>}
        <label>连接名称<span>*</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：生产模型连接" /></label>
        <div className="two-fields"><label>Provider<span>*</span><select value={provider} onChange={(event) => setProvider(event.target.value)}><option value="">请选择 Provider</option>{MODEL_PROVIDERS.map((item) => <option key={item} value={item}>{item}</option>)}</select></label><label>模型名称<span>*</span><input value={modelName} onChange={(event) => setModelName(event.target.value)} placeholder="填写服务端实际 Model ID" /></label></div>
        <label>API 地址<span>*</span><input value={apiBase} onChange={(event) => setApiBase(event.target.value)} placeholder="例如：https://api.provider.com/v1" /><small>外部服务必须使用 HTTPS；HTTP 仅允许 localhost / loopback。</small></label>
        <label>API Key{!editing && <span>*</span>}<input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={editing?.has_api_key ? "留空则保留已加密密钥" : "在本机输入，不会回显"} /><small>{editing?.has_api_key ? "当前已有加密密钥。接口不会返回原值。" : "主密钥文件权限固定为 0600。"}</small></label>
        <label className="toggle-field"><span><b>启用连接</b><small>停用后不出现在可挂载模型中</small></span><button type="button" className={`switch ${enabled ? "on" : ""}`} aria-pressed={enabled} onClick={() => setEnabled(!enabled)}><i /></button></label>
      </div>
    </Modal>
  );
}
