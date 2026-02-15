ARG UV_VERSION=latest
ARG DEBIAN_VERSION=bookworm

FROM ghcr.io/astral-sh/uv:$UV_VERSION AS uv


FROM mcr.microsoft.com/vscode/devcontainers/base:$DEBIAN_VERSION

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        git \
        libopus0 \
        unzip \
    # To remove the image size, it is recommended refresh the package cache as follows
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deno.land/install.sh -o /tmp/deno_install.sh \
    && DENO_INSTALL=/usr/local sh /tmp/deno_install.sh -y \
    && rm /tmp/deno_install.sh

COPY --from=uv --chown=vscode: /uv /uvx /bin/
