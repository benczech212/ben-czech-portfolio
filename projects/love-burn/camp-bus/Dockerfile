# One image for every Python service in the virtual camp (and, later, the booth Pi
# if we go containers there). Code is bind-mounted by docker-compose so edits and
# mappings.yaml hot-reload take effect without a rebuild.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt clients/python/pyproject.toml ./tmp/
COPY clients/python /app/clients/python
RUN pip install --no-cache-dir -r tmp/requirements.txt -e /app/clients/python && rm -rf tmp
COPY . /app
ENV PYTHONUNBUFFERED=1 CAMPBUS_HOST=mosquitto CAMPBUS_PORT=1883
CMD ["python", "tools/spy.py"]
