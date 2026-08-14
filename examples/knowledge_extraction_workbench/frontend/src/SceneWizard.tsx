import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, api, jsonBody, upload, watchJob } from "./api";
import { Button, EmptyState, Icon, Modal, Notice, StatusBadge, formatBytes, formatDate } from "./components";
import type { Asset, AssetPreview, Job, JobEvent, KnowledgeDocument, Material, Revision, Round, SceneDetail, Suggestion } from "./types";

const assetMeta: Record<string, { title: string; detail: string; icon: "book" | "brain" | "spark" | "file" | "check" }> = {
  RULES_XLSX: { title: "规则清单", detail: "结构化 Excel，可直接审阅与交付", icon: "book" },
  THOUGHT_CHAIN_MD: { title: "决策研判链", detail: "可审计判断步骤，不包含隐藏思维过程", icon: "brain" },
  SKILL_ZIP: { title: "openJiuwen Skill", detail: "SKILL.md + scripts / references / assets", icon: "spark" },
  QA_JSONL: { title: "QA 数据集", detail: "带素材引用的问答 JSONL", icon: "file" },
  EVAL_JSONL: { title: "合成评测集", detail: "无独立标注集，状态保持待评测", icon: "check" },
};

type SuggestionMode = "CONSISTENCY" | "REGULATORY" | "GAP" | "CUSTOM";

type AssistantRequest = {
  mode: SuggestionMode;
  instruction: string;
  label: string;
};

type AssistantMessage = {
  id: string;
  role: "assistant" | "user";
  text: string;
};

