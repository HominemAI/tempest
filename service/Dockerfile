FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=30 install -y --no-install-recommends nodejs libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY papers/emotion_training/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir 'psycopg[binary]>=3.2,<4'
COPY service /app/service
COPY papers/web/pressure.js papers/web/relationship-multipliers.json /app/papers/web/
COPY papers/web/inference.js papers/web/inference.css papers/web/state-index.js papers/web/state-index.css papers/web/tempest.css papers/web/theme.js /app/papers/web/
COPY papers/inference.html papers/state-index.html /app/papers/
COPY papers/personality_state_mapping.py papers/personality_synthesis.py /app/papers/
COPY papers/personality-mapping/active.json /app/papers/personality-mapping/active.json
COPY papers/personality-mapping/v2/anchors.json /app/papers/personality-mapping/v2/anchors.json
COPY papers/personality-mapping/delta-v1/anchors.json /app/papers/personality-mapping/delta-v1/anchors.json
COPY papers/emotion_training/predict_jev_discrete.py papers/emotion_training/train_jev_discrete.py papers/emotion_training/pipeline.py /app/papers/emotion_training/
COPY --chown=10001:10001 model-bundle/ /models/
RUN useradd --uid 10001 --create-home tempest && mkdir /data && chown tempest:tempest /data
USER tempest
RUN python -c "from pathlib import Path; files = [p for p in Path('/models').rglob('*') if p.is_file()]; assert files, 'Empty model bundle'; [(p.open('rb').close()) for p in files]; assert Path('/models/encoder/model.safetensors').is_file(), 'Missing encoder weights'"
ENV TEMPEST_DB=/data/tempest.sqlite TEMPEST_MODEL_DIR=/models/checkpoint TEMPEST_ENCODER_DIR=/models/encoder
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --start-period=120s --interval=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=5)"
CMD ["python","service/server.py","--host","0.0.0.0","--port","8080"]
