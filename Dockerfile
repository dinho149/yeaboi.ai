# yeaboi, as a web app.
#
# Two-stage, and the interesting part is what is NOT here: Node. The front-end
# bundles are built by `make web` and committed to src/yeaboi/web/static, so the
# image needs no toolchain to serve them. That constraint was originally about
# letting `pip install yeaboi` work without Node; it pays again here.
#
# The TUI's optional extras are also absent. This image serves the web app —
# it does not need voice, charts, or a provider SDK to hand a browser a page,
# and every one of them is weight on a thing that boots on a schedule.

# ── build ────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS build

# uv, because the project already uses it and it resolves in seconds. Pinned by
# digest-free tag on purpose: this is a build stage whose output is a wheel.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /src
# Copy the metadata first so the dependency layer survives a source-only edit.
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN uv build --wheel --out-dir /dist

# ── runtime ──────────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Non-root. The app writes only to its data directory, so there is no reason
# for it to be able to write anywhere else.
RUN useradd --create-home --uid 10001 yeaboi

COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# The data home. paths.py reads YEABOI_HOME; everything durable — app.db,
# sessions, exports — lands under it, and compose mounts a volume here so a
# rebuild does not take the projects with it.
ENV YEABOI_HOME=/data
RUN mkdir -p /data && chown yeaboi:yeaboi /data
VOLUME ["/data"]

USER yeaboi
EXPOSE 5599

# HEAD rather than GET: it is the cheaper probe and it works, which was not
# true before — the stdlib handler answered 501 for any verb it had no method
# for, and a health check is exactly the caller that would have found that.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:5599/api/health', method='HEAD'), timeout=4)"

# 0.0.0.0 because the only way into a container is from outside it. The default
# for `yeaboi app` stays 127.0.0.1 — a laptop should not join the LAN by
# accident, and a container cannot serve anything if it does not.
ENTRYPOINT ["yeaboi", "app", "--host", "0.0.0.0", "--port", "5599"]
