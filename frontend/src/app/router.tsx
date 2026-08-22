import { Navigate, createBrowserRouter } from "react-router-dom";

import { SettingsPage } from "../features/settings/SettingsPage";
import { TaskPage, TaskRoutePage } from "../features/tasks/TaskPage";
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
        element: <TaskPage />,
      },
      {
        path: "tasks/:taskId",
        element: <TaskRoutePage />,
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
