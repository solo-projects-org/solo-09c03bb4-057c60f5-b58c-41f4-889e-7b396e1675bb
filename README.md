# 售后数仓 · 车型适配拆库服务

把车型适配明细拆成**备件表**与**工况表**两张表存入 SQLite，不留任何逐对
"备件 × 工况"配对明细；车间按车型把两表笛卡尔拼回时，保证不会出现不适用
的组合。FastAPI 提供装载、批量更正、**厂商补报筛选**与取数接口，Docker
Compose 一键启动。

## 核心规则

1. **按车型录入备件号与工况配对**，重复配对只算一条（服务端去重）。
2. 可入库的配对集合必须是完整矩形：`配对数 = 备件种数 × 工况种数`。
   只有两表能**原样拼回**这些配对时才入库；否则拒绝，并指出一对
   "两表会拼出、但拟入库数据中没有"的组合示例，已入库的原数据照常可查。
3. **入库后不留逐对明细**：持久层只有 `vehicle_models`、`model_parts`、
   `model_conditions` 三张表，配对关系由两表笛卡尔积隐式表达。
4. **批量更正**（plus 标"有"、minus 标"无"）按整批更正后的结果核定，
   通过才在同一事务内一起更新两张表；不通过整体回滚，两表保持原样。
5. **核定不遍历两表的所有配对**：核定只做 `COUNT` / `GROUP BY` 聚合；
   更正路径从不 JOIN 两张持久表，只在批次临时表上聚合（数据量 = 本批
   录入条数），复杂度与两表拼回的配对总数无关。反例仅在单个问题备件
   范围内 `LIMIT 1` 取证。
6. 取数一次返回该车型同时生效的两张表。
7. **厂商补报筛选（只读，不写库）**：以同一已提交状态的两张在库表为基准，
   先按成员判定剔除补报中已在库的配对（不 JOIN、不展开在库全量配对），
   再从剩余补报中选出**配对数最多**的可并入子集，使"在库矩形 ∪ 可采纳
   清单"仍是完整矩形、仍能由原两表无损存储；其余配对进待补证清单并附
   退回原因。每批新备件号（基准备件表中没有的）超过 12 个直接 422 提示
   拆批。基准两表未变时，非空的可采纳清单可原样作为更正接口的 `plus`。

## 目录结构

```
app/
  main.py        FastAPI 路由与 Swagger 元数据
  service.py     装载 / 批量更正 / 补报筛选 / 取数业务逻辑（核定与筛选算法在此）
  database.py    SQLite 连接与建表
  schemas.py     请求/响应模型
tests/           26 个关键流程自测（含补报筛选 11 个）
Dockerfile
docker-compose.yml
requirements.txt
```

## 用 Docker Compose 启动

```bash
docker compose up --build
```

- API 地址：`http://localhost:8000`
- Swagger UI（在线试用）：`http://localhost:8000/docs`
- OpenAPI 文档：`http://localhost:8000/openapi.json`
- SQLite 数据保存在命名卷 `warehouse-data`（容器内 `/data/warehouse.db`），
  容器重建不丢数据。

停止：`docker compose down`（加 `-v` 会同时删除数据卷）。

