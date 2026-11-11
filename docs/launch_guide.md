# 🚀 Запуск пилота: пошаговый гайд

## Шаг 1. Топ-ап Vast.ai
- Регистрация: vast.ai → аккаунт пополняется **криптой** (USDT/ETH/BTC) или картой
- Рекомендуемый старт: **$20-50** (A100 на $0.80/ч = ~60 часов теста)

## Шаг 2. Аренда карточки
В фильтрах: нужный GPU (смотри панель — где сейчас низкая медиана), num_gpus=1,
reliability > 0.98, inet_down > 500 Mbps. При создании инстанса:
- Docker image: `vllm/vllm-openai:latest`
- On-start script: содержимое `deploy/onstart.sh` (модель в переменной MODEL)
- Port: 8000 → Host port (http) — API доступен снаружи

## Шаг 3. Проверка
```bash
curl http://<адрес>:<порт>/v1/models
curl http://<адрес>:<порт>/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"Qwen2.5-7B-Instruct","messages":[{"role":"user","content":"hi"}]}'
```

## Шаг 4. Заявка в OpenRouter Providers
- Форма: https://openrouter.ai/providers/apply
- Что дают: авто-роутинг трафика 10M+ разработчиков, биллинг, аптайм-мониторинг
- Черновик ответов (заполни и отправь):
  - *What service do you provide?* — OpenAI-compatible inference endpoint for open models (vLLM), model: <MODEL>, hosted on dedicated rented GPUs with auto-restart and health monitoring
  - *Models & pricing:* — model list + pricing per 1M tokens (ориентир: минимум на 15-20% ниже медианы OpenRouter, см. панель)
  - *Uptime/SLA:* — on-demand instances with automated restart, ~99% target
  - *Endpoint:* — https://<адрес>:<порт>/v1
- ⚠️ Перед подачей проверить раздел payouts: способы выплат для твоей юрисдикции (это главный гейт пилота)

## Шаг 5. Ценообразование
Смотри панель: цена должна быть **ниже медианы OpenRouter** по этой модели
(тогда роутер ведёт трафик к тебе), но **выше себестоимости аренды**. Держи маржу из расчёта панельной колонки «маржа/ч».

## Правила безопасности
- ❌ Не перепродавай голые инстансы (ToS-бан)
- ❌ Не гоняй недоверенный код клиентов
- ✅ Автоперезапуск + health-check обязательны (аптайм влияет на роутинг)
