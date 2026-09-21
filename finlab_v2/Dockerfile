FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN python -m py_compile app.py triaid_fin/*.py *.py

EXPOSE 8080

CMD ["/bin/sh","-lc","exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
