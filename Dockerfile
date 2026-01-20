
FROM python:3.11-slim

RUN apt-get update && apt-get install -y libespeak1 ffmpeg

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY index.html ./index.html

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
