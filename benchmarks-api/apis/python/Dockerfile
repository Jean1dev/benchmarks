FROM python:3.14.7-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt
COPY app.py ./
USER 65534:65534
EXPOSE 8080
CMD ["python", "-O", "app.py"]
