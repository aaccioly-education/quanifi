FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY LICENSE NOTICE /app/

RUN addgroup --system quanifi && adduser --system --ingroup quanifi quanifi
COPY web/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY manage.py /app/manage.py
COPY web /app/web
COPY docker/web/entrypoint.sh /app/docker-entrypoint.sh
RUN mkdir -p /app/var && chown -R quanifi:quanifi /app/var && \
    DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput && \
    sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod 0755 /app/docker-entrypoint.sh

USER quanifi
EXPOSE 8080

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "web.config.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "60", "--access-logfile", "-"]
