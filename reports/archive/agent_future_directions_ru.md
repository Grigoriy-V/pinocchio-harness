# Направления развития local-multimodal-agent

## Общая идея

Сейчас не обязательно определять `local-multimodal-agent` как будущий продукт.

Гораздо полезнее воспринимать его как **R&D-полигон для общего agent runtime / harness**, на котором можно развивать:

- память и knowledge;
- работу с инструментами;
- планирование;
- выполнение многошаговых задач;
- валидацию результата;
- repair/retry;
- sandbox execution;
- observability;
- работу локальных моделей в более сильном агентном цикле.

Telegram-ассистент в этом случае — первый полноценный host/application для этого runtime, а не конечная форма проекта.

## 1. Personal scale-to-zero agent

Одно из основных направлений — личный агент, который почти всегда выключен, но сохраняет знания и рабочее состояние.

```text
                 YOU
                  │
         ┌────────┴────────┐
         │                 │
     Knowledge           Actions
         │                 │
 notes / memory         web
 projects               files
 documents              documents
 history                sandbox
 preferences            APIs
         │                 │
         └────────┬────────┘
                  │
               HARNESS
```

Ключевая идея:

> Память и знания живут постоянно, compute — нет.

То есть агент может scale-to-zero, но после пробуждения помнить:

- заметки;
- прошлые решения;
- проекты;
- документы;
- рабочий контекст;
- результаты предыдущих задач.

Примеры:

```text
"Что мы решили по H3 в ShotOps?"
"Сравни последний тест с Seedance."
"Собери по этому проекту сводку."
"Сделай contact sheet и пришли."
```

Это может оставаться полностью личным инструментом, без попытки превращать его в продукт.

## 2. Sandbox как универсальная вычислительная рука

Вместо того чтобы заранее устанавливать агенту все возможные инструменты, можно дать ему отдельную изолированную среду выполнения.

```text
Agent
  ↓
Sandbox
  ├─ Python
  ├─ uv / pip
  ├─ CLI
  ├─ npm при необходимости
  ├─ установка зависимостей
  ├─ выполнение скриптов
  └─ запись результатов в workspace
```

Агент сможет сам решать:

```bash
uv venv .venv
uv pip install pypdf pandas pillow
python script.py
```

Это полезнее, чем постепенно добавлять десятки узких tools.

Важно: sandbox не должен быть тем же контейнером, где лежат основные секреты агента, Telegram token и доступ к БД.

То есть:

```text
Main Agent Worker
  ├─ reasoning
  ├─ memory
  ├─ tools
  └─ secrets

Sandbox
  ├─ arbitrary code
  ├─ install packages
  ├─ temporary environment
  └─ limited workspace access
```

Git тоже можно позже добавить именно в sandbox, если понадобится clone/local commit.

## 3. ShotOps как content-generation capability layer

Репозиторий:

https://github.com/Grigoriy-V/shotops

ShotOps не стоит превращать в самого агента.

Лучше сохранить разделение:

```text
ShotOps = production / generation engine
Agent   = reasoning / orchestration layer
```

ShotOps может постепенно собрать под собой разные генеративные возможности:

```text
ShotOps
├─ Blender / blockout
├─ Seedance
├─ H3
├─ image generation APIs
├─ local image models
├─ video generation APIs
├─ local video models
├─ image editing
├─ upscaling
├─ QA / evaluation
└─ provider abstraction
```

Сам по себе ShotOps тогда становится удобным слоем генеративных production tools.

А сверху его может использовать любой агент:

```text
                AGENT
                  │
               Harness
                  │
             ShotOps tools
                  │
       ┌──────────┼──────────┐
     image       video      3D/blockout
     models      models      Blender
```

Пример естественного запроса:

> Сделай 6-секундный establishing shot ночного Токио. Сначала собери blockout, потом попробуй H3 и Seedance, сравни варианты и пришли лучший.

Возможный loop:

```text
understand
↓
plan
↓
create scene
↓
render blockout
↓
inspect blockout
↓
generate with H3
↓
generate with Seedance
↓
inspect outputs
↓
repair prompt/reference if needed
↓
compare
↓
deliver
```

Это уже не просто UI поверх генеративных API, а **agentic generative production**.

При этом внешний Claude или Codex всё ещё сможет работать с ShotOps независимо.

## 4. Generative Narrative Game как другой consumer harness

Репозиторий:

https://github.com/Grigoriy-V/generative-narrative-game

В игру не обязательно подключать всего personal-agent целиком.

Из общего проекта игре прежде всего полезны сами принципы agent loop:

```text
understand state
↓
plan
↓
generate
↓
validate
↓
repair
↓
commit canonical result
```

У игры уже есть собственный runtime, который должен владеть:

- canonical game state;
- validation;
- persistence;
- правилами;
- progression;
- ограничениями.

LLM должна оставаться исполнителем внутри этой системы.

Общие части, которые можно переиспользовать концептуально или позже кодом:

