# Handout: передача и установка портала Toshkent Invest

Единый документ для ИТ-отдела и для Claude, подключённого к серверу компании. Здесь всё: что передаётся, состояние проекта, установка, проверка, приёмка, эксплуатация, ошибки. При расхождении с другими документами репозитория для установки главный этот файл.

Релиз: `0.3.0-rc.13` или новее. В заголовках части старых документов указан rc.12: ставить нужно rc.13.

| Кто читает | Разделы |
|---|---|
| Руководитель ИТ | А, Б, В |
| Администратор, который ставит портал | В, затем 0–9 |
| Claude на сервере | 0–9, 11, 12; границы разрешений в [PROMPT.md](PROMPT.md) |
| Эксплуатация | 10, 11, 13 |
| Приёмка | 14 |

## А. Что передаётся

- **Портал**: сотрудники, структура, офисы, новости, документы с поиском внутри сканов, корзина, роли «сотрудник», HR, администратор; языки UZ / RU / EN. Вход по логину и паролю, сессия 12 часов ([ADR 003](../docs/adr/003-password-login-and-three-ui-languages.md)).
- **Состав на сервере**: один Linux-сервер, Docker Compose, контейнеры `proxy` (Nginx, HTTPS), `app` (Django), `worker` (проверка файлов и OCR), `postgres` 17, `clamav`. Наружу открыт один порт HTTPS на частном IP. Node.js на сервере нет.
- **Управление**: `portalctl` (Python, стандартная библиотека).
- **Архив для установки**: артефакт `release-package` последнего зелёного запуска [CI на `main`](https://github.com/Murod-trd/portal-murod/actions/workflows/ci.yml?query=branch%3Amain). Открыть запуск, внизу раздел Artifacts (нужен вход в GitHub). Внутри скачанного файла лежат ZIP релиза и `.sha256`. Клон репозитория для установки не подходит. Артефакт хранится 30 дней: после скачивания сохранить копию у себя.
- **Не передаются**: пароли, ключи, сертификаты, данные сотрудников. Секреты создаёт `portalctl configure` на сервере.

## Б. Состояние на 24.11.2026

| Проверено в CI для кода rc.13 ([запуск](https://github.com/Murod-trd/portal-murod/actions/runs/45383497043), коммит `12408f0`) | Результат |
|---|---|
| Тесты backend на PostgreSQL | 477 прошли, 2 пропущены (OCR: в CI нет его программ) |
| Тесты упаковщика | 11 прошли |
| Интерфейс: типы, линтер, тесты, сборка | прошли |
| Сборка образов PostgreSQL, API, worker; роли базы; `nginx -t` | прошли |
| Архив релиза: состав и права файлов | прошли |

Отдельно 24.11.2026 закреплённые версии пакетов Python и npm сверены с базой OSV: известных уязвимостей не найдено.

**Не проверено, остаётся на установку и приёмку:**

- полная установка `portalctl install` на Linux и работа всех контейнеров вместе;
- распознавание сканов и антивирус в rc.13 (в rc.12 проверялись локально);
- скан Docker-образов для rc.13;
- DNS, TLS, VPN компании, нагрузка, восстановление на втором сервере, независимый пентест.

Код готов к установке и приёмке. `productionApproved=false`: **реальные данные сотрудников не вводить до завершения раздела 14.**

## В. Что делает ИТ

**Подготовить до установки:**

| Что | Требование |
|---|---|
| Сервер | отдельная Linux x86_64 VM, от 4 vCPU, 16 ГБ RAM, от 40 ГБ свободно |
| Программы | Docker Engine, Compose 2.24+, Python 3.10+, OpenSSL, unzip, curl |
| Сеть | прямой выход в интернет для сборки и сигнатур ClamAV (список в разделе 1) |
| Адрес | внутреннее DNS-имя и частный IPv4 сервера |
| Доступ | точные CIDR офиса и VPN |
| TLS | сертификат с цепочкой и ключ на имя сайта; корневой CA, если он внутренний |
| Копии | отдельный диск или NAS; recovery-ключ хранится отдельно от копий |
| Люди | логин и ФИО первого администратора, ответственный за копии и мониторинг |

**Порядок:**

1. Получить доступ к репозиторию, скачать архив, сверить SHA-256 (раздел 2).
2. На отдельном стенде без реальных данных выполнить полную проверку (раздел 14, пункт 1).
3. Установить на рабочий сервер: разделы 0–9. Вручную или через Claude: заполнить и вставить [PROMPT.md](PROMPT.md).
4. Пройти приёмку (раздел 14), затем открыть портал сотрудникам.

**Запрещено:** править файлы релиза на сервере; отключать антивирус, TLS, проверку manifest, песочницу worker, список сетей; выполнять `docker compose down -v`, `docker system prune`, `docker volume rm`; загружать демо-данные; пересылать содержимое `.deploy/`, пароли и ключи.

**Ошибка в коде:** прислать владельцу релиз, точный текст ошибки, раздел и команду, без секретов. Исправление делается в репозитории, затем новый зелёный CI и новый архив.

---

# Установка

Обозначения: `R=/opt/toshkent-portal/portal` (каталог релиза). Все команды `portalctl` выполняются из `R` от root. Команды с примерами адресов нужно заменить значениями владельца.

Порядок: 0 осмотр → 1 пакеты → 2 релиз → 3 TLS/DNS → 4 configure → 5 doctor → 6 install → 7 проверка → 8 расписание → 9 передача. Не переходить дальше, пока шаг не прошёл.

## 0. Осмотр (только чтение)

```bash
cat /etc/os-release; uname -m; nproc; free -g; df -h / /opt /var/lib
python3 --version; openssl version; docker version; docker compose version
ip -4 addr; ss -ltnp; timedatectl
docker ps -a; docker volume ls; docker network ls
docker context inspect; docker info --format '{{json .SecurityOptions}}'
getenforce 2>/dev/null; ls -la /opt/toshkent-portal 2>/dev/null
```

| Требование | Если не так |
|---|---|
| Linux, `x86_64` | Windows Server: нужна отдельная Linux-VM. Остановиться и сообщить |
| ≥ 4 vCPU | остановиться и сообщить |
| Docker видит ≥ 12 ГиБ RAM | VM «ровно 12 ГБ» покажет около 11,7 ГиБ и не пройдёт. Выделить 16 ГБ (минимум 13) |
| ≥ 40 ГиБ свободно там, где `/opt` и где `/var/lib/docker` | `doctor` проверяет только каталог релиза, `/var/lib/docker` проверить самому |
| Docker не rootless и без userns-remap, сокет `/var/run/docker.sock` | не менять daemon без согласия владельца |
| Время синхронизировано (NTP) | иначе ломаются сессии, TLS и свежесть антивируса |
| Нужный порт HTTPS свободен на нужном IP | занят: спросить владельца про другой порт, например `https://host:8443` |

**Если портал уже установлен** (есть `R/.deploy` или тома `toshkent-invest-portal_*`): не выполнять `configure`/`install` заново и ничего не удалять. Сообщить владельцу; обновление делается по разделу 10.

## 1. Пакеты

Поставить только недостающее, из официальных источников: Docker Engine + compose plugin 2.24+ (https://docs.docker.com/engine/install/ для данной ОС), Python 3.10+, openssl, unzip, curl. `gh` нужен, только если архив скачивается с GitHub.

Не ставить Node.js и npm. Python-пакеты на хост не нужны: `portalctl` использует только стандартную библиотеку.

Проверка: `docker run --rm hello-world` (контейнер удалится сам), `docker compose version` ≥ 2.24, `systemctl is-enabled docker`.

Выход в интернет нужен к: `registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com`, `dl-cdn.alpinelinux.org`, `pypi.org`, `files.pythonhosted.org`, `codeload.github.com`, `raw.githubusercontent.com`, `database.clamav.net`. Проверить заранее:

```bash
for h in registry-1.docker.io dl-cdn.alpinelinux.org pypi.org files.pythonhosted.org codeload.github.com raw.githubusercontent.com database.clamav.net; do
  printf '%s ' "$h"; curl -sS -o /dev/null -m 15 -w '%{http_code}\n' "https://$h/" || true; done
```

Любой HTTP-код означает, что сеть доступна; ошибка curl означает блокировку, её решает ИТ.

## 2. Релиз

Ставится **только** архив `release-package` из зелёного запуска CI на `main`. Клон репозитория не подходит: в нём нет `frontend-dist/` и `RELEASE-MANIFEST.json`, `doctor` его отклонит.

Вариант А: владелец загрузил ZIP и `.sha256`. Вариант Б, если владелец сам выполнил `gh auth login` на сервере:

```bash
gh run list -R Murod-trd/portal-murod -b main -w CI -L 5 --json databaseId,conclusion,headSha,createdAt
mkdir -p /root/portal-release && cd /root/portal-release
gh run download <ID зелёного запуска> -R Murod-trd/portal-murod -n release-package
```

Артефакт хранится 30 дней. Если он истёк, попросить владельца запустить CI вручную (GitHub → Actions → CI → Run workflow, ветка `main`).

Если в `main` файл `RELEASE` всё ещё `0.3.0-rc.12`, значит PR `fix/server-readiness` не смержен: остановиться и сообщить. Архив rc.12 не ставить: по коду его установка на Linux падает (права файлов и `--password-stdin`).

```bash
cd /root/portal-release
sha256sum -c toshkent-portal-*.zip.sha256
mkdir -p /opt/toshkent-portal
unzip -q toshkent-portal-*.zip -d /opt/toshkent-portal
cd /opt/toshkent-portal/portal && cat RELEASE
```

В каталоге должен лежать ровно один ZIP. Файлы релиза не редактировать: хеши проверяются по manifest. Исправление кода делается в GitHub, через новый CI и новый архив.

## 3. DNS и TLS

DNS-имя сайта должно указывать на частный IP сервера у клиентов **и на самом сервере** (`getent hosts portal.company.internal`). Если внутреннего DNS ещё нет, с согласия владельца добавить строку в `/etc/hosts` сервера; для сотрудников DNS делает ИТ.

Сертификат: PEM, полная цепочка (сначала сертификат сервера), имя сайта в SAN, срок не меньше 7 дней, ключ без пароля. Если сертификат выдан внутренним CA, нужен `--ca-file`, а корень CA должен быть доверенным на рабочих компьютерах.

Нет сертификата: **спросить владельца**. Только после явного согласия выпустить временный внутренний (корень потом надо установить на компьютеры сотрудников, иначе браузер покажет предупреждение):

```bash
umask 077; mkdir -p /root/portal-tls && cd /root/portal-tls; H=portal.company.internal
openssl req -x509 -newkey rsa:4096 -nodes -days 1825 -keyout ca.key -out ca.pem \
  -subj '/CN=Toshkent Invest Portal CA' -addext 'keyUsage=critical,keyCertSign,cRLSign'
openssl req -newkey rsa:2048 -nodes -keyout privkey.pem -out server.csr -subj "/CN=$H"
printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:%s\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n' "$H" > ext.cnf
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 397 -sha256 -extfile ext.cnf -out server.pem
cat server.pem ca.pem > fullchain.pem
openssl verify -CAfile ca.pem server.pem
```

Если адрес сайта IP, вместо `DNS:` использовать `IP:`. `ca.key` передать владельцу для хранения вне сервера.

## 4. Configure

```bash
cd /opt/toshkent-portal/portal
python3 portalctl configure --origin https://portal.company.internal \
  --bind 10.20.0.10 --allow 10.20.0.0/16 --allow 10.8.0.0/24 \
  --tls-cert /root/portal-tls/fullchain.pem --tls-key /root/portal-tls/privkey.pem \
  --ca-file /root/portal-tls/ca.pem \
  --backup-dir /mnt/backup/toshkent-portal
```

- `--bind`: частный IPv4, назначенный этому серверу. Публичный IP отклоняется намеренно.
- `--allow`: точные CIDR (адрес сети, не хоста). Подсеть самого сервера или `<bind>/32` должна входить, иначе не пройдёт собственная HTTPS-проверка. `127.0.0.1/32` добавляется автоматически.
- `--backup-dir`: абсолютный путь вне каталога релиза, лучше отдельный диск или NAS.
- `--ca-file` убрать, если цепочке доверяет ОС.

Команда создаёт `.deploy/` с секретами (права 0700/0600). Не выводить содержимое, не копировать в чат. Повторный запуск откажет: так и задумано, секреты не перезаписываются. Если параметры заданы неверно и **установка ещё ни разу не запускалась**, спросить владельца и перенести `.deploy` в сторону (`mv .deploy /root/portal-deploy-old-<дата>`), затем повторить.

## 5. Doctor

```bash
python3 portalctl doctor
```

Ожидается `"host": "compatible"`, `"packageIntegrity": "verified"`. `doctor` также открывает на чтение файлы релиза для контейнеров; `.deploy` он не меняет. Ошибку исправлять по причине, проверку не обходить.

## 6. Install

Сборка и загрузка сигнатур занимают примерно 15–60 минут. Пароль администратора: от 12 символов, не из одних цифр, не распространённый.

Вариант А (предпочтительно): владелец сам запускает в своём терминале, пароль вводится скрыто:

```bash
sudo python3 portalctl install --admin-login imya.familiya --admin-name 'ФИО администратора'
```

Вариант Б (без терминала, запускает Claude). Пароль не показывать:

```bash
umask 077; openssl rand -base64 24 > /root/portal-admin.pass
cd /opt/toshkent-portal/portal
nohup python3 portalctl install --admin-login imya.familiya --admin-name 'ФИО администратора' \
  --password-stdin < /root/portal-admin.pass > /root/portal-install.log 2>&1 &
```

Следить: `tail -n 30 /root/portal-install.log`. Владелец сам прочитает `/root/portal-admin.pass` по SSH, сохранит пароль и подтвердит; затем файл удалить.

Что делает install: pull ClamAV и Nginx → сборка PostgreSQL/API/worker (Tesseract компилируется из исходников) → PostgreSQL и ClamAV → миграции → API, worker, Nginx → готовность → администратор → живая проверка файлов → первая копия. При ошибке Nginx останавливается. После исправления причины ту же команду можно повторить: созданный администратор сохранится. После успеха повторный install запрещён.

## 7. Проверка

```bash
cd /opt/toshkent-portal/portal
python3 portalctl check --pipeline --files; echo "exit=$?"
python3 portalctl status
python3 -c 'import json;d=json.load(open(".deploy/last-check.json"));print([k for k,v in d["checks"].items() if not v] or "all true")'
curl -sS --cacert .deploy/tls/ca.pem https://portal.company.internal/api/health/ready
curl -sS --cacert .deploy/tls/ca.pem -o /dev/null -w '%{http_code}\n' https://portal.company.internal/
curl -sS --cacert .deploy/tls/ca.pem -o /dev/null -w '%{http_code}\n' https://portal.company.internal/api/v1/employees
```

Ожидается: `exit=0`, `all true`, `{"status":"ready"}`, `200`, `401`. Без собственного CA убрать `--cacert`.

Через 15 минут: контейнеры `Up (healthy)` и нет перезапусков:

```bash
docker ps --filter name=toshkent-invest-portal --format '{{.Names}} {{.Status}}'
for c in $(docker ps -q --filter name=toshkent-invest-portal); do docker inspect -f '{{.Name}} restarts={{.RestartCount}} oom={{.State.OOMKilled}}' $c; done
```

Попросить владельца проверить с рабочего компьютера и через VPN: вход, смена языка UZ/RU/EN, создание офиса и подразделения, сотрудника, новости, загрузка PDF со сканом и поиск внутри него, вход вторым пользователем с ролью «сотрудник». С телефона без VPN сайт открываться не должен.

## 8. Расписание

```bash
python3 portalctl schedule --enable
systemctl list-timers 'toshkent-portal-*' --no-pager
sleep 150; systemctl status toshkent-portal-health.service --no-pager | head -15
```

Две проверки: копия ежедневно (сайт на короткое время закрывается), здоровье каждую минуту. Служба oneshot: нормальное состояние `inactive (dead)`, `status=0/SUCCESS`. `planned-maintenance` во время копии не ошибка.

## 9. Передача владельцу

1. **Recovery-ключ** `.deploy/keys/backup.key`: владелец или ИТ копирует его в защищённое место **отдельно** от сервера и копий. Без него копии не восстановить. В чат не выводить.
2. Проверить копию: `python3 portalctl verify-backup <путь из .deploy/last-backup.json>`.
3. Удалить `/root/portal-admin.pass`, если был.
4. Продление TLS: заменить `.deploy/tls/fullchain.pem` и `privkey.pem` с прежними правами, затем `docker compose --env-file .deploy/portal.env -f compose.yaml exec -T proxy nginx -t`, `... exec -T proxy nginx -s reload`, `portalctl check`.
5. Учётные записи остальных сотрудников создаёт администратор в интерфейсе. Демо-данные не загружать.

## 10. Обновление уже установленного портала

1. `python3 portalctl backup`, дождаться успеха; `verify-backup`.
2. Сверить `sha256` нового архива. **Не распаковывать поверх**: старые файлы в `apps/web` и `frontend-dist` сломают проверку интерфейса. Каталог заменить целиком, перенеся `.deploy`:

   ```bash
   systemctl stop toshkent-portal-health.timer toshkent-portal-backup.timer
   cd /opt/toshkent-portal && mv portal "portal-old-$(date +%Y%m%d%H%M)"
   unzip -q /root/portal-release/toshkent-portal-*.zip -d /opt/toshkent-portal
   mv portal-old-*/.deploy portal/.deploy   # должен быть ровно один portal-old-* с .deploy
   ```

3. `cd portal && python3 portalctl doctor && python3 portalctl update`, затем `systemctl start` обоих таймеров. Обновление на тот же номер `RELEASE` запрещено. Старый каталог не удалять до подтверждения владельца.
4. `update` не пересоздаёт `.deploy/nginx.conf`. При переходе с rc.12 (с согласия владельца, после копии файла с правами 0600) внести в него те же правки, что в `deploy/nginx.conf.in`: строки `resolver` и `set $portal_api`, три `proxy_pass $portal_api;`. Затем `nginx -t` и перезапуск `proxy`. В файле есть секрет: не выводить целиком.
5. Смена семейства хранения PostgreSQL автоматически запрещена: [POSTGRES_MIGRATION_RU.md](../docs/POSTGRES_MIGRATION_RU.md).

## 11. Ошибки

Логи без секретов: `docker compose --env-file .deploy/portal.env -f compose.yaml logs --tail 100 <app|worker|clamav|postgres|proxy>`. Не выполнять `docker compose config` и полный `docker inspect`: они печатают секреты.

| Симптом | Вероятная причина | Что делать |
|---|---|---|
| `Allocate at least 4 vCPU and 12 GiB RAM` | VM на 12 ГБ | выделить 16 ГБ |
| `Use the packaged release` / `Release file has changed` | клон вместо архива или правки файлов | заново распаковать архив CI |
| `Use the standard local Docker Engine` | чужой docker context, rootless, `DOCKER_HOST` | `unset DOCKER_HOST`, `docker context use default`; rootless: спросить |
| `Configured private bind address is not assigned` | неверный `--bind` | взять IP из `ip -4 addr` |
| `TLS certificate ...` | не то имя, ключ от другого сертификата, срок < 7 дней | исправить сертификат |
| `apk add` не находит `postgresql17=17.12-r0` | Alpine выпустил новую версию пакета | не подбирать на сервере. Обновить в GitHub `Dockerfile` и `deploy/images.lock.json` (одна версия для трёх пакетов), получить новый архив через CI |
| Docker Hub 429 | лимит загрузок | подождать или `docker login` самим владельцем |
| `postgres` unhealthy, в логе `01-roles.sh: Permission denied` | права файла или SELinux | `ls -lZ deploy/postgres/01-roles.sh`, должно быть чтение для всех; при SELinux enforcing: спросить про `chcon -t container_file_t`. Том частично инициализирован: удалять только с согласия и только на новой установке без данных |
| Нет ролей `portal_owner`/`portal_app` | первая инициализация оборвалась | то же, что выше |
| `clamav` долго не healthy | первая загрузка базы | до 15 минут нормально, смотреть лог `clamav` |
| `antivirus: false`, `ANTIVIRUS_SIGNATURES_STALE` | нет доступа к `database.clamav.net` (403/блокировка) | ИТ открывает доступ. Проверку свежести не отключать |
| `worker` постоянно перезапускается, `Parser sandbox is unavailable` | нет seccomp в ядре или чужой профиль Docker | `docker info` → SecurityOptions должен содержать seccomp |
| `ocrModels: false` | сборка не скачала модели | повторить install, проверить доступ к `raw.githubusercontent.com` |
| `httpsReadiness: false`, с сервера `403` | собственный адрес не в `--allow` | узнать адрес (`docker network inspect toshkent-invest-portal_edge`, шлюз) и с согласия владельца добавить **точный** `/32` в `.deploy/nginx.conf` и `installation.json` → `nginx -t` → reload |
| `httpsReadiness: false`, ошибка имени или сертификата | сервер не знает DNS-имя, нет `--ca-file` | раздел 3 |
| Браузер: соединение закрыто сразу | открывают по IP или другому имени | открывать точный `--origin`, с `https://` |
| `403` у сотрудников | их сеть не в списке | сверить CIDR с владельцем |
| `429` у всех сразу | весь офис за одним NAT-адресом | настроить сеть так, чтобы портал видел адреса клиентов; изменение лимитов согласовать |
| `502` | API перезапускается | лог `app`, `docker ps` |
| `the input device is not a TTY` | вариант А без терминала | вариант Б |
| `backupFresh: false` | нет свежей копии или каталога | `portalctl backup`, проверить диск |
| `Another install/update/backup/restore is running` | идёт другая операция | дождаться |

Если причина в коде: не править на сервере. Записать точную ошибку, файл, строку и сообщить владельцу для исправления в репозитории.

## 12. Форма отчёта

```
Итог: РАБОТАЕТ / РАБОТАЕТ ЧАСТИЧНО / НЕ РАБОТАЕТ
Сервер: ОС, CPU, RAM, диск
Релиз: номер, sourceCommit из RELEASE-MANIFEST.json, запуск CI, SHA-256 сверен да/нет
Адрес сайта, слушающий IP:порт, разрешённые сети
Установлено на сервер: пакеты и версии
portalctl install: код выхода; check --pipeline --files: код выхода, список checks
HTTPS снаружи контейнера: ready / 200 / 401
Таймеры: активны да/нет, последняя автоматическая проверка
Первая копия: путь, verify-backup да/нет; recovery-ключ передан владельцу да/нет
Контейнеры через 15 минут: состояние, перезапуски
Проверил владелец из браузера: что именно
Изменено вне портала: список или «ничего»
Не проверено / риски / нужно от владельца
```

## 13. Эксплуатация

| Когда | Действие |
|---|---|
| Каждый день | таймеры сами делают копию и проверку; смотреть `systemctl --failed` и `python3 portalctl status` |
| Каждую неделю | `python3 portalctl check --pipeline --files`; место на дисках релиза, Docker и копий; `.partial-*` в каталоге копий |
| Каждый месяц | `verify-backup` последней копии; срок TLS; новый релиз |
| По плану ИТ | пробное восстановление на отдельном сервере |

Ошибки служб `toshkent-portal-health` и `toshkent-portal-backup` подключить к мониторингу ИТ: сам портал уведомления не рассылает. Копии хранятся 14 дней, минимум три последних.

Восстановление: [OPERATIONS.md](../docs/OPERATIONS.md), раздел «Восстановление». Нужны копия, `backup.key` и архив того же релиза, поэтому установленные архивы хранить. Команда заменяет данные, запускать только с согласия владельца.

Забытый пароль сбрасывает администратор в интерфейсе. PDF с автодействием, JavaScript или вложениями отклоняются: это политика безопасности.

## 14. Приёмка

Ставить отметку только после фактической проверки, с датой и фамилией.

- [ ] 1. Стенд: `python3 operations/acceptance.py --work-dir /var/tmp/toshkent-acceptance-01`, код выхода 0, `technicalChecksPassed: true` в `evidence/report.json` ([RUN_ACCEPTANCE_RU.md](../RUN_ACCEPTANCE_RU.md)). Передавать только папку `evidence`.
- [ ] 2. Рабочий сервер: разделы 0–9 выполнены, отчёт по разделу 12 заполнен.
- [ ] 3. Браузерная матрица из офиса и VPN ([BROWSER_ACCEPTANCE_RU.md](../docs/BROWSER_ACCEPTANCE_RU.md)); без VPN сайт не открывается.
- [ ] 4. Реальные сканы компании: текст находится поиском.
- [ ] 5. Копия восстановлена на втором сервере, RPO и RTO утверждены.
- [ ] 6. Recovery-ключ хранится отдельно, доступ к нему проверен.
- [ ] 7. Мониторинг и оповещения подключены.
- [ ] 8. Нагрузка и поведение при отказах проверены.
- [ ] 9. Независимый пентест и повторная проверка исправлений.
- [ ] 10. Владелец и ИТ разрешили ввод реальных данных.

Подробности и границы: [REMAINING_ACCEPTANCE_RU.md](../docs/REMAINING_ACCEPTANCE_RU.md).
