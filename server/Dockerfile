FROM python:3.12-slim
WORKDIR /app
COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY heartapp/__init__.py heartapp/room_protocol.py heartapp/relay.py heartapp/lobby.py /app/heartapp/
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).read()"
CMD ["python", "-m", "heartapp.relay", "--allow-plaintext", "--host", "0.0.0.0", "--port", "8765"]
