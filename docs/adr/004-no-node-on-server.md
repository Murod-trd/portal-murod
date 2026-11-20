# ADR 004 — на сервере нет Node.js и JavaScript

20.11.2026. Уточнение [ADR 001](001-python-backend.md) по прямому правилу владельца: бэкенд и всё серверное (API, worker, `operations/`, `portalctl`, сборка образов на сервере, скрипты деплоя) — только Python; shell — только там, где он уже используется для инфраструктуры. JavaScript/TypeScript допускается исключительно в `apps/web/`: браузерный React-интерфейс, который только показывает данные и решения бэкенда.

Что изменилось относительно rc.12:

- Сервер больше не собирает фронтенд. Раньше `portalctl install/update` строил на сервере контейнер `frontend-tools` с Node 24 + npm. Он удалён вместе с `deploy/node-runtime.lock.json`, `deploy/build-tools.lock.json`, загрузчиками Node/npm и JS-сниппетом в `operations/security_scan.py`.
- Статический бандл `frontend-dist/` собирается в GitHub CI (или на машине разработчика) и приходит на сервер внутри релизного архива (`package_release.py --include-frontend`). `portalctl` отказывается ставить релиз без готового фронтенда.
- При сборке образа сервер только проверяет SHA-256 исходников и артефактов из `BUILD-PROVENANCE.json` кодом на Python (`operations/frontend_dist.py`) и копирует файлы. В образах API/worker Node и npm нет.
- Весь JS-инструментарий фронтенда лежит в `apps/web/` (`scripts/build.mjs`, `tests/audit.mjs`, `eslint.config.js`). Тест `test_server_side_has_no_javascript` не даёт появиться JS/TS вне `apps/web/`, сервисам Node в compose и вызовам node/npm из серверного Python.

Где взять готовый релиз: артефакт `release-package` каждого зелёного прогона CI на `main` (архив + `.sha256`). Задание `server-images` в CI собирает образы API и worker из готового бандла и проверяет, что Node в них нет.

Следствие для доверия: на сервере больше не воспроизводится сборка фронтенда из исходников; целостность обеспечивают manifest релиза и provenance бандла, привязанные к исходникам того же коммита.
