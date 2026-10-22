#!/usr/bin/env bash
set -euo pipefail
export PATH=/opt/python/cp313-cp313/bin:$PATH
export HOME=/tmp/build-home UV_CACHE_DIR=/tmp/uv-cache MAX_CONCURRENCY=2
mkdir -p "$HOME" /tmp/build /output/wheelhouse
cd /tmp/build
exec > >(tee /output/build.log) 2>&1
fetch() {
  curl --fail --location --retry 2 "$2" -o "$3"
  printf '%s  %s\n' "$1" "$3" | sha256sum --check -
}
fetch 04705c110cb2469caa79fb71fba3d7bf834914706e9641a4589485c1f832565b https://www.ijg.org/files/jpegsrc.v9f.tar.gz jpeg.tar.gz
fetch e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564 https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.6.0.tar.gz webp.tar.gz
# PyPI JSON locates the sdist; only the independently fixed digest authorizes its bytes.
curl --fail --location https://pypi.org/pypi/pillow/12.3.0/json -o pillow-index.json
URL=$(python -c 'import json; print(next(x["url"] for x in json.load(open("pillow-index.json"))["urls"] if x["packagetype"]=="sdist"))')
fetch 3b8182a766685eaa002637e28b4ec8d6b18819a0c71f579bf0dbaa5830297cce "$URL" pillow.tar.gz
sha256sum jpeg.tar.gz webp.tar.gz pillow.tar.gz > /output/source-sha256.txt
tar --no-same-owner -xf jpeg.tar.gz
tar --no-same-owner -xf webp.tar.gz
tar --no-same-owner -xf pillow.tar.gz
(cd jpeg-9f; CFLAGS=-fPIC ./configure --prefix=/opt/compat-jpeg --disable-shared --enable-static; make -j2; make install)
(cd libwebp-1.6.0; ./configure --prefix=/opt/compat-webp --enable-shared --disable-static --disable-sdl --disable-gl --disable-jpeg --disable-png --disable-tiff --disable-gif; make -j2; make install)
export CFLAGS='-I/opt/compat-jpeg/include -I/opt/compat-webp/include'
export LDFLAGS='-L/opt/compat-jpeg/lib -L/opt/compat-webp/lib'
export PKG_CONFIG_PATH=/opt/compat-webp/lib/pkgconfig
export LD_LIBRARY_PATH=/opt/compat-webp/lib
uv --no-config build --wheel --out-dir /tmp/raw pillow-12.3.0
# Pin the auditor instead of using whichever executable happens to be on PATH.
uv --no-config tool run --from auditwheel==6.6.0 auditwheel repair --plat manylinux2014_x86_64 --wheel-dir /output/wheelhouse /tmp/raw/*.whl
uv --no-config tool run --from auditwheel==6.6.0 auditwheel show /output/wheelhouse/*.whl > /output/auditwheel.txt
uv --no-config venv --python /opt/python/cp313-cp313/bin/python /tmp/verify
uv --no-config pip install --python /tmp/verify/bin/python --only-binary :all: /output/wheelhouse/*.whl
/tmp/verify/bin/python /input/verify-pillow.py
# Complete fixed client third-party set, using only wheels for the target interpreter.
uv --no-config run --no-project --with packaging==26.3 python /input/manifest-dependencies.py /output/wheelhouse
uv --no-config pip install --python /tmp/verify/bin/python --no-index --only-binary :all: /output/wheelhouse/*.whl
uv --no-config pip check --python /tmp/verify/bin/python
/tmp/verify/bin/python /input/verify-pillow.py
