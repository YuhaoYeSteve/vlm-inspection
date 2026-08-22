import { Navigate, createBrowserRouter } from "react-router-dom";

import { HistoryPage } from "../features/history/HistoryPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { TaskPage, TaskRoutePage } from "../features/tasks/TaskPage";
import { TemplatesPage } from "../features/templates/TemplatesPage";
import { App } from "./App";

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
        element: <HistoryPage />,
      },
      {
        path: "settings",
        element: <SettingsPage />,
      },
    ],
  },
]);
