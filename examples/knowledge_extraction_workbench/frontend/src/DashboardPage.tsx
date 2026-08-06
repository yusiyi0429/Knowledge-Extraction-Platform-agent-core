import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

import { ApiError, api, jsonBody, upload, watchJob } from "./api";
import { Button, EmptyState, Icon, Modal, Notice, StatusBadge, formatDate } from "./components";
import type { Candidate, Job, Material, SceneSummary } from "./types";

interface DashboardMetrics {
  scenes: number;
  rules: number;
  published_rounds: number;
  skills: number;
  running_jobs: number;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

export function DashboardPage({ onOpenScene }: { onOpenScene: (sceneId: string) => void }) {
  const [metrics, setMetrics] = useState<DashboardMetrics>({ scenes: 0, rules: 0, published_rounds: 0, skills: 0, running_jobs: 0 });
  const [scenes, setScenes] = useState<SceneSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("ALL");
  const [newOpen, setNewOpen] = useState(false);
  const [exploreOpen, setExploreOpen] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<SceneSummary | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [dashboard, sceneResult] = await Promise.all([
        api<{ metrics: DashboardMetrics }>("/dashboard"),
        api<{ items: SceneSummary[] }>(`/scenes?status=${encodeURIComponent(status)}&q=${encodeURIComponent(search)}`),
      ]);
      setMetrics(dashboard.metrics);
      setScenes(sceneResult.items);
    } catch (error) {
      setNotice(messageOf(error));
    } finally {
      setLoading(false);
    }
  }, [search, status]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 180);
    return () => window.clearTimeout(timer);
  }, [load]);

  async function archiveScene() {
    if (!archiveTarget) return;
    try {
      await api(`/scenes/${archiveTarget.id}`, { method: "DELETE" });
      setArchiveTarget(null);
      setNotice(`已归档“${archiveTarget.name}”，历史轮次、素材与资产仍保留。`);
      await load();
    } catch (error) {
      setNotice(messageOf(error));
      setArchiveTarget(null);
    }
  }

  const filteredLabel = useMemo(() => {
    if (loading) return "正在同步工作台数据";
    return `${scenes.length} 个可见场景`;
  }, [loading, scenes.length]);

  return (
    <div className="page dashboard-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Knowledge lifecycle</p>
          <h1>知识萃取<span>工作台</span></h1>
          <p>从素材证据到可发布知识资产，所有指标均来自本机 SQLite。</p>
        </div>
        <div className="head-actions">
          <Button icon="spark" onClick={() => setExploreOpen(true)}>场景探索</Button>
          <Button kind="primary" icon="plus" onClick={() => setNewOpen(true)}>新建场景</Button>
        </div>
      </header>

      {notice && <Notice tone={notice.startsWith("已") ? "success" : "warning"}><span>{notice}</span><button onClick={() => setNotice(null)}>关闭</button></Notice>}

      <section className="metric-grid" aria-label="工作台指标">
        <Metric icon="layers" value={metrics.scenes} label="活跃场景" foot={metrics.running_jobs ? `${metrics.running_jobs} 个任务运行中` : "当前无运行任务"} />
        <Metric icon="book" value={metrics.rules} label="结构化规则" foot="来自已保存研判文档" />
        <Metric icon="check" value={metrics.published_rounds} label="已发布轮次" foot="发布后保持不可变" />
        <Metric icon="spark" value={metrics.skills} label="可用 Skill" foot="内置模板与上传实例" />
      </section>

      <section className="scene-section">
        <div className="section-toolbar">
          <div>
            <h2>场景与萃取轮次</h2>
            <span>{filteredLabel}</span>
          </div>
          <div className="filters">
            <label className="search-field">
              <Icon name="search" size={16} />
              <input aria-label="搜索场景" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称、目标或描述" />
            </label>
            <div className="segment" aria-label="状态筛选">
              {["ALL", "DRAFT", "REVIEW", "PUBLISHED", "FAILED"].map((item) => (
                <button key={item} className={status === item ? "active" : ""} onClick={() => setStatus(item)}>
                  {{ ALL: "全部", DRAFT: "草稿", REVIEW: "待对齐", PUBLISHED: "已发布", FAILED: "失败" }[item]}
                </button>
              ))}
            </div>
          </div>
        </div>

        {loading ? (
          <div className="skeleton-grid">{[1, 2, 3].map((item) => <div className="scene-skeleton" key={item} />)}</div>
        ) : scenes.length === 0 ? (
          <EmptyState
            icon="layers"
            title={search || status !== "ALL" ? "没有匹配的场景" : "从第一个知识场景开始"}
            detail={search || status !== "ALL" ? "调整搜索词或状态筛选后重试。" : "直接新建场景并上传素材，或先让场景探索从多份资料中发现候选。"}
          />
        ) : (
          <div className="scene-grid">
            {scenes.map((scene) => (
              <article className="scene-card" key={scene.id} onClick={() => onOpenScene(scene.id)} tabIndex={0} onKeyDown={(event) => event.key === "Enter" && onOpenScene(scene.id)}>
                <div className="scene-card-top">
                  <StatusBadge status={scene.status} />
                  <button className="card-action" aria-label={`归档 ${scene.name}`} onClick={(event) => { event.stopPropagation(); setArchiveTarget(scene); }}>
                    <Icon name="archive" size={17} />
                  </button>
                </div>
                <h3>{scene.name}<span>v{scene.round?.version || 1}</span></h3>
                <p>{scene.description || scene.goal || "尚未填写场景描述，进入场景补充目标与边界。"}</p>
                <div className="scene-metrics">
                  <span><b>{scene.material_count}</b>素材</span>
                  <span><b>{scene.rule_count}</b>规则</span>
                  <span><b>{scene.asset_count}/5</b>资产</span>
                </div>
                <footer>
                  <span className="owner-avatar">{scene.owner.slice(0, 1)}</span>
                  <span>{scene.owner}</span>
                  <time>{formatDate(scene.updated_at)}</time>
                  <Icon name="chevron" size={15} />
                </footer>
              </article>
            ))}
          </div>
        )}
      </section>

      <NewSceneModal open={newOpen} onClose={() => setNewOpen(false)} onCreated={onOpenScene} />
      <ExplorationModal open={exploreOpen} onClose={() => setExploreOpen(false)} onCreated={onOpenScene} />
      <Modal
        open={Boolean(archiveTarget)}
        title="归档场景"
        subtitle="归档仅从工作台隐藏场景，不会删除历史证据、修订或发布资产。"
        onClose={() => setArchiveTarget(null)}
        footer={<><Button onClick={() => setArchiveTarget(null)}>取消</Button><Button kind="danger" icon="archive" onClick={() => void archiveScene()}>确认归档</Button></>}
      >
        <p className="confirm-copy">确定归档“<strong>{archiveTarget?.name}</strong>”吗？运行中的场景会返回冲突，不会被误归档。</p>
      </Modal>
    </div>
  );
}

