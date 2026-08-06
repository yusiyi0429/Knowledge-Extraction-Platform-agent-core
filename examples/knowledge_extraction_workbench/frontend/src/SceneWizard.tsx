import { useCallback, useEffect, useMemo, useState } from "react";

import { api, jsonBody, upload, watchJob } from "./api";
import { Button, EmptyState, Icon, Notice, StatusBadge, formatBytes, formatDate } from "./components";
import type { Asset, Job, JobEvent, KnowledgeDocument, Material, Revision, Round, SceneDetail, Suggestion } from "./types";

const assetMeta: Record<string, { title: string; detail: string; icon: "book" | "brain" | "spark" | "file" | "check" }> = {
  RULES_XLSX: { title: "规则清单", detail: "结构化 Excel，可直接审阅与交付", icon: "book" },
  THOUGHT_CHAIN_MD: { title: "决策研判链", detail: "可审计判断步骤，不包含隐藏思维过程", icon: "brain" },
  SKILL_ZIP: { title: "openJiuwen Skill", detail: "SKILL.md + scripts / references / assets", icon: "spark" },
  QA_JSONL: { title: "QA 数据集", detail: "带素材引用的问答 JSONL", icon: "file" },
  EVAL_JSONL: { title: "合成评测集", detail: "无独立标注集，状态保持待评测", icon: "check" },
};

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

export function SceneWizard({ sceneId, onBack }: { sceneId: string; onBack: () => void }) {
  const [scene, setScene] = useState<SceneDetail | null>(null);
  const [round, setRound] = useState<Round | null>(null);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [document, setDocument] = useState<KnowledgeDocument | null>(null);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<{ tone: "success" | "warning" | "danger"; text: string } | null>(null);

  const loadRoundData = useCallback(async (roundId: string) => {
    const [materialResult, suggestionResult, revisionResult, assetResult] = await Promise.all([
      api<{ items: Material[] }>(`/rounds/${roundId}/materials`),
      api<{ items: Suggestion[] }>(`/rounds/${roundId}/suggestions`),
      api<{ items: Revision[] }>(`/rounds/${roundId}/revisions`),
      api<{ items: Asset[] }>(`/rounds/${roundId}/assets`),
    ]);
    setMaterials(materialResult.items);
    setSuggestions(suggestionResult.items);
    setRevisions(revisionResult.items);
    setAssets(assetResult.items);
    if (revisionResult.items.length > 0) {
      setDocument(await api<KnowledgeDocument>(`/rounds/${roundId}/document`));
    } else setDocument(null);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const detail = await api<SceneDetail>(`/scenes/${sceneId}`);
      const latest = detail.rounds[0] || null;
      setScene(detail);
      setRound(latest);
      if (latest) await loadRoundData(latest.id);
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setLoading(false);
    }
  }, [loadRoundData, sceneId]);

  useEffect(() => { void load(); }, [load]);

  async function newRound() {
    if (!scene) return;
    try {
      const created = await api<Round>(`/scenes/${scene.id}/rounds`, { method: "POST" });
      setRound(created);
      setStep(1);
      setNotice({ tone: "success", text: `已创建 v${created.version}，并继承上一轮启用素材与配置。` });
      await load();
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    }
  }

  if (loading || !scene || !round) {
    return <div className="page"><div className="wizard-loading"><span className="spinner" />正在加载场景与轮次…</div></div>;
  }

  const published = round.status === "PUBLISHED";
  return (
    <div className="wizard-page">
      <header className="wizard-head">
        <div className="wizard-title">
          <button className="back-button" onClick={onBack} aria-label="返回工作台"><Icon name="chevron" /></button>
          <div><span>场景工作区</span><h1>{scene.name}</h1></div>
          <StatusBadge status={round.status} />
        </div>
        <div className="version-track">
          <span>轮次</span>
          {scene.rounds.slice().reverse().map((item) => (
            <button key={item.id} className={item.id === round.id ? "active" : ""} onClick={() => { setRound(item); void loadRoundData(item.id); }}>
              <i />v{item.version}{item.status === "PUBLISHED" && <Icon name="check" size={12} />}
            </button>
          ))}
          {published && <Button kind="text" icon="plus" onClick={() => void newRound()}>新一轮</Button>}
        </div>
        <nav className="stepper" aria-label="萃取步骤">
          {[
            [1, "场景与素材", "目标 · 素材 · 子场景"],
            [2, "知识萃取与对齐", "萃取 · 修订 · 确认"],
            [3, "知识生成及发布", "资产 · 下载 · 发布"],
          ].map(([number, title, detail], index) => (
            <div className="step-wrap" key={number}>
              {index > 0 && <span className={`step-line ${step > Number(number) ? "done" : ""}`} />}
              <button className={`step-item ${step === number ? "active" : ""} ${step > Number(number) ? "done" : ""}`} onClick={() => setStep(Number(number))}>
                <b>{step > Number(number) ? <Icon name="check" size={14} /> : number}</b>
                <span><strong>{title}</strong><small>{detail}</small></span>
              </button>
            </div>
          ))}
        </nav>
      </header>

      {notice && <div className="wizard-notice"><Notice tone={notice.tone}>{notice.text}<button onClick={() => setNotice(null)}>关闭</button></Notice></div>}
      <main className="wizard-body">
        {step === 1 && <SceneMaterialsStep scene={scene} round={round} materials={materials} published={published} onReload={load} onNext={() => setStep(2)} setNotice={setNotice} />}
        {step === 2 && <AlignmentStep round={round} document={document} suggestions={suggestions} revisions={revisions} published={published} onReload={() => loadRoundData(round.id)} onNext={() => setStep(3)} setNotice={setNotice} />}
        {step === 3 && <AssetsStep round={round} assets={assets} document={document} published={published} onReload={load} onNewRound={newRound} setNotice={setNotice} />}
      </main>
    </div>
  );
}

