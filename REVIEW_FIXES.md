# Исправления по ревью (ветка `fixes-review`)

Основа — ревью коммита 248eb03 (`REVIEW_strateg.md`, 16.11.2026). Номера — из таблицы ревью. Каждое исправление — отдельный коммит. После каждого блока `python3 -m unittest discover -s tests` зелёный: было 164 теста, стало 203. Полный прогон агента по сценарию не запускался.

## Что сделано

| № | Что сделано | Файлы | Тест |
|---|---|---|---|
| 5 | «Закрыть направление» — закрыть сам продукт или линию бизнеса, о которой вопрос, с расчётом, что теряем и что высвобождаем. «Свернуть акцию» — активный вариант, а не закрытие. Варианты не должны отличаться одной настройкой. В чек-листе критика два новых пункта: «закрытие продукта рассмотрено всерьёз», «варианты не близнецы» | `skills/options/SKILL.md`, `agents/critic.md`, `templates/options.md`, копии в `.claude/` | `test_fixes_review.ReviewItem05Options` |
| 4 | `markitdown[docx,pdf,pptx,xlsx]`. При любой ошибке markitdown на .docx (в том числе `MissingDependencyException`) включается встроенный разборщик, причина печатается. Если не справился и он, обе ошибки попадают в журнал | `requirements.txt`, `tools/extract/docs.py`, `README.md` | `test_docs.test_docx_falls_back_when_markitdown_fails` |
| 3 + 7 | `tools/cost.py` читает расход головной модели и субагентов из транскриптов Claude Code (`~/.claude/projects/<папка агента>/*.jsonl` и `*/subagents/*.jsonl`, поле `usage`: input, output, cache_read, cache_creation с разбивкой 5 мин / 1 ч). Учитывает только записи за время проекта, от `created_at` в project.json или первого события журнала. Одно сообщение, записанное несколькими строками, считается один раз. Цены — `config/prices.json`, модель с датой в имени находит цену по префиксу. Время — сумма активных интервалов, паузы длиннее 10 минут не считаются. `status --brief` показывает «бюджет ≈ X% (N из M ток.) · ≈ $Y», на 80% — «⚠ предупредите», на 100% — «⛔ остановитесь». Нет транскрипта — «оценка недоступна», а не $0. AGENTS.md и README переписаны под это | `tools/cost.py`, `strateg.py`, `AGENTS.md`, `README.md`, `tests/data/*.jsonl` | `test_cost.TranscriptTests` (9 тестов на фикстуре jsonl: дедупликация, окно проекта, субагенты, `<synthetic>`, битые строки, стоимость с кэшем, активное время, 80/100%, нет транскрипта) |
| 17 | Разрешены `Bash(cp inbox/:*)`, `Bash(ls:*)`, `Bash(grep:*)`, `WebSearch`, `WebFetch`, `.venv/bin/python strateg.py`. Папка `inbox/` описана в README (быстрый старт) и в навыке intake, в git попадает только `inbox/.gitkeep` | `.claude/settings.json`, `.gitignore`, `inbox/.gitkeep`, `README.md`, `skills/intake/SKILL.md` | `ReviewItem17Permissions` |
| 8 | Шапка с форматом записи в `claims.md` и `sources.md` шаблона проекта: HTML-комментарий, парсер реестра его не видит. Новый `templates/claims.md` с примерами и таблицей полей | `projects/_template/state/*.md`, `templates/claims.md`, `AGENTS.md` | `test_configs.test_state_files`: шапка есть, записей ноль, `check` чист |
| 11 | `extract docs/web/tables` ищут уже выданный SRC по отпечатку содержимого (sha256, поле «Хеш») или по «Где» и возвращают его, а не заводят дубль. Тот же путь с новым содержимым — новый SRC с пометкой «новая версия, прежняя — SRC-xx». `--again` принудительно заводит новый. Поле «Где» везде записывается одинаково: путь от папки проекта или URL | `tools/extract/common.py`, `docs.py`, `web.py`, `tables.py`, `skills/intake/SKILL.md` | `ReviewItem11ExtractDedup` (6 тестов) |
| 12 | Базовый период не включает затронутые когорты; граница базы указывается в отчёте. У критика новый режим 3 — проверка расчётов: читает скрипт, а не только итог (база, месяцы жизни, единицы, фильтры, перезапуск) | `skills/data-analysis/SKILL.md`, `agents/critic.md` | `ReviewItem12Baseline` |
| 13 + 14 | Одностраничник: до 250 слов без сносок, до 2 графиков, номера CLM/SRC — в сносках `[^1]`. `report` рисует сноски блоком «Основания», остальные графики даёт ссылками и печатает «Внимание: …», если правила нарушены; `check` выдаёт то же предупреждение. В AGENTS.md ответ в чате — до 15 строк | `templates/one-pager.md`, `tools/report.py`, `strateg.py`, `skills/strategy/SKILL.md`, `AGENTS.md` | `ReviewItem13OnePager` |
| 9 | `model: sonnet` у субагента `persona`; в `routing.json` это записано как запасной путь для `synthetic_persona`. Исследователь и критик остаются на головной модели | `agents/persona.md`, `config/routing.json`, `skills/synthetic-interviews/SKILL.md`, `README.md` | `ReviewItem09Routing` |
| 10 | `tools/screenshot.py`: URL → `materials/screens/<имя>.png` (вся страница) + `.txt` (видимый текст, телефоны и почта скрыты), SRC и журнал, `--mobile`. Без playwright — код 3 и понятный текст; без браузера — подсказка `playwright install chromium`. `setup --ux` теперь ставит и браузер. Навык ux-review вызывает скрипт | `tools/screenshot.py`, `strateg.py`, `skills/ux-review/SKILL.md`, `README.md` | `ReviewItem10Screenshot` (без playwright и с подменным playwright) |
| 15 | doctor считает только непустые ключи `.env`, модуль `yaml` убран из списка | `strateg.py` | `test_strateg.test_doctor_writes_json` |
| 16 | `extract web` и `screenshot.py` проверяют `deny`/`allow` из `config/sources.json` и поля `sources` в project.json: до загрузки и после перенаправления. Отказ — код 4, в журнал ничего не пишется | `tools/extract/common.py`, `web.py`, `tools/screenshot.py`, `config/sources.json`, `README.md` | `ReviewItem16SourceLists` |
| 18 | `snapshot` сам обновляет «Последний снимок» в state.md: имя, этап, время, число снимков | `tools/snapshot.py` | `ReviewItem18LastSnapshot` |
| 19 | В README перечислены `log`, `chart`, `report`, `doctor`, `setup`, `screenshot.py`. Порог устаревания источников — 90 дней и в коде, и в навыке. `restore` принимает номер (`3`, `03`) или этап (`S2` — последний снимок этапа). `status` и `status --brief` показывают решения с наступившей датой пересмотра. «Где» в одном формате — см. № 11 | `README.md`, `strateg.py`, `tools/snapshot.py`, `skills/followup/SKILL.md` | `ReviewItem19DocsAndCode` |
| 21 | Вместо «закрепим после проверки» — границы версий: `markitdown[…]>=0.1.2,<0.2` (только Python 3.10+), `trafilatura>=1.12,<3`, `pandas>=2.2,<4`, `playwright>=1.48,<2`, `scenedetect>=0.6.4,<0.8` | `requirements*.txt`, `README.md` | `test_configs.test_requirements_files` |
| 22 | В project.json две роли: `user_role` (кто работает с агентом) и `recipient` (кому адресован результат); правило «не путай» в AGENTS.md и framing. Подписи примечаний на графике раскладываются по рядам без наложения, отступ сверху растёт под число рядов | `projects/_template/project.json`, `strateg.py`, `AGENTS.md`, `skills/framing/SKILL.md`, `tools/chart.py` | `ReviewItem22RolesAndNotes`; картинку проверил глазами |
| 20 | Jev убран: `TYPESAFE_API_KEY` из `.env.example`, строка из README. Причина — в разделе «Что не сделано» | `.env.example`, `README.md` | `test_configs.test_env_example` |
| — | Цены в `config/prices.json` сверены 17.11.2026 с [официальной страницей](https://platform.claude.com/docs/en/about-claude/pricing). У Sonnet и Haiku теперь свои ссылки ([Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview), [Haiku 5.5](https://platform.claude.com/docs/en/models/haiku-5-5/overview)); добавлены цены кэша и ступень Haiku для запросов длиннее 100 000 токенов. Значения совпали с прежними, ошибочными были только ссылки | `config/prices.json` | `ReviewPricesSources` |

## Что не сделано и почему

- **№ 1, 2, 6** — демо-проект, видео, PDF, замеры и сравнение с аналитиком. Не входили в задание, и без полного прогона их не сделать.
- **№ 20, Jev.** Простого проверяемого пути нет. API описан только на сторонних страницах, и они расходятся: `api.typesafe.ai/v1/systemone`, `/v1/decisions`, OpenRouter `/api/alpha/decisions`. Ключа нет, так что клиент нечем проверить. Классифицировать статус утверждения и так может головная модель, а `check` проверяет поля. Поэтому упоминания убраны. Поле `modules` в project.json, которое ревью тоже относит к № 20, оставлено: задание касалось только Jev.
- **№ 21.** Указаны границы версий, а не точные `==`. Проверить установку здесь нельзя: системный Python на этом Mac — 3.9.6, а markitdown, trafilatura и playwright требуют 3.10+ (по PyPI). Поэтому markitdown помечен `python_version >= "3.10"`. На 3.9 `setup` всё равно споткнётся о trafilatura и playwright — стоит поднять требование в README до 3.10 или проверить `setup` на 3.10+.
- **№ 10.** `screenshot.py` проверен только тестами с подменным playwright: настоящего playwright на машине нет.
- **№ 3 + 7, ограничения оценки.**
  - Если в одной папке агента одновременно идут два проекта, расход общих сессий попадёт в оба.
  - Haiku считается по нижней ступени цены.
  - Для очень длинных путей Claude Code может сокращать имя папки транскриптов. Тогда папку можно задать в `STRATEG_TRANSCRIPTS`.
  - Доля лимита подписки не считается: лимиты публикуются не в токенах.

## Сколько ушло

Посчитано новым `tools/cost.py` по транскрипту этой сессии, заодно проверка на настоящих данных:

- Головная модель (Opus 5.5): 105 сообщений; вход 214, выход 94 890, чтение кэша 19,2 млн, запись кэша 231 тыс. токенов — **≈ $7,59** по ценам API.
- Субагент загрузки страницы цен (Haiku 5.5): ≈ $0,005.
- Активное время — около 20 минут на все 18 коммитов.
- В счёт бюджета, по правилу из AGENTS.md (вход + выход + запись кэша), — около 356 тыс. токенов.
- Лимит подписки Pro на конец работы: 5-часовое окно — 35% (сброс в 22:30 UTC), недельный — 39%. Сколько было в начале, среда не показывала, поэтому точную долю этой работы назвать нельзя.
- Изменения: 55 файлов, +1 618 / −194 строки. Ветка не отправлена в удалённый репозиторий.