function Metric({ icon, value, label, foot }: { icon: "layers" | "book" | "check" | "spark"; value: number; label: string; foot: string }) {
  return (
    <article className="metric-card">
      <span className="metric-icon"><Icon name={icon} size={20} /></span>
      <strong>{value}</strong>
      <h3>{label}</h3>
      <p>{foot}</p>
    </article>
  );
}

function NewSceneModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (sceneId: string) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [goal, setGoal] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const result = await api<{ scene: { id: string } }>("/scenes", {
        method: "POST",
        body: jsonBody({ name, description, goal }),
      });
      setName("");
      setDescription("");
      setGoal("");
      onClose();
      onCreated(result.scene.id);
    } catch (requestError) {
      setError(messageOf(requestError));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      title="新建知识场景"
      subtitle="只填写必要信息，素材与子场景可在下一步继续完善。"
      onClose={onClose}
      footer={<><Button onClick={onClose}>取消</Button><Button kind="primary" disabled={saving || !name.trim()} onClick={() => document.getElementById("new-scene-submit")?.click()}>{saving ? "创建中…" : "创建并进入"}</Button></>}
    >
      <form className="form-stack" onSubmit={submit}>
        {error && <Notice tone="danger">{error}</Notice>}
        <label>场景名称<span>*</span><input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：企业差旅费用审核" /></label>
        <label>场景描述<textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明业务范围、使用对象和当前痛点" /></label>
        <label>萃取目标<textarea value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="希望从素材中沉淀哪些规则、流程或问答" /></label>
        <button id="new-scene-submit" type="submit" hidden />
      </form>
    </Modal>
  );
}

