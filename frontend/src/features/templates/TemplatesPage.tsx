import { X } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { InspectionResult, TemplateDetail } from "../../api/types";
import { ResultPanel } from "../tasks/ResultPanel";
import { TemplateEditor, type TemplateDraft } from "./TemplateEditor";

function toDraft(template: TemplateDetail): TemplateDraft {
  return {
    name: template.name,
    description: template.description,
    task_prompt: template.task_prompt,
    rules: template.rules.map((rule) => ({ ...rule })),
    model_params: { ...template.model_params },
  };
}

function validateDraft(draft: TemplateDraft): string | null {
  const ids = draft.rules.map((rule) => rule.rule_id);
  if (new Set(ids).size !== ids.length) return "规则 ID 必须唯一";
  if (ids.some((id) => !/^[a-z][a-z0-9_]{1,63}$/.test(id))) {
    return "规则 ID 必须以小写字母开头，只能包含小写字母、数字和下划线";
  }
  if (draft.rules.some((rule) => !rule.name.trim() || !rule.text.trim())) {
    return "每条规则都需要名称和检查要求";
  }
  return null;
}

export function TemplatesPage() {
  const queryClient = useQueryClient();
  const templates = useQuery({ queryKey: ["templates"], queryFn: api.listTemplates });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<TemplateDraft | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [testImage, setTestImage] = useState<File | null>(null);
  const [testPreviewUrl, setTestPreviewUrl] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<InspectionResult | null>(null);

  useEffect(() => {
    if (!selectedId && templates.data?.length) setSelectedId(templates.data[0].id);
  }, [selectedId, templates.data]);

  const selected = useQuery({
    queryKey: ["template", selectedId],
    queryFn: () => api.getTemplate(selectedId as string),
    enabled: selectedId !== null,
  });

  useEffect(() => {
    if (selected.data) setDraft(toDraft(selected.data));
  }, [selected.data]);

  useEffect(() => {
    if (!testImage || typeof URL.createObjectURL !== "function") {
      setTestPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(testImage);
    setTestPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [testImage]);

  const save = useMutation({
    mutationFn: (value: TemplateDraft) => api.updateTemplate(selectedId as string, value),
    onSuccess: (updated) => {
      queryClient.setQueryData(["template", updated.id], updated);
      setDraft(toDraft(updated));
      setValidationError(null);
      void queryClient.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (error) =>
      setValidationError(error instanceof ApiError ? error.message : "模板保存失败"),
  });

  const testRun = useMutation({
    mutationFn: () => api.testTemplate(selectedId as string, testImage as File),
    onSuccess: setTestResult,
    onError: (error) =>
      setValidationError(error instanceof ApiError ? error.message : "单图测试失败"),
  });

  const preview = useMemo(() => {
    if (!selected.data || !draft) return null;
    return {
      system: selected.data.system_prompt,
      user: `任务说明：${draft.task_prompt}\n检查规则：\n${JSON.stringify(draft.rules, null, 2)}`,
    };
  }, [draft, selected.data]);

  function submitDraft() {
    if (!draft) return;
    const error = validateDraft(draft);
    setValidationError(error);
    if (!error) save.mutate(draft);
  }

  return (
    <main className="page templates-page">
      <header className="page-header compact-header">
        <p className="section-label">规则与版本</p>
        <h1>提示词模板</h1>
        <p>固定证据边界，按岗位维护规则。每次保存都会创建不可变的新版本。</p>
      </header>

      <div className="template-workspace">
        <aside className="template-list" aria-label="模板列表">
          <div className="list-heading">
            <span>模板</span>
            <strong>{templates.data?.length ?? 0}</strong>
          </div>
          {templates.isLoading && <div className="skeleton list-skeleton" />}
          {templates.data?.map((template) => (
            <button
              type="button"
              key={template.id}
              aria-label={template.name}
              className={`template-list-item${selectedId === template.id ? " is-selected" : ""}`}
              onClick={() => setSelectedId(template.id)}
            >
              <span>{template.name}</span>
              <small>v{template.version}</small>
            </button>
          ))}
        </aside>

        {selected.data && draft ? (
          <TemplateEditor
            template={selected.data}
            draft={draft}
            onChange={setDraft}
            onSave={submitDraft}
            onPreview={() => setPreviewOpen(true)}
            onTestImage={setTestImage}
            onRunTest={() => {
              if (!testImage) {
                setValidationError("请先选择一张测试图片");
                return;
              }
              testRun.mutate();
            }}
            saving={save.isPending}
            testing={testRun.isPending}
            validationError={validationError}
          />
        ) : (
          <section className="template-editor skeleton editor-skeleton" aria-label="正在加载模板" />
        )}
      </div>

      {previewOpen && preview && (
        <div className="modal-backdrop" role="presentation">
          <section className="modal-card prompt-modal" role="dialog" aria-modal="true" aria-label="最终提示词预览">
            <div className="modal-heading">
              <h2>最终提示词预览</h2>
              <button type="button" aria-label="关闭" onClick={() => setPreviewOpen(false)}>
                <X size={20} />
              </button>
            </div>
            <h3>系统证据政策</h3>
            <pre>{preview.system}</pre>
            <h3>任务提示词</h3>
            <pre>{preview.user}</pre>
          </section>
        </div>
      )}

      {testResult && (
        <div className="drawer-backdrop" role="presentation">
          <aside className="result-drawer" aria-label="单图测试结果">
            <div className="modal-heading">
              <div>
                <p className="section-label">单图测试</p>
                <h2>结构化结果</h2>
              </div>
              <button type="button" aria-label="关闭测试结果" onClick={() => setTestResult(null)}>
                <X size={20} />
              </button>
            </div>
            {testImage && testPreviewUrl && (
              <img className="test-preview" src={testPreviewUrl} alt={`${testImage.name} 预览`} />
            )}
            <ResultPanel result={testResult} />
          </aside>
        </div>
      )}
    </main>
  );
}
