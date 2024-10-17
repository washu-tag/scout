
FROM python:3.12-slim AS base_image

WORKDIR /app

RUN pip install --upgrade pip setuptools

COPY . /app/
RUN pip3 install .

EXPOSE 8080
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["gunicorn -k uvicorn.workers.UvicornWorker -b :8080 --timeout 500 main:app"]