## 本地无 Docker 运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
WAREHOUSE_DB=./data/warehouse.db .venv/bin/uvicorn app.main:app --port 8000
```

## Swagger 试用样例

### 1. 装载（`POST /api/v1/models/load`）

合法矩形（2 备件 × 2 工况 = 4 对，含一条重复配对，重复只算一条）：

```json
{
  "modelId": "SEDAN-A1",
  "pairs": [
    {"partNo": "P-1001", "conditionCode": "C-HOT"},
    {"partNo": "P-1001", "conditionCode": "C-COLD"},
    {"partNo": "P-1002", "conditionCode": "C-HOT"},
    {"partNo": "P-1002", "conditionCode": "C-COLD"},
    {"partNo": "P-1002", "conditionCode": "C-COLD"}
  ]
}
```

非矩形（缺 `P-1002 / C-COLD` 这一对）会返回 422，并给出反例：

```json
{
  "modelId": "SEDAN-A2",
  "pairs": [
    {"partNo": "P-1001", "conditionCode": "C-HOT"},
    {"partNo": "P-1001", "conditionCode": "C-COLD"},
    {"partNo": "P-1002", "conditionCode": "C-HOT"}
  ]
}
```

响应：

```json
{
  "detail": {
    "message": "配对集合不是完整矩形，拆成两表后会拼出拟入库数据中没有的组合",
    "extraCombinationExample": {"partNo": "P-1002", "conditionCode": "C-COLD"}
  }
}
```

### 2. 批量更正（`POST /api/v1/models/correct`）

整批：移除备件 P-1002 的两个工况，同时给 P-1001 增加新工况 C-DUST。
更正后为 `P-1001 × {C-HOT, C-COLD, C-DUST}` 完整矩形，整批通过：

```json
{
  "modelId": "SEDAN-A1",
  "plus":  [{"partNo": "P-1001", "conditionCode": "C-DUST"}],
  "minus": [
    {"partNo": "P-1002", "conditionCode": "C-HOT"},
    {"partNo": "P-1002", "conditionCode": "C-COLD"}
  ]
}
```

只删一对（剩余配对不是矩形）会被整批拒绝，两表保持原样：

```json
{
  "modelId": "SEDAN-A1",
  "plus": [],
  "minus": [{"partNo": "P-1001", "conditionCode": "C-HOT"}]
}
```

### 3. 取数（`GET /api/v1/models/SEDAN-A1`）

一次响应同时返回两张生效表：`parts`（备件表）与 `conditions`（工况表），
并附 `effectivePairCount`（两表拼回后的配对数）。

### 4. 厂商补报筛选（`POST /api/v1/models/screen`，只读不写库）

把厂商补报拆成**可采纳清单**（能先入仓的配对）与**待补证清单**（需退回
补证的配对）。基准是该车型同一已提交状态的两张在库表。

筛选规则：

1. 补报配对先去重；备件号与工况都在基准两表中的配对视为已在库，直接剔除
   （只做单表成员判定，不展开在库 `备件 × 工况` 全量配对）；
2. 统计补报中不在基准备件表内的新备件号种数，**每批最多 12 个**，超出
   返回 422 并提示拆批；
3. 在剩余补报上求配对数最多的可并入子集，使合并结果仍是完整矩形：
   - 新备件必须覆盖**全部**在库工况；
   - 新工况必须被**全部**在库备件覆盖；
   - 选中的新备件与新工况之间的交叉配对必须在补报中出现；
4. 新备件上限 12，算法枚举全部新备件子集（至多 2^12=4096 个）精确求最优，
   复杂度只与本批补报条数有关，与在库配对总数无关；并列时按"更少新备件、
   更少新工况、字典序"确定性择优；
5. 返回四块内容：基准两表（`baseline`，原样回显）、可采纳清单
   （`acceptablePairs`）、待补证清单（`pendingPairs`，每条带 `reasonCode`
   与中文原因）、统计摘要（`summary`）。

请求示例（基准为 `P1,P2 × C1,C2`）：

```json
{
  "modelId": "SEDAN-A1",
  "pairs": [
    {"partNo": "P1", "conditionCode": "C1"},
    {"partNo": "P1", "conditionCode": "C3"},
    {"partNo": "P2", "conditionCode": "C3"},
    {"partNo": "P1", "conditionCode": "C4"},
    {"partNo": "Q1", "conditionCode": "C1"},
    {"partNo": "Q1", "conditionCode": "C2"},
    {"partNo": "Q1", "conditionCode": "C3"},
    {"partNo": "Q2", "conditionCode": "C1"}
  ]
}
```

响应（节选）：

```json
{
  "modelId": "SEDAN-A1",
  "baseline": {
    "partCount": 2, "conditionCount": 2, "effectivePairCount": 4,
    "parts": ["P1", "P2"], "conditions": ["C1", "C2"]
  },
  "acceptablePairs": [
    {"partNo": "P1", "conditionCode": "C3"},
    {"partNo": "P2", "conditionCode": "C3"},
    {"partNo": "Q1", "conditionCode": "C1"},
    {"partNo": "Q1", "conditionCode": "C2"},
    {"partNo": "Q1", "conditionCode": "C3"}
  ],
  "pendingPairs": [
    {"partNo": "P1", "conditionCode": "C4",
     "reasonCode": "CONDITION_INCOMPLETE",
     "reason": "新工况未被全部在库备件覆盖，并入会拼出缺失组合，需补齐各在库备件对该工况的配对"},
    {"partNo": "Q2", "conditionCode": "C1",
     "reasonCode": "PART_INCOMPLETE",
     "reason": "新备件未覆盖全部在库工况，并入会拼出缺失组合，需补齐该备件的在库工况配对"}
  ],
  "summary": {
    "submittedPairCount": 8,
    "duplicateInWarehouseCount": 1,
    "newPartNoCount": 2,
    "acceptedPairCount": 5,
    "pendingPairCount": 2,
    "acceptedNewPartCount": 1,
    "acceptedNewConditionCount": 1,
    "plusReady": true
  }
}
```

`reasonCode` 三种取值：

| reasonCode | 含义 |
| --- | --- |
| `PART_INCOMPLETE` | 新备件没覆盖全部在库工况，需补齐该备件的在库工况配对 |
| `CONDITION_INCOMPLETE` | 新工况没被全部在库备件覆盖，需补齐各在库备件对该工况的配对 |
| `EDGE_MISSING` | 新备件/新工况个体证据齐全，但补报缺少使其与最优可采纳矩形同时并入的交叉配对 |

**与更正接口衔接**：基准两表未变且 `summary.plusReady = true` 时，
`acceptablePairs` 可原样作为 `POST /api/v1/models/correct` 的 `plus`
（`minus` 传空数组），提交后合并结果必然通过矩形核定。筛选本身不写库。

curl 示例：

```bash
curl -s -X POST http://localhost:8000/api/v1/models/screen \
  -H 'Content-Type: application/json' \
  -d '{"modelId":"SEDAN-A1","pairs":[{"partNo":"Q1","conditionCode":"C1"},{"partNo":"Q1","conditionCode":"C2"}]}'
