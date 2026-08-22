import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError


class UnsupportedImage(ValueError):
    pass


class DuplicateImage(ValueError):
    pass


@dataclass(frozen=True)
class IngestedImage:
    sha256: str
    original_path: Path
    inference_path: Path
    thumbnail_path: Path


class ImageIngestor:
    MAX_BYTES = 20 * 1024 * 1024
    ALLOWED_FORMATS: ClassVar[set[str]] = {"JPEG", "PNG", "WEBP"}

    def __init__(self, image_root: Path) -> None:
        self.image_root = image_root.resolve()

    def ingest(self, task_id: str, filename: str, content: bytes) -> IngestedImage:
        if not content or len(content) > self.MAX_BYTES:
            raise UnsupportedImage("image size must be between 1 byte and 20 MiB")

        digest = hashlib.sha256(content).hexdigest()
        task_root = (self.image_root / task_id).resolve()
        if self.image_root not in task_root.parents:
            raise UnsupportedImage("invalid task image directory")
        self._reserve_hash(task_root, digest)

        safe_name = Path(filename).name or "image"
        unique_name = f"{uuid4()}-{safe_name}"
        try:
            with Image.open(BytesIO(content)) as source:
                if source.format not in self.ALLOWED_FORMATS:
                    raise UnsupportedImage(f"unsupported image format: {source.format}")
                normalized = self._to_rgb(ImageOps.exif_transpose(source))
                original = self._atomic_bytes(task_root / "original" / unique_name, content)
                inference = self._atomic_jpeg(
                    task_root / "inference" / f"{unique_name}.jpg",
                    normalized,
                    2048,
                    90,
                )
                thumbnail = self._atomic_jpeg(
                    task_root / "thumbs" / f"{unique_name}.jpg",
                    normalized,
                    1600,
                    82,
                )
        except UnsupportedImage:
            self._release_hash(task_root, digest)
            raise
        except (UnidentifiedImageError, OSError) as exc:
            self._release_hash(task_root, digest)
            raise UnsupportedImage("image cannot be decoded") from exc

        return IngestedImage(digest, original, inference, thumbnail)

    @staticmethod
    def _to_rgb(image: Image.Image) -> Image.Image:
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return image.convert("RGB")

    def _reserve_hash(self, task_root: Path, digest: str) -> None:
        hashes_path = task_root / "hashes.json"
        hashes = self._read_hashes(hashes_path)
        if digest in hashes:
            raise DuplicateImage("duplicate image in task")
        hashes.add(digest)
        self._write_hashes(hashes_path, hashes)

    def _release_hash(self, task_root: Path, digest: str) -> None:
        hashes_path = task_root / "hashes.json"
        hashes = self._read_hashes(hashes_path)
        hashes.discard(digest)
        self._write_hashes(hashes_path, hashes)

    @staticmethod
    def _read_hashes(path: Path) -> set[str]:
        if not path.is_file():
            return set()
        return set(json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def _write_hashes(cls, path: Path, hashes: set[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(sorted(hashes), ensure_ascii=False)
        cls._atomic_bytes(path, content.encode())

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    @staticmethod
    def _atomic_jpeg(path: Path, source: Image.Image, max_edge: int, quality: int) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
        image = source.copy()
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        try:
            image.save(temporary, format="JPEG", quality=quality)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
            image.close()
        return path
