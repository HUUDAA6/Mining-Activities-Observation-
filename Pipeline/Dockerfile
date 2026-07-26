FROM apache/airflow:3.0.2 AS airflow

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gdal-bin \
       libgdal-dev \
       build-essential \
       curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV CPLUS_INCLUDE_PATH=/usr/include/gdal \
    C_INCLUDE_PATH=/usr/include/gdal

USER airflow
COPY requirements.txt /requirements-airflow.txt
RUN pip install --no-cache-dir -r /requirements-airflow.txt
