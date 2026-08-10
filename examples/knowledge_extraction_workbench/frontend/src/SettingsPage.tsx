import { useCallback, useEffect, useMemo, useState } from "react";

import { api, jsonBody, upload } from "./api";
import { Button, EmptyState, Icon, Notice, StatusBadge, formatDate } from "./components";
import { modelAdapterLabel } from "./modelAdapters";
import type {
  AbilityMount,
  AbilityParams,
  AbilityScope,
  ModelConnection,
  SkillVersion,
} from "./types";

type NoticeState = { tone: "success" | "danger"; text: string } | null;
type DrawerState = { mode: "new" | "view" | "edit"; skill?: SkillVersion; source?: SkillVersion } | null;

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败。";
}

export function SettingsPage() {
  const [tab, setTab] = useState<"agents" | "skills">("agents");
  const [scopes, setScopes] = useState<AbilityScope[]>([]);
  const [scopeKey, setScopeKey] = useState("GLOBAL");
  const [mounts, setMounts] = useState<AbilityMount[]>([]);
  const [models, setModels] = useState<ModelConnection[]>([]);
  const [bulkModelId, setBulkModelId] = useState("");
  const [skills, setSkills] = useState<SkillVersion[]>([]);
  const [drawer, setDrawer] = useState<DrawerState>(null);
  const [notice, setNotice] = useState<NoticeState>(null);
  const [busy, setBusy] = useState(false);

  const loadMounts = useCallback(async (key: string) => {
    const result = await api<{ items: AbilityMount[] }>(
      `/ability-mounts?scope_key=${encodeURIComponent(key)}`,
    );
    setMounts(result.items);
    const mountedModelIds = new Set(
      result.items.map((item) => item.model_connection_id).filter((id): id is string => Boolean(id)),
    );
    if (mountedModelIds.size === 1) {
      setBulkModelId([...mountedModelIds][0]);
    }
  }, []);

  const loadLibrary = useCallback(async () => {
    const [scopeResult, modelResult, skillResult] = await Promise.all([
      api<{ items: AbilityScope[] }>("/ability-scopes"),
      api<{ items: ModelConnection[] }>("/models"),
      api<{ items: SkillVersion[] }>("/skills"),
    ]);
    setScopes(scopeResult.items);
    setModels(modelResult.items);
    setBulkModelId((current) => {
      const available = modelResult.items.filter((model) => model.enabled && model.has_api_key);
      return available.some((model) => model.id === current) ? current : available[0]?.id || "";
    });
    setSkills(skillResult.items);
  }, []);

  useEffect(() => {
    void loadLibrary().catch((error: unknown) => {
      setNotice({ tone: "danger", text: messageOf(error) });
    });
  }, [loadLibrary]);

  useEffect(() => {
    void loadMounts(scopeKey).catch((error: unknown) => {
      setNotice({ tone: "danger", text: messageOf(error) });
    });
  }, [loadMounts, scopeKey]);

  const selectedScope = scopes.find((item) => item.key === scopeKey) || scopes[0];
  const enabledModels = models.filter((model) => model.enabled && model.has_api_key);
  const templates = skills.filter((skill) => skill.kind === "TEMPLATE");
  const instances = skills.filter((skill) => skill.kind === "INSTANCE");
  const extractionMounts = mounts.filter((mount) => mount.stage === "EXTRACTION");
  const generationMounts = mounts.filter((mount) => mount.stage === "GENERATION");

  function changeScope(key: string) {
    setScopeKey(key);
  }

  async function resetDefaults() {
    setBusy(true);
    try {
      const result = await api<{ items: AbilityMount[] }>("/ability-mounts/defaults", {
        method: "POST",
        body: jsonBody({ scope_key: scopeKey, model_connection_id: bulkModelId }),
      });
      setMounts(result.items);
      const modelName = result.items[0]?.model?.name || "真实模型";
      setNotice({ tone: "success", text: `7 个智能体已挂载 ${modelName} 与推荐 Skill。` });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  function exportConfiguration() {
    const payload = {
      schema_version: "knowledge-workbench/ability-config/v1",
      scope_key: scopeKey,
      scope_label: selectedScope?.label || "通用场景",
      items: mounts.map((mount) => ({
        ability_key: mount.ability_key,
        enabled: mount.enabled,
        model_connection_id: mount.model_connection_id,
        skill_version_id: mount.skill_version_id,
        params: mount.params,
      })),
    };
    const href = URL.createObjectURL(
      new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
    );
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `agent-config-${scopeKey.replaceAll(":", "-")}.json`;
    anchor.click();
    URL.revokeObjectURL(href);
  }

  async function importConfiguration(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    try {
      const parsed = JSON.parse(await file.text()) as { items?: unknown[] };
      const result = await api<{ items: AbilityMount[] }>("/ability-mounts/configuration", {
        method: "PUT",
        body: jsonBody({ ...parsed, scope_key: scopeKey }),
      });
      setMounts(result.items);
      setNotice({ tone: "success", text: "智能体配置已导入并应用到当前场景。" });
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
      await loadLibrary();
      setNotice({ tone: "success", text: "Skill 包通过安全校验并已作为实例加入库。" });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function savedMount() {
    await loadMounts(scopeKey);
    setNotice({ tone: "success", text: "智能体配置已保存，后续任务会冻结这一版本。" });
  }

  function openSkill(skill: SkillVersion) {
    setDrawer({ mode: skill.read_only ? "view" : "edit", skill });
  }

  return (
    <div className="page settings-page">
      <header className="page-head compact settings-head">
        <div>
          <p className="eyebrow">Agent &amp; Skill configuration</p>
          <h1>智能体与 <span>Skill</span></h1>
          <p>按场景配置各环节能力；智能体仅在对应操作触发时执行，不常驻运行。</p>
        </div>
        {tab === "agents" ? (
          <div className="head-actions model-bulk-actions">
            <label className="bulk-model-control">
              <span>批量模型</span>
              <select
                aria-label="批量应用模型"
                value={bulkModelId}
                onChange={(event) => setBulkModelId(event.target.value)}
                disabled={busy || enabledModels.length === 0}
              >
                {enabledModels.length === 0 && <option value="">暂无可用模型</option>}
                {enabledModels.map((model) => (
                  <option key={model.id} value={model.id}>{model.name} · {modelAdapterLabel(model.provider)}</option>
                ))}
              </select>
            </label>
            <Button kind="primary" icon="refresh" onClick={() => void resetDefaults()} disabled={busy || !bulkModelId}>
              应用到 7 个智能体
            </Button>
          </div>
        ) : (
          <div className="head-actions">
            <label className="button button-ghost skill-upload-button">
              <Icon name="upload" size={16} />上传 Skill ZIP
              <input data-testid="skill-upload-new" type="file" accept=".zip" hidden onChange={(event) => void uploadSkill(event.target.files)} disabled={busy} />
            </label>
            <Button kind="primary" icon="plus" onClick={() => setDrawer({ mode: "new", source: templates[0] })} disabled={templates.length === 0}>
              从模板新建 Skill
            </Button>
          </div>
        )}
      </header>

      {notice && <Notice tone={notice.tone}>{notice.text}<button onClick={() => setNotice(null)}>关闭</button></Notice>}
      {tab === "agents" && enabledModels.length === 0 && (
        <Notice tone="warning">尚未接入可用的真实模型，请先在“模型接入”中保存并测试一个调用适配器连接。</Notice>
      )}

      <div className="page-tabs settings-tabs">
        <button className={tab === "agents" ? "active" : ""} onClick={() => setTab("agents")}>智能体配置 <span>{mounts.length}</span></button>
        <button className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}>Skill 库 <span>{skills.length}</span></button>
      </div>

      {tab === "agents" ? (
        <>
          <section className="scene-config-bar" aria-label="配置作用域">
            <span className="scope-icon"><Icon name="layers" size={20} /></span>
            <div className="scope-copy">
              <strong>配置作用域</strong>
              <small>场景与子场景可独立覆盖通用配置</small>
            </div>
            <label>
              <span>当前场景</span>
              <select value={scopeKey} onChange={(event) => changeScope(event.target.value)}>
                {scopes.map((scope) => <option key={scope.key} value={scope.key}>{scope.label}</option>)}
              </select>
            </label>
            <div className="scope-state">
              <b>{scopeKey === "GLOBAL" ? "默认基线" : "场景级配置"}</b>
              <span>{mounts.filter((mount) => mount.inherited).length} 项继承默认</span>
            </div>
            <div className="scope-actions">
              <label className="button button-ghost">
                <Icon name="upload" size={15} />导入配置
                <input type="file" accept="application/json,.json" hidden onChange={(event) => void importConfiguration(event.target.files?.[0])} />
              </label>
              <Button icon="download" onClick={exportConfiguration}>导出配置</Button>
            </div>
          </section>

          <AgentPhase
            number="02"
            title="知识萃取与对齐"
            description="从素材形成研判文档，并通过差异建议完成人机对齐。"
            mounts={extractionMounts}
            models={enabledModels}
            skills={skills}
            scopeKey={scopeKey}
            onSaved={savedMount}
            onEditSkill={openSkill}
          />
          <AgentPhase
            number="03"
            title="知识生成及发布"
            description="基于定稿文档生成规则、研判链、Skill、QA 与评测资产。"
            mounts={generationMounts}
            models={enabledModels}
            skills={skills}
            scopeKey={scopeKey}
            onSaved={savedMount}
            onEditSkill={openSkill}
          />
        </>
      ) : (
        <SkillLibrary
          templates={templates}
          instances={instances}
          onView={openSkill}
          onCreate={(source) => setDrawer({ mode: "new", source })}
        />
      )}

      <SkillDrawer
        state={drawer}
        skills={skills}
        scopeLabel={selectedScope?.label || "通用场景"}
        onClose={() => setDrawer(null)}
        onSaved={async (message) => {
          await Promise.all([loadLibrary(), loadMounts(scopeKey)]);
          setDrawer(null);
          setNotice({ tone: "success", text: message });
        }}
      />
    </div>
  );
}

function AgentPhase({
  number,
  title,
  description,
  mounts,
  models,
  skills,
  scopeKey,
  onSaved,
  onEditSkill,
}: {
  number: string;
  title: string;
  description: string;
  mounts: AbilityMount[];
  models: ModelConnection[];
  skills: SkillVersion[];
  scopeKey: string;
  onSaved: () => Promise<void>;
  onEditSkill: (skill: SkillVersion) => void;
}) {
  return (
    <section className="agent-phase">
      <header className="agent-phase-head">
        <span>{number}</span>
        <div><h2>{title}</h2><p>{description}</p></div>
        <em>{mounts.length} 个智能体</em>
      </header>
      <div className="agent-grid">
        {mounts.map((mount) => (
          <AgentCard
            key={`${scopeKey}:${mount.id}:${mount.updated_at}`}
            mount={mount}
            models={models}
            skills={skills}
            scopeKey={scopeKey}
            onSaved={onSaved}
            onEditSkill={onEditSkill}
          />
        ))}
      </div>
    </section>
  );
}

function AgentCard({
  mount,
  models,
  skills,
  scopeKey,
  onSaved,
  onEditSkill,
}: {
  mount: AbilityMount;
  models: ModelConnection[];
  skills: SkillVersion[];
  scopeKey: string;
  onSaved: () => Promise<void>;
  onEditSkill: (skill: SkillVersion) => void;
}) {
  const [enabled, setEnabled] = useState(mount.enabled);
  const [modelId, setModelId] = useState(mount.model_connection_id || "");
  const [skillId, setSkillId] = useState(mount.skill_version_id || "");
  const [params, setParams] = useState<AbilityParams>(mount.params);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const selectedSkill = skills.find((skill) => skill.id === skillId);

  function updateParam(key: string, value: string | number) {
    setParams((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      await api(`/ability-mounts/${mount.id}`, {
        method: "PUT",
        body: jsonBody({
          scope_key: scopeKey,
          enabled,
          model_connection_id: modelId || null,
          skill_version_id: skillId || null,
          params,
        }),
      });
      await onSaved();
    } catch (requestError) {
      setError(messageOf(requestError));
    } finally {
      setSaving(false);
    }
  }

  return (
    <article className={`agent-card-pro ${enabled ? "" : "agent-off"}`}>
      <header>
        <span className="agent-symbol"><Icon name={mount.stage === "EXTRACTION" ? "search" : "brain"} size={20} /></span>
        <div><h3>{mount.display_name}</h3><small>{mount.trigger}</small></div>
        {mount.inherited && <em className="inherit-tag">继承默认</em>}
        <button className={`switch ${enabled ? "on" : ""}`} aria-label={`${enabled ? "停用" : "启用"}${mount.display_name}`} aria-pressed={enabled} onClick={() => setEnabled(!enabled)}><i /></button>
      </header>
      <p className="agent-description">{mount.description}</p>
      <div className="agent-config-panel">
        <label><span>挂载 Skill</span><select value={skillId} onChange={(event) => setSkillId(event.target.value)}><option value="">不挂载</option>{skills.map((skill) => <option key={skill.id} value={skill.id}>{skill.name} · v{skill.version}</option>)}</select></label>
        <label><span>运行模型</span><select value={modelId} onChange={(event) => setModelId(event.target.value)}><option value="">不挂载</option>{models.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.model_name}</option>)}</select></label>
        <AgentParamFields abilityKey={mount.ability_key} params={params} onChange={updateParam} />
      </div>
      {error && <p className="card-error" role="alert">{error}</p>}
      <footer>
        <span><Icon name="layers" size={13} />显示于：{mount.location}</span>
        {selectedSkill && <Button kind="text" icon="edit" onClick={() => onEditSkill(selectedSkill)}>编辑 Skill</Button>}
        <Button kind="primary" onClick={() => void save()} disabled={saving || (enabled && (!modelId || !skillId))}>{saving ? "保存中…" : "保存配置"}</Button>
      </footer>
    </article>
  );
}

function AgentParamFields({ abilityKey, params, onChange }: { abilityKey: string; params: AbilityParams; onChange: (key: string, value: string | number) => void }) {
  if (abilityKey === "KNOWLEDGE_EXTRACTOR") {
    return <div className="agent-param-grid three"><SelectParam label="输出稳定性" value={String(params.stability || "STRICT")} onChange={(value) => onChange("stability", value)} options={[['STRICT', '严格'], ['BALANCED', '均衡']]} /><NumberParam label="最大片段" value={Number(params.max_chunks || 24)} min={4} max={60} onChange={(value) => onChange("max_chunks", value)} /><NumberParam label="并发上限" value={Number(params.concurrency || 3)} min={1} max={6} onChange={(value) => onChange("concurrency", value)} /></div>;
  }
  if (abilityKey === "ALIGNMENT_REVIEWER") {
    return <div className="agent-param-grid"><SelectParam label="输出稳定性" value={String(params.stability || "STRICT")} onChange={(value) => onChange("stability", value)} options={[['STRICT', '严格'], ['BALANCED', '均衡']]} /></div>;
  }
  if (abilityKey === "RULE_GENERATOR") {
    return <div className="agent-param-grid"><SelectParam label="输出格式" value={String(params.output_format || "XLSX_JSON")} onChange={(value) => onChange("output_format", value)} options={[['XLSX_JSON', 'Excel + JSON'], ['JSON', 'JSON']]} /></div>;
  }
  if (abilityKey === "THOUGHT_CHAIN_GENERATOR") {
    return <div className="agent-param-grid"><SelectParam label="输出格式" value={String(params.output_format || "MARKDOWN_OUTLINE")} onChange={(value) => onChange("output_format", value)} options={[['MARKDOWN_OUTLINE', 'Markdown 大纲'], ['PROCESS_JSON', '流程 JSON']]} /></div>;
  }
  if (abilityKey === "SKILL_GENERATOR") {
    return <div className="agent-param-grid two"><NumberParam label="精选范例数" value={Number(params.few_shot_count || 8)} min={0} max={20} onChange={(value) => onChange("few_shot_count", value)} /><SelectParam label="包格式" value="OPENJIUWEN" onChange={(value) => onChange("package_format", value)} options={[['OPENJIUWEN', 'openJiuwen Skill']]} /></div>;
  }
  if (abilityKey === "QA_GENERATOR") {
    return <div className="agent-param-grid two"><SelectParam label="问题风格" value={String(params.question_style || "BUSINESS")} onChange={(value) => onChange("question_style", value)} options={[['BUSINESS', '业务问法'], ['EXAM', '考核问法'], ['MIXED', '混合']]} /><SelectParam label="生成密度" value={String(params.density || "STANDARD")} onChange={(value) => onChange("density", value)} options={[['LIGHT', '精简'], ['STANDARD', '标准'], ['DENSE', '密集']]} /></div>;
  }
  return <div className="agent-param-grid two"><NumberParam label="测试集比例 %" value={Number(params.test_split || 20)} min={10} max={50} onChange={(value) => onChange("test_split", value)} /><SelectParam label="边界覆盖" value={String(params.boundary_coverage || "HIGH")} onChange={(value) => onChange("boundary_coverage", value)} options={[['STANDARD', '标准'], ['HIGH', '加强']]} /><p className="param-hint">无独立标注集时，输出会明确标记为“合成评测集”。</p></div>;
}

function SelectParam({ label, value, options, onChange }: { label: string; value: string; options: Array<[string, string]>; onChange: (value: string) => void }) {
  return <label><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>;
}

function NumberParam({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <label><span>{label}</span><input type="number" value={value} min={min} max={max} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

function SkillLibrary({ templates, instances, onView, onCreate }: { templates: SkillVersion[]; instances: SkillVersion[]; onView: (skill: SkillVersion) => void; onCreate: (source: SkillVersion) => void }) {
  return (
    <div className="skill-workbench">
      <section className="skill-section">
        <header><div><h2>通用 Skill 模板</h2><p>平台维护的只读方法论，复制后可按场景形成独立实例。</p></div><span>{templates.length} 个模板</span></header>
        <div className="skill-template-grid">
          {templates.map((skill, index) => (
            <article key={skill.id}>
              <header><span className="skill-symbol"><Icon name={index % 2 === 0 ? "spark" : "book"} size={19} /></span><em>只读模板</em></header>
              <h3>{skill.name}</h3>
              <p>{skill.description}</p>
              <footer><span>v{skill.version}</span><Button kind="text" onClick={() => onView(skill)}>查看</Button><Button icon="plus" onClick={() => onCreate(skill)}>复制为实例</Button></footer>
            </article>
          ))}
        </div>
      </section>

      <section className="skill-section instance-section">
        <header><div><h2>场景 Skill 实例</h2><p>实例拥有独立版本，可编辑元数据并上传经过安全校验的新包。</p></div><span>{instances.length} 个实例</span></header>
        {instances.length === 0 ? (
          <EmptyState icon="spark" title="还没有场景 Skill" detail="从上方模板复制一个实例，或上传符合 openJiuwen 规范的 ZIP。" />
        ) : (
          <div className="skill-instance-list">
            {instances.map((skill) => (
              <article key={skill.id}>
                <span className="skill-symbol"><Icon name="spark" size={19} /></span>
                <div><header><h3>{skill.name}</h3><StatusBadge status={skill.status} /><em>v{skill.version}</em></header><p>{skill.description}</p><small>{skill.scene_name || "通用场景"} · 来源 {skill.source_name || "本地导入"} · {formatDate(skill.created_at)}</small></div>
                <Button icon="edit" onClick={() => onView(skill)}>编辑与版本</Button>
                <Button icon="plus" onClick={() => onCreate(skill)}>复制</Button>
              </article>
            ))}
          </div>
        )}
      </section>
      <Notice tone="info">ZIP 会校验根目录 SKILL.md、路径穿越、符号链接、文件数和解压体积；包不会直接解压到运行目录。</Notice>
    </div>
  );
}

function SkillDrawer({ state, skills, scopeLabel, onClose, onSaved }: { state: DrawerState; skills: SkillVersion[]; scopeLabel: string; onClose: () => void; onSaved: (message: string) => Promise<void> }) {
  const templatesAndInstances = useMemo(() => skills.filter((skill) => skill.status === "ENABLED"), [skills]);
  const [sourceId, setSourceId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sceneName, setSceneName] = useState(scopeLabel);
  const [notes, setNotes] = useState("");
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!state) return;
    const source = state.source || state.skill || templatesAndInstances[0];
    setSourceId(source?.id || "");
    setName(state.mode === "new" ? `${source?.name || "Skill"} 实例` : state.skill?.name || "");
    setDescription(state.mode === "new" ? source?.description || "" : state.skill?.description || "");
    setSceneName(state.mode === "new" ? scopeLabel : state.skill?.scene_name || scopeLabel);
    setNotes(state.mode === "new" ? "基于通用模板创建，可独立迭代。" : state.skill?.notes || "");
    setVersions([]);
    setError("");
    if (state.skill) {
      void api<{ items: SkillVersion[] }>(`/skills/${state.skill.id}/versions`)
        .then((result) => setVersions(result.items))
        .catch((requestError) => setError(messageOf(requestError)));
    }
  }, [scopeLabel, state, templatesAndInstances]);

  useEffect(() => {
    if (!state) return;
    const listener = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [onClose, state]);

  if (!state) return null;
  const readOnly = state.mode === "view";
  const activeSkill = state.skill;

  async function save() {
    setBusy(true);
    setError("");
    try {
      if (state?.mode === "new") {
        await api("/skills/instances", { method: "POST", body: jsonBody({ source_skill_id: sourceId, name, description, scene_name: sceneName, notes }) });
        await onSaved("已创建场景 Skill 实例。");
      } else if (state?.mode === "edit" && activeSkill) {
        await api(`/skills/${activeSkill.id}`, { method: "PUT", body: jsonBody({ name, description, scene_name: sceneName, notes }) });
        await onSaved("Skill 实例信息已保存。");
      }
    } catch (requestError) {
      setError(messageOf(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function uploadVersion(file: File | undefined) {
    if (!file || !activeSkill) return;
    setBusy(true);
    setError("");
    try {
      await upload(`/skills/${activeSkill.id}/versions`, file);
      await onSaved("Skill 新版本已通过校验并启用，相关智能体挂载已同步更新。");
    } catch (requestError) {
      setError(messageOf(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function copyTemplate() {
    if (!activeSkill) return;
    setBusy(true);
    setError("");
    try {
      await api("/skills/instances", {
        method: "POST",
        body: jsonBody({
          source_skill_id: activeSkill.id,
          name: `${activeSkill.name} 实例`,
          description: activeSkill.description,
          scene_name: scopeLabel,
          notes: "基于通用模板创建，可独立迭代。",
        }),
      });
      await onSaved("已从模板创建场景 Skill 实例。");
    } catch (requestError) {
      setError(messageOf(requestError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="skill-drawer-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="skill-drawer" role="dialog" aria-modal="true" aria-label={state.mode === "new" ? "新建 Skill 实例" : activeSkill?.name || "Skill 详情"}>
        <header><div><span>{state.mode === "new" ? "CREATE INSTANCE" : readOnly ? "TEMPLATE DETAIL" : "INSTANCE DETAIL"}</span><h2>{state.mode === "new" ? "从模板新建 Skill" : activeSkill?.name}</h2><p>{readOnly ? "模板为只读；复制后可维护独立版本。" : "Skill 包遵循 openJiuwen 目录规范。"}</p></div><button className="icon-button" aria-label="关闭" onClick={onClose}><Icon name="close" /></button></header>
        <div className="skill-drawer-body">
          {error && <Notice tone="danger">{error}</Notice>}
          <section className="drawer-section"><h3>基本信息</h3><div className="form-stack">
            {state.mode === "new" && <label>基础模板<select value={sourceId} onChange={(event) => setSourceId(event.target.value)}>{templatesAndInstances.map((skill) => <option key={skill.id} value={skill.id}>{skill.name} · v{skill.version}</option>)}</select></label>}
            <label>Skill 名称<input value={name} readOnly={readOnly} onChange={(event) => setName(event.target.value)} /></label>
            <label>适用场景<input value={sceneName} readOnly={readOnly} onChange={(event) => setSceneName(event.target.value)} /></label>
            <label>说明<textarea value={description} readOnly={readOnly} onChange={(event) => setDescription(event.target.value)} /></label>
            <label>版本备注<textarea value={notes} readOnly={readOnly} onChange={(event) => setNotes(event.target.value)} /></label>
          </div></section>
          {activeSkill && <section className="drawer-section package-section"><h3>Skill 包</h3><div className="package-card"><span><Icon name="file" size={20} /></span><div><strong>{activeSkill.name}</strong><small>v{activeSkill.version} · SKILL.md / scripts / references / assets</small></div><a className="button button-ghost" href={activeSkill.download_url}><Icon name="download" size={15} />下载</a></div>{!readOnly && <label className="button button-ghost version-upload"><Icon name="upload" size={15} />上传新版本 ZIP<input data-testid="skill-version-upload" type="file" accept=".zip" hidden onChange={(event) => void uploadVersion(event.target.files?.[0])} disabled={busy} /></label>}</section>}
          {activeSkill && <section className="drawer-section"><h3>版本历史</h3><div className="version-list">{versions.map((version, index) => <div key={version.id}><span>v{version.version}</span><div><strong>{index === 0 ? "当前版本" : "历史版本"}</strong><small>{version.notes || "版本包已归档"}</small></div><time>{formatDate(version.created_at)}</time></div>)}</div></section>}
        </div>
        <footer>
          {readOnly && activeSkill ? <><a className="button button-ghost" href={activeSkill.download_url}><Icon name="download" size={15} />下载模板</a><Button kind="primary" icon="plus" onClick={() => void copyTemplate()} disabled={busy}>{busy ? "创建中…" : "复制为实例"}</Button></> : <><Button onClick={onClose}>取消</Button><Button kind="primary" onClick={() => void save()} disabled={busy || !name.trim() || !sourceId}>{busy ? "保存中…" : state.mode === "new" ? "创建实例" : "保存修改"}</Button></>}
        </footer>
      </aside>
    </div>
  );
}
