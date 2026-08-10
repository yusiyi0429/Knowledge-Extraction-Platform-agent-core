import { useCallback, useEffect, useRef, useState } from "react";

import { api, jsonBody, upload, watchJob } from "./api";
import { Button, EmptyState, Icon, Notice, StatusBadge, formatDate } from "./components";
import type {
  FeedbackAnalysis,
  FeedbackCase,
  FeedbackTask,
  Job,
  ModelConnection,
  RuntimeSkill,
} from "./types";

const classificationAttributions = ["规则阈值", "规则缺失", "规则太宽", "思维链缺环", "数据质量", "其他"];
const generationAttributions = ["遗漏要点", "事实错误", "逻辑错误", "依据不足", "建议不当", "合规问题", "其他"];

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

export function FeedbackPage({ initialTaskId }: { initialTaskId?: string }) {
  const [skills, setSkills] = useState<RuntimeSkill[]>([]);
  const [models, setModels] = useState<ModelConnection[]>([]);
  const [tasks, setTasks] = useState<FeedbackTask[]>([]);
  const [activeTask, setActiveTask] = useState<FeedbackTask | null>(null);
  const [cases, setCases] = useState<FeedbackCase[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("新一轮错例分析");
  const [roundId, setRoundId] = useState("");
  const [modelId, setModelId] = useState("");
  const [taskType, setTaskType] = useState<"CLASSIFICATION" | "GENERATION">("CLASSIFICATION");
  const [openCase, setOpenCase] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState("");
  const [notice, setNotice] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);
  const stopWatching = useRef<(() => void) | null>(null);

  const load = useCallback(async () => {
    const [skillResult, modelResult, taskResult] = await Promise.all([
      api<{ items: RuntimeSkill[] }>("/runtime/skills"),
      api<{ items: ModelConnection[] }>("/models"),
      api<{ items: FeedbackTask[] }>("/feedback-tasks"),
    ]);
    const availableModels = modelResult.items.filter((model) => model.enabled && model.has_api_key);
    setSkills(skillResult.items);
    setModels(availableModels);
    setTasks(taskResult.items);
    setRoundId((current) => skillResult.items.some((item) => item.round_id === current) ? current : skillResult.items[0]?.round_id || "");
    setModelId((current) => availableModels.some((item) => item.id === current) ? current : availableModels[0]?.id || "");
    if (initialTaskId) {
      const task = await api<FeedbackTask>(`/feedback-tasks/${initialTaskId}`);
      setActiveTask(task);
      setCases(task.cases);
      setOpenCase(task.cases[0]?.id || null);
    }
  }, [initialTaskId]);

  useEffect(() => {
    void load().catch((error: unknown) => setNotice({ tone: "danger", text: messageOf(error) }));
    return () => stopWatching.current?.();
  }, [load]);

  async function openTask(taskId: string) {
    setBusy(true);
    try {
      const task = await api<FeedbackTask>(`/feedback-tasks/${taskId}`);
      setActiveTask(task);
      setCases(task.cases);
      setOpenCase(task.cases[0]?.id || null);
      setCreating(false);
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  function backToList() {
    stopWatching.current?.();
    setActiveTask(null);
    setCases([]);
    setCreating(false);
    setProgress(0);
    setPhase("");
    window.location.hash = "#/feedback";
    void load();
  }

  async function createTask() {
    if (!roundId || !modelId) return;
    setBusy(true);
    try {
      const task = await api<FeedbackTask>("/feedback-tasks", {
        method: "POST",
        body: jsonBody({ name, round_id: roundId, model_connection_id: modelId, task_type: taskType }),
      });
      setActiveTask(task);
      setCases([]);
      setCreating(false);
      window.location.hash = `#/feedback/${task.id}`;
      await load();
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function uploadCases(file: File | undefined) {
    if (!file || !activeTask) return;
    setBusy(true);
    try {
      const task = await upload<FeedbackTask>(`/feedback-tasks/${activeTask.id}/cases`, file);
      setActiveTask(task);
      setCases(task.cases);
      setNotice({ tone: "success", text: `已解析 ${task.case_count} 条错例，可开始 AI 初判。` });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function refreshTask(taskId: string) {
    const [task, list] = await Promise.all([
      api<FeedbackTask>(`/feedback-tasks/${taskId}`),
      api<{ items: FeedbackTask[] }>("/feedback-tasks"),
    ]);
    setActiveTask(task);
    setCases(task.cases);
    setOpenCase(task.cases[0]?.id || null);
    setTasks(list.items);
  }

  async function analyze() {
    if (!activeTask || activeTask.case_count === 0) return;
    setBusy(true);
    setProgress(0);
    setNotice(null);
    try {
      const result = await api<{ task: FeedbackTask; job: Job }>(`/feedback-tasks/${activeTask.id}/analyze`, { method: "POST" });
      setActiveTask(result.task);
      stopWatching.current?.();
      stopWatching.current = watchJob(
        result.job.id,
        (event) => {
          setProgress(event.progress);
          setPhase(event.message);
          if (event.status === "COMPLETED") {
            setBusy(false);
            void refreshTask(activeTask.id);
          }
          if (event.status === "FAILED") {
            setBusy(false);
            setNotice({ tone: "danger", text: event.message });
          }
        },
        (error) => {
          setBusy(false);
          setNotice({ tone: "danger", text: error.message });
        },
      );
    } catch (error) {
      setBusy(false);
      setNotice({ tone: "danger", text: messageOf(error) });
    }
  }

  function updateExpert(index: number, patch: Partial<FeedbackAnalysis>) {
    setCases((current) => current.map((item, itemIndex) => itemIndex === index ? {
      ...item,
      expert: { ...(item.expert || { knowledge_gap: "", attribution: "其他" }), ...patch },
      expert_confirmed: false,
    } : item));
  }

  function confirmCase(index: number) {
    setCases((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, expert_confirmed: true } : item));
  }

  async function saveCases() {
    if (!activeTask) return;
    setBusy(true);
    try {
      const task = await api<FeedbackTask>(`/feedback-tasks/${activeTask.id}`, {
        method: "PUT",
        body: jsonBody({ cases }),
      });
      setActiveTask(task);
      setCases(task.cases);
      setNotice({ tone: "success", text: task.status === "READY" ? "全部错例已由专家确认，可以回流到下一轮。" : "专家修订已保存。" });
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function promote() {
    if (!activeTask) return;
    setBusy(true);
    try {
      const result = await api<{ scene_id: string }>(`/feedback-tasks/${activeTask.id}/promote`, { method: "POST" });
      window.location.hash = `#/scenes/${result.scene_id}`;
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
      setBusy(false);
    }
  }

  return (
    <div className="page feedback-page">
      <header className="page-head compact">
        <div>
          <p className="eyebrow">Knowledge feedback loop</p>
          <h1>错例分析<span>与回流</span></h1>
          <p>业务 Skill 提供场景口径，内置错例分析 Skill 提供分析框架；AI 只做初判，专家定稿后才能进入下一轮萃取。</p>
        </div>
        {!activeTask && !creating && <Button kind="primary" icon="plus" onClick={() => setCreating(true)} disabled={skills.length === 0 || models.length === 0}>新建错例分析</Button>}
      </header>

      {notice && <Notice tone={notice.tone}>{notice.text}<button onClick={() => setNotice(null)}>关闭</button></Notice>}

      {activeTask ? (
        <section className="feedback-workflow">
          <button className="feedback-back" onClick={backToList}><Icon name="chevron" size={14} />返回任务列表</button>
          <header className="feedback-task-head"><div><span>{activeTask.id.slice(0, 8)}</span><h2>{activeTask.name}</h2><p>{activeTask.source_filename || "等待导入错例"}</p></div><StatusBadge status={activeTask.status} /></header>

          <article className="feedback-step completed">
            <span className="feedback-step-number">1</span>
            <div><h2>场景 Skill 与分析方法</h2><p>分析格式在任务创建时冻结，避免同一批次字段结构变化。</p></div>
            <div className="feedback-frozen-config"><b>{skills.find((item) => item.round_id === activeTask.round_id)?.label || "已发布 Skill"}</b><span>{models.find((item) => item.id === activeTask.model_connection_id)?.name || "已配置模型"}</span><em>{activeTask.task_type === "GENERATION" ? "生成式" : "判别式"}</em></div>
            <aside><Icon name="spark" size={17} /><div><b>错例分析与回流 Skill v1.0</b><small>平台内置 · 无需单独挂载</small></div></aside>
          </article>

          <article className="feedback-step">
            <span className="feedback-step-number">2</span>
            <div><h2>导入错误案例</h2><p>支持 JSON/JSONL、CSV/TSV、XLSX、TXT 和 Markdown；至少提供输入，建议同时包含原输出和标准答案。</p></div>
            <label className="feedback-upload"><Icon name="upload" size={22} /><b>{activeTask.source_filename || "选择一批错例文件"}</b><span>{activeTask.case_count ? `已解析 ${activeTask.case_count} 条错例` : "一条记录也可以作为单条任务"}</span><input type="file" accept=".json,.jsonl,.csv,.tsv,.xlsx,.txt,.md" hidden onChange={(event) => void uploadCases(event.target.files?.[0])} disabled={busy || activeTask.status === "PROMOTED"} /></label>
            {busy && <div className="runtime-progress"><span style={{ width: `${progress}%` }} /><div><b>{phase || "正在处理"}</b><em>{progress}%</em></div></div>}
            <div className="feedback-step-action"><Button kind="primary" icon="spark" onClick={() => void analyze()} disabled={busy || activeTask.case_count === 0 || activeTask.status === "PROMOTED"}>{activeTask.status === "REVIEW" || activeTask.status === "READY" ? "重新分析" : "开始 AI 初判"}</Button></div>
          </article>

          {(activeTask.status === "REVIEW" || activeTask.status === "READY" || activeTask.status === "PROMOTED") && (
            <article className="feedback-step feedback-review-step">
              <span className="feedback-step-number">3</span>
              <div><h2>AI 初判与专家修订</h2><p>左侧初判保持只读；专家字段可直接修改，逐条确认后统一保存。</p></div>
              <div className="feedback-case-list">{cases.map((item, index) => <FeedbackCaseEditor key={item.id} item={item} index={index} taskType={activeTask.task_type} open={openCase === item.id} onToggle={() => setOpenCase(openCase === item.id ? null : item.id)} onChange={updateExpert} onConfirm={confirmCase} disabled={activeTask.status === "PROMOTED"} />)}</div>
              <div className="feedback-output-bar">
                <div><Icon name="check" size={17} /><span><b>{cases.filter((item) => item.expert_confirmed).length}/{cases.length} 条已确认</b><small>全部确认后可输出并回流</small></span></div>
                <Button onClick={() => void saveCases()} disabled={busy || activeTask.status === "PROMOTED"}>保存专家修订</Button>
                <a className="button button-ghost" href={`/api/v1/feedback-tasks/${activeTask.id}/export?format=json`}><Icon name="download" size={15} />JSON</a>
                <a className="button button-ghost" href={`/api/v1/feedback-tasks/${activeTask.id}/export?format=xlsx`}><Icon name="download" size={15} />Excel</a>
                <Button kind="primary" icon="refresh" onClick={() => void promote()} disabled={busy || activeTask.status !== "READY"}>{activeTask.status === "PROMOTED" ? "已进入下一轮" : "作为下一轮萃取素材"}</Button>
              </div>
            </article>
          )}
        </section>
      ) : creating ? (
        <section className="feedback-create">
          <header><div><span className="section-index">新任务</span><h2>定义这批错例的分析上下文</h2><p>只配置影响当前任务的三个字段，不创建专用智能体。</p></div><button onClick={() => setCreating(false)} aria-label="关闭"><Icon name="close" /></button></header>
          <div className="feedback-create-grid">
            <label className="full"><span>任务名称</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
            <label><span>已发布 Skill（即业务场景）</span><select value={roundId} onChange={(event) => setRoundId(event.target.value)}>{skills.map((skill) => <option key={skill.round_id} value={skill.round_id}>{skill.label}</option>)}</select></label>
            <label><span>分析模型</span><select value={modelId} onChange={(event) => setModelId(event.target.value)}>{models.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.model_name}</option>)}</select></label>
            <fieldset className="full"><legend>分析格式</legend><button className={taskType === "CLASSIFICATION" ? "active" : ""} onClick={() => setTaskType("CLASSIFICATION")}><b>判别式</b><span>正确标签 · 错因 · 正确原因 · 归因</span></button><button className={taskType === "GENERATION" ? "active" : ""} onClick={() => setTaskType("GENERATION")}><b>生成式</b><span>问题点 · 应有写法 · 知识缺口 · 归因</span></button></fieldset>
          </div>
          <footer><Button onClick={() => setCreating(false)}>取消</Button><Button kind="primary" icon="arrow" onClick={() => void createTask()} disabled={busy || !name.trim() || !roundId || !modelId}>{busy ? "创建中…" : "创建并导入错例"}</Button></footer>
        </section>
      ) : tasks.length === 0 ? (
        <EmptyState icon="refresh" title="还没有错例分析任务" detail="从正式评测报告回流错例，或新建任务导入一批业务错误案例。" action={<Button kind="primary" icon="plus" onClick={() => setCreating(true)}>新建错例分析</Button>} />
      ) : (
        <section className="feedback-task-list">
          <div className="feedback-task-list-head"><span>任务</span><span>分析格式</span><span>错例</span><span>状态</span><span>更新时间</span><span /></div>
          {tasks.map((task) => <button key={task.id} onClick={() => void openTask(task.id)}><div><b>{task.name}</b><small>{task.id.slice(0, 8)} · {skills.find((item) => item.round_id === task.round_id)?.label || "已发布 Skill"}</small></div><em>{task.task_type === "GENERATION" ? "生成式" : "判别式"}</em><strong>{task.case_count}</strong><StatusBadge status={task.status} /><time>{formatDate(task.updated_at)}</time><Icon name="chevron" size={15} /></button>)}
        </section>
      )}
    </div>
  );
}

function FeedbackCaseEditor({ item, index, taskType, open, onToggle, onChange, onConfirm, disabled }: { item: FeedbackCase; index: number; taskType: "CLASSIFICATION" | "GENERATION"; open: boolean; onToggle: () => void; onChange: (index: number, patch: Partial<FeedbackAnalysis>) => void; onConfirm: (index: number) => void; disabled: boolean }) {
  const analysis = item.analysis || { knowledge_gap: "", attribution: "其他" };
  const expert = item.expert || analysis;
  const attributions = taskType === "GENERATION" ? generationAttributions : classificationAttributions;
  return <article className={open ? "open" : ""}>
    <button className="feedback-case-row" onClick={onToggle}><Icon name="chevron" size={14} /><span>{item.id}</span><b>{item.summary || item.input}</b><em className={item.expert_confirmed ? "confirmed" : ""}>{item.expert_confirmed ? "专家已确认" : "待专家确认"}</em></button>
    {open && <div className="feedback-case-body">
      <section className="feedback-case-context"><h3>案例信息</h3><p><b>输入</b>{item.input}</p><p><b>原输出</b>{item.original_output || "未提供"}</p>{item.expected && <p><b>标准答案</b>{item.expected}</p>}</section>
      <div className="feedback-analysis-columns">
        <section className="feedback-ai-analysis"><header><h3>AI 初判</h3><span>只读</span></header>{taskType === "GENERATION" ? <><FieldView label="问题点" value={(analysis.issues || []).map((issue) => `${issue.type}：${issue.description}`).join("\n")} /><FieldView label="应有写法" value={analysis.expected_content} /><FieldView label="知识缺口" value={analysis.knowledge_gap} /></> : <><FieldView label="建议正确标签" value={analysis.correct_label} /><FieldView label="错因分析" value={analysis.error_reason} /><FieldView label="正确原因" value={analysis.correct_reason} /><FieldView label="知识缺口" value={analysis.knowledge_gap} /></>}<FieldView label="主要归因" value={analysis.attribution} /></section>
        <section className="feedback-expert-edit"><header><h3>业务专家修订</h3><span>{item.expert_confirmed ? "已确认" : "在线编辑"}</span></header>
          {taskType === "GENERATION" ? <><label><span>问题点清单（一行一条）</span><textarea disabled={disabled} value={(expert.issues || []).map((issue) => issue.description).join("\n")} onChange={(event) => onChange(index, { issues: event.target.value.split("\n").filter(Boolean).map((description) => ({ type: expert.attribution || "其他", description })) })} /></label><label><span>应有写法 / 正确内容</span><textarea disabled={disabled} value={expert.expected_content || ""} onChange={(event) => onChange(index, { expected_content: event.target.value })} /></label></> : <><label><span>正确标签</span><input disabled={disabled} value={expert.correct_label || ""} onChange={(event) => onChange(index, { correct_label: event.target.value })} /></label><label><span>错因分析</span><textarea disabled={disabled} value={expert.error_reason || ""} onChange={(event) => onChange(index, { error_reason: event.target.value })} /></label><label><span>正确原因</span><textarea disabled={disabled} value={expert.correct_reason || ""} onChange={(event) => onChange(index, { correct_reason: event.target.value })} /></label></>}
          <label><span>知识缺口</span><textarea disabled={disabled} value={expert.knowledge_gap || ""} onChange={(event) => onChange(index, { knowledge_gap: event.target.value })} /></label>
          <label><span>主要归因</span><select disabled={disabled} value={expert.attribution || "其他"} onChange={(event) => onChange(index, { attribution: event.target.value })}>{attributions.map((value) => <option key={value}>{value}</option>)}</select></label>
          {!disabled && <Button kind="primary" icon="check" onClick={() => onConfirm(index)}>确认本条定稿</Button>}
        </section>
      </div>
    </div>}
  </article>;
}

function FieldView({ label, value }: { label: string; value?: string }) {
  return <div className="feedback-field-view"><span>{label}</span><p>{value || "—"}</p></div>;
}
