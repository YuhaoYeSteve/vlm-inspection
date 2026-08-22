import { Navigate, createBrowserRouter } from "react-router-dom";

import { SettingsPage } from "../features/settings/SettingsPage";
import { TemplatesPage } from "../features/templates/TemplatesPage";
import { App } from "./App";

function Placeholder({ title, description }: { title: string; description: string }) {
  return (
    <main className="page page-placeholder">
      <header className="page-header">
        <p className="section-label">Seed Vision</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </header>
    </main>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Navigate to="/tasks/new" replace /> },
      {
        path: "tasks/new",
        element: <Placeholder title="运行巡检" description="创建任务并导入 1 到 100 张图片。" />,
      },
      {
        path: "tasks/:taskId",
        element: <Placeholder title="任务详情" description="查看进度、图片与逐项证据。" />,
      },
      {
        path: "templates",
        element: <TemplatesPage />,
      },
      {
        path: "history",
        element: <Placeholder title="历史记录" description="回看任务状态与飞书报告。" />,
      },
      {
        path: "settings",
        element: <SettingsPage />,
      },
    ],
  },
]);