function ExplorationModal({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (sceneId: string) => void }) {
  const [name, setName] = useState("多素材场景探索");
  const [goal, setGoal] = useState("");
  const [explorationId, setExplorationId] = useState<string | null>(null);
  const [materials, setMaterials] = useState<Material[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState("等待上传素材");
  const [error, setError] = useState("");

  const reset = useCallback(() => {
    setName("多素材场景探索");
    setGoal("");
    setExplorationId(null);
    setMaterials([]);
    setCandidates([]);
    setBusy(false);
    setProgress(0);
    setPhase("等待上传素材");
    setError("");
  }, []);

  async function ensureExploration(): Promise<string> {
    if (explorationId) return explorationId;
    const result = await api<{ id: string }>("/explorations", { method: "POST", body: jsonBody({ name, goal }) });
    setExplorationId(result.id);
    return result.id;
  }

  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      const id = await ensureExploration();
      const uploaded: Material[] = [];
      for (const file of Array.from(files)) {
        setPhase(`正在解析 ${file.name}`);
        uploaded.push(await upload<Material>(`/explorations/${id}/materials`, file));
      }
      setMaterials((current) => [...current, ...uploaded]);
      setPhase(`已准备 ${materials.length + uploaded.length} 份素材`);
    } catch (requestError) {
      setError(messageOf(requestError));
    } finally {
      setBusy(false);
    }
  }

  async function analyze() {
    setBusy(true);
    setError("");
    setCandidates([]);
    try {
      const id = await ensureExploration();
      const job = await api<Job>(`/explorations/${id}/analyze`, { method: "POST" });
      setPhase(job.message);
      watchJob(
        job.id,
        (event) => {
          setProgress(event.progress);
          setPhase(event.message);
          if (event.status === "COMPLETED") {
            void api<{ items: Candidate[] }>(`/explorations/${id}/candidates`).then((result) => {
              setCandidates(result.items);
              setBusy(false);
            }).catch((requestError) => {
              setError(messageOf(requestError));
              setBusy(false);
            });
          }
          if (event.status === "FAILED") setBusy(false);
        },
        (streamError) => { setError(streamError.message); setBusy(false); },
      );
    } catch (requestError) {
      setError(messageOf(requestError));
      setBusy(false);
    }
  }

  async function useCandidate(candidate: Candidate) {
    if (!explorationId) return;
    setBusy(true);
    try {
      const result = await api<{ scene_id: string }>(`/explorations/${explorationId}/candidates/${candidate.id}/create-scene`, { method: "POST" });
      reset();
      onClose();
      onCreated(result.scene_id);
    } catch (requestError) {
      setError(messageOf(requestError));
      setBusy(false);
    }
  }

  function close() {
    if (busy) return;
    reset();
    onClose();
  }

  return (
    <Modal
      open={open}
      wide
      title="场景探索"
      subtitle="轮询覆盖每份素材全文，过滤短碎片后识别可落地的知识场景。"
      onClose={close}
      footer={candidates.length === 0 ? <><Button onClick={close} disabled={busy}>取消</Button><Button kind="primary" icon="spark" onClick={() => void analyze()} disabled={busy || materials.length === 0}>{busy ? "分析中…" : "分析候选场景"}</Button></> : <Button onClick={close}>稍后处理</Button>}
    >
      <div className="exploration-layout">
        <section className="explore-inputs">
          {error && <Notice tone="danger">{error}</Notice>}
          <label>探索名称<input value={name} onChange={(event) => setName(event.target.value)} disabled={Boolean(explorationId)} /></label>
          <label>探索目标<textarea value={goal} onChange={(event) => setGoal(event.target.value)} disabled={Boolean(explorationId)} placeholder="例如：发现制度、流程和案例共同覆盖的审核场景" /></label>
          <label className={`upload-zone ${busy ? "disabled" : ""}`}>
            <Icon name="upload" size={25} />
            <strong>选择多份素材</strong>
            <span>PDF / DOCX / XLSX / CSV / TSV / TXT / MD，单文件 ≤ 200 MB</span>
            <input type="file" multiple accept=".pdf,.docx,.xlsx,.csv,.tsv,.txt,.md" onChange={(event) => void uploadFiles(event.target.files)} disabled={busy} />
          </label>
          <div className="material-mini-list">
            {materials.map((material) => <span key={material.id}><Icon name="file" size={14} /><b>{material.name}</b><small>{material.sha256.slice(0, 8)}</small></span>)}
          </div>
          {(busy || progress > 0) && <div className="job-progress"><div><i style={{ width: `${progress}%` }} /></div><p>{phase}</p></div>}
        </section>
        <section className="candidate-panel">
          <div className="candidate-head"><span>结构化候选</span><b>{candidates.length || "—"}</b></div>
          {candidates.length === 0 ? (
            <EmptyState icon="spark" title="等待分析" detail="候选会显示场景目标、置信度和原始素材引用；不会只读取文档开头。" />
          ) : (
            <div className="candidate-list">
              {candidates.map((candidate) => (
                <article key={candidate.id}>
                  <header><h3>{candidate.name}</h3><span>{Math.round(candidate.confidence * 100)}%</span></header>
                  <p>{candidate.description}</p>
                  <div className="source-chip"><Icon name="file" size={13} />{candidate.source_refs[0]?.material_name} · 片段 {(candidate.source_refs[0]?.chunk_index ?? 0) + 1}</div>
                  <Button kind="primary" icon="arrow" onClick={() => void useCandidate(candidate)} disabled={busy}>带入新场景</Button>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </Modal>
  );
}
