import { CheckCircle, EyeSlash, WarningCircle, XCircle } from "@phosphor-icons/react";

import type { FindingStatus, InspectionResult, Verdict } from "../../api/types";

const verdictLabels: Record<Verdict, string> = {
  pass: "合格",
  fail: "不合格",
  unverifiable: "无法判断",
};

const findingLabels: Record<FindingStatus, string> = {
  pass: "通过",
  fail: "不通过",
  not_visible: "未入镜",
};

function VerdictIcon({ verdict }: { verdict: Verdict }) {
  if (verdict === "pass") return <CheckCircle size={22} weight="fill" />;
  if (verdict === "fail") return <XCircle size={22} weight="fill" />;
  return <WarningCircle size={22} weight="fill" />;
}

export function ResultPanel({ result }: { result: InspectionResult }) {
  return (
    <section className="result-panel" aria-label="结构化巡检结果">
      <div className={`result-summary verdict-${result.verdict}`}>
        <VerdictIcon verdict={result.verdict} />
        <div>
          <span>{verdictLabels[result.verdict]}</span>
          <strong>{result.summary}</strong>
        </div>
      </div>
      {result.unverifiable_reason && (
        <p className="unverifiable-reason">{result.unverifiable_reason}</p>
      )}
      <div className="finding-list">
        {result.findings.map((finding) => (
          <article className="finding-row" key={finding.rule_id}>
            <div className="finding-title">
              {finding.status === "not_visible" && <EyeSlash size={17} aria-hidden="true" />}
              <strong>{finding.rule_name}</strong>
              <span className={`status-badge finding-${finding.status}`}>
                {findingLabels[finding.status]}
              </span>
            </div>
            <p>{finding.observation}</p>
            <dl>
              <div>
                <dt>视觉证据</dt>
                <dd>{finding.visual_evidence}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
    </section>
  );
}
