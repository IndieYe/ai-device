"""Run on the build container AND on an isolated target before publishing artifacts."""

from io import BytesIO

from PIL import Image, UnidentifiedImageError, features


def main():
    for feature in ("jpg", "webp", "zlib"):
        if not features.check(feature):
            raise RuntimeError(f"required image feature unavailable: {feature}")
    for format_name in ("PNG", "JPEG", "WEBP"):
        content = BytesIO()
        Image.new("RGB", (32, 24), (12, 34, 56)).save(content, format=format_name)
        data = content.getvalue()
        with Image.open(BytesIO(data)) as image:
            image.load()
            assert image.size == (32, 24) and image.format == format_name
        print(format_name, len(data), "decode OK")
    try:
        Image.open(BytesIO(b"invalid-image"))
    except UnidentifiedImageError:
        print("invalid image rejected")
    else:
        raise AssertionError("invalid image accepted")


if __name__ == "__main__":
    main()
