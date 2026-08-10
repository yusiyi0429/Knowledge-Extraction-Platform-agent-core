import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, jsonBody, uploadWithFields, watchJob } from "./api";
import { Button, EmptyState, Icon, Notice, StatusBadge, formatDate } from "./components";
import type {
  EvaluationRun,
  Job,
  ModelConnection,
  RuntimeSkill,
  TryoutResult,
} from "./types";

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试。";
}

export function RunEvaluationPage() {
  const [skills, setSkills] = useState<RuntimeSkill[]>([]);
  const [models, setModels] = useState<ModelConnection[]>([]);
  const [evaluations, setEvaluations] = useState<EvaluationRun[]>([]);
  const [roundId, setRoundId] = useState("");
  const [modelId, setModelId] = useState("");
  const [tab, setTab] = useState<"tryout" | "evaluation">("tryout");
  const [input, setInput] = useState("");
  const [tryoutFile, setTryoutFile] = useState<File | null>(null);
  const [tryout, setTryout] = useState<TryoutResult | null>(null);
  const [datasetFile, setDatasetFile] = useState<File | null>(null);
  const [activeEvaluation, setActiveEvaluation] = useState<EvaluationRun | null>(null);
  const [openCase, setOpenCase] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [phase, setPhase] = useState("");
  const [notice, setNotice] = useState<{ tone: "success" | "danger" | "warning"; text: string } | null>(null);
  const stopWatching = useRef<(() => void) | null>(null);

  const load = useCallback(async () => {
    const [skillResult, modelResult, evaluationResult] = await Promise.all([
      api<{ items: RuntimeSkill[] }>("/runtime/skills"),
      api<{ items: ModelConnection[] }>("/models"),
      api<{ items: EvaluationRun[] }>("/evaluations"),
    ]);
    const availableModels = modelResult.items.filter((model) => model.enabled && model.has_api_key);
    setSkills(skillResult.items);
    setModels(availableModels);
    setEvaluations(evaluationResult.items);
    setRoundId((current) => skillResult.items.some((skill) => skill.round_id === current) ? current : skillResult.items[0]?.round_id || "");
    setModelId((current) => availableModels.some((model) => model.id === current) ? current : availableModels[0]?.id || "");
  }, []);

  useEffect(() => {
    void load().catch((error: unknown) => setNotice({ tone: "danger", text: messageOf(error) }));
    return () => stopWatching.current?.();
  }, [load]);

  const selectedSkill = skills.find((skill) => skill.round_id === roundId);
  const selectedHistory = useMemo(
    () => evaluations.filter((evaluation) => evaluation.round_id === roundId),
    [evaluations, roundId],
  );
  const completedHistory = selectedHistory.filter((evaluation) => evaluation.status === "COMPLETED");
  const delta = completedHistory.length >= 2 && completedHistory[0].accuracy !== null && completedHistory[1].accuracy !== null
    ? completedHistory[0].accuracy - completedHistory[1].accuracy
    : null;

  async function runTryout() {
    if (!roundId || !modelId || (!input.trim() && !tryoutFile)) return;
    setBusy(true);
    setTryout(null);
    setNotice(null);
    try {
      const result = tryoutFile
        ? await uploadWithFields<TryoutResult>("/runtime/tryouts/upload", tryoutFile, {
            round_id: roundId,
            model_connection_id: modelId,
            input,
          })
        : await api<TryoutResult>("/runtime/tryouts", {
            method: "POST",
            body: jsonBody({ round_id: roundId, model_connection_id: modelId, input }),
          });
      setTryout(result);
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
    } finally {
      setBusy(false);
    }
  }

  async function refreshEvaluation(evaluationId: string) {
    const [evaluation, history] = await Promise.all([
      api<EvaluationRun>(`/evaluations/${evaluationId}`),
      api<{ items: EvaluationRun[] }>("/evaluations"),
    ]);
    setActiveEvaluation(evaluation);
    setEvaluations(history.items);
  }

  async function startEvaluation() {
    if (!roundId || !modelId) return;
    setBusy(true);
    setProgress(0);
    setPhase("正在创建评测实验");
    setActiveEvaluation(null);
    setNotice(null);
    try {
      const result = datasetFile
        ? await uploadWithFields<{ evaluation: EvaluationRun; job: Job }>("/evaluations/upload", datasetFile, {
            round_id: roundId,
            model_connection_id: modelId,
          })
        : await api<{ evaluation: EvaluationRun; job: Job }>("/evaluations", {
            method: "POST",
            body: jsonBody({ round_id: roundId, model_connection_id: modelId }),
          });
      setActiveEvaluation(result.evaluation);
      stopWatching.current?.();
      stopWatching.current = watchJob(
        result.job.id,
        (event) => {
          setProgress(event.progress);
          setPhase(event.message);
          if (event.status === "COMPLETED") {
            setBusy(false);
            void refreshEvaluation(result.evaluation.id);
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

  async function sendErrorsToFeedback() {
    if (!activeEvaluation) return;
    setBusy(true);
    try {
      const task = await api<{ id: string }>(`/evaluations/${activeEvaluation.id}/feedback`, {
        method: "POST",
        body: jsonBody({ task_type: "CLASSIFICATION" }),
      });
      window.location.hash = `#/feedback/${task.id}`;
    } catch (error) {
      setNotice({ tone: "danger", text: messageOf(error) });
      setBusy(false);
    }
  }

  return (
    <div className="page runtime-page">
      <header className="page-head compact">
        <div>
          <p className="eyebrow">Published Skill runtime</p>
          <h1>智能体<span>运行与评测</span></h1>
          <p>由通用业务智能体承载：选择已发布 Skill 即选择业务场景，模型与实验快照独立记录。</p>
        </div>
      </header>

      {notice && <Notice tone={notice.tone}>{notice.text}<button onClick={() => setNotice(null)}>关闭</button></Notice>}

      {skills.length === 0 ? (
        <EmptyState
          icon="spark"
          title="还没有可运行的已发布 Skill"
          detail="先在场景工作区完成五类资产生成并发布轮次，发布后会自动出现在这里。"
        />
      ) : (
        <>
          <section className="runtime-config" aria-label="运行配置">
            <span className="runtime-agent-mark"><Icon name="model" size={21} /></span>
            <label><span>已发布 Skill</span><select value={roundId} onChange={(event) => { setRoundId(event.target.value); setActiveEvaluation(null); }}>
              {skills.map((skill) => <option key={skill.round_id} value={skill.round_id}>{skill.label} · {formatDate(skill.published_at)}</option>)}
            </select></label>
            <label><span>运行模型</span><select value={modelId} onChange={(event) => setModelId(event.target.value)}>
              {models.length === 0 && <option value="">暂无可用模型</option>}
              {models.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.model_name}</option>)}
            </select></label>
            <div className="runtime-agent-copy"><b>通用业务智能体</b><small>OpenJiuwen · 按请求运行</small></div>
          </section>

          <div className="page-tabs runtime-tabs">
            <button className={tab === "tryout" ? "active" : ""} onClick={() => setTab("tryout")}>对话试跑</button>
            <button className={tab === "evaluation" ? "active" : ""} onClick={() => setTab("evaluation")}>真实评测 <span>{selectedHistory.length}</span></button>
          </div>

          {tab === "tryout" ? (
            <section className="tryout-layout">
              <article className="tryout-composer">
                <header><div><h2>快速验证业务判断</h2><p>用于探索和调试，不计入正式评测准确率。</p></div><span>{selectedSkill?.label}</span></header>
                <textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder="描述一个业务案例、问题或需要研判的情况…" />
                <div className="tryout-actions">
                  <label className="button button-ghost">
                    <Icon name="upload" size={15} />{tryoutFile ? tryoutFile.name : "附加资料"}
                    <input type="file" accept=".pdf,.docx,.xlsx,.csv,.tsv,.txt,.md" hidden onChange={(event) => setTryoutFile(event.target.files?.[0] || null)} />
                  </label>
                  {tryoutFile && <button className="clear-file" onClick={() => setTryoutFile(null)}>移除</button>}
                  <Button kind="primary" icon="send" onClick={() => void runTryout()} disabled={busy || !modelId || (!input.trim() && !tryoutFile)}>{busy ? "运行中…" : "发送试跑"}</Button>
                </div>
              </article>
              <article className="tryout-result">
                {!tryout ? <EmptyState icon="model" title="等待一次真实试跑" detail="结果会展示业务结论、命中规则、可审计决策路径和人工复核状态。" /> : (
                  <>
                    <header><div><span>业务结论</span><h2>{tryout.verdict || "已形成结论"}</h2></div><strong>{Math.round(tryout.confidence * 100)}%<small>置信度</small></strong></header>
                    <section><h3>完整回答</h3><p>{tryout.answer}</p></section>
                    <section><h3>判断理由</h3><p>{tryout.reason || "模型未返回补充理由。"}</p></section>
                    <div className="tryout-evidence">
                      <section><h3>命中规则</h3>{tryout.matched_rules.length ? tryout.matched_rules.map((rule) => <span key={rule}>{rule}</span>) : <p>未返回规则编号</p>}</section>
                      <section><h3>决策路径</h3><ol>{tryout.decision_path.map((step) => <li key={step}>{step}</li>)}</ol></section>
                    </div>
                    <footer className={tryout.review_required ? "review-required" : "review-clear"}><Icon name={tryout.review_required ? "warning" : "check"} size={15} />{tryout.review_required ? "建议转人工复核" : "本次未触发人工复核"}</footer>
                  </>
                )}
              </article>
            </section>
          ) : (
            <div className="evaluation-layout">
              <section className="evaluation-run-card">
                <div>
                  <span className="section-index">01</span>
                  <h2>选择测试集并运行</h2>
                  <p>默认使用该发布轮次生成的评测集；也可上传带标准答案的 JSONL、CSV、TSV 或 XLSX。</p>
                </div>
                <div className="dataset-source">
                  <span><Icon name="file" size={17} /></span>
                  <div><b>{datasetFile?.name || selectedSkill?.evaluation_asset?.filename || "暂无评测集"}</b><small>{datasetFile ? "用户上传 · 将冻结到本次实验" : selectedSkill?.evaluation_asset?.synthetic ? "合成评测集 · 待真实运行" : "发布资产"}</small></div>
                  <label className="button button-ghost"><Icon name="upload" size={15} />上传测试集<input type="file" accept=".jsonl,.csv,.tsv,.xlsx" hidden onChange={(event) => setDatasetFile(event.target.files?.[0] || null)} /></label>
                </div>
                {busy && <div className="runtime-progress"><span style={{ width: `${progress}%` }} /><div><b>{phase}</b><em>{progress}%</em></div></div>}
                <Button kind="primary" icon="arrow" onClick={() => void startEvaluation()} disabled={busy || !modelId || (!datasetFile && !selectedSkill?.evaluation_asset)}>{busy ? "评测运行中" : "开始真实评测"}</Button>
              </section>

              <section className="experiment-history">
                <header><div><span className="section-index">02</span><h2>实验历史</h2><p>每次评测保留 Skill、模型、测试集和逐条输出，可跨轮次观察知识改进。</p></div>{delta !== null && <strong className={delta >= 0 ? "delta-up" : "delta-down"}>{delta >= 0 ? "+" : ""}{(delta * 100).toFixed(1)}%</strong>}</header>
                {selectedHistory.length === 0 ? <EmptyState icon="history" title="尚无实验" detail="运行一次真实评测后，这里会出现可审计的实验快照。" /> : (
                  <div className="experiment-list">{selectedHistory.map((evaluation) => (
                    <button key={evaluation.id} className={activeEvaluation?.id === evaluation.id ? "active" : ""} onClick={() => void refreshEvaluation(evaluation.id)}>
                      <span className="experiment-id">#{evaluation.id.slice(0, 6)}</span><b>{evaluation.dataset_name}</b><small>{evaluation.dataset_kind === "GENERATED" ? "发布评测集" : "上传评测集"} · {formatDate(evaluation.created_at)}</small><StatusBadge status={evaluation.status} />
                      <strong>{evaluation.accuracy === null ? "—" : `${Math.round(evaluation.accuracy * 1000) / 10}%`}<small>{evaluation.correct_count}/{evaluation.sample_count}</small></strong>
                    </button>
                  ))}</div>
                )}
              </section>

              {activeEvaluation?.status === "COMPLETED" && <EvaluationReport evaluation={activeEvaluation} openCase={openCase} onOpenCase={setOpenCase} onFeedback={() => void sendErrorsToFeedback()} busy={busy} />}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function EvaluationReport({ evaluation, openCase, onOpenCase, onFeedback, busy }: { evaluation: EvaluationRun; openCase: string | null; onOpenCase: (id: string | null) => void; onFeedback: () => void; busy: boolean }) {
  return (
    <section className="evaluation-report">
      <header><div><span className="section-index">03</span><h2>评测报告</h2><p>{evaluation.dataset_name} · 逐条语义比对标准答案</p></div>{evaluation.wrong_count > 0 && <Button kind="primary" icon="refresh" onClick={onFeedback} disabled={busy}>将 {evaluation.wrong_count} 条错例送去分析</Button>}</header>
      <div className="evaluation-kpis">
        <article className="primary"><strong>{evaluation.accuracy === null ? "—" : `${Math.round(evaluation.accuracy * 1000) / 10}%`}</strong><span>总准确率</span></article>
        <article><strong>{evaluation.sample_count}</strong><span>测试样本</span></article>
        <article><strong>{evaluation.wrong_count}</strong><span>错例</span></article>
        <article><strong>{evaluation.review_count}</strong><span>触发人工复核</span></article>
      </div>
      <div className="evaluation-cases">
        <div className="evaluation-case-head"><span>样本</span><span>标准答案 / 智能体输出</span><span>结果</span></div>
        {evaluation.results.map((item) => {
          const open = openCase === item.id;
          return <article key={item.id} className={!item.correct ? "case-failed" : ""}>
            <button onClick={() => onOpenCase(open ? null : item.id)}><span><Icon name="chevron" size={14} />{item.id}</span><div><b>{item.input}</b><small>标准：{item.expected} · 输出：{item.answer}</small></div><em>{item.correct ? "✓ 符合" : "✕ 不符合"}</em></button>
            {open && <div className="evaluation-case-detail"><section><h3>判断理由</h3><p>{item.reason}</p></section><section><h3>命中规则</h3><p>{item.matched_rules.join(" · ") || "未返回规则编号"}</p></section><section><h3>差异说明</h3><p>{item.mismatch_reason || "输出与标准答案一致。"}</p></section></div>}
          </article>;
        })}
      </div>
    </section>
  );
}
