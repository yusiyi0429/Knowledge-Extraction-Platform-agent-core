import { useCallback, useEffect, useState } from "react";

import { api, jsonBody, upload } from "./api";
import { Button, EmptyState, Icon, Modal, Notice, StatusBadge, formatDate } from "./components";
import type { AbilityMount, ModelConnection, SkillVersion } from "./types";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败。";
}

export function SettingsPage() {
  const [tab, setTab] = useState<"abilities" | "skills">("abilities");
  const [mounts, setMounts] = useState<AbilityMount[]>([]);
  const [models, setModels] = useState<ModelConnection[]>([]);
  const [skills, setSkills] = useState<SkillVersion[]>([]);
  const [editing, setEditing] = useState<AbilityMount | null>(null);
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [mountResult, modelResult, skillResult] = await Promise.all([
        api<{ items: AbilityMount[] }>("/ability-mounts"),
        api<{ items: ModelConnection[] }>("/models"),
        api<{ items: SkillVersion[] }>("/skills"),
      ]);
      setMounts(mountResult.items);
      setModels(modelResult.items);
      setSkills(skillResult.items);
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function resetDefaults() {
    setBusy(true);
    try {
      await api("/ability-mounts/defaults", { method: "POST" });
      await load();
      setNotice({ tone: "success", text: "7 项能力已恢复为可运行的 Fake Model 默认配置。" });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function uploadSkill(files: FileList | null) {
    if (!files?.[0]) return;
    setBusy(true);
    try {
      await upload<SkillVersion>("/skills", files[0]);
      await load();
      setNotice({ tone: "success", text: "Skill 包通过安全校验并已加入库。" });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page settings-page">
      <header className="page-head compact">
        <div><p className="eyebrow">On-demand capability</p><h1>智能体与 <span>Skill</span></h1><p>7 张卡片是按需执行配置，不启动常驻多智能体系统。</p></div>
        {tab === "abilities" ? <Button kind="primary" icon="refresh" onClick={() => void resetDefaults()} disabled={busy}>一键使用默认配置</Button> : <label className="button button-primary"><Icon name="upload" size={16} />上传 Skill ZIP<input type="file" accept=".zip" hidden onChange={(event) => void uploadSkill(event.target.files)} disabled={busy} /></label>}
      </header>
      {notice && <Notice tone={notice.tone}>{notice.text}<button onClick={() => setNotice(null)}>关闭</button></Notice>}
      <div className="page-tabs"><button className={tab === "abilities" ? "active" : ""} onClick={() => setTab("abilities")}>智能体配置 <span>{mounts.length}</span></button><button className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}>Skill 库 <span>{skills.length}</span></button></div>

      {tab === "abilities" ? (
        <section className="ability-grid">
          {mounts.map((mount, index) => (
            <article className={`ability-card ${mount.enabled ? "" : "disabled"}`} key={mount.id}>
              <header><span className="ability-number">A{String(index + 1).padStart(2, "0")}</span><StatusBadge status={mount.enabled ? "ENABLED" : "DISABLED"} /></header>
              <span className="ability-icon"><Icon name={index < 2 ? "search" : index < 5 ? "brain" : "spark"} size={23} /></span>
              <h3>{mount.display_name}</h3>
              <p>{mount.description}</p>
              <dl><div><dt>模型</dt><dd>{mount.model?.name || "未挂载"}</dd></div><div><dt>Skill</dt><dd>{mount.skill?.name || "未挂载"}</dd></div><div><dt>参数</dt><dd>{mount.params.max_chunks || 24} chunks · 并发 {mount.params.concurrency || 3}</dd></div></dl>
              <Button icon="settings" onClick={() => setEditing(mount)}>配置能力</Button>
            </article>
          ))}
        </section>
      ) : (
        <section className="skill-library">
          {skills.length === 0 ? <EmptyState icon="spark" title="Skill 库为空" detail="上传符合 SKILL.md + scripts / references / assets 规范的 ZIP。" /> : skills.map((skill) => (
            <article key={skill.id}><span className="skill-symbol"><Icon name="spark" size={21} /></span><div><header><h3>{skill.name}</h3><span>v{skill.version}</span>{skill.built_in && <em>内置</em>}</header><p>{skill.description}</p><footer><StatusBadge status={skill.status} /><time>加入于 {formatDate(skill.created_at)}</time></footer></div></article>
          ))}
          <Notice tone="info">上传时校验根目录 SKILL.md、front matter、路径穿越、符号链接、文件数与解压大小；ZIP 不会直接解压到运行目录。</Notice>
        </section>
      )}

      <AbilityModal mount={editing} models={models} skills={skills} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); await load(); setNotice({ tone: "success", text: "能力配置已保存，后续任务会冻结该版本。" }); }} />
    </div>
  );
}