```text
bounded loop
structured state
model calls
validation
repair
retry policy
trace
token budget
tool/action results
truthful completion
```

Это особенно важно для локальных моделей.

Большая облачная модель иногда может выполнить:

```text
understand → plan → self-check → answer
```

внутри одного inference.

Меньшей локальной модели можно помочь внешним harness:

```text
draft
→ validate narrative constraints
→ identify defect
→ repair
→ validate again
→ commit
```

То есть сильный внешний loop может частично компенсировать разницу между локальной и более сильной закрытой моделью.

## 5. Возможная общая картина

Со временем может получиться:

```text
                  AGENT CORE
                     │
       ┌─────────────┼─────────────┐
       │             │             │
   Personal       ShotOps       Narrative
    Agent        Content Agent     Game
       │             │             │
 knowledge      generation       game state
 memory         Blender          Director
 web/files      H3/Seedance      Planner
 sandbox        image APIs       validation
```

Но сейчас **не стоит физически выносить `agent-core` в отдельный repository/package**.

Пока ещё неизвестно, какие части действительно будут общими.

Лучший порядок:

1. развивать harness внутри `local-multimodal-agent`;
2. проверить его идеи на personal-agent задачах;
3. использовать похожие принципы в ShotOps;
4. использовать похожие принципы в Narrative Game;
5. только когда начнётся реальное дублирование одного и того же кода — выносить общий core.

Иначе легко создать слишком раннюю абстракцию.

## 6. Что является главным направлением harness

Главная работа — не бесконечное добавление tools.

Нужен сильный цикл:

```text
understand
↓
plan
↓
act
↓
observe
↓
adjust
↓
validate
↓
repair if needed
↓
finish
```

Хороший benchmark:

> Дай агенту естественную задачу, где нужно использовать несколько возможностей, создать реальный результат, проверить его и только потом сказать, что задача выполнена.

Например:

```text
"Изучи X, используй всё необходимое,
создай Y, проверь результат и пришли мне."
```

Проваленный PDF-кейс хорошо подходит как будущий benchmark именно поэтому: он требует планирования, выбора инструментов, создания артефакта, проверки и доставки.

## 7. Возможный порядок развития

После observability / telemetry:

### 1. Harness

Сделать надёжнее:

```text
plan
→ act
→ observe
→ validate
→ repair
```

Плюс:

- bounded execution;
- понятное завершение;
- контроль бюджета;
- trace;
- error recovery.

### 2. Sandbox execution

Дать возможность:

- запускать Python;
- устанавливать пакеты;
- запускать CLI;
- создавать временные environments;
- обрабатывать файлы;
- позже работать с Git.

### 3. Knowledge

Развивать:

- заметки;
- project knowledge;
- долговременную память;
- retrieval;
- provenance;
- связь знания с исходным документом/проектом.

### 4. Capability boundary

Сделать так, чтобы внешние модули можно было подключать как capabilities.

Например:

```text
search_web(...)
read_document(...)
execute(...)
remember(...)

generate_image(...)
generate_video(...)
render_shot(...)
```

Тогда ShotOps сможет стать одним большим подключаемым capability layer.

### 5. External accounts

Позже:

- GitHub;
- Gmail;
- Calendar;
- Drive;
- другие API.

## 8. Что пока не стоит строить

Не нужно сейчас уходить в:

```text
multi-agent swarm
autonomous software engineer
полноценную IDE
100 integrations
marketplace tools
general desktop computer-use
огромный vector-RAG stack
отдельный generic agent framework
```

Это размоет задачу раньше, чем станет понятно, какие примитивы реально работают.

## 9. Возможная долгосрочная инженерная тема

Не обязательно строить одного универсального супер-агента.

Более интересная задача:

> Построить достаточно хороший общий agent loop, который превращает локальную LLM из модели, отвечающей текстом, в исполнителя в разных средах.

Три проекта проверяют разные стороны этого подхода.

### local-multimodal-agent

Проверяет:

- memory;
- knowledge;
- tools;
- general reasoning;
- web/files;
- long-lived personal context;
- scale-to-zero runtime.

### ShotOps

Проверяет:

- multimodal reasoning;
- generative content production;
- orchestration дорогих generation tools;
- visual validation;
- iterative generation.

### Generative Narrative Game

Проверяет:

- long-horizon planning;
- state;
- constraints;
- validation;
- repair;
- consistency локальных моделей.

Если одни и те же harness-принципы реально улучшают все три среды, это уже сильный самостоятельный инженерный результат.

## Короткая формулировка

Пока полезно думать о `local-multimodal-agent` не как о продукте, а как об:

> **экспериментальном runtime для автономных локальных агентов, где personal assistant — первый host, ShotOps может стать генеративным capability layer, а Narrative Game — отдельным тестом planning/validation loop.**

К этой формулировке можно вернуться позже и пересмотреть её после того, как появится более зрелый harness.
