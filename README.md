# Lab 5 — Failure Detection and Fallback Reads

## Запуск

Из папки `lab5` чтобы установить все нужное:

```powershell
python -m pip install -r requirements.txt
```

Для автоматического выполнения всех пяти экспериментов:

```powershell
python demo.py
```

Работает на портах 5001–5003. Скрипт запускает три реальных процесса, проверяет HTTP-ответы,
останавливает и перезапускает только собственные процессы, затем завершает их. Общий лог всех
сервисов и проверок: `logs/experiments.log`. Повторный запуск перезаписывает лог.

Если нужно запустить все вручную:

```powershell
python catalog_service.py --role replica
python catalog_service.py --role primary
python orders_service.py
```

| Сервис | Порт | Health |
| --- | --- | --- |
| Catalog Primary | 5001 | GET /health |
| Catalog Replica | 5002 | GET /health |
| Order Service | 5003 | GET /health |

Дальше сделать это или сделать то же самое в постмане

```powershell
Invoke-RestMethod http://127.0.0.1:5001/products/1 -Method Put -ContentType application/json -Body '{"price":450000,"mode":"sync"}'
Invoke-RestMethod http://127.0.0.1:5003/orders -Method Post -ContentType application/json -Body '{"product_id":1,"quantity":1}'
```

## Поведение

Каждый заказ сначала читает Primary. Таймаут по умолчанию 1 секунда. Ошибка соединения, таймаут,
HTTP-ошибка или некорректный ответ приводят к чтению Replica. Если оба каталога недоступны,
Order возвращает HTTP 503 с причинами. Валидный HTTP 404 означает отсутствие товара и возвращается
без fallback. Отдельный health-запрос перед каждым заказом не нужен: успешный health не гарантирует
успешность следующего чтения.

Ответ заказа содержит order_id, catalog_source, catalog_version, цену и остальные поля товара.
Источник выбирается автоматически, передавать catalog_source не требуется.
После восстановления следующий заказ снова начинает с primary.Ппостоянного переключения или
кэширования отказа нет.

Replica отклоняет клиентские POST /products и PUT /products/1 с HTTP 405 (тк записывать может только лидер).

План как были проведены тесты

## Пять экспериментов вручную

1. При работающих сервисах создайте заказ: `catalog_source=primary`, цена 450000, версия 2.
2. Остановите Primary через Ctrl+C и повторите заказ: `catalog_source=replica`. В терминале Order будет причина ошибки Primary и сообщение о fallback.
3. Снова запустите Primary, восстановите baseline командой sync выше и создайте заказ: источник снова `primary`.
4. Перезапустите Primary командой `python catalog_service.py --role primary --response-delay 3`, восстановите baseline и создайте заказ. Order получит ReadTimeout через 1 секунду и прочитает Replica. GET /health Primary по-прежнему отвечает; прямой GET /products/1 завершится через 3 секунды.
5. Перезапустите Primary без задержки чтения: `python catalog_service.py --role primary --replication-delay 5`. Восстановите baseline 450000/v2, затем выполните команду ниже и остановите Primary в течение 5 секунд. Новый заказ получит из Replica старую цену 450000/v2, хотя Primary уже принял 400000/v3.

```powershell
Invoke-RestMethod http://127.0.0.1:5001/products/1 -Method Put -ContentType application/json -Body '{"price":400000,"mode":"async"}'
```

## Ручные эксперименты

### Проверка сервисов и подготовка

Primary отвечает на GET /health: HTTP 200, status ok.

![Primary отвечает на GET /health: HTTP 200, status ok.](screenshots/01-primary-health.png)

Replica отвечает на GET /health: HTTP 200, status ok.

![Replica отвечает на GET /health: HTTP 200, status ok.](screenshots/02-replica-health.png)

Синхронно установлена цена 450000, версия 2.

![Синхронно установлена цена 450000, версия 2.](screenshots/03-baseline-sync.png)

Прямое чтение Replica подтверждает цену 450000 и версию 2.

![Прямое чтение Replica подтверждает цену 450000 и версию 2.](screenshots/05-replica-product.png)

### Эксперимент 1 — обычная работа

Заказ 1: HTTP 201, catalog_source primary, catalog_version 2, цена 450000.

![Заказ 1: HTTP 201, catalog_source primary, catalog_version 2, цена 450000.](screenshots/04-normal-primary-order.png)

### Эксперимент 2 — Primary остановлен

Primary остановлен: в терминале снова доступна строка ввода.

