# Toshkent Invest — корпоративный портал

Текущая редакция **0.3.0-rc.13** (подготовка к серверу; проверки rc.12 ниже относятся к rc.12). Интерфейс: **UZ / RU / English**. По решению владельца вход — логин и пароль, без обязательных дополнительных экранов; серверная сессия на 12 часов. Новые возможности и подтверждённая область: [статус rc.12](docs/RC12_TECHNICAL_STATUS_RU.md), [5 замечаний владельца](docs/USER_REVIEW_TRACKER_RU.md). Штатную Linux-установку и подключение сервера выполняет ИТ. `productionApproved=false` до внешнего допуска.

Backend и worker: Python/Django/DRF. Frontend: статический React/TypeScript/Vite. Node используется только для сборки браузерных файлов. Сервер, DNS/TLS/VPN и установку компании настраивает ИТ.

Установка на сервер через Claude: [server-handoff/](server-handoff/README.md). Для ИТ: [FOR_IT_RU.md](FOR_IT_RU.md). Инструкции: [HANDOVER_RU.md](HANDOVER_RU.md), [DEPLOYMENT.md](docs/DEPLOYMENT.md), [OPERATIONS.md](docs/OPERATIONS.md). Предстоящие проверки: [FINAL_VERIFICATION_RU.md](docs/FINAL_VERIFICATION_RU.md), браузерная матрица — [BROWSER_ACCEPTANCE_RU.md](docs/BROWSER_ACCEPTANCE_RU.md).

## Реализованные разделы

- Сотрудники, поиск с серверной приватностью, офисы и структура; индивидуальные аккаунты EMPLOYEE/HR/ADMIN, пароль/сессии/CSRF/MFA.
- Свой мобильный/видимость/этаж/кабинет; внутренний номер видим; год рождения скрыт от обычных коллег. Архивирование отзывает доступ, восстановление карточки не включает аккаунт автоматически.
- CSV/XLSX-импорт с предпросмотром, решениями о совпадениях и атомарным commit; фото отдельной защищённой загрузкой.
- Новости, переводы RU/UZ, редактор, даты Ташкента, закрепление и уникальные открытия без поимённого контроля читателей.
- Документы с неизменяемыми редакциями, читаемыми старыми URL, текущей редакцией, поиском внутри PDF/Word/сканов; карантин, ClamAV и изолированный Python OCR-worker.
- Корзина, аудит, миграции, операции установки/обновления и аутентифицированное шифрованное копирование/восстановление.

## Изменения rc.10

Подготовленный PostgreSQL target заменяет Go-helper gosu на Alpine su-exec. На сервере нет Node/npm: фронтенд собирается в CI, релиз приходит с готовым `frontend-dist/`, сервер только проверяет его SHA-256 на Python ([ADR 004](docs/adr/004-no-node-on-server.md)). Установщик собирает PostgreSQL/API/worker; сканер дополнен строгой инвентаризацией prepared-образов. Прежний строгий upstream-скан сохранён. Подробности и оставшиеся блокеры: [SUPPLY_SECURITY_RU.md](docs/SUPPLY_SECURITY_RU.md).

Frontend-сборка теперь записывает хеши входных файлов и артефактов. Упаковщик умеет включать статическую сборку через `--include-frontend` и отказывается от несовпадающей/устаревшей сборки. Код этих изменений ещё не исполнялся.

## Разработка

Обычная работа с исходниками не требует запуска Docker. Использовать отдельный Python venv и существующие hash-locks; Node нужен только для frontend. SQLite разрешена для разработки и не подтверждает PostgreSQL. Для настоящих загрузок нужны реальный ClamAV и Linux/seccomp; OCR использует Poppler/Tesseract и закреплённые модели. Без AV допуск файла закрыт.

Windows (проверено на Python 3.14): `python -m venv C:\pv`; затем `C:\pv\Scripts\python -m pip install --require-hashes --no-deps -r requirements.lock -r requirements-dev.lock` и `C:\pv\Scripts\python -m pip install tzdata colorama` (в Linux-lock они не входят). Настройка: `C:\pv\Scripts\python scripts/setup.py` (создаёт `.runtime`, миграции и демо-данные; пароли в `.runtime/demo-credentials.json`). Запуск: `C:\pv\Scripts\python scripts/dev.py` (API на :3000; worker файлов/OCR на Windows не запускается, нужен Linux/Docker/WSL). Тесты: `C:\pv\Scripts\python -m pytest backend/tests -q`; тесты seccomp и POSIX-прав на Windows пропускаются.

Команды сборки/проверок и упаковки приведены в FINAL_VERIFICATION_RU.md для последующего разрешённого запуска. Production не использует setup.py/seed_demo, тестовые пароли или вымышленные аккаунты.

## Статус доказательств

[IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md), [SECURITY_ACCEPTANCE.md](docs/SECURITY_ACCEPTANCE.md), [RESTORE_ACCEPTANCE.md](docs/RESTORE_ACCEPTANCE.md) и датированные `docs/verification/` содержат область каждого прогона. Исторические 234 PostgreSQL tests и AV/OCR/restore относятся к прежнему коммиту e01d909 и его кандидатам. Они не подтверждают новые Dockerfile, downloader, packaging или rc.10. Текущий authoring-статус: `docs/verification/2026-11-02-rc10-authoring.json`.

Известные upstream High/Critical не объявлены закрытыми. Финальная браузерная/серверная приёмка и архив со свежей frontend-сборкой отложены. `productionApproved=false`.