const assistantModes: Array<{ mode: Exclude<SuggestionMode, "CUSTOM">; label: string }> = [
  { mode: "CONSISTENCY", label: "一致性检查" },
  { mode: "REGULATORY", label: "监管对齐" },
  { mode: "GAP", label: "查漏补缺" },
];

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

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load]);

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
        {step === 1 && <SceneMaterialsStep key={`${scene.id}:${round.id}`} scene={scene} round={round} materials={materials} published={published} onReload={load} onNext={() => setStep(2)} setNotice={setNotice} />}
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
  const [subscene, setSubscene] = useState(round.subscenes[0] ?? "");
  const [busy, setBusy] = useState(false);

  async function save(advance = false) {
    setBusy(true);
    try {
      await api(`/scenes/${scene.id}`, {
        method: "PATCH",
        body: jsonBody({ name, description, goal, subscenes: subscene.trim() ? [subscene.trim()] : [] }),
      });
      setNotice({ tone: "success", text: advance ? "场景已保存，可开始知识萃取。" : "场景目标与子场景已保存。" });
      await onReload();
      if (advance) onNext();
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
        <header><h3>本轮子场景</h3><span>每轮萃取聚焦一个具体业务分支</span></header>
        <div className="field-grid">
          <label className="full">子场景名称<input value={subscene} onChange={(event) => setSubscene(event.target.value)} disabled={published} placeholder="例如：差旅申请前置审核" /></label>
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
      <footer className="pane-actions"><Button onClick={() => void save()} disabled={published || busy || !name.trim()}>{busy ? "处理中…" : "保存场景"}</Button><Button kind="primary" icon="arrow" onClick={() => published ? onNext() : void save(true)} disabled={busy || !name.trim() || materials.filter((item) => item.enabled).length === 0}>{published ? "查看知识萃取" : "保存并进入知识萃取"}</Button></footer>
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
  const [assistantInput, setAssistantInput] = useState("");
  const [assistantMessages, setAssistantMessages] = useState<AssistantMessage[]>([]);
  const [assistantError, setAssistantError] = useState<string | null>(null);
  const [assistantRunning, setAssistantRunning] = useState(false);
  const [lastAssistantRequest, setLastAssistantRequest] = useState<AssistantRequest | null>(null);
  const assistantThreadRef = useRef<HTMLDivElement>(null);

  useEffect(() => setMarkdown(document?.markdown || ""), [document?.markdown]);

  const pendingSuggestion = suggestions.find((item) => item.status === "PENDING") || null;

  useEffect(() => {
    assistantThreadRef.current?.scrollTo({ top: assistantThreadRef.current.scrollHeight, behavior: "smooth" });
  }, [assistantError, assistantMessages.length, assistantRunning, pendingSuggestion?.id]);

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

  function appendAssistantMessage(role: AssistantMessage["role"], text: string) {
    setAssistantMessages((current) => [...current, { id: `${role}-${Date.now()}-${current.length}`, role, text }]);
  }

  async function suggest(request: AssistantRequest) {
    if (!document || pendingSuggestion || busy) return;
    const instruction = request.instruction.trim();
    if (request.mode === "CUSTOM" && !instruction) {
      setAssistantError("请先输入希望 AI 如何修改文档。");
      return;
    }
    const normalizedRequest = { ...request, instruction };
    let savedBeforeSuggestion = false;
    setLastAssistantRequest(normalizedRequest);
    setAssistantError(null);
    appendAssistantMessage("user", request.label);
    setBusy(true);
    setAssistantRunning(true);
    try {
      if (markdown !== document.markdown) {
        await api<KnowledgeDocument>(`/rounds/${round.id}/document`, {
          method: "PUT",
          body: jsonBody({ markdown, base_revision: document.revision, reason: "AI 分析前保存" }),
        });
        savedBeforeSuggestion = true;
      }
      const created = await api<Suggestion>(`/rounds/${round.id}/suggestions`, {
        method: "POST",
        body: jsonBody({ mode: request.mode, instruction }),
      });
      await onReload();
      appendAssistantMessage("assistant", created.explanation);
      if (request.mode === "CUSTOM") setAssistantInput("");
      setNotice({ tone: "success", text: "AI 修改建议已生成，采纳前不会修改正文。" });
    } catch (error) {
      if (savedBeforeSuggestion) await onReload().catch(() => undefined);
      const retryHint = error instanceof ApiError && error.retryable ? "当前指令已保留，可以直接重试。" : "请检查指令后重试。";
      setAssistantError(`${messageOf(error)} ${retryHint}`);
    } finally {
      setAssistantRunning(false);
      setBusy(false);
    }
  }

  function sendAssistantInput() {
    const instruction = assistantInput.trim();
    void suggest({ mode: "CUSTOM", instruction, label: instruction });
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
      const resolvedMessage = action === "apply" ? "已采纳，左侧文档已更新并生成新修订。" : "已放弃该建议，正文没有变化。";
      appendAssistantMessage("assistant", resolvedMessage);
      setAssistantError(null);
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
      <aside className="suggestion-side ai-assistant">
        <header><span className="ai-mark"><Icon name="spark" size={18} /></span><div><h3>AI 修改助手</h3><p>分析 · 查漏 · 批量改 · 按意图改写</p></div></header>
        <div className="assistant-quick" aria-label="AI 快捷分析">
          {assistantModes.map((item) => (
            <button
              key={item.mode}
              type="button"
              onClick={() => void suggest({ mode: item.mode, instruction: "", label: item.label })}
              disabled={busy || published || Boolean(pendingSuggestion)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="assistant-thread" ref={assistantThreadRef} aria-live="polite">
          <div className="assistant-message assistant">
            我可以分析这份研判文档、检查规则冲突与遗漏，也可以按你的意图修改。所有改动都会先展示差异，由你确认后再写入正文。
          </div>
          {assistantMessages.map((message) => (
            <div key={message.id} className={`assistant-message ${message.role}`}>{message.text}</div>
          ))}
          {assistantRunning ? <div className="assistant-message assistant running"><i className="spinner small" />正在通读当前修订并核对来源…</div> : null}
          {assistantError ? (
            <div className="assistant-error" role="alert">
              <Icon name="warning" size={14} />
              <span>{assistantError}</span>
              {lastAssistantRequest && !pendingSuggestion ? <button type="button" onClick={() => void suggest(lastAssistantRequest)} disabled={busy}>重试</button> : null}
            </div>
          ) : null}
          {pendingSuggestion ? (
            <div className="suggestion-card">
              <span className="suggestion-label">建议改动 · 基于 revision {pendingSuggestion.base_revision}</span>
              <h4>请确认本次修改</h4>
              <p>{pendingSuggestion.explanation}</p>
              <div className="diff-block">
                <del><b>改前</b>{pendingSuggestion.old_text}</del>
                <ins><b>改后</b>{pendingSuggestion.new_text}</ins>
              </div>
              {pendingSuggestion.source_refs.length > 0 ? (
                <div className="suggestion-sources">{pendingSuggestion.source_refs.map((source) => <span key={`${source.material_id}-${source.chunk_index}`}><Icon name="file" size={12} />{source.material_name} #{source.chunk_index + 1}</span>)}</div>
              ) : <p className="source-empty">本条建议未引用新的外部依据，请结合左侧原文复核。</p>}
              <footer><Button onClick={() => void resolveSuggestion("reject")} disabled={busy}>放弃</Button><Button kind="primary" icon="check" onClick={() => void resolveSuggestion("apply")} disabled={busy}>采纳建议</Button></footer>
            </div>
          ) : null}
        </div>
        <div className="alignment-foot"><Icon name="database" size={14} />采纳与放弃均进入修订日志</div>
        <Button kind="primary" icon="arrow" onClick={onNext}>进入资产生成</Button>
        <div className="assistant-composer">
          <textarea
            aria-label="AI 修改指令"
            value={assistantInput}
            onChange={(event) => setAssistantInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (!busy && !published && !pendingSuggestion) sendAssistantInput();
              }
            }}
            placeholder={pendingSuggestion ? "请先采纳或放弃当前建议" : "用大白话说明要怎么改，或让 AI 分析…"}
            maxLength={1000}
            disabled={busy || published || Boolean(pendingSuggestion)}
          />
          <button
            className="assistant-send"
            type="button"
            aria-label="发送修改指令"
            onClick={sendAssistantInput}
            disabled={busy || published || Boolean(pendingSuggestion) || !assistantInput.trim()}
          >
            <Icon name="send" size={18} />
          </button>
          <small>Enter 发送 · Shift + Enter 换行</small>
        </div>
      </aside>
    </div>
  );
}

function JobTimeline({ events }: { events: JobEvent[] }) {
  const ordered = [...events].sort((left, right) => left.seq - right.seq);
  const latest = ordered[ordered.length - 1];
  const terminal = latest?.status === "COMPLETED" || latest?.status === "FAILED";
  const failed = latest?.status === "FAILED";
  return (
    <div className={`job-timeline ${terminal ? failed ? "failed" : "completed" : "running"}`} aria-live="polite">
      <header className="job-timeline-status">
        <span>{failed ? <Icon name="warning" size={14} /> : terminal ? <Icon name="check" size={14} /> : <i className="spinner small" />}{failed ? "任务执行失败" : terminal ? "任务已完成" : "任务执行中"}</span>
        <b>{latest?.progress || 0}%</b>
      </header>
      <div className="job-bar"><i style={{ width: `${latest?.progress || 0}%` }} /></div>
      {ordered.map((event, index) => {
        const active = !terminal && index === ordered.length - 1 && event.status === "RUNNING";
        const eventFailed = event.status === "FAILED";
        return <div key={event.seq} className={eventFailed ? "failed" : active ? "active" : "done"}><span>{eventFailed ? <Icon name="warning" size={13} /> : active ? <i className="spinner small" /> : <Icon name="check" size={13} />}</span><b>{event.message}</b><small>{event.status === "COMPLETED" ? "已完成" : `${event.progress}%`}</small></div>;
      })}
    </div>
  );
}

function AssetPreviewContent({ preview }: { preview: AssetPreview }) {
  return (
    <div className={`asset-preview asset-preview-${preview.mode}`}>
      {preview.mode === "table" && (
        <div className="asset-preview-table-wrap">
          <table><thead><tr>{preview.columns?.map((column, index) => <th key={`${column}-${index}`}>{column || `第 ${index + 1} 列`}</th>)}</tr></thead><tbody>{preview.rows?.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, cellIndex) => <td key={cellIndex}>{value === null ? "—" : String(value)}</td>)}</tr>)}</tbody></table>
        </div>
      )}
      {preview.mode === "markdown" && <pre className="asset-preview-document">{preview.text || "文件内容为空。"}</pre>}
      {preview.mode === "archive" && (
        <div className="asset-preview-archive">
          <section><h3>Skill 包目录</h3><div className="archive-entry-list">{preview.entries?.map((entry) => <div key={entry.path}><Icon name="file" size={14} /><span>{entry.path}</span><small>{formatBytes(entry.size_bytes)}</small></div>)}</div></section>
          <section><h3>SKILL.md</h3><pre className="asset-preview-document">{preview.text || "包内未找到可预览的 SKILL.md。"}</pre></section>
        </div>
      )}
      {preview.mode === "jsonl" && (
        <div className="jsonl-preview-list">{preview.items?.map((item, index) => <article key={index}><span>记录 {String(index + 1).padStart(2, "0")}</span><pre>{JSON.stringify(item, null, 2)}</pre></article>)}</div>
      )}
      {preview.truncated && <p className="preview-truncated">为保证页面流畅，仅展示前一部分内容；下载文件可查看完整资产。</p>}
    </div>
  );
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
  const [previewAsset, setPreviewAsset] = useState<Asset | null>(null);
  const [preview, setPreview] = useState<AssetPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const latest = useMemo(() => Object.fromEntries(assets.map((asset) => [asset.kind, asset])), [assets]);
  const complete = Object.keys(assetMeta).every((kind) => latest[kind] && !latest[kind].stale);
  const readyCount = Object.keys(assetMeta).filter((kind) => latest[kind] && !latest[kind].stale).length;

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

  async function openPreview(asset: Asset) {
    setPreviewAsset(asset);
    setPreview(null);
    setPreviewLoading(true);
    try {
      setPreview(await api<AssetPreview>(`/assets/${asset.id}/preview`));
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
      setPreviewAsset(null);
    } finally {
      setPreviewLoading(false);
    }
  }

  function closePreview() {
    setPreviewAsset(null);
    setPreview(null);
  }

  return (
    <div className="step-pane assets-pane">
      <div className="pane-heading"><p>Step 03 · 知识生成及发布</p><h2>把审定知识转成交付资产</h2><span>规则、研判链和 Skill 由规范化 JSON 确定性生成；QA 与评测样本保留来源。</span></div>
      {!document ? <EmptyState icon="book" title="尚无可生成文档" detail="返回知识萃取与对齐，完成第一版研判文档后再生成资产。" /> : (
        <>
          <section className="asset-summary"><div className="asset-ring" style={{ "--progress": `${readyCount / 5 * 360}deg` } as React.CSSProperties}><span><b>{readyCount}</b>/5</span></div><div><h3>{published ? `v${round.version} 资产已发布` : complete ? "资产生成已完成，待确认发布" : "准备生成交付资产"}</h3><p>文档 revision {document.revision} · 规则 {document.structured.rules.length} 条 · 评测状态：<b>待评测</b></p></div><Button kind="primary" icon="spark" onClick={() => void generate()} disabled={busy || published}>{busy ? "生成中…" : assets.length ? "重新生成全部" : "生成五类资产"}</Button></section>
          {events.length > 0 && <JobTimeline events={events} />}
          <div className="asset-section-title"><div><strong>生成的知识资产</strong><span>{complete ? "五类当前版本资产可预览、单独下载或整包下载" : "生成完成后可整包下载"}</span></div>{complete ? <a className="button button-ghost" href={`/api/v1/rounds/${round.id}/assets/download`} download><Icon name="download" size={15} />下载全部</a> : <button className="button button-ghost" disabled><Icon name="download" size={15} />下载全部</button>}</div>
          <section className="asset-grid">
            {Object.entries(assetMeta).map(([kind, meta], index) => {
              const asset = latest[kind];
              return <article className={asset && !asset.stale ? "ready" : ""} key={kind}><span className="asset-index">0{index + 1}</span><span className="asset-icon"><Icon name={meta.icon} size={22} /></span><div><h3>{meta.title}{kind === "EVAL_JSONL" && <em>合成</em>}</h3><p>{meta.detail}</p>{asset && <small className={asset.stale ? "stale" : ""}>{asset.stale ? "文档已修订，需重新生成" : `v${asset.version} · revision ${asset.source_revision} · ${formatBytes(asset.size_bytes)} · ${formatDate(asset.created_at)}`}</small>}</div><footer>{asset ? <><a className="button button-ghost" href={asset.download_url}><Icon name="download" size={15} />下载</a><Button icon="eye" onClick={() => void openPreview(asset)}>预览</Button>{!published && <button className="icon-button" aria-label={`重新生成${meta.title}`} onClick={() => void generate(kind)} disabled={busy}><Icon name="refresh" size={15} /></button>}</> : <span>待生成</span>}</footer></article>;
            })}
          </section>
          <section className="publish-card"><div><span className="publish-icon"><Icon name={published ? "check" : "upload"} size={22} /></span><div><h3>{published ? `v${round.version} 已发布` : "发布当前萃取轮次"}</h3><p>{published ? "该轮次的文档、素材与资产已锁定；继续工作请创建新一轮。" : "发布后当前轮次不可修改，历史素材、修订和资产永久保留。"}</p></div></div>{published ? <Button kind="primary" icon="plus" onClick={() => void onNewRound()}>创建新一轮</Button> : <Button kind="primary" icon="upload" onClick={() => void publish()} disabled={!complete || busy}>确认发布</Button>}</section>
          <Modal open={Boolean(previewAsset)} title={`${previewAsset ? assetMeta[previewAsset.kind]?.title || previewAsset.filename : "资产"}预览`} subtitle={previewAsset ? `${previewAsset.filename} · v${previewAsset.version} · revision ${previewAsset.source_revision}` : undefined} onClose={closePreview} wide footer={previewAsset ? <><a className="button button-ghost" href={previewAsset.download_url}><Icon name="download" size={15} />下载此资产</a><Button kind="primary" onClick={closePreview}>关闭预览</Button></> : undefined}>
            {previewLoading ? <div className="asset-preview-loading"><span className="spinner" />正在读取资产内容…</div> : preview ? <AssetPreviewContent preview={preview} /> : null}
          </Modal>
        </>
      )}
    </div>
  );
}