```

### 5. 车型清单（`GET /api/v1/models`）

## 错误码约定

| 状态码 | 含义 |
| --- | --- |
| 200 | 成功 |
| 404 | 车型不存在（取数/更正/筛选） |
| 409 | 车型已入库（重复装载，更正请走 correct 接口） |
| 422 | 入参为空、plus/minus 同批冲突、核定不通过（含反例，两表不变），或补报新备件号超过每批 12 个上限（筛选不写库） |

## 自测

```bash
python3 -m pip install pytest httpx
python3 -m pytest tests/ -v
```

共 26 个用例：原 15 个覆盖矩形装载与去重、非矩形拒绝与反例、重复装载
409、整批更正成功/失败回滚、两表一次取数、无逐对明细表、plus/minus 冲突、
404、200×4 大车型小批次更正的聚合性能、SQL 级"从不两表 JOIN"追踪、
新备件/新工况/幽灵成员等边界；新增 11 个补报筛选用例覆盖新工况/新备件
准入、最大双团精确择优与确定性、在库配对剔除、12 个新备件上限 422、
只读不写库、可采纳清单直连更正接口、404/空入参边界、大基准小批次不
JOIN 不退化、全量在库配对等场景。另用独立暴力枚举脚本对 397 组随机
补报校验筛选结果为全局最优且合并集恒为矩形。
