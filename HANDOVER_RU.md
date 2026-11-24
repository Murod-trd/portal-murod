# Установка и передача ИТ — rc.12

> Актуальная пошаговая инструкция для **0.3.0-rc.13**: [server-handoff/handout.md](server-handoff/handout.md).

03.11.2026. Штатный Linux-прогон и подключение корпоративного сервера переданы владельцем ИТ-службе. Локальные результаты и границы: [FOR_IT_RU.md](FOR_IT_RU.md), [статус rc.12](docs/RC12_TECHNICAL_STATUS_RU.md).

## До установки

Получить окончательный архив с manifest, контрольной суммой и датированными результатами. ИТ готовит Linux x86_64, ресурсы по DEPLOYMENT.md, частный IP/DNS, TLS, офисные/VPN CIDR, отдельное место копий и recovery-ключа. Не использовать реальные персональные данные на приёмочном стенде.

Ниже **примеры**, не параметры компании. После разрешённой приёмки из постоянного каталога релиза:

```bash
sudo python3 portalctl configure --origin https://portal.company.internal \
  --bind 10.20.0.10 --allow 10.20.0.0/16 --allow 10.8.0.0/24 \
  --tls-cert /secure/fullchain.pem --tls-key /secure/privkey.pem \
  --ca-file /secure/company-root-ca.pem \
  --backup-dir /mnt/backup/toshkent-portal
sudo python3 portalctl doctor
sudo python3 portalctl install --admin-login individual.admin --admin-name 'ФИО администратора'
sudo python3 portalctl schedule --enable
```

Пароль первого администратора вводится скрыто; не помещать его в команду, чат или лог. В стандартном согласованном режиме достаточно индивидуальных логина и пароля: дополнительных экранов и аутентификатора нет. Серверная сессия — 12 часов. Повторная configure не перезаписывает секреты. После установки:

```bash
sudo python3 portalctl check --pipeline --files
sudo python3 portalctl status
sudo python3 portalctl backup
```

Install собирает PostgreSQL/API/worker (без Node: интерфейс приходит в релизе готовым, [ADR 004](docs/adr/004-no-node-on-server.md)) и выполняет реальные диагностические стадии. Он не заменяет независимый пентест и внешнюю приёмку. Для сборки нужны официальные источники образов, Python, OCR-моделей и свежих ClamAV-сигнатур либо проверенные корпоративные зеркала. Это не офлайн-пакет готовых контейнеров.

## Обновление и восстановление

Инструкции: [OPERATIONS.md](docs/OPERATIONS.md), [RESTORE_ACCEPTANCE.md](docs/RESTORE_ACCEPTANCE.md), [POSTGRES_MIGRATION_RU.md](docs/POSTGRES_MIGRATION_RU.md). Сначала аутентифицированная копия и план отката; восстановление проверять отдельно, с новыми томами. Recovery-ключ хранится отдельно от копий и исходников. Между снимком и восстановлением запись не открывать.

`portalctl restore` требует явные hostname/--replace и соответствующие release-образы API/worker. Смена PostgreSQL major/ОС/storage family на старом томе запрещена. Не удалять прежние данные/тома для обхода отказа.

## Статус

rc.12 прошёл перечисленные локальные проверки; полный Linux install/acceptance остаётся задачей ИТ. Исторические отчёты лежат в docs/verification; scope каждого ограничен соответствующим запуском. Наличие архива, кода установщика или технического PASS не является разрешением эксплуатации. Корпоративные DNS/TLS/VPN и допуск реальных данных выполняет ИТ. `productionApproved=false`.

## При обновлении старой политики входа

В существующей .deploy/portal.env явно установить REQUIRE_MFA=none, REQUIRE_PASSWORD_CHANGE=false, SESSION_TTL_SECONDS=43200, SESSION_IDLE_SECONDS=43200 и пересоздать собственные app-сервисы штатным обновлением. Старые явно заданные параметры не заменяются молча. Не удалять аккаунты, OTP-данные или тома. Legacy-имя поля API temporaryPassword оставлено для совместимости; обязательная смена выданного пароля в стандартном режиме не требуется.
