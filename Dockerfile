ARG DEBIAN_VERSION=bookworm
ARG UV_VERSION=0.7.12
ARG PY_VERSION=3.13

FROM ghcr.io/astral-sh/uv:python${PY_VERSION}-${DEBIAN_VERSION}-slim AS builder

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image; see `standalone.Dockerfile`
# for an example.
ENV UV_PYTHON_DOWNLOADS=0

# Install packages needed for building the app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    git-svn=1:2.39.5-0+deb12u2 \
    # build-essential=12.9 \
    # ca-certificates=20230311 \
    # curl=7.88.1-10+deb12u12 \
    # make=4.3-4.1 \
    unzip=6.0-28 \
    # To remove the image size, it is recommended refresh the package cache as follows
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Build the project in `/app`
WORKDIR /app

# Add in sound effect files and unpack them
COPY sounds.zip .
RUN unzip -u sounds.zip

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Then, add the rest of the project source code and install it
# Installing separately from its dependencies allows optimal layer caching
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Then, use a final image without uv - make sure the versions match the builder!
FROM python:${PY_VERSION}-slim-${DEBIAN_VERSION}

# Run from the app dir
WORKDIR /app

# Install packages needed for running the app
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ffmpeg=7:7.1.3-0+deb13u1 \
    libopus0=1.3.1-3 \
    # To remove the image size, it is recommended refresh the package cache as follows
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Mount persistent data directories
ENV PERSISTENT_DATA_DIR="/app/persistent"
VOLUME ["/app/persistent"]

# Copy sound effects from the builder
COPY --from=builder --chown=app:app /app/sounds /app/sounds

# Copy the app and pre-built venv from the builder - that should be all that's needed
COPY --from=builder --chown=app:app /app/src /app/src
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Place executables in the environment at the front of the path
ENV PATH="/app/.venv/bin:$PATH"

# Run the built app
CMD ["balaambot"]
