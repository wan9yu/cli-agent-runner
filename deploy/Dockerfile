# Template Dockerfile for running the whole unit (supervisor + agent CLI) in
# one container. This is a recipe to copy and adapt into your own project —
# see docs/recipes/deploy-all-in-one.md — not a published or maintained image.

FROM python:3.12-slim

# git is required: agent-runner's dirty-tree detection shells out to it.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir cli-agent-runner
# Pin a specific release for reproducible builds if you need one, e.g.
# `cli-agent-runner==X.Y.Z`.

# --- Agent CLI slot -----------------------------------------------------
# agent-runner is CLI-agnostic and ships none. Install whichever agent you
# configured in agent-runner.toml here, e.g. (needs a Node base image, or an
# added Node install step first):
#   RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent
# -------------------------------------------------------------------------

WORKDIR /work
ENTRYPOINT ["agent-runner"]
CMD ["serve"]
