import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";

import { DashboardPage } from "./DashboardPage";
import { FeedbackPage } from "./FeedbackPage";
import { ModelsPage } from "./ModelsPage";
import { RunEvaluationPage } from "./RunEvaluationPage";
import { SceneWizard } from "./SceneWizard";
import { SettingsPage } from "./SettingsPage";
import { UsersPage } from "./UsersPage";
import { Button, Icon, type IconName } from "./components";

interface LocalSession {
  displayName: string;
  createdAt: string;
}

type Route =
  | { page: "dashboard" }
  | { page: "scene"; sceneId: string }
  | { page: "evaluation" }
  | { page: "feedback"; taskId?: string }
  | { page: "settings" }
  | { page: "models" }
  | { page: "users" };

const sessionKey = "knowledge-workbench-local-session-v1";

function parseRoute(): Route {
  const value = window.location.hash.replace(/^#\/?/, "");
  const parts = value.split("/").filter(Boolean);
  if (parts[0] === "scenes" && parts[1]) return { page: "scene", sceneId: parts[1] };
  if (parts[0] === "evaluation") return { page: "evaluation" };
  if (parts[0] === "feedback") return { page: "feedback", taskId: parts[1] };
  if (parts[0] === "settings") return { page: "settings" };
  if (parts[0] === "models") return { page: "models" };
  if (parts[0] === "users") return { page: "users" };
  return { page: "dashboard" };
}

function readSession(): LocalSession | null {
  try {
    const value = localStorage.getItem(sessionKey);
    return value ? JSON.parse(value) as LocalSession : null;
  } catch {
    return null;
  }
}

export default function App() {
  const [session, setSession] = useState<LocalSession | null>(readSession);
  const [route, setRoute] = useState<Route>(parseRoute);

  useEffect(() => {
    const listener = () => setRoute(parseRoute());
    window.addEventListener("hashchange", listener);
    if (!window.location.hash) window.location.hash = "#/";
    return () => window.removeEventListener("hashchange", listener);
  }, []);

  function login(displayName: string) {
    const next = { displayName, createdAt: new Date().toISOString() };
    localStorage.setItem(sessionKey, JSON.stringify(next));
    setSession(next);
  }

  function logout() {
    localStorage.removeItem(sessionKey);
    setSession(null);
  }

  if (!session) return <LocalLogin onLogin={login} />;

  return (
    <Shell route={route} session={session} onLogout={logout}>
      {route.page === "dashboard" && <DashboardPage onOpenScene={(sceneId) => { window.location.hash = `#/scenes/${sceneId}`; }} />}
      {route.page === "scene" && <SceneWizard sceneId={route.sceneId} onBack={() => { window.location.hash = "#/"; }} />}
      {route.page === "evaluation" && <RunEvaluationPage />}
      {route.page === "feedback" && <FeedbackPage initialTaskId={route.taskId} />}
      {route.page === "settings" && <SettingsPage />}
      {route.page === "models" && <ModelsPage />}
      {route.page === "users" && <UsersPage />}
    </Shell>
  );
}

function LocalLogin({ onLogin }: { onLogin: (displayName: string) => void }) {
  const [name, setName] = useState("本机用户");

  function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim()) onLogin(name.trim());
  }

  return (
    <main className="login-page">
      <section className="login-visual">
        <div className="login-brand"><span className="logo-mark"><Icon name="brain" size={24} /></span><div><b>openJiuwen</b><span>Knowledge Workbench</span></div></div>
        <div className="evidence-map" aria-hidden="true">
          <span className="map-node source">素材证据<i /></span>
          <span className="map-path path-one" />
          <span className="map-node rules">规则与流程<i /></span>
          <span className="map-path path-two" />
          <span className="map-node review">对齐修订<i /></span>
          <span className="map-path path-three" />
          <span className="map-node assets">五类资产<i /></span>
          <div className="map-core"><Icon name="brain" size={30} /><b>知识萃取</b><small>可追溯 · 可修订 · 可发布</small></div>
        </div>
        <div className="login-proof"><span>01</span><p><b>证据可追溯</b>每条规则保留素材与片段位置</p><span>02</span><p><b>运行可复现</b>模型、Skill、参数与哈希按 Job 冻结</p><span>03</span><p><b>发布不可变</b>新变化进入下一萃取轮次</p></div>
      </section>
      <section className="login-panel">
        <form onSubmit={submit}>
          <span className="local-pill"><i />本机演示模式</span>
          <h1>进入知识萃取工作台</h1>
          <p>使用本机显示名称开始。这里没有后端账号、密码或权限校验。</p>
          <label>显示名称<input autoFocus value={name} onChange={(event) => setName(event.target.value)} /></label>
          <Button kind="primary" icon="arrow" type="submit" disabled={!name.trim()}>进入本机工作台</Button>
          <div className="login-warning"><Icon name="warning" size={15} />登录状态只保存在当前浏览器 localStorage，不能作为安全认证。</div>
        </form>
      </section>
    </main>
  );
}

