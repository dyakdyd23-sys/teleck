FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m checker && mkdir -p /srv/app/data && chown -R checker:checker /srv/app
USER checker
EXPOSE 8000
CMD ["python","run.py"]
