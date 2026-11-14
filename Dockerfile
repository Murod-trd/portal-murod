# Node is used solely in this disposable frontend compiler stage.
ARG NODE_IMAGE=alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a
ARG PYTHON_IMAGE=python:3.12-alpine3.24@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8
ARG POSTGRES_IMAGE=alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a

# Download/authenticate archives before copying compiler tools into Node.
FROM ${PYTHON_IMAGE} AS build-tools-download
COPY deploy/build-tools.lock.json /build-tools.lock.json
COPY scripts/prepare_build_tools.py /prepare_build_tools.py
COPY scripts/verified_https.py /verified_https.py
RUN python /prepare_build_tools.py --lock /build-tools.lock.json --destination /verified
COPY deploy/node-runtime.lock.json /node-runtime.lock.json
COPY scripts/prepare_node.py /prepare_node.py
RUN python /prepare_node.py --lock /node-runtime.lock.json --destination /verified-node

# Clean Alpine rootfs; no upstream Node/npm container is a build input.
FROM ${NODE_IMAGE} AS frontend-tools
RUN apk add --no-cache libstdc++
COPY --from=build-tools-download /verified-node/node /usr/local/bin/node
COPY --from=build-tools-download /verified-node/LICENSE /usr/share/licenses/node/LICENSE
COPY --from=build-tools-download /verified/npm /usr/local/lib/node_modules/npm
RUN rm -rf /usr/local/lib/node_modules/npm/node_modules/undici
COPY --from=build-tools-download /verified/undici /usr/local/lib/node_modules/npm/node_modules/undici
COPY deploy/build-tools.lock.json /usr/share/portal/build-tools.lock.json
COPY deploy/node-runtime.lock.json /usr/share/portal/node-runtime.lock.json
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version

FROM frontend-tools AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY apps/web ./apps/web
COPY RELEASE ./RELEASE
COPY scripts/build_frontend.mjs ./scripts/build_frontend.mjs
RUN npm run build:web

# Signed PostgreSQL 17 APK packages on a clean rootfs. Upstream entrypoint is vendored
# with its MIT license and the already-tested su-exec substitution.
FROM ${POSTGRES_IMAGE} AS postgres
RUN apk add --no-cache bash ca-certificates tzdata zstd su-exec=0.3-r0 \
 postgresql17=17.12-r0 postgresql17-client=17.12-r0 postgresql17-contrib=17.12-r0
ENV LANG=en_US.utf8 PG_MAJOR=17 PG_VERSION=17.12 PGDATA=/var/lib/postgresql/data PATH=/usr/lib/postgresql17/bin:${PATH}
RUN mkdir -p /docker-entrypoint-initdb.d /var/lib/postgresql/data /var/run/postgresql \
 && chown postgres:postgres /var/lib/postgresql/data /var/run/postgresql \
 && chmod 700 /var/lib/postgresql/data && chmod 3775 /var/run/postgresql \
 && echo "listen_addresses = '*'" >> "$(pg_config --sharedir)/postgresql.conf.sample"
COPY deploy/postgres/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY deploy/postgres/LICENSE /usr/share/licenses/docker-postgres/LICENSE
COPY deploy/postgres/UPSTREAM.json /usr/share/portal/postgres-entrypoint-source.json
RUN chmod 755 /usr/local/bin/docker-entrypoint.sh \
 && echo '1692706708ac7d9c9f9718c1fa4b23abbcc2feecd1416eb51320cefe75f18a5d  /usr/local/bin/docker-entrypoint.sh' | sha256sum -c - \
 && postgres --version
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
EXPOSE 5432
CMD ["postgres"]

# Compile the patched upstream release without network/archive or legacy model support.
FROM ${PYTHON_IMAGE} AS tesseract-build
RUN apk add --no-cache build-base cmake ninja pkgconf leptonica-dev
COPY deploy/native-sources.lock.json /native-sources.lock.json
COPY scripts/download_native_source.py /download_native_source.py
COPY scripts/verified_https.py /verified_https.py
RUN python /download_native_source.py --lock /native-sources.lock.json --output /tmp/tesseract.tar.gz \
 && mkdir /source && tar -xzf /tmp/tesseract.tar.gz -C /source --strip-components=1
RUN cmake -S /source -B /build -G Ninja \
 -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/tesseract \
 -DTESSDATA_PREFIX=/usr/share/tesseract-ocr/5 \
 -DBUILD_TRAINING_TOOLS=OFF -DBUILD_TESTS=OFF -DENABLE_NATIVE=OFF \
 -DGRAPHICS_DISABLED=ON -DDISABLED_LEGACY_ENGINE=ON -DDISABLE_ARCHIVE=ON -DDISABLE_CURL=ON \
 -DENABLE_PRECOMPILED_HEADERS=OFF \
 && cmake --build /build --parallel 2 \
 && DESTDIR=/out cmake --install /build \
 && /out/opt/tesseract/bin/tesseract --version

FROM ${PYTHON_IMAGE} AS api
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORTAL_RUNTIME_DIR=/tmp/portal-runtime
WORKDIR /app
COPY requirements.lock ./
COPY requirements-build.lock ./
RUN apk add --no-cache font-dejavu
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements-build.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && addgroup -g 10001 portal && adduser -D -u 10001 -G portal portal && mkdir -p /var/lib/portal/files /var/lib/portal/backup-work && chown 10001:10001 /var/lib/portal/files /var/lib/portal/backup-work && chmod 700 /var/lib/portal/files /var/lib/portal/backup-work
# Package installation belongs to image construction, never the running portal.
RUN python -m pip uninstall --yes pip && rm -rf /usr/local/lib/python3.12/ensurepip
COPY backend/config ./backend/config
RUN python -c "from PIL import ImageFont; ImageFont.truetype('DejaVuSans.ttf',56)"
COPY backend/portal ./backend/portal
COPY backend/manage.py ./backend/manage.py
COPY operations ./operations
COPY RELEASE ./RELEASE
COPY --from=frontend /build/frontend-dist ./frontend-dist
USER 10001:10001
EXPOSE 8000
CMD ["gunicorn","--chdir","backend","config.wsgi:application","--bind","0.0.0.0:8000","--workers","2","--threads","2","--timeout","60","--max-requests","500","--max-requests-jitter","50","--worker-tmp-dir","/tmp","--no-control-socket"]

FROM api AS worker
USER root
COPY backend/worker.py /app/backend/worker.py
RUN apk add --no-cache libseccomp poppler-utils leptonica libstdc++
COPY --from=tesseract-build /out/opt/tesseract/bin/tesseract /opt/tesseract/bin/tesseract
COPY --from=tesseract-build /source/LICENSE /usr/share/licenses/tesseract/LICENSE
COPY deploy/native-sources.lock.json /usr/share/portal/native-sources.lock.json
ENV PATH=/opt/tesseract/bin:${PATH}
COPY scripts/setup_ocr.py /app/scripts/setup_ocr.py
COPY scripts/verified_https.py /app/scripts/verified_https.py
RUN python scripts/setup_ocr.py --destination /usr/share/tesseract-ocr/5/tessdata
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata
USER 10001:10001
CMD ["python","backend/worker.py"]

# Only disposable acceptance deployments use this target.
FROM worker AS verification
USER root
RUN apk add --no-cache openssl
COPY requirements-dev.lock ./
COPY --from=build-tools-download /usr/local/lib/python3.12/ensurepip /usr/local/lib/python3.12/ensurepip
RUN python -m ensurepip && python -m pip install --no-cache-dir --require-hashes -r requirements-dev.lock
COPY . /app
USER 10001:10001
CMD ["python","operations/native_checks.py"]