type NoticeSetter = (notice: { tone: "success" | "warning" | "danger"; text: string } | null) => void;

function SceneMaterialsStep({
  scene,
  round,
  materials,
  published,
  onReload,
  onNext,
  setNotice,
}: {
  scene: SceneDetail;
  round: Round;
  materials: Material[];
  published: boolean;
  onReload: () => Promise<void>;
  onNext: () => void;
  setNotice: NoticeSetter;
}) {
  const [name, setName] = useState(scene.name);
  const [description, setDescription] = useState(scene.description);
  const [goal, setGoal] = useState(scene.goal);
  const [subscenes, setSubscenes] = useState<string[]>(round.subscenes.length ? round.subscenes : [""]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setName(scene.name);
    setDescription(scene.description);
    setGoal(scene.goal);
    setSubscenes(round.subscenes.length ? round.subscenes : [""]);
  }, [round.id, round.subscenes, scene.description, scene.goal, scene.name]);

  async function save() {
    setBusy(true);
    try {
      await api(`/scenes/${scene.id}`, {
        method: "PATCH",
        body: jsonBody({ name, description, goal, subscenes: subscenes.filter((item) => item.trim()) }),
      });
      setNotice({ tone: "success", text: "场景目标与子场景已保存。" });
      await onReload();
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const file of Array.from(files)) await upload<Material>(`/rounds/${round.id}/materials`, file);
      setNotice({ tone: "success", text: `已上传并解析 ${files.length} 份素材。` });
      await onReload();
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function toggleMaterial(material: Material) {
    try {
      await api(`/materials/${material.id}`, { method: "PATCH", body: jsonBody({ enabled: !material.enabled }) });
      await onReload();
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    }
  }

  return (
    <div className="step-pane materials-pane">
      <div className="pane-heading"><p>Step 01 · 场景与素材</p><h2>定义知识边界，准备可信素材</h2><span>仅保留影响萃取结果的必要字段；发布轮次中的内容保持只读。</span></div>
      {published && <Notice tone="warning">当前为已发布快照。创建新一轮后才能继续修改。</Notice>}
      <section className="form-card">
        <header><h3>场景目标</h3><span>告诉萃取流程“什么属于这个场景”</span></header>
        <div className="field-grid">
          <label className="full">场景名称<span>*</span><input value={name} onChange={(event) => setName(event.target.value)} disabled={published} /></label>
          <label className="full">场景描述<textarea value={description} onChange={(event) => setDescription(event.target.value)} disabled={published} placeholder="业务范围、使用对象与边界" /></label>
          <label className="full">萃取目标<textarea value={goal} onChange={(event) => setGoal(event.target.value)} disabled={published} placeholder="需要沉淀的规则、流程、QA 或评测维度" /></label>
        </div>
      </section>
      <section className="form-card">
        <header><h3>子场景</h3><span>可选，用于拆分不同流程或规则分支</span></header>
        <div className="subscene-list">
          {subscenes.map((item, index) => (
            <div key={`${index}-${round.id}`}><span>{String(index + 1).padStart(2, "0")}</span><input value={item} onChange={(event) => setSubscenes((current) => current.map((value, itemIndex) => itemIndex === index ? event.target.value : value))} disabled={published} placeholder="例如：差旅申请前置审核" />{subscenes.length > 1 && !published && <button aria-label="删除子场景" onClick={() => setSubscenes((current) => current.filter((_, itemIndex) => itemIndex !== index))}><Icon name="close" size={15} /></button>}</div>
          ))}
          {!published && <Button kind="text" icon="plus" onClick={() => setSubscenes((current) => [...current, ""])}>添加子场景</Button>}
        </div>
      </section>
      <section className="form-card material-card">
        <header><h3>知识素材</h3><span>跨素材公平取样，不只读取文件开头</span></header>
        {!published && <label className={`upload-strip ${busy ? "disabled" : ""}`}><Icon name="upload" size={21} /><span><strong>上传素材</strong><small>PDF / DOCX / XLSX / CSV / TSV / TXT / MD · 单文件 ≤ 200 MB</small></span><input type="file" multiple disabled={busy} accept=".pdf,.docx,.xlsx,.csv,.tsv,.txt,.md" onChange={(event) => void uploadFiles(event.target.files)} /></label>}
        {materials.length === 0 ? <EmptyState title="尚未上传素材" detail="至少上传一份包含业务规则、流程或案例正文的文件。" /> : (
          <div className="material-table">
            <div className="material-row header"><span>素材</span><span>角色</span><span>大小</span><span>内容哈希</span><span>状态</span></div>
            {materials.map((material) => <div className={`material-row ${material.enabled ? "" : "muted"}`} key={material.id}><span><Icon name="file" size={16} /><b>{material.name}</b></span><span>{material.role === "REFERENCE" ? "参考依据" : material.role}</span><span>{formatBytes(material.size_bytes)}</span><span className="mono">{material.sha256.slice(0, 12)}</span><span><button className={`switch ${material.enabled ? "on" : ""}`} aria-label={`${material.enabled ? "停用" : "启用"}${material.name}`} onClick={() => void toggleMaterial(material)} disabled={published}><i /></button>{material.enabled ? "启用" : "停用"}</span></div>)}
          </div>
        )}
      </section>
      <footer className="pane-actions"><Button onClick={() => void save()} disabled={published || busy || !name.trim()}>{busy ? "处理中…" : "保存场景"}</Button><Button kind="primary" icon="arrow" onClick={onNext} disabled={materials.filter((item) => item.enabled).length === 0}>进入知识萃取</Button></footer>
    </div>
  );
}

