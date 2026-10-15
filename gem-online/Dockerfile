FROM python:3.12-slim
WORKDIR /app
COPY local-workshop/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY local-workshop/engine.py local-workshop/server.py local-workshop/presets.json ./
COPY local-workshop/pixel_fixer.py local-workshop/pixel_fixer_worker.py ./
COPY local-workshop/vendor ./vendor
COPY local-workshop/static ./static
COPY local-workshop/unity ./unity
ENV PYTHONUNBUFFERED=1 PORT=10000
EXPOSE 10000
CMD ["python", "server.py", "--cloud", "--data", "/data"]
