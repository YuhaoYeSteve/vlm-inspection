import { CheckCircle, Clock, EyeSlash, SpinnerGap, WarningCircle, XCircle } from "@phosphor-icons/react";

import type { TaskImage } from "../../api/types";

export type ImageFilter =
  | "all"
  | "queued"
  | "running"
  | "pass"
  | "fail"
  | "unverifiable"
  | "failed";

export function imageDisplayStatus(image: TaskImage): Exclude<ImageFilter, "all"> | "canceled" {
  if (image.status === "succeeded" && image.result) return image.result.verdict;
  if (image.status === "succeeded") return "unverifiable";
  return image.status;
}

const statusLabels = {
  queued: "等待中",
  running: "检查中",
  pass: "合格",
  fail: "不合格",
  unverifiable: "无法判断",
  failed: "执行失败",
  canceled: "已取消",
} as const;

const filters: Array<{ value: ImageFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "queued", label: "等待中" },
  { value: "running", label: "检查中" },
  { value: "pass", label: "合格" },
  { value: "fail", label: "不合格" },
  { value: "unverifiable", label: "无法判断" },
  { value: "failed", label: "执行失败" },
];

function StatusIcon({ status }: { status: ReturnType<typeof imageDisplayStatus> }) {
  if (status === "pass") return <CheckCircle size={15} weight="fill" aria-hidden="true" />;
  if (status === "fail" || status === "failed") {
    return <XCircle size={15} weight="fill" aria-hidden="true" />;
  }
  if (status === "running") return <SpinnerGap size={15} weight="bold" aria-hidden="true" />;
  if (status === "unverifiable") {
    return <WarningCircle size={15} weight="fill" aria-hidden="true" />;
  }
  if (status === "canceled") return <EyeSlash size={15} weight="bold" aria-hidden="true" />;
  return <Clock size={15} weight="bold" aria-hidden="true" />;
}

interface ImageGalleryProps {
  images: TaskImage[];
  filter: ImageFilter;
  selectedId: string | null;
  onFilterChange: (filter: ImageFilter) => void;
  onSelect: (imageId: string) => void;
}

export function ImageGallery({
  images,
  filter,
  selectedId,
  onFilterChange,
  onSelect,
}: ImageGalleryProps) {
  const counts = new Map<ImageFilter, number>([["all", images.length]]);
  for (const image of images) {
    const status = imageDisplayStatus(image);
    if (status !== "canceled") counts.set(status, (counts.get(status) ?? 0) + 1);
  }
  const visibleImages =
    filter === "all" ? images : images.filter((image) => imageDisplayStatus(image) === filter);

  return (
    <section className="image-gallery" aria-label="巡检图片画廊">
      <div className="gallery-heading">
        <div>
          <p className="section-label">批量结果</p>
          <h2>图片画廊</h2>
        </div>
        <span>{visibleImages.length} / {images.length}</span>
      </div>
      <div className="filter-strip" aria-label="按状态筛选">
        {filters.map((item) => (
          <button
            type="button"
            key={item.value}
            className={filter === item.value ? "is-active" : ""}
            aria-pressed={filter === item.value}
            onClick={() => onFilterChange(item.value)}
          >
            {item.label} {counts.get(item.value) ?? 0}
          </button>
        ))}
      </div>
      {visibleImages.length ? (
        <div className="thumbnail-grid">
          {visibleImages.map((image) => {
            const status = imageDisplayStatus(image);
            return (
              <button
                type="button"
                key={image.id}
                aria-label={`查看 ${image.original_name}`}
                aria-pressed={selectedId === image.id}
                className={`thumbnail-card status-${status}${selectedId === image.id ? " is-selected" : ""}`}
                onClick={() => onSelect(image.id)}
              >
                <span className="thumbnail-frame">
                  <img src={image.thumbnail_url} alt={`${image.original_name} 巡检图片`} />
                  <span className={`image-status image-status-${status}`}>
                    <StatusIcon status={status} />
                    {statusLabels[status]}
                  </span>
                </span>
                <span className="thumbnail-meta">
                  <strong>{image.original_name}</strong>
                  <small>#{String(image.position + 1).padStart(2, "0")}</small>
                </span>
              </button>
            );
          })}
        </div>
      ) : (
        <div className="gallery-empty">当前筛选条件下没有图片。</div>
      )}
    </section>
  );
}
