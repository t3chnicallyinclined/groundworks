# XScholar engine image (tier 1: self-hosted, one box).
#
# The core has no third-party dependencies (pyproject.toml `dependencies = []`), so this
# image installs only the package itself -- no torch, no extras. Keep it small.
FROM python:3.13-slim

WORKDIR /app

# Copy the repo (see .dockerignore for what is left out: instance outputs, VCS, caches).
COPY . .

# Core install only -- no [mcp] or [embed] extras, so no third-party packages land in the
# image (docs/LICENSING.md s.1: keep the default product free of anything that needs its
# own license review before it ships).
RUN pip install --no-cache-dir .

# `python -m xscholar.cli` is the one entrypoint (see scripts/run_instance.py and README
# "Start here"). compose.yaml's `engine` service overrides the entrypoint for the demo
# command, which calls scripts/run_instance.py directly against the compose database.
ENTRYPOINT ["python", "-m", "xscholar.cli"]
CMD ["--help"]
