from io import BytesIO

import pytest
from PIL import Image

from app.services.images import DuplicateImage, ImageIngestor, UnsupportedImage


def image_bytes(
    *,
    color: str = "red",
    size: tuple[int, int] = (3000, 1000),
    image_format: str = "PNG",
) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format=image_format)
    return output.getvalue()


def test_ingest_creates_bounded_derivatives(tmp_path) -> None:
    item = ImageIngestor(tmp_path).ingest(
        "task-1",
        "../sample.png",
        image_bytes(),
    )

    assert item.original_path.parent.name == "original"
    assert "sample.png" in item.original_path.name
    with Image.open(item.inference_path) as image:
        assert image.mode == "RGB"
        assert max(image.size) == 2048
    with Image.open(item.thumbnail_path) as image:
        assert image.mode == "RGB"
        assert max(image.size) == 1600


def test_ingest_rejects_duplicate_hash(tmp_path) -> None:
    ingestor = ImageIngestor(tmp_path)
    ingestor.ingest("task-1", "a.png", image_bytes())

    with pytest.raises(DuplicateImage):
        ingestor.ingest("task-1", "b.png", image_bytes())


def test_ingest_rejects_non_image(tmp_path) -> None:
    with pytest.raises(UnsupportedImage):
        ImageIngestor(tmp_path).ingest("task-1", "bad.txt", b"not an image")


def test_unsupported_format_does_not_reserve_hash(tmp_path) -> None:
    ingestor = ImageIngestor(tmp_path)
    bitmap = image_bytes(image_format="BMP")

    with pytest.raises(UnsupportedImage, match="unsupported image format"):
        ingestor.ingest("task-1", "bad.bmp", bitmap)
    with pytest.raises(UnsupportedImage, match="unsupported image format"):
        ingestor.ingest("task-1", "bad-again.bmp", bitmap)


def test_ingest_rejects_empty_and_oversized_content(tmp_path) -> None:
    ingestor = ImageIngestor(tmp_path)

    with pytest.raises(UnsupportedImage, match="between 1 byte and 20 MiB"):
        ingestor.ingest("task-1", "empty.png", b"")
    with pytest.raises(UnsupportedImage, match="between 1 byte and 20 MiB"):
        ingestor.ingest("task-1", "large.png", b"x" * (ImageIngestor.MAX_BYTES + 1))
