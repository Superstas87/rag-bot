#!/bin/bash
set -e   # остановиться при первой ошибке

cd /media/sf_ML/rag-bot
echo "🛑 Останавливаю контейнеры..."
sudo docker compose down

echo "🔨 Пересобираю образ бота..."
sudo docker compose build --no-cache bot

echo "🚀 Запускаю контейнеры..."
sudo docker compose up -d

echo "📜 Смотрю логи (Ctrl+C для выхода)..."
sudo docker logs -f rag-bot