function AbilityModal({ mount, models, skills, onClose, onSaved }: { mount: AbilityMount | null; models: ModelConnection[]; skills: SkillVersion[]; onClose: () => void; onSaved: () => Promise<void> }) {
  const [enabled, setEnabled] = useState(true);
  const [modelId, setModelId] = useState("");
  const [skillId, setSkillId] = useState("");
  const [maxChunks, setMaxChunks] = useState(24);
  const [concurrency, setConcurrency] = useState(3);
  const [temperature, setTemperature] = useState(0.2);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!mount) return;
    setEnabled(mount.enabled);
    setModelId(mount.model_connection_id || "");
    setSkillId(mount.skill_version_id || "");
    setMaxChunks(mount.params.max_chunks || 24);
    setConcurrency(mount.params.concurrency || 3);
    setTemperature(mount.params.temperature ?? 0.2);
    setError("");
  }, [mount]);

  async function save() {
    if (!mount) return;
    setSaving(true);
    setError("");
    try {
      await api(`/ability-mounts/${mount.id}`, {
        method: "PUT",
        body: jsonBody({ enabled, model_connection_id: modelId || null, skill_version_id: skillId || null, params: { max_chunks: maxChunks, concurrency, temperature } }),
      });
      await onSaved();
    } catch (requestError) {
      setError(messageOf(requestError));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open={Boolean(mount)} title={mount?.display_name || "能力配置"} subtitle="任务启动时会冻结模型、Skill、参数与素材快照。" onClose={onClose} footer={<><Button onClick={onClose}>取消</Button><Button kind="primary" onClick={() => void save()} disabled={saving}>{saving ? "保存中…" : "保存配置"}</Button></>}>
      <div className="form-stack">
        {error && <Notice tone="danger">{error}</Notice>}
        <label className="toggle-field"><span><b>启用能力</b><small>关闭后不会挂载到新任务</small></span><button className={`switch ${enabled ? "on" : ""}`} onClick={() => setEnabled(!enabled)}><i /></button></label>
        <label>模型<select value={modelId} onChange={(event) => setModelId(event.target.value)}><option value="">不挂载</option>{models.filter((model) => model.enabled).map((model) => <option value={model.id} key={model.id}>{model.name} · {model.model_name}</option>)}</select></label>
        <label>Skill<select value={skillId} onChange={(event) => setSkillId(event.target.value)}><option value="">不挂载</option>{skills.filter((skill) => skill.status === "ENABLED").map((skill) => <option value={skill.id} key={skill.id}>{skill.name} · v{skill.version}</option>)}</select></label>
        <div className="three-fields"><label>最大片段<input type="number" min={4} max={60} value={maxChunks} onChange={(event) => setMaxChunks(Number(event.target.value))} /></label><label>并发上限<input type="number" min={1} max={6} value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /></label><label>Temperature<input type="number" min={0} max={1} step={0.1} value={temperature} onChange={(event) => setTemperature(Number(event.target.value))} /></label></div>
      </div>
    </Modal>
  );
}
