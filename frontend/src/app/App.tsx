import {
  ClockCounterClockwise,
  GearSix,
  ImagesSquare,
  ListChecks,
} from "@phosphor-icons/react";
import { NavLink, Outlet } from "react-router-dom";

const navigation = [
  { to: "/tasks/new", label: "运行任务", icon: ImagesSquare },
  { to: "/templates", label: "提示词模板", icon: ListChecks },
  { to: "/history", label: "历史记录", icon: ClockCounterClockwise },
  { to: "/settings", label: "设置", icon: GearSix },
];

export function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">S</span>
          <div>
            <strong>Seed 巡检台</strong>
            <span>可见证据工作流</span>
          </div>
        </div>
        <nav aria-label="主导航">
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) => `nav-link${isActive ? " is-active" : ""}`}
            >
              <Icon size={20} weight="bold" aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <p className="sidebar-note">图片发送至火山方舟，报告缩略图发送至飞书。</p>
      </aside>
      <div className="app-content">
        <Outlet />
      </div>
    </div>
  );
}
