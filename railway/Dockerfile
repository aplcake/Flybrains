FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY community.py community_host.py community_storage.py community_backup.py ./
ENV HOST=0.0.0.0 PORT=8080 PYTHONUNBUFFERED=1 BRAINSORT_COMMUNITY_DB=/data/community.sqlite
CMD ["python", "community_host.py"]
