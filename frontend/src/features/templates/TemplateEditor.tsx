import {
  ArrowDown,
  ArrowUp,
  Eye,
  FloppyDisk,
  Plus,
  Trash,
  UploadSimple,
} from "@phosphor-icons/react";
import type { ChangeEvent } from "react";

import type { TemplateDetail, TemplateRule } from "../../api/types";

export interface TemplateDraft {
  name: string;
  description: string;
  task_prompt: string;
  rules: TemplateRule[];
  model_params: { temperature: number; max_output_tokens: number };
}

interface TemplateEditorProps {
  template: TemplateDetail;
  draft: TemplateDraft;
  onChange: (draft: TemplateDraft) => void;
  onSave: () => void;
  onPreview: () => void;
  onTestImage: (file: File) => void;
  onRunTest: () => void;
  saving: boolean;
  testing: boolean;
  validationError: string | null;
}

export function TemplateEditor({
  template,
  draft,
  onChange,
  onSave,
  onPreview,
  onTestImage,
  onRunTest,
  saving,
  testing,
  validationError,
}: TemplateEditorProps) {
  function updateRule(index: number, patch: Partial<TemplateRule>) {
    onChange({
      ...draft,
      rules: draft.rules.map((rule, ruleIndex) =>
        ruleIndex === index ? { ...rule, ...patch } : rule,
      ),
    });
  }

  function moveRule(index: number, offset: -1 | 1) {
    const target = index + offset;
    if (target < 0 || target >= draft.rules.length) return;
    const rules = [...draft.rules];
    [rules[index], rules[target]] = [rules[target], rules[index]];
    onChange({ ...draft, rules });
  }

  function chooseImage(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) onTestImage(file);
  }

  return (
    <section className="template-editor" aria-labelledby="template-editor-heading">
      <div className="editor-heading">
        <div>
          <p className="section-label">模板编辑</p>
          <h2 id="template-editor-heading">{template.name}</h2>
        </div>
        <span className="version-label">当前版本 v{template.version}</span>
      </div>

      <div className="policy-panel">
        <strong>固定证据政策</strong>
        <p>{template.system_prompt}</p>
      </div>

      <div className="field-grid editor-fields">
        <label className="field">
          <span>模板名称</span>
          <input value={draft.name} onChange={(event) => onChange({ ...draft, name: event.target.value })} />
        </label>
        <label className="field">
          <span>说明</span>
          <input
            value={draft.description}
            onChange={(event) => onChange({ ...draft, description: event.target.value })}
          />
        </label>
        <label className="field field-wide">
          <span>任务补充说明</span>
          <textarea
            rows={3}
            value={draft.task_prompt}
            onChange={(event) => onChange({ ...draft, task_prompt: event.target.value })}
          />
        </label>
      </div>

      <div className="rules-heading">
        <div>
          <h3>检查规则</h3>
          <span>{draft.rules.length} 项</span>
        </div>
        <button
          className="button quiet-button"
          type="button"
          onClick={() =>
            onChange({
              ...draft,
              rules: [...draft.rules, { rule_id: "", name: "", text: "" }],
            })
          }
        >
          <Plus size={16} weight="bold" /> 添加规则
        </button>
      </div>

      <div className="rule-list">
        {draft.rules.map((rule, index) => (
          <article className="rule-row" data-testid="rule-row" key={index}>
            <span className="rule-index">{String(index + 1).padStart(2, "0")}</span>
            <div className="rule-fields">
              <label className="field">
                <span>规则 ID</span>
                <input
                  value={rule.rule_id}
                  onChange={(event) => updateRule(index, { rule_id: event.target.value })}
                />
              </label>
              <label className="field">
                <span>规则名称</span>
                <input value={rule.name} onChange={(event) => updateRule(index, { name: event.target.value })} />
              </label>
              <label className="field field-wide">
                <span>检查要求</span>
                <textarea
                  rows={2}
                  value={rule.text}
                  onChange={(event) => updateRule(index, { text: event.target.value })}
                />
              </label>
            </div>
            <div className="rule-actions">
              <button
                type="button"
                aria-label="上移规则"
                disabled={index === 0}
                onClick={() => moveRule(index, -1)}
              >
                <ArrowUp size={16} />
              </button>
              <button
                type="button"
                aria-label="下移规则"
                disabled={index === draft.rules.length - 1}
                onClick={() => moveRule(index, 1)}
              >
                <ArrowDown size={16} />
              </button>
              <button
                type="button"
                aria-label="删除规则"
                onClick={() =>
                  onChange({ ...draft, rules: draft.rules.filter((_, itemIndex) => itemIndex !== index) })
                }
              >
                <Trash size={16} />
              </button>
            </div>
          </article>
        ))}
      </div>

      {validationError && <p className="inline-error" role="alert">{validationError}</p>}

      <div className="editor-footer">
        <div className="template-test-control">
          <label className="button quiet-button file-button">
            <UploadSimple size={17} /> 选择单张测试图片
            <input type="file" accept="image/jpeg,image/png,image/webp" onChange={chooseImage} />
          </label>
          <button className="button quiet-button" type="button" disabled={testing} onClick={onRunTest}>
            {testing ? "正在检查..." : "运行单图测试"}
          </button>
        </div>
        <div className="editor-primary-actions">
          <button className="button quiet-button" type="button" onClick={onPreview}>
            <Eye size={17} /> 预览最终提示词
          </button>
          <button className="button primary-button" type="button" disabled={saving} onClick={onSave}>
            <FloppyDisk size={17} weight="bold" /> {saving ? "保存中..." : "保存新版本"}
          </button>
        </div>
      </div>
    </section>
  );
}
