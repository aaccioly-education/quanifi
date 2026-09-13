FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

RUN addgroup --system quanifi && adduser --system --ingroup quanifi quanifi
COPY web/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY manage.py /app/manage.py
COPY web /app/web
RUN mkdir -p /app/var && chown -R quanifi:quanifi /app/var && \
    DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

USER quanifi
EXPOSE 8080

CMD ["gunicorn", "web.config.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "60", "--access-logfile", "-"]
