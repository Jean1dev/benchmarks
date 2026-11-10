FROM python:3.14.7-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56
WORKDIR /workspace
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY analysis /workspace/analysis
COPY benchmark /workspace/benchmark
ENTRYPOINT ["python", "/workspace/analysis/report.py"]
CMD ["--results", "/workspace/results", "--reports", "/workspace/reports"]
