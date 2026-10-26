# One image for both Python services (plc_sim and edge_app); compose picks the command.
# Models and the replay data are NOT baked in: compose mounts ./models and ./data/test.csv read-only.
FROM python:3.11-slim
WORKDIR /app
COPY requirements-edge.txt .
RUN pip install --no-cache-dir -r requirements-edge.txt
COPY plc_sim/ plc_sim/
COPY edge_app/ edge_app/
ENV PYTHONUNBUFFERED=1
