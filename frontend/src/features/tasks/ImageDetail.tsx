import { ArrowClockwise, Clock, SpinnerGap, WarningCircle } from "@phosphor-icons/react";

import type { TaskImage } from "../../api/types";
import { ResultPanel } from "./ResultPanel";
import { TokenUsageDetails } from "./TokenUsageDetails";

interface ImageDetailProps {
  image: TaskImage | null;
  retrying: boolean;
  onRetry: (imageId: string) => void;
}

export function ImageDetail({ image, retrying, onRetry }: ImageDetailProps) {
  if (!image) {
    return (
      <aside className="image-detail image-detail-empty" aria-label="单图证据详情">
        <p>从左侧选择一张图片查看大图与证据。</p>
      </aside>
    );
  }

  return (
    <aside className="image-detail" aria-label={`${image.original_name} 证据详情`}>
      <div className="detail-heading">
        <div>
          <p className="section-label">单图证据</p>
          <h2>{image.original_name}</h2>
        </div>
        <span>尝试 {image.attempt_count} 次</span>
      </div>
      <div className="large-image-frame">
        <img src={image.original_url} alt={`${image.original_name} 大图`} />
      </div>

      {image.result && <ResultPanel result={image.result} />}

      <TokenUsageDetails
        imageName={image.original_name}
        usage={image.token_usage}
        calls={image.token_calls}
      />

      {image.status === "failed" && (
        <section className="execution-error" aria-label="图片执行失败">
          <WarningCircle size={22} weight="fill" aria-hidden="true" />
          <div>
            <strong>模型执行失败</strong>
            <p>{image.error_message || "本次检查未产生结构化结果。"}</p>
            {image.error_code && <code>{image.error_code}</code>}
          </div>
          <button
            type="button"
            className="button quiet-button"
            disabled={retrying}
            onClick={() => onRetry(image.id)}
          >
            <ArrowClockwise size={16} weight="bold" aria-hidden="true" />
            {retrying ? "正在重试" : "重试此图片"}
          </button>
        </section>
      )}

      {image.status === "running" && (
        <div className="detail-state detail-running">
          <SpinnerGap size={19} weight="bold" aria-hidden="true" />
          模型正在检查这张图片
        </div>
      )}
      {image.status === "queued" && (
        <div className="detail-state">
          <Clock size={19} weight="bold" aria-hidden="true" />
          图片正在等待检查
        </div>
      )}
      {image.status === "canceled" && <div className="detail-state">这张图片已取消检查。</div>}
    </aside>
  );
}
