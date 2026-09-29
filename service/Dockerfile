# Application-only updates reuse the tested CPU dependencies and model bundle.
# Rebuild service/Dockerfile.runtime when changing dependencies or model weights,
# then update this pinned runtime reference deliberately.
FROM ghcr.io/hominemai/tempest:v0.2.0@sha256:6231e7dc4c81021cd214f417197fc532ee15a5fa8bc1502b350b12a23b60b10c
RUN pip install --no-cache-dir 'psycopg_pool>=3.2,<4'
COPY service /app/service
COPY papers/web/pressure.js papers/web/relationship-multipliers.json /app/papers/web/
COPY papers/web/inference.js papers/web/inference.css papers/web/state-index.js papers/web/state-index.css papers/web/tempest.css papers/web/theme.js /app/papers/web/
COPY papers/inference.html papers/state-index.html /app/papers/