function Shell({ route, session, onLogout, children }: { route: Route; session: LocalSession; onLogout: () => void; children: ReactNode }) {
  const crumb = useMemo(() => {
    if (route.page === "scene") return ["工作台", "场景工作区"];
    return { dashboard: ["工作台"], evaluation: ["智能体运行与评测"], feedback: ["错例分析与回流"], settings: ["智能体与 Skill"], models: ["模型接入"], users: ["用户管理"] }[route.page];
  }, [route]);

  const nav: Array<{ page: Route["page"]; label: string; icon: IconName; hash: string }> = [
    { page: "dashboard", label: "工作台", icon: "grid", hash: "#/" },
    { page: "evaluation", label: "运行与评测", icon: "trend", hash: "#/evaluation" },
    { page: "feedback", label: "错例分析与回流", icon: "refresh", hash: "#/feedback" },
    { page: "settings", label: "智能体与 Skill", icon: "settings", hash: "#/settings" },
    { page: "models", label: "模型接入", icon: "model", hash: "#/models" },
    { page: "users", label: "用户管理", icon: "users", hash: "#/users" },
  ];

  return (
    <div className="app-shell">
      <aside className="rail" aria-label="主导航">
        <a className="logo-mark" href="#/" aria-label="知识萃取工作台"><Icon name="brain" size={22} /></a>
        {nav.slice(0, 1).map((item) => <RailLink key={item.page} item={item} active={route.page === item.page || route.page === "scene"} />)}
        <span className="rail-divider" />
        {nav.slice(1).map((item) => <RailLink key={item.page} item={item} active={route.page === item.page} />)}
        <span className="rail-spacer" />
        <button className="rail-avatar" title="退出本机演示" onClick={onLogout}>{session.displayName.slice(0, 1)}</button>
      </aside>
      <section className="shell-main">
        <header className="topbar">
          <div className="brand"><strong>知识萃取智能体工作台</strong><span>OPENJIUWEN</span></div>
          <div className="crumbs">{crumb.map((item, index) => <span key={item}>{index > 0 && <Icon name="chevron" size={12} />}<b className={index === crumb.length - 1 ? "current" : ""}>{item}</b></span>)}</div>
          <div className="topbar-right"><span className="demo-badge"><i />本机演示</span><span className="top-avatar">{session.displayName.slice(0, 1)}</span><div><b>{session.displayName}</b><small>本地会话</small></div><button className="logout-button" onClick={onLogout}>退出</button></div>
        </header>
        <div className="shell-content">{children}</div>
      </section>
    </div>
  );
}

function RailLink({ item, active }: { item: { label: string; icon: IconName; hash: string }; active: boolean }) {
  return <a href={item.hash} title={item.label} className={`rail-button ${active ? "active" : ""}`}><Icon name={item.icon} size={21} /><span>{item.label}</span></a>;
}
