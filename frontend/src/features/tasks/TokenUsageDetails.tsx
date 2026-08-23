import { useState } from "react";

import type { TokenUsageCall, TokenUsageSummary } from "../../api/types";
import { formatTokens } from "./TokenUsageSummary";

interface TokenUsageDetailsProps {
  imageName: string;
  usage: TokenUsageSummary;
  calls: TokenUsageCall[];
}

const purposeLabels: Record<TokenUsageCall["purpose"], string> = {
  inspection: "初始检查",
  schema_fallback: "Schema 降级",
  json_repair: "JSON 修复",
};

const statusLabels: Record<TokenUsageCall["status"], string> = {
  succeeded: "成功",
  failed: "失败",
};

function emptyMessage(usage: TokenUsageSummary) {
  if (usage.availability === "not_started") return "尚无模型调用";
  if (usage.availability === "untracked") return "该图片运行时尚未采集 Token 用量";
  return "方舟未返回 Token 用量";
}

function callTokens(value: number | null) {
  return value === null ? "—" : formatTokens(value);
}

export function TokenUsageDetails({ imageName, usage, calls }: TokenUsageDetailsProps) {
  const [expanded, setExpanded] = useState(false);
  const known = usage.availability === "complete" || usage.availability === "partial";

  return (
    <section className="image-token-usage" aria-label={`${imageName} Token 用量`}>
      <div className="token-summary-header">
        <h3>本图 Token 用量</h3>
        {calls.length > 0 && (
          <button
            type="button"
            className="token-details-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? "收起调用明细" : "展开调用明细"}
          </button>
        )}
      </div>

      {known ? (
        <>
          <div className="image-token-grid">
            <div>
              <span>{usage.availability === "partial" ? "已知总 Token" : "总 Token"}</span>
              <strong>{formatTokens(usage.total_tokens)}</strong>
            </div>
            <div>
              <span>输入</span>
              <strong>{formatTokens(usage.input_tokens)}</strong>
            </div>
            <div>
              <span>缓存命中</span>
              <strong>{formatTokens(usage.cached_input_tokens)}</strong>
            </div>
            <div>
              <span>输出</span>
              <strong>{formatTokens(usage.output_tokens)}</strong>
            </div>
            <div>
              <span>推理 Token</span>
              <strong>{formatTokens(usage.reasoning_tokens)}</strong>
            </div>
          </div>
          {usage.unknown_call_count > 0 && (
            <p className="token-unknown">
              {usage.unknown_call_count} 次调用用量未知，未计入已知总量。
            </p>
          )}
        </>
      ) : (
        <p className={usage.availability === "unavailable" ? "token-empty token-warning" : "token-empty"}>
          {emptyMessage(usage)}
        </p>
      )}

      {expanded && calls.length > 0 && (
        <div className="token-call-table">
          <table>
            <thead>
              <tr>
                <th scope="col">调用</th>
                <th scope="col">输入</th>
                <th scope="col">缓存</th>
                <th scope="col">输出</th>
                <th scope="col">推理</th>
                <th scope="col">总计</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((call) => (
                <tr key={`${call.attempt_number}-${call.sequence}`}>
                  <th scope="row">
                    <span className="token-call-label">
                      尝试 {call.attempt_number} · {purposeLabels[call.purpose]}
                    </span>
                    <span className={`token-call-status token-call-status-${call.status}`}>
                      {statusLabels[call.status]}
                    </span>
                  </th>
                  {call.total_tokens === null ? (
                    <td colSpan={5} className="token-call-error">
                      {call.usage_error === "invalid_usage" ? "usage 数据异常" : "方舟未返回 usage"}
                    </td>
                  ) : (
                    <>
                      <td>{callTokens(call.input_tokens)}</td>
                      <td>{callTokens(call.cached_input_tokens)}</td>
                      <td>{callTokens(call.output_tokens)}</td>
                      <td>{callTokens(call.reasoning_tokens)}</td>
                      <td>{formatTokens(call.total_tokens)}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
