import { FileArrowUp, Play, X } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type DragEvent, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../../api/client";
import type { UploadSummary } from "../../api/types";
import { ImageDetail } from "./ImageDetail";
import { ImageGallery, imageDisplayStatus, type ImageFilter } from "./ImageGallery";
import { TaskToolbar } from "./TaskToolbar";
import { useTaskEvents } from "./useTaskEvents";

function apiErrorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

function chunk<T>(items: T[], size: number): T[][] {
  return Array.from({ length: Math.ceil(items.length / size) }, (_, index) =>
    items.slice(index * size, (index + 1) * size),
  );
}

function emptyUploadSummary(): UploadSummary {
  return { accepted: [], skipped: [], rejected: [] };
}

function mergeUploadSummary(target: UploadSummary, source: UploadSummary): void {
  target.accepted = target.accepted.concat(source.accepted);
  target.skipped = target.skipped.concat(source.skipped);
  target.rejected = target.rejected.concat(source.rejected);
}

async function uploadInChunks(taskId: string, files: File[]) {
  const summary = emptyUploadSummary();
  for (const batch of chunk(files, 10)) {
    mergeUploadSummary(summary, await api.uploadTaskImages(taskId, batch));
  }
  return summary;
}

function TaskWorkspace({ taskId }: { taskId: string }) {
  const queryClient = useQueryClient();
  const taskQuery = useQuery({ queryKey: ["task", taskId], queryFn: () => api.getTask(taskId) });
  const [selectedImageId, setSelectedImageId] = useState<string | null>(null);
  const [filter, setFilter] = useState<ImageFilter>("all");
  const [uploadSummary, setUploadSummary] = useState<UploadSummary | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [confirmReport, setConfirmReport] = useState(false);

  useTaskEvents(taskId);

  const task = taskQuery.data;
  useEffect(() => {
    if (!task) return;
    setSelectedImageId((current) => {
      if (current && task.images.some((image) => image.id === current)) return current;
      return task.images[0]?.id ?? null;
    });
  }, [task]);

  const action = useMutation({
    mutationFn: (value: "start" | "pause" | "resume" | "cancel") =>
      api.taskAction(taskId, value),
    onSuccess: async () => {
      setUploadError(null);
      await queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    },
    onError: (error) => setUploadError(apiErrorMessage(error, "任务操作失败")),
  });

  const retry = useMutation({
    mutationFn: (imageId: string) => api.retryImage(taskId, imageId),
    onSuccess: async () => {
      setUploadError(null);
      await queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    },
    onError: (error) => setUploadError(apiErrorMessage(error, "图片重试失败")),
  });

  const report = useMutation({
    mutationFn: (includeFailures: boolean) => api.generateReport(taskId, includeFailures),
    onSuccess: async () => {
      setConfirmReport(false);
      setActionMessage("报告生成请求已提交");
      await queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    },
    onError: (error) => setUploadError(apiErrorMessage(error, "报告生成失败")),
  });

  async function uploadFiles(files: File[]) {
    if (!task || files.length === 0) return;
    if (files.length + task.images.length > 100) {
      setUploadError("单个任务最多 100 张图片");
      return;
    }
    setUploading(true);
    setUploadError(null);
    try {
      const summary = await uploadInChunks(taskId, files);
      setUploadSummary(summary);
      await queryClient.invalidateQueries({ queryKey: ["task", taskId] });
    } catch (error) {
      setUploadError(apiErrorMessage(error, "图片上传失败"));
    } finally {
      setUploading(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    if (task?.status === "draft") void uploadFiles(Array.from(event.dataTransfer.files));
  }

  const visibleImages = useMemo(() => {
    if (!task) return [];
    return filter === "all"
      ? task.images
      : task.images.filter((image) => imageDisplayStatus(image) === filter);
  }, [filter, task]);

  function changeFilter(next: ImageFilter) {
    setFilter(next);
    if (!task) return;
    const nextVisible = next === "all"
      ? task.images
      : task.images.filter((image) => imageDisplayStatus(image) === next);
    if (!nextVisible.some((image) => image.id === selectedImageId)) {
      setSelectedImageId(nextVisible[0]?.id ?? null);
    }
  }

  if (taskQuery.isLoading) {
    return <main className="page"><div className="skeleton task-page-skeleton" /></main>;
  }
  if (!task) {
    return (
      <main className="page">
        <p className="notice error-notice" role="alert">
          {apiErrorMessage(taskQuery.error, "任务加载失败")}
        </p>
      </main>
    );
  }

  const selectedImage = task.images.find((image) => image.id === selectedImageId) ?? null;
  const busy = action.isPending || report.isPending || uploading;

  return (
    <main
      className="page task-page"
      onDragOver={(event) => event.preventDefault()}
      onDrop={handleDrop}
    >
      <TaskToolbar
        task={task}
        busy={busy}
        uploadSummary={uploadSummary}
        uploadError={uploadError}
        actionMessage={actionMessage}
        onFiles={(files) => void uploadFiles(files)}
        onAction={(value) => action.mutate(value)}
        onReport={() => {
          if (task.counts.failed > 0) setConfirmReport(true);
          else report.mutate(false);
        }}
      />

      <section className="task-workspace">
        <ImageGallery
          images={task.images}
          filter={filter}
          selectedId={selectedImageId}
          onFilterChange={changeFilter}
          onSelect={setSelectedImageId}
        />
        <ImageDetail
          image={selectedImage}
          retrying={retry.isPending}
          onRetry={(imageId) => retry.mutate(imageId)}
        />
      </section>

      {filter !== "all" && visibleImages.length === 0 && <span className="visually-hidden">筛选结果为空</span>}

      {confirmReport && (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-card confirm-card" role="dialog" aria-modal="true" aria-label="确认生成报告">
            <div className="modal-heading">
              <div>
                <p className="section-label">需要确认</p>
                <h2>报告将包含执行失败项</h2>
              </div>
              <button type="button" aria-label="关闭" onClick={() => setConfirmReport(false)}>
                <X size={20} />
              </button>
            </div>
            <p>当前有 {task.counts.failed} 张图片未获得模型结果。报告会明确标记这些图片，已完成结果不会被改变。</p>
            <div className="confirm-actions">
              <button type="button" className="button quiet-button" onClick={() => setConfirmReport(false)}>取消</button>
              <button
                type="button"
                className="button primary-button"
                disabled={report.isPending}
                onClick={() => report.mutate(true)}
              >
                确认生成
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

function NewTaskPage() {
  const navigate = useNavigate();
  const templates = useQuery({ queryKey: ["templates"], queryFn: api.listTemplates });
  const [name, setName] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!templateId && templates.data?.length) setTemplateId(templates.data[0].id);
  }, [templateId, templates.data]);

  const createAndStart = useMutation({
    mutationFn: async () => {
      if (!name.trim()) throw new Error("请输入任务名称");
      if (!templateId) throw new Error("请选择提示词模板");
      if (files.length === 0) throw new Error("请至少选择一张图片");
      if (files.length > 100) throw new Error("单个任务最多 100 张图片");
      const created = await api.createTask(name.trim(), templateId);
      await uploadInChunks(created.id, files);
      await api.taskAction(created.id, "start");
      return created.id;
    },
    onSuccess: (id) => navigate(`/tasks/${id}`),
    onError: (caught) => setError(apiErrorMessage(caught, caught instanceof Error ? caught.message : "任务创建失败")),
  });

  function chooseFiles(nextFiles: File[]) {
    if (nextFiles.length > 100) {
      setFiles([]);
      setError("单个任务最多 100 张图片");
      return;
    }
    setFiles(nextFiles);
    setError(null);
  }

  return (
    <main className="page new-task-page">
      <header className="page-header compact-header">
        <p className="section-label">Seed Vision</p>
        <h1>运行巡检</h1>
        <p>创建一个任务，导入 1 到 100 张图片。每张图片独立调用模型并保留可核对的视觉证据。</p>
      </header>
      <section
        className="new-task-panel"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          chooseFiles(Array.from(event.dataTransfer.files));
        }}
      >
        <div className="new-task-fields">
          <label className="field">
            任务名称
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：8 月 22 日门店仪容巡检" />
          </label>
          <label className="field">
            提示词模板
            <select value={templateId} onChange={(event) => setTemplateId(event.target.value)}>
              {templates.data?.map((template) => (
                <option key={template.id} value={template.id}>{template.name} · v{template.version}</option>
              ))}
            </select>
          </label>
        </div>
        <label className="upload-dropzone">
          <FileArrowUp size={30} weight="bold" aria-hidden="true" />
          <strong>{files.length ? `已选择 ${files.length} 张图片` : "选择图片或拖放到此处"}</strong>
          <span>支持 JPEG、PNG、WebP；单个任务最多 100 张</span>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            multiple
            aria-label="上传图片"
            onChange={(event) => chooseFiles(Array.from(event.currentTarget.files ?? []))}
          />
        </label>
        {error && <p className="notice error-notice" role="alert">{error}</p>}
        <div className="new-task-footer">
          <span>实际模型与并发参数会在任务创建时生成快照。</span>
          <button
            type="button"
            className="button primary-button"
            disabled={createAndStart.isPending}
            onClick={() => createAndStart.mutate()}
          >
            <Play size={17} weight="fill" aria-hidden="true" />
            {createAndStart.isPending ? "正在创建" : "开始任务"}
          </button>
        </div>
      </section>
    </main>
  );
}

export function TaskPage({ taskId }: { taskId?: string }) {
  return taskId ? <TaskWorkspace taskId={taskId} /> : <NewTaskPage />;
}

export function TaskRoutePage() {
  const { taskId } = useParams<{ taskId: string }>();
  return <TaskPage taskId={taskId} />;
}
