import { ArrowClockwise, ArrowSquareOut, ClipboardText } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../../api/client";
import type { TaskStatus, TaskSummary } from "../../api/types";

const statusLabels: Record<TaskStatus, string> = {
  draft: "草稿",
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  review_required: "等待确认",
  exporting: "生成报告中",
  completed: "已完成",
  canceled: "已取消",
};

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function ReportCell({ task, retrying, onRetry }: {
  task: TaskSummary;
  retrying: boolean;
  onRetry: () => void;
}) {
  if (task.report_url) {
    return (
      <a
        className="table-link"
        href={task.report_url}
        target="_blank"
        rel="noreferrer"
        aria-label={`打开${task.name}飞书文档`}
      >
        打开文档
        <ArrowSquareOut size={14} weight="bold" aria-hidden="true" />
      </a>
    );
  }
  if (task.report_status === "failed") {
    return (
      <button
        type="button"
        className="table-action"
        aria-label={`重试${task.name}报告`}
        disabled={retrying}
        onClick={onRetry}
      >
        <ArrowClockwise size={14} weight="bold" aria-hidden="true" />
        {retrying ? "提交中" : "重试报告"}
      </button>
    );
  }
  if (task.report_status === "pending" || task.report_status === "running") {
    return <span className="report-state report-running">生成中</span>;
  }
  return <span className="report-state">未生成</span>;
}

export function HistoryPage() {
  const queryClient = useQueryClient();
  const tasks = useQuery({ queryKey: ["tasks"], queryFn: api.listTasks });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sortedTasks = useMemo(
    () => [...(tasks.data ?? [])].sort((left, right) =>
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime()),
    [tasks.data],
  );

  const retryReport = useMutation({
    mutationFn: (task: TaskSummary) => api.generateReport(task.id, task.counts.failed > 0),
    onSuccess: async () => {
      setError(null);
      setMessage("报告重试已提交");
      await queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
    onError: (caught) => {
      setMessage(null);
      setError(caught instanceof ApiError ? caught.message : "报告重试失败");
    },
  });

  return (
    <main className="page history-page">
      <header className="page-header compact-header history-heading">
        <div>
          <p className="section-label">任务档案</p>
          <h1>历史记录</h1>
          <p>回看每次批量巡检的结果分布、执行状态与飞书报告。</p>
        </div>
        <span>{sortedTasks.length} 个任务</span>
      </header>

      {message && <p className="notice success-notice" role="status">{message}</p>}
      {error && <p className="notice error-notice" role="alert">{error}</p>}

      {tasks.isLoading && <div className="skeleton history-skeleton" aria-label="正在加载历史记录" />}
      {tasks.isError && (
        <p className="notice error-notice" role="alert">
          {tasks.error instanceof ApiError ? tasks.error.message : "历史记录加载失败"}
        </p>
      )}
      {!tasks.isLoading && !tasks.isError && sortedTasks.length === 0 && (
        <section className="history-empty">
          <ClipboardText size={30} weight="bold" aria-hidden="true" />
          <h2>还没有巡检记录</h2>
          <p>创建第一个批量任务后，状态与报告会出现在这里。</p>
          <Link className="button primary-button" to="/tasks/new">创建任务</Link>
        </section>
      )}
      {sortedTasks.length > 0 && (
        <div className="history-table-wrap">
          <table className="history-table">
            <thead>
              <tr>
                <th>任务</th>
                <th>模板 / 模型</th>
                <th>状态</th>
                <th>合格 / 不合格 / 无法判断 / 执行失败</th>
                <th>飞书报告</th>
                <th><span className="visually-hidden">操作</span></th>
              </tr>
            </thead>
            <tbody>
              {sortedTasks.map((task) => (
                <tr key={task.id}>
                  <td>
                    <strong>{task.name}</strong>
                    <small>{formatTime(task.created_at)}</small>
                  </td>
                  <td>
                    <span>{task.template_name} · v{task.template_version}</span>
                    <small>{task.model}</small>
                  </td>
                  <td>
                    <span className={`task-status task-status-${task.status}`}>
                      {statusLabels[task.status]}
                    </span>
                  </td>
                  <td>
                    <span className="count-sequence">
                      {task.counts.pass} / {task.counts.fail} / {task.counts.unverifiable} / {task.counts.failed}
                    </span>
                  </td>
                  <td>
                    <ReportCell
                      task={task}
                      retrying={retryReport.isPending && retryReport.variables?.id === task.id}
                      onRetry={() => retryReport.mutate(task)}
                    />
                  </td>
                  <td>
                    <Link
                      className="table-link"
                      to={`/tasks/${task.id}`}
                      aria-label={`查看${task.name}`}
                    >
                      查看任务
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
