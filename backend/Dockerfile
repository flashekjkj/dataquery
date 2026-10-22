FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/huggingface \
    HF_HUB_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# CPU wheels avoid installing CUDA libraries for this deployment.
ARG TORCH_VERSION=2.14.1
RUN python -m pip install --no-cache-dir "torch==${TORCH_VERSION}" --index-url https://download.pytorch.org/whl/cpu
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY backend/requirements-docker.txt ./requirements-docker.txt
RUN python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements-docker.txt

COPY backend/ ./
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
