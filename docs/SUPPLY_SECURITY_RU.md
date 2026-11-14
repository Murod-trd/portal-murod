> Актуальное уточнение rc.12, 03.11.2026: по прямому решению владельца стандартный вход — логин/пароль без дополнительных экранов; сессии 12 часов, интерфейс UZ/RU/EN. Прежние обязательные MFA/первую смену заменяет [ADR 003](adr/003-password-login-and-three-ui-languages.md). [Текущий технический статус](RC12_TECHNICAL_STATUS_RU.md). Linux-установка и корпоративное подключение переданы ИТ.

# Комплектация безопасности rc.11

Дата: 03.11.2026. Все фактические базовые rootfs, подготовленные PostgreSQL/Node-tools и окончательные API/worker прошли `operations/security_scan.py --strict`. Любой High/Critical, включая unfixed, по-прежнему блокирует результат; CVE-ignore/понижения severity/исключений нет. Это датированный скан, не независимый пентест или гарантия отсутствия будущих CVE. Полные raw reports: verification/2026-11-03-rc11-security-*/.

## Реальные входы сборки

`deploy/images.lock.json` закрепляет фактические rootfs. PostgreSQL и Node aliases используют один чистый Alpine 3.24 digest, не прежние upstream контейнеры с gosu/bundled npm. Пять именованных параметров сохранены; уникальных rootfs четыре.

- PostgreSQL **17.12-r0** (server/client/contrib) и su-exec **0.3-r0** устанавливаются из подписанных Alpine APK. Вендорный MIT upstream entrypoint сохранён с единственной заменой gosu на su-exec; исходный commit, SHA-256 и лицензия — deploy/postgres/UPSTREAM.json и LICENSE. Реальные версии пакетов/сервера/entrypoint проверяются инвентаризацией, не тегом образа.
- Новый `POSTGRES_STORAGE_FAMILY=postgresql17-apk-alpine3.24` привязан к package major. Upstream→APK, major или OS-family переход на прежнем PGDATA запрещён. Копии покрывают family HMAC-подписью; несовпадение/отсутствие family при восстановлении в новую семью отклоняется до изменения сервисов. Переход — только отдельная логическая миграция в новые тома.
- Node **24.22.2** — официальный musl-архив nodejs.org из deploy/node-runtime.lock.json. Python downloader проверяет полный archive SHA-256, exact bin/node + LICENSE, регулярность/уникальность файлов, лимиты и binary SHA-256/ELF x86_64. Bundled npm/yarn не извлекаются и не исполняются. Подпись PGP checksum-list отдельно проверенной не заявляется.
- npm **12.2.1** и undici **6.29.1** загружаются из официального registry с отдельным полным SHA-512, ограничениями безопасной распаковки и проверкой имени/версии. Старые инструменты не используются для установки новых. Node/npm существуют только в одноразовом frontend builder, не в production backend.
- Зависимости Python устанавливаются по hash-lock. pip/ensurepip удаляются целиком из финальных runtime API/worker; verification target получает инструменты отдельно. Tesseract **5.5.4** собирается по закреплённому upstream source SHA; инвентаризируются версия и бинарный SHA, поскольку общий ОС-скан не доказывает анализ самодельного бинарника OCR.

## Подтверждённые результаты

| Область | High/Critical | Другие результаты |
|---|---|---|
| Пять базовых aliases | 0/0 | Неизменённая Python base: 5 Medium/1 Low |
| Подготовленный PostgreSQL | 0/0 | Находок нет |
| Одноразовый frontend-tools | 0/0 | 3 Medium сохранены: glob, ip-address, minimatch; см. raw report |
| Окончательные API и worker | 0/0 | Находок нет по использованной базе Trivy |

Проектные npm-зависимости при чистом npm ci: 0 vulnerabilities. Bandit: 0 High, 0 Medium, 78 Low, без nosec. Наличие Medium у одноразовых инструментов не скрывается и не означает прохождения независимой внешней приёмки.

Инвентаризация и отчёты связаны с конкретными image IDs. Последний final scan: [summary](verification/2026-11-03-rc11-security-final-promoted/summary.json). Python и frontend bytes сверены с текущими исходниками. Новый Linux/серверный прогон ИТ должен обновить базу сканера и повторить gate. `productionApproved=false`.
