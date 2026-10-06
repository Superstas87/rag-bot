FROM python:3.11-slim

WORKDIR /app

COPY telegram_bot_psychology.py .

RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn \
    python-telegram-bot \
    qdrant-client \
    fastembed \
    pypdf \
    langchain-text-splitters \
    requests\
    python-dotenv

CMD ["python", "telegram_bot_psychology.py"]
