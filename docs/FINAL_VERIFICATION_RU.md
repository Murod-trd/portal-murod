> Актуальное уточнение rc.12, 03.11.2026: по прямому решению владельца стандартный вход — логин/пароль без дополнительных экранов; сессии 12 часов, интерфейс UZ/RU/EN. Прежние обязательные MFA/первую смену заменяет [ADR 003](adr/003-password-login-and-three-ui-languages.md). [Текущий технический статус](RC12_TECHNICAL_STATUS_RU.md). Linux-установка и корпоративное подключение переданы ИТ.

# Воспроизводимые проверки rc.11

Локальные результаты выполненных проверок приведены в [статусе rc.11](RC11_TECHNICAL_STATUS_RU.md) и docs/verification. Следующие команды описывают воспроизведение. Полную штатную Linux-команду и подключение сервера владелец передал ИТ. Не считать её выполненной по результатам отдельных контейнерных стадий.

## Код и frontend

Использовать отдельный venv, существующие hash-locks, вымышленные данные, случайные тестовые ключи и отдельную PostgreSQL test-БД. Production-параметры не использовать. После подготовки окружения:

```bash
python -m pytest backend/tests -q
python -m unittest operations.test_package_release
ruff check backend operations scripts --select F,E9
bandit -r backend operations scripts -x backend/tests,backend/portal/migrations -ll
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py spectacular --validate --fail-on-warn --file /tmp/portal-openapi.yaml
python backend/manage.py generate_api_types --check
npm run typecheck
npm run lint
npm run test:web
npm run build:web
python operations/frontend_dist.py
```

`npm run build:web` запускает Vite через `apps/web/scripts/build.mjs` (только CI/машина разработчика, не сервер); `operations/frontend_dist.py` перепроверяет результат на Python так же, как сборка образа на сервере. При неуспехе или изменении исходников в процессе provenance не выдаётся. Успешная сборка создаёт `frontend-dist/BUILD-PROVENANCE.json`: хеши исходников и всех статических артефактов. Это подтверждение происхождения сборки, не серверная/браузерная приёмка.

Дополнительно проверить новые сценарии: отказ загрузчика инструментов при неправильном SHA/redirect/path traversal/символической ссылке; отказ упаковщика при устаревшем frontend, изменённом asset, symlink и несовпадении состава файлов. Проверки downloader входят в 283 PostgreSQL-теста; 10 stdlib packaging-тестов прошли отдельно. `--include-frontend` выполнен и архив-кандидат проверен извлечением, manifest/CRC/UTF-8/secret scan.

## Контейнерный стенд

Разработчик создаёт manifest из чистого Git. Получатель готового архива Git не восстанавливает и manifest не пересоздаёт: проверяет переданный manifest и запускает acceptance.py. Для разработчика:

```bash
python3 operations/package_release.py --manifest-only
python3 operations/acceptance.py --work-dir /var/tmp/toshkent-rc11-acceptance-01 --keep-lab
```

Каталог должен быть новым и вне исходников. Команда создаёт только собственные два проекта и не направляется на рабочий портал. Она собирает PostgreSQL/frontend-tools/API/worker/verification, запускает native PostgreSQL и benchmark, настоящие AV/OCR/HTTPS workflows, backup/restore и строгие сканы `base`, `prepared`, `final`. Не ослаблять требования ради exit code. Все текущие base/prepared/final gates локально прошли; любые новые High/Critical блокируют повторный прогон. Исторические upstream rc.10 отчёты сохранены и не являются входами сборки rc.11.

Для нового PostgreSQL target отдельно подтвердить initdb → роли/pg_trgm → ограниченный app → restart/stop/сигналы → pg_dump/pg_restore в новый том. Для frontend-tools — реальные версии npm/undici, npm ci по существующему lock, typecheck/lint/build и полный Trivy. Сохранить image ID и raw reports. В `prepared`-скане отсутствие ожидаемой инвентаризации даёт отказ.

Браузерная матрица: [BROWSER_ACCEPTANCE_RU.md](BROWSER_ACCEPTANCE_RU.md). Второй физический сервер, RPO/RTO компании, реальный OCR-корпус, нагрузка/отказы, независимый пентест и офис/VPN — отдельные свидетельства; один локальный прогон их не подтверждает.

## Комплект для ИТ после проверок

Зафиксировать окончательный код и документы. После успешной frontend-сборки того же исходного дерева:

```bash
python3 operations/package_release.py --include-frontend --output /secure-release/Toshkent_Invest_Portal_rc11.zip
cd /secure-release
sha256sum -c Toshkent_Invest_Portal_rc11.zip.sha256
```

Упаковщик требует чистый Git, проверяет исходные/artifact-хеши frontend, включает общий `RELEASE-MANIFEST.json` и атомарно записывает рядом `<archive>.sha256`; секреты, зависимости и `.deploy` не включаются. Для source-only архива опустить `--include-frontend`. После упаковки отдельно извлечь в новый каталог и сверить состав/хеши manifest. Не выдавать старый frontend-dist за новую сборку; отсутствие provenance требует пересборки.

В итоговом отчёте чётко отделить PASS/FAIL/NOT RUN, source-only/собранные образы/установку компании. `technicalChecksPassed` не выставляется вручную, `productionApproved=false` не меняется ради передачи файлов.
