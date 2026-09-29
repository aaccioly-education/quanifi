# syntax=docker/dockerfile:1.7
# Quanifi quickstart NiFi image: apache/nifi:2.9.0 + the processors listed in
# QUANIFI_PROCESSORS with their Python dependencies pre-installed exactly where
# NiFi looks for them, + the demo canvas. Built by `docker compose up`; see
# docs/guides/DOCKER_QUICKSTART.md. Nothing is written into the base image's
# VOLUME paths: Compose keeps a volume's old content when the image changes.
# The dependency layers are built from manifest.json alone, before any processor
# source is copied, so a code-only change reuses them; changing a processor's
# version or dependencies, uv.lock, pyproject.toml or quanifi_build.py re-runs them.
ARG NIFI_IMAGE=apache/nifi:2.9.0
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.11

FROM ${UV_IMAGE} AS uv

FROM ${NIFI_IMAGE} AS select
ARG QUANIFI_PROCESSORS=docker/processors.txt
ARG QUANIFI_CANVAS=demo/grover/qiskit-grover.json
USER root
WORKDIR /src
COPY nifi_extensions/ nifi_extensions/
COPY docker/ docker/
COPY demo/ demo/
RUN python3 docker/nifi/quanifi_build.py select --src nifi_extensions \
        --processors "${QUANIFI_PROCESSORS}" --out /out/nifi_extensions --manifest /out/manifest.json \
 && python3 docker/nifi/quanifi_build.py flow --canvas "${QUANIFI_CANVAS}" \
        --manifest /out/manifest.json --out /out/flow/flow.json.gz

FROM ${NIFI_IMAGE} AS test
USER root
COPY LICENSE NOTICE /opt/quanifi/
RUN install -d -o nifi -g nifi /opt/nifi/quanifi-tests
USER nifi
WORKDIR /opt/nifi/quanifi-tests
COPY --from=select --chown=nifi:nifi /out/manifest.json .quanifi-manifest.json
RUN --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=bind,source=pyproject.toml,target=/tmp/quanifi-build/pyproject.toml \
    --mount=type=bind,source=uv.lock,target=/tmp/quanifi-build/uv.lock \
    --mount=type=bind,source=docker/nifi/quanifi_build.py,target=/tmp/quanifi-build/quanifi_build.py \
    python3 /tmp/quanifi-build/quanifi_build.py testenv --manifest .quanifi-manifest.json \
        --project /tmp/quanifi-build --venv /opt/nifi/quanifi-tests/.venv --python /usr/bin/python3 \
        --uv /usr/local/bin/uv --extra "pytest>=8.0"
COPY --from=select --chown=nifi:nifi /out/nifi_extensions/ nifi_extensions/
COPY --chown=nifi:nifi tests/ tests/
COPY --chown=nifi:nifi tools/ tools/
COPY --chown=nifi:nifi docker/ docker/
COPY --chown=nifi:nifi demo/ demo/
COPY --chown=nifi:nifi pyproject.toml uv.lock ./
ENV PATH=/opt/nifi/quanifi-tests/.venv/bin:${PATH}
ENTRYPOINT []
CMD ["sh", "-c", "exec python -m pytest -q -p no:cacheprovider $(sed -e 's/#.*//' docker/test-files.txt)"]

FROM ${NIFI_IMAGE} AS runtime
ARG QUANIFI_PREBAKE=true
USER root
COPY LICENSE NOTICE /opt/quanifi/
COPY --from=select /out/manifest.json /opt/quanifi/manifest.json
RUN install -d -o nifi -g nifi /opt/nifi/nifi-current/reports
USER nifi
RUN --mount=type=bind,from=uv,source=/uv,target=/usr/local/bin/uv \
    --mount=type=bind,source=pyproject.toml,target=/tmp/quanifi-build/pyproject.toml \
    --mount=type=bind,source=uv.lock,target=/tmp/quanifi-build/uv.lock \
    --mount=type=bind,source=docker/nifi/quanifi_build.py,target=/tmp/quanifi-build/quanifi_build.py \
    python3 /tmp/quanifi-build/quanifi_build.py prebake --manifest /opt/quanifi/manifest.json \
        --project /tmp/quanifi-build --work-dir /opt/nifi/nifi-current/work/python \
        --python /usr/bin/python3 --uv /usr/local/bin/uv --enabled "${QUANIFI_PREBAKE}"
USER root
COPY --from=select /out/nifi_extensions/ /opt/quanifi/nifi_extensions/
COPY --from=select /out/flow/ /opt/quanifi/flow/
COPY docker/nifi/entrypoint.sh docker/nifi/quanifi_monitor.py tools/nifi_ready.py /opt/quanifi/bin/
RUN sed -i 's/\r$//' /opt/quanifi/bin/entrypoint.sh \
 && chmod 0755 /opt/quanifi/bin/entrypoint.sh
USER nifi
ENV QUANIFI_AUTO_RECOVER=true \
    QUANIFI_AUTO_RESUME=false \
    QUANIFI_MAX_RECOVERIES=3 \
    QUANIFI_STALL_SECONDS=240 \
    QUANIFI_QUIET_SECONDS=20 \
    QUANIFI_STARTUP_TIMEOUT_SECONDS=1200
HEALTHCHECK --interval=30s --timeout=10s --start-period=20m --start-interval=5s --retries=3 \
    CMD ["python3", "/opt/quanifi/bin/quanifi_monitor.py", "check"]
ENTRYPOINT ["/opt/quanifi/bin/entrypoint.sh"]
