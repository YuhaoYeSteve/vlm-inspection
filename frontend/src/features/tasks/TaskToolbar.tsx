import {
  FileArrowUp,
  FileText,
  Pause,
  Play,
  Stop,
} from "@phosphor-icons/react";

import type { TaskDetail, TaskStatus, UploadSummary } from "../../api/types";

const taskStatusLabels: Record<TaskStatus, string> = {
  draft: "草稿",
  queued: "排队中",
  running: "运行中",
  paused: "已暂停",
  review_required: "等待确认",
  exporting: "生成报告中",
  completed: "已完成",
  canceled: "已取消",
};

interface TaskToolbarProps {
  task: TaskDetail;
  busy: boolean;
  uploadSummary: UploadSummary | null;
  uploadError: string | null;
  actionMessage: string | null;
  onFiles: (files: File[]) => void;
  onAction: (action: "start" | "pause" | "resume" | "cancel") => void;
  onReport: () => void;
}

export function TaskToolbar({
  task,
  busy,
  uploadSummary,
  uploadError,
  actionMessage,
  onFiles,
  onAction,
  onReport,
}: TaskToolbarProps) {
  const canCancel = ["draft", "queued", "running", "paused", "review_required"].includes(task.status);
  const canReport = task.status === "review_required";

  return (
    <>
      <header className="task-header">
        <div>
          <p className="section-label">批量巡检任务</p>
          <div className="task-title-line">
            <h1>{task.name}</h1>
            <span className={`task-status task-status-${task.status}`}>
              {taskStatusLabels[task.status]}
            </span>
          </div>
          <p>{task.template_name} · v{task.template_version} · {task.model}</p>
        </div>
        <div className="task-actions">
          {task.status === "draft" && (
            <>
              <label className="button quiet-button file-button">
                <FileArrowUp size={17} weight="bold" aria-hidden="true" />
                添加图片
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  multiple
                  aria-label="上传图片"
                  onChange={(event) => {
                    onFiles(Array.from(event.currentTarget.files ?? []));
                    event.currentTarget.value = "";
                  }}
                />
              </label>
              <button
                type="button"
                className="button primary-button"
                disabled={busy || task.images.length === 0}
                onClick={() => onAction("start")}
              >
                <Play size={17} weight="fill" aria-hidden="true" />
                开始任务
              </button>
            </>
          )}
          {(task.status === "queued" || task.status === "running") && (
            <button
              type="button"
              className="button quiet-button"
              disabled={busy}
              onClick={() => onAction("pause")}
            >
              <Pause size={17} weight="fill" aria-hidden="true" />
              暂停任务
            </button>
          )}
          {task.status === "paused" && (
            <button
              type="button"
              className="button primary-button"
              disabled={busy}
              onClick={() => onAction("resume")}
            >
              <Play size={17} weight="fill" aria-hidden="true" />
              继续任务
            </button>
          )}
          {canReport && (
            <button
              type="button"
              className="button primary-button"
              disabled={busy}
              onClick={onReport}
            >
              <FileText size={17} weight="bold" aria-hidden="true" />
              {task.counts.failed > 0 ? "带失败项生成报告" : "生成飞书报告"}
            </button>
          )}
          {task.report_url && (
            <a className="button primary-button" href={task.report_url} target="_blank" rel="noreferrer">
              打开飞书文档
            </a>
          )}
          {canCancel && (
            <button
              type="button"
              className="button quiet-button danger-button"
              disabled={busy}
              onClick={() => onAction("cancel")}
            >
              <Stop size={17} weight="fill" aria-hidden="true" />
              取消任务
            </button>
          )}
        </div>
      </header>

      <section className="task-metrics" aria-label="任务统计">
        <div><span>图片</span><strong>{task.images.length}</strong></div>
        <div className="metric-pass"><span>合格</span><strong>{task.counts.pass}</strong></div>
        <div className="metric-fail"><span>不合格</span><strong>{task.counts.fail}</strong></div>
        <div className="metric-warning"><span>无法判断</span><strong>{task.counts.unverifiable}</strong></div>
        <div className="metric-error"><span>执行失败</span><strong>{task.counts.failed}</strong></div>
      </section>

      {uploadSummary && (
        <p className="upload-summary" role="status">
          已接收 {uploadSummary.accepted.length} 张，跳过 {uploadSummary.skipped.length} 张，拒绝 {uploadSummary.rejected.length} 张。
        </p>
      )}
      {uploadError && <p className="notice error-notice" role="alert">{uploadError}</p>}
      {actionMessage && <p className="notice success-notice" role="status">{actionMessage}</p>}
    </>
  );
}