![Primary остановлен: в терминале снова доступна строка ввода.](screenshots/06-primary-stopped.png)

Заказ 2 успешно создан из Replica: цена 450000, версия 2, время ответа 1.05 с.

![Заказ 2 успешно создан из Replica: цена 450000, версия 2, время ответа 1.05 с.](screenshots/07-stopped-primary-replica-order.png)

Order фиксирует ConnectTimeout при обращении к Primary и выполняет fallback на Replica.

![Order фиксирует ConnectTimeout при обращении к Primary и выполняет fallback на Replica.](screenshots/08-order-fallback-log.png)

### Эксперимент 3 — восстановление

Primary запущен повторно; синхронная запись получила ACK от Replica.

![Primary запущен повторно; синхронная запись получила ACK от Replica.](screenshots/09-primary-restarted.png)

После перезапуска восстановлена цена 450000, версия 2.

![После перезапуска восстановлена цена 450000, версия 2.](screenshots/10-recovery-baseline.png)

Заказ 3 снова читает Primary: HTTP 201, цена 450000, версия 2.

![Заказ 3 снова читает Primary: HTTP 201, цена 450000, версия 2.](screenshots/11-recovered-primary-order.png)

### Эксперимент 4 — медленный Primary

Primary запущен с параметром --response-delay 3.

![Primary запущен с параметром --response-delay 3.](screenshots/12-slow-primary-start.png)

Перед чтением восстановлен baseline 450000/v2.

![Перед чтением восстановлен baseline 450000/v2.](screenshots/13-slow-primary-baseline.png)

Заказ 4 получен из Replica за 1.07 с при задержке Primary 3 с.

![Заказ 4 получен из Replica за 1.07 с при задержке Primary 3 с.](screenshots/14-slow-primary-replica-order.png)

Primary продолжает отвечать на GET /health: HTTP 200, status ok.

![Primary продолжает отвечать на GET /health: HTTP 200, status ok.](screenshots/15-slow-primary-health.png)

Лог Primary показывает задержку чтения 3.0 с и последующий успешный ответ.

![Лог Primary показывает задержку чтения 3.0 с и последующий успешный ответ.](screenshots/16-primary-delay-log.png)

Лог Order показывает ReadTimeout с timeout 1.0 с и успешный fallback. Таймаут не доказывает остановку Primary.

![Лог Order показывает ReadTimeout с timeout 1.0 с и успешный fallback. Таймаут не доказывает остановку Primary.](screenshots/17-order-read-timeout-log.png)

### Эксперимент 5 — устаревшая Replica

Перед асинхронным обновлением оба каталога синхронизированы: 450000/v2.

![Перед асинхронным обновлением оба каталога синхронизированы: 450000/v2.](screenshots/18-stale-replica-baseline.png)

Primary принял асинхронное изменение цены на 400000, версия 3, и вернул SUCCESS.

![Primary принял асинхронное изменение цены на 400000, версия 3, и вернул SUCCESS.](screenshots/19-async-new-price.png)

При повторной попытке Primary остановлен до отправки v3. ACK для v3 в верхней строке относится к предыдущему запуску; после новой команды запуска виден ACK только для baseline v2.

![При повторной попытке Primary остановлен до отправки v3. ACK для v3 в верхней строке относится к предыдущему запуску; после новой команды запуска виден ACK только для baseline v2.](screenshots/20-primary-stopped-before-delivery.png)

Заказ 5 читает Replica и получает старую цену 450000/v2 вместо принятой Primary цены 400000/v3.

![Заказ 5 читает Replica и получает старую цену 450000/v2 вместо принятой Primary цены 400000/v3.](screenshots/21-stale-replica-order.png)

## Результаты

Фактические HTTP-ответы, версии, ошибки и длительности приведены в `logs/experiments.log`.

| Эксперимент | Primary | Источник заказа | Цена / версия | Результат |
| --- | --- | --- | --- | --- |
| 1. Normal operation | Работает | primary | 450000 / 2 | Актуальные данные |
| 2. Primary stopped | Остановлен | replica | 450000 / 2 | Чтение продолжается |
| 3. Recovery | Перезапущен, baseline восстановлен | primary | 450000 / 2 | Возврат к основному сервису |
| 4. Slow primary | Работает, задержка 3 с | replica | 450000 / 2 | Ложное подозрение после таймаута 1 с |
| 5. Stale replica | Принял 400000/v3, остановлен до доставки | replica | 450000 / 2 | Возвращена старая цена |

