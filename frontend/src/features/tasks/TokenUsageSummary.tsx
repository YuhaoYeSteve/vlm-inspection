import type { TokenUsageSummary as TokenUsageSummaryData } from "../../api/types";

const tokenFormatter = new Intl.NumberFormat("zh-CN");

export function formatTokens(value: number) {
  return tokenFormatter.format(value);
}

interface TokenUsageSummaryProps {
  usage: TokenUsageSummaryData;
}

function emptyMessage(usage: TokenUsageSummaryData) {
  if (usage.availability === "not_started") return "尚无模型调用";
  if (usage.availability === "untracked") return "该任务运行时尚未采集 Token 用量";
  return "方舟未返回 Token 用量";
}

export function TokenUsageSummary({ usage }: TokenUsageSummaryProps) {
  const known = usage.availability === "complete" || usage.availability === "partial";

  if (!known) {
    return (
      <section
        className={`token-summary token-empty${usage.availability === "unavailable" ? " token-warning" : ""}`}
        aria-label="任务 Token 用量"
      >
        <p>{emptyMessage(usage)}</p>
      </section>
    );
  }

  const cacheRate = usage.input_tokens === 0
    ? "—"
    : `${((usage.cached_input_tokens / usage.input_tokens) * 100).toFixed(1)}%`;

  return (
    <section className="token-summary" aria-label="任务 Token 用量">
      <div className="token-summary-header">
        <h2>任务 Token 用量</h2>
        {usage.unknown_call_count > 0 && (
          <span className="token-unknown">{usage.unknown_call_count} 次调用用量未知</span>
        )}
      </div>
      <div className="token-summary-grid">
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
          <small>缓存率 {cacheRate}</small>
        </div>
        <div>
          <span>未缓存输入</span>
          <strong>{formatTokens(usage.uncached_input_tokens)}</strong>
        </div>
        <div>
          <span>输出</span>
          <strong>{formatTokens(usage.output_tokens)}</strong>
        </div>
        <div>
          <span>推理 Token</span>
          <strong>{formatTokens(usage.reasoning_tokens)}</strong>
          <small>包含在输出中</small>
        </div>
      </div>
    </section>
  );
}