function AlignmentStep({
  round,
  document,
  suggestions,
  revisions,
  published,
  onReload,
  onNext,
  setNotice,
}: {
  round: Round;
  document: KnowledgeDocument | null;
  suggestions: Suggestion[];
  revisions: Revision[];
  published: boolean;
  onReload: () => Promise<void>;
  onNext: () => void;
  setNotice: NoticeSetter;
}) {
  const [markdown, setMarkdown] = useState(document?.markdown || "");
  const [busy, setBusy] = useState(false);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [selectedRule, setSelectedRule] = useState(0);

  useEffect(() => setMarkdown(document?.markdown || ""), [document?.markdown]);

  const pendingSuggestion = suggestions.find((item) => item.status === "PENDING") || null;

  const observeJob = useCallback((job: Job, done: () => Promise<void>) => {
    setEvents([]);
    watchJob(job.id, (event) => {
      setEvents((current) => [...current.filter((item) => item.seq !== event.seq), event]);
      if (event.status === "COMPLETED") void done().finally(() => setBusy(false));
      if (event.status === "FAILED") { setNotice({ tone: "danger", text: event.message }); setBusy(false); }
    }, (error) => { setNotice({ tone: "warning", text: error.message }); setBusy(false); });
  }, [setNotice]);

  async function extract() {
    setBusy(true);
    try {
      const job = await api<Job>(`/rounds/${round.id}/extract`, { method: "POST" });
      observeJob(job, async () => { await onReload(); setNotice({ tone: "success", text: "知识萃取完成，研判文档已生成。" }); });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
      setBusy(false);
    }
  }

  async function save() {
    if (!document) return;
    setBusy(true);
    try {
      await api(`/rounds/${round.id}/document`, { method: "PUT", body: jsonBody({ markdown, base_revision: document.revision, reason: "手工保存" }) });
      setNotice({ tone: "success", text: "研判文档已保存为新修订。" });
      await onReload();
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function suggest() {
    setBusy(true);
    try {
      await api(`/rounds/${round.id}/suggestions`, { method: "POST" });
      await onReload();
      setNotice({ tone: "success", text: "AI 差异建议已生成，采纳前不会修改正文。" });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function resolveSuggestion(action: "apply" | "reject") {
    if (!pendingSuggestion || !document) return;
    setBusy(true);
    try {
      await api(`/suggestions/${pendingSuggestion.id}/${action}`, {
        method: "POST",
        body: action === "apply" ? jsonBody({ base_revision: document.revision }) : undefined,
      });
      await onReload();
      setNotice({ tone: "success", text: action === "apply" ? "已采纳建议并生成新修订。" : "已放弃建议，正文未发生变化。" });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  if (!document) {
    return (
      <div className="step-pane align-empty-pane">
        <div className="extract-orb"><span /><i><Icon name="brain" size={28} /></i></div>
        <h2>开始受控知识萃取</h2>
        <p>系统将校验素材哈希，公平覆盖全文片段，并以并发上限 3 执行 Map/Reduce。配置、素材与模板版本会冻结到本次任务。</p>
        <Button kind="primary" icon="spark" onClick={() => void extract()} disabled={busy || published}>{busy ? "正在萃取…" : "启动知识萃取"}</Button>
        {events.length > 0 && <JobTimeline events={events} />}
      </div>
    );
  }

  const rules = document.structured.rules || [];
  return (
    <div className="alignment-workbench">
      <aside className="knowledge-outline">
        <header><span>知识结构</span><b>{rules.length} 条规则</b></header>
        <button className="outline-section active"><Icon name="book" size={15} />规则清单</button>
        <div className="outline-rules">
          {rules.map((rule, index) => <button key={rule.id} className={selectedRule === index ? "active" : ""} onClick={() => setSelectedRule(index)}><span>{rule.id}</span>{rule.title}</button>)}
        </div>
        <button className="outline-section"><Icon name="layers" size={15} />业务流程 <small>{document.structured.process?.length || 0}</small></button>
        <section className="revision-list"><h4>修订记录</h4>{revisions.slice(0, 5).map((revision) => <div key={revision.id}><b>v{revision.revision}</b><span>{revision.reason}</span><time>{formatDate(revision.created_at)}</time></div>)}</section>
      </aside>
      <section className="document-editor">
        <header><div><h2>知识研判文档</h2><p>Markdown · revision {document.revision} · {document.structured.generated_by}</p></div><div><Button icon="refresh" onClick={() => void extract()} disabled={busy || published}>重新萃取</Button><Button kind="primary" onClick={() => void save()} disabled={busy || published || markdown === document.markdown}>保存修订</Button></div></header>
        <textarea aria-label="知识研判 Markdown" value={markdown} onChange={(event) => setMarkdown(event.target.value)} readOnly={published} spellCheck={false} />
        {rules[selectedRule] && <article className="evidence-strip"><header><b>{rules[selectedRule].id}</b><span>{rules[selectedRule].title}</span></header><p>{rules[selectedRule].action}</p><footer>{rules[selectedRule].sources.map((source) => <span key={`${source.material_id}-${source.chunk_index}`}><Icon name="file" size={12} />{source.material_name} · 片段 {source.chunk_index + 1}</span>)}</footer></article>}
      </section>
      <aside className="suggestion-side">
        <header><span className="ai-mark"><Icon name="spark" size={18} /></span><div><h3>一致性对齐</h3><p>建议模式 · 不直接改正文</p></div></header>
        {!pendingSuggestion ? (
          <EmptyState icon="spark" title="暂无待处理建议" detail="让对齐能力检查规则是否缺少例外、留痕或人工升级条件。" action={!published ? <Button kind="primary" onClick={() => void suggest()} disabled={busy}>生成 AI 建议</Button> : undefined} />
        ) : (
          <div className="suggestion-card">
            <span className="suggestion-label">基于 revision {pendingSuggestion.base_revision}</span>
            <h4>建议补充审计闭环</h4>
            <p>{pendingSuggestion.explanation}</p>
            <div className="diff-block"><del>{pendingSuggestion.old_text}</del><ins>{pendingSuggestion.new_text}</ins></div>
            <div className="suggestion-sources">{pendingSuggestion.source_refs.map((source) => <span key={`${source.material_id}-${source.chunk_index}`}><Icon name="file" size={12} />{source.material_name} #{source.chunk_index + 1}</span>)}</div>
            <footer><Button onClick={() => void resolveSuggestion("reject")} disabled={busy}>放弃</Button><Button kind="primary" icon="check" onClick={() => void resolveSuggestion("apply")} disabled={busy}>采纳建议</Button></footer>
          </div>
        )}
        <div className="alignment-foot"><Icon name="database" size={14} />所有采纳与放弃操作均进入修订日志</div>
        <Button kind="primary" icon="arrow" onClick={onNext}>进入资产生成</Button>
      </aside>
    </div>
  );
}

function JobTimeline({ events }: { events: JobEvent[] }) {
  const latest = events[events.length - 1];
  return <div className="job-timeline"><div className="job-bar"><i style={{ width: `${latest?.progress || 0}%` }} /></div>{events.map((event) => <div key={event.seq} className={event.status === "RUNNING" ? "active" : "done"}><span>{event.status === "RUNNING" ? <i className="spinner small" /> : <Icon name="check" size={13} />}</span><b>{event.message}</b><small>{event.progress}%</small></div>)}</div>;
}

function AssetsStep({
  round,
  assets,
  document,
  published,
  onReload,
  onNewRound,
  setNotice,
}: {
  round: Round;
  assets: Asset[];
  document: KnowledgeDocument | null;
  published: boolean;
  onReload: () => Promise<void>;
  onNewRound: () => Promise<void>;
  setNotice: NoticeSetter;
}) {
  const [busy, setBusy] = useState(false);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const latest = useMemo(() => Object.fromEntries(assets.map((asset) => [asset.kind, asset])), [assets]);
  const complete = Object.keys(assetMeta).every((kind) => latest[kind] && !latest[kind].stale);

  function observe(job: Job, success: string) {
    setEvents([]);
    watchJob(job.id, (event) => {
      setEvents((current) => [...current.filter((item) => item.seq !== event.seq), event]);
      if (event.status === "COMPLETED") void onReload().then(() => setNotice({ tone: "success", text: success })).finally(() => setBusy(false));
      if (event.status === "FAILED") { setNotice({ tone: "danger", text: event.message }); setBusy(false); }
    }, (error) => { setNotice({ tone: "warning", text: error.message }); setBusy(false); });
  }

  async function generate(kind?: string) {
    setBusy(true);
    try {
      const job = await api<Job>(`/rounds/${round.id}/assets`, { method: "POST", body: jsonBody(kind ? { kind } : {}) });
      observe(job, kind ? `${assetMeta[kind].title}已生成新版本。` : "五类知识资产已生成。");
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
      setBusy(false);
    }
  }

  async function publish() {
    setBusy(true);
    try {
      await api(`/rounds/${round.id}/publish`, { method: "POST" });
      setNotice({ tone: "success", text: `v${round.version} 已发布并锁定为不可变快照。` });
      await onReload();
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="step-pane assets-pane">
      <div className="pane-heading"><p>Step 03 · 知识生成及发布</p><h2>把审定知识转成交付资产</h2><span>规则、研判链和 Skill 由规范化 JSON 确定性生成；QA 与评测样本保留来源。</span></div>
      {!document ? <EmptyState icon="book" title="尚无可生成文档" detail="返回知识萃取与对齐，完成第一版研判文档后再生成资产。" /> : (
        <>
          <section className="asset-summary"><div className="asset-ring" style={{ "--progress": `${Object.keys(latest).length / 5 * 360}deg` } as React.CSSProperties}><span><b>{Object.keys(latest).length}</b>/5</span></div><div><h3>{complete ? "交付资产已齐备" : "准备生成交付资产"}</h3><p>文档 revision {document.revision} · 规则 {document.structured.rules.length} 条 · 评测状态：<b>待评测</b></p></div><Button kind="primary" icon="spark" onClick={() => void generate()} disabled={busy || published}>{busy ? "生成中…" : assets.length ? "重新生成全部" : "生成五类资产"}</Button></section>
          {events.length > 0 && <JobTimeline events={events} />}
          <section className="asset-grid">
            {Object.entries(assetMeta).map(([kind, meta], index) => {
              const asset = latest[kind];
              return <article className={asset && !asset.stale ? "ready" : ""} key={kind}><span className="asset-index">0{index + 1}</span><span className="asset-icon"><Icon name={meta.icon} size={22} /></span><div><h3>{meta.title}{kind === "EVAL_JSONL" && <em>合成</em>}</h3><p>{meta.detail}</p>{asset && <small className={asset.stale ? "stale" : ""}>{asset.stale ? "文档已修订，需重新生成" : `v${asset.version} · revision ${asset.source_revision} · ${formatBytes(asset.size_bytes)} · ${formatDate(asset.created_at)}`}</small>}</div><footer>{asset ? <><a className="button button-ghost" href={asset.download_url}><Icon name="download" size={15} />下载</a>{!published && <button className="icon-button" aria-label={`重新生成${meta.title}`} onClick={() => void generate(kind)} disabled={busy}><Icon name="refresh" size={15} /></button>}</> : <span>待生成</span>}</footer></article>;
            })}
          </section>
          <section className="publish-card"><div><span className="publish-icon"><Icon name={published ? "check" : "upload"} size={22} /></span><div><h3>{published ? `v${round.version} 已发布` : "发布当前萃取轮次"}</h3><p>{published ? "该轮次的文档、素材与资产已锁定；继续工作请创建新一轮。" : "发布后当前轮次不可修改，历史素材、修订和资产永久保留。"}</p></div></div>{published ? <Button kind="primary" icon="plus" onClick={() => void onNewRound()}>创建新一轮</Button> : <Button kind="primary" icon="upload" onClick={() => void publish()} disabled={!complete || busy}>确认发布</Button>}</section>
        </>
      )}
    </div>
  );
}
