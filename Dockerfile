FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PRODUCTION=1 TRUST_PROXY=1 DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["gunicorn", "app:app", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:8000"]
