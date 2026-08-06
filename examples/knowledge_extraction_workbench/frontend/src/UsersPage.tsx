import { useEffect, useState } from "react";

import { Button, EmptyState, Icon, Modal, Notice, StatusBadge, formatDate } from "./components";

interface LocalUser {
  id: string;
  name: string;
  email: string;
  role: string;
  enabled: boolean;
  lastLogin: string | null;
}

const storageKey = "knowledge-workbench-demo-users-v1";
const initialUsers: LocalUser[] = [
  { id: "local-owner", name: "本机用户", email: "local@workbench.demo", role: "工作台所有者", enabled: true, lastLogin: new Date().toISOString() },
  { id: "local-reviewer", name: "业务复核员", email: "reviewer@workbench.demo", role: "知识复核", enabled: true, lastLogin: null },
];

function readUsers(): LocalUser[] {
  try {
    const saved = localStorage.getItem(storageKey);
    return saved ? JSON.parse(saved) as LocalUser[] : initialUsers;
  } catch {
    return initialUsers;
  }
}

export function UsersPage() {
  const [users, setUsers] = useState<LocalUser[]>(readUsers);
  const [adding, setAdding] = useState(false);

  useEffect(() => localStorage.setItem(storageKey, JSON.stringify(users)), [users]);

  return (
    <div className="page users-page">
      <header className="page-head compact"><div><p className="eyebrow">Local demonstration</p><h1>用户<span>管理</span></h1><p>保留原型中的协作外观，仅用于演示角色与界面状态。</p></div><Button kind="primary" icon="plus" onClick={() => setAdding(true)}>添加演示用户</Button></header>
      <Notice tone="warning"><strong>本机演示模式，不是安全认证。</strong> 登录状态、用户列表和启用状态仅写入当前浏览器 localStorage；后端不识别这些账号，也不形成权限边界。</Notice>
      {users.length === 0 ? <EmptyState icon="users" title="没有演示用户" detail="添加一个本机演示角色，仅用于检查页面交互。" /> : (
        <section className="user-list">
          <div className="user-row user-header"><span>用户</span><span>演示角色</span><span>最近登录</span><span>状态</span></div>
          {users.map((user) => <article className="user-row" key={user.id}><div><span className="user-avatar">{user.name.slice(0, 1)}</span><div><h3>{user.name}</h3><p>{user.email}</p></div></div><span>{user.role}</span><time>{user.lastLogin ? formatDate(user.lastLogin) : "从未登录"}</time><div><StatusBadge status={user.enabled ? "ENABLED" : "DISABLED"} /><button className={`switch ${user.enabled ? "on" : ""}`} onClick={() => setUsers((current) => current.map((item) => item.id === user.id ? { ...item, enabled: !item.enabled } : item))} aria-label={`${user.enabled ? "停用" : "启用"}${user.name}`}><i /></button></div></article>)}
        </section>
      )}
      <AddUserModal open={adding} onClose={() => setAdding(false)} onAdd={(user) => { setUsers((current) => [...current, user]); setAdding(false); }} />
    </div>
  );
}

function AddUserModal({ open, onClose, onAdd }: { open: boolean; onClose: () => void; onAdd: (user: LocalUser) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("知识复核");

  function add() {
    onAdd({ id: crypto.randomUUID(), name: name.trim(), email: email.trim(), role, enabled: true, lastLogin: null });
    setName("");
    setEmail("");
    setRole("知识复核");
  }

  return (
    <Modal open={open} title="添加演示用户" subtitle="仅写入浏览器 localStorage，不创建后端账号。" onClose={onClose} footer={<><Button onClick={onClose}>取消</Button><Button kind="primary" onClick={add} disabled={!name.trim() || !email.trim()}>添加到演示列表</Button></>}>
      <div className="form-stack"><Notice tone="warning">此操作不授予任何真实权限。</Notice><label>显示名称<span>*</span><input value={name} onChange={(event) => setName(event.target.value)} /></label><label>演示邮箱<span>*</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label><label>演示角色<select value={role} onChange={(event) => setRole(event.target.value)}><option>知识复核</option><option>场景设计</option><option>素材维护</option><option>只读观察</option></select></label></div>
    </Modal>
  );
}
