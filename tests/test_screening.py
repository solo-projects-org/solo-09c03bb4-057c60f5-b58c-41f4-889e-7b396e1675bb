"""厂商补报筛选接口（POST /api/v1/models/screen）端到端自测。

覆盖：
1. 新工况被全部在库备件覆盖 → 整组进可采纳清单
2. 新工况未被全部在库备件覆盖 → 进待补证清单，原因 CONDITION_INCOMPLETE
3. 新备件覆盖全部在库工况 → 进可采纳；只覆盖部分 → 待补证 PART_INCOMPLETE
4. 新备件 × 新工况二部图取最大双团（精确择优，含并列确定性）
5. 已在库配对从补报中剔除（不展开在库全量配对，仅成员判定）
6. 每批 >12 个新备件号 → 422，且不写库
7. 筛选全程只读：两表、车型时间戳、库行数均不变
8. 可采纳清单非空时可原样作为更正接口 plus 提交，合并后仍是完整矩形
9. 404 / 空补报 / 空白字符串等入参边界
10. 大基准（200 备件 × 4 工况）小批次筛选：不两表 JOIN、不随在库配对数变慢
11. 全部配对已在库 → 可采纳为空、plusReady=false
"""

from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.database import db_path
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_DB", str(tmp_path / "screen.db"))
    with TestClient(app) as c:
        yield c


def _pairs(spec: list[tuple[str, str]]) -> list[dict]:
    return [{"partNo": p, "conditionCode": c} for p, c in spec]


def _load(client, model_id: str, parts: list[str], conds: list[str]) -> None:
    spec = [(p, c) for p in parts for c in conds]
    r = client.post(
        "/api/v1/models/load",
        json={"modelId": model_id, "pairs": _pairs(spec)},
    )
    assert r.status_code == 200, r.text


def _screen(client, model_id: str, spec: list[tuple[str, str]]):
    return client.post(
        "/api/v1/models/screen",
        json={"modelId": model_id, "pairs": _pairs(spec)},
    )


def _set_of(pairs: list[dict]) -> set[tuple[str, str]]:
    return {(d["partNo"], d["conditionCode"]) for d in pairs}


def test_1_new_condition_covered_by_all_parts_is_accepted(client):
    # 基准：P1,P2 × C1,C2
    _load(client, "M1", ["P1", "P2"], ["C1", "C2"])
    # 新工况 C3 被两个在库备件同时覆盖 → 可整体并入（2×3 矩形）
    r = _screen(client, "M1", [("P1", "C3"), ("P2", "C3")])
    assert r.status_code == 200, r.text
    body = r.json()
    assert _set_of(body["acceptablePairs"]) == {("P1", "C3"), ("P2", "C3")}
    assert body["pendingPairs"] == []
    s = body["summary"]
    assert s["submittedPairCount"] == 2
    assert s["duplicateInWarehouseCount"] == 0
    assert s["acceptedPairCount"] == 2
    assert s["acceptedNewConditionCount"] == 1
    assert s["acceptedNewPartCount"] == 0
    assert s["plusReady"] is True
    # 基准两表原样回显
    assert body["baseline"]["parts"] == ["P1", "P2"]
    assert body["baseline"]["conditions"] == ["C1", "C2"]
    assert body["baseline"]["effectivePairCount"] == 4


def test_2_partial_new_condition_goes_pending(client):
    _load(client, "M2", ["P1", "P2"], ["C1", "C2"])
    # C3 只有 P1 报了，P2 缺证 → 不能并入，整组待补证
    r = _screen(client, "M2", [("P1", "C3")])
    assert r.status_code == 200
    body = r.json()
    assert body["acceptablePairs"] == []
    assert body["pendingPairs"] == [
        {
            "partNo": "P1",
            "conditionCode": "C3",
            "reasonCode": "CONDITION_INCOMPLETE",
            "reason": body["pendingPairs"][0]["reason"],
        }
    ]
    assert body["summary"]["plusReady"] is False


def test_3_new_part_coverage_rules(client):
    _load(client, "M3", ["P1"], ["C1", "C2"])
    # Q1 覆盖全部在库工况 → 可采纳；Q2 只报了 C1 → 待补证
    r = _screen(
        client,
        "M3",
        [("Q1", "C1"), ("Q1", "C2"), ("Q2", "C1")],
    )
    assert r.status_code == 200
    body = r.json()
    assert _set_of(body["acceptablePairs"]) == {("Q1", "C1"), ("Q1", "C2")}
    pending = body["pendingPairs"]
    assert len(pending) == 1
    assert pending[0]["partNo"] == "Q2"
    assert pending[0]["reasonCode"] == "PART_INCOMPLETE"
    assert body["summary"]["acceptedNewPartCount"] == 1
    assert body["summary"]["newPartNoCount"] == 2


def test_4_max_biclique_selection_between_parts_and_conditions(client):
    # 基准：P1 × C1（单备件单工况矩形）
    _load(client, "M4", ["P1"], ["C1"])
    # Q1、Q2 均覆盖 C1（可行新备件）；D1、D2 均被 P1 覆盖（可行新工况）。
    # 交叉边：Q1 同时有 D1、D2；Q2 只有 D1。
    # 选 Q1+Q2 只能带 D1（3+... 算下来 5 对），选 Q1 可带 D1+D2（也是 5 对），
    # 空备件带两工况仅 2 对。并列时按"更少新备件"确定性择优 → Q1 带 D1、D2。
    spec = [
        ("Q1", "C1"), ("Q2", "C1"),
        ("P1", "D1"), ("P1", "D2"),
        ("Q1", "D1"), ("Q1", "D2"),
        ("Q2", "D1"),
    ]
    r = _screen(client, "M4", spec)
    assert r.status_code == 200, r.text
    body = r.json()
    accepted = _set_of(body["acceptablePairs"])
    assert accepted == {
        ("Q1", "C1"),
        ("P1", "D1"), ("P1", "D2"),
        ("Q1", "D1"), ("Q1", "D2"),
    }
    # Q2 的配对整体待补证（Q2 个体合格，受交叉边限制 → EDGE_MISSING）
    pending = _set_of(body["pendingPairs"])
    assert pending == {("Q2", "C1"), ("Q2", "D1")}
    assert all(d["reasonCode"] == "EDGE_MISSING" for d in body["pendingPairs"])
    # 再次请求结果完全一致（确定性）
    r2 = _screen(client, "M4", spec)
    assert r2.json()["acceptablePairs"] == body["acceptablePairs"]


def test_5_duplicate_and_in_warehouse_pairs_are_removed(client):
    _load(client, "M5", ["P1", "P2"], ["C1", "C2"])
    spec = [
        ("P1", "C1"),  # 已在库，剔除
        ("P1", "C1"),  # 补报内重复 + 已在库
        ("P2", "C2"),  # 已在库，剔除
        ("P1", "C3"), ("P2", "C3"),  # 新工况证据齐 → 采纳
    ]
    r = _screen(client, "M5", spec)
    assert r.status_code == 200
    body = r.json()
    assert _set_of(body["acceptablePairs"]) == {("P1", "C3"), ("P2", "C3")}
    # 去重后 4 条提交，其中 2 条在库（补报内重复的同键先被去重）
    assert body["summary"]["submittedPairCount"] == 4
    assert body["summary"]["duplicateInWarehouseCount"] == 2


def test_6_more_than_twelve_new_part_nos_rejected(client):
    _load(client, "M6", ["P1"], ["C1", "C2"])
    # 13 个新备件，每个都覆盖全部在库工况，依然因批次上限被拒
    spec = [(f"Q{i:02d}", c) for i in range(13) for c in ("C1", "C2")]
    r = _screen(client, "M6", spec)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["limit"] == 12
    assert detail["newPartNoCount"] == 13
    assert len(detail["newPartNos"]) == 13

    # 恰好 12 个 → 放行
    ok = _screen(
        client, "M6", [(f"Q{i:02d}", c) for i in range(12) for c in ("C1", "C2")]
    )
    assert ok.status_code == 200
    assert ok.json()["summary"]["acceptedNewPartCount"] == 12


def test_7_screening_is_read_only(client):
    _load(client, "M7", ["P1", "P2"], ["C1", "C2"])
    conn = sqlite3.connect(db_path())
    before = {
        "parts": conn.execute("SELECT part_no FROM model_parts ORDER BY 1").fetchall(),
        "conds": conn.execute(
            "SELECT condition_code FROM model_conditions ORDER BY 1"
        ).fetchall(),
        "stamp": conn.execute(
            "SELECT created_at, updated_at FROM vehicle_models WHERE model_id='M7'"
        ).fetchone(),
        "rows": conn.execute("SELECT COUNT(*) FROM model_parts").fetchone()[0]
        + conn.execute("SELECT COUNT(*) FROM model_conditions").fetchone()[0],
    }
    conn.close()

    # 含会被 422 拒绝的批次和正常批次各跑一次
    r1 = _screen(
        client, "M7", [(f"Q{i}", "C1") for i in range(13)]
    )
    assert r1.status_code == 422
    r2 = _screen(client, "M7", [("P1", "C3"), ("P2", "C3"), ("Q9", "C1")])
    assert r2.status_code == 200

    conn = sqlite3.connect(db_path())
    after = {
        "parts": conn.execute("SELECT part_no FROM model_parts ORDER BY 1").fetchall(),
        "conds": conn.execute(
            "SELECT condition_code FROM model_conditions ORDER BY 1"
        ).fetchall(),
        "stamp": conn.execute(
            "SELECT created_at, updated_at FROM vehicle_models WHERE model_id='M7'"
        ).fetchone(),
        "rows": conn.execute("SELECT COUNT(*) FROM model_parts").fetchone()[0]
        + conn.execute("SELECT COUNT(*) FROM model_conditions").fetchone()[0],
    }
    conn.close()
    assert before == after
    # 库里没有任何筛选临时痕迹
    conn = sqlite3.connect(db_path())
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()
    assert tables == {"vehicle_models", "model_parts", "model_conditions"}


def test_8_acceptable_list_feeds_correct_api_as_plus(client):
    _load(client, "M8", ["P1", "P2"], ["C1", "C2"])
    spec = [
        ("P1", "C3"), ("P2", "C3"),       # 证据齐的新工况
        ("P1", "C4"),                      # 证据不齐的新工况 → 待补证
        ("Q1", "C1"), ("Q1", "C2"),        # 证据齐的新备件
        ("Q1", "C3"),                      # 新备件与新工况 C3 的交叉边，补齐后可同时并入
        ("Q2", "C1"),                      # 证据不齐的新备件 → 待补证
    ]
    r = _screen(client, "M8", spec)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["plusReady"] is True
    plus = body["acceptablePairs"]

    # 可采纳清单原样提交给原更正接口（minus 为空）
    cr = client.post(
        "/api/v1/models/correct",
        json={"modelId": "M8", "plus": plus, "minus": []},
    )
    assert cr.status_code == 200, cr.text
    merged = cr.json()
    assert merged["parts"] == ["P1", "P2", "Q1"]
    assert merged["conditions"] == ["C1", "C2", "C3"]
    # 合并后仍是完整矩形：3 备件 × 3 工况 = 9 对
    assert merged["effectivePairCount"] == 9

    # 待补证项确实没有混入
    assert _set_of(plus).isdisjoint(
        {(d["partNo"], d["conditionCode"]) for d in body["pendingPairs"]}
    )


def test_9_input_boundaries(client):
    _load(client, "M9", ["P1"], ["C1"])
    # 车型不存在
    assert _screen(client, "NOPE", [("P1", "C1")]).status_code == 404
    # 空补报（pydantic min_length 与服务端归一化双重拦截）
    r = client.post(
        "/api/v1/models/screen", json={"modelId": "M9", "pairs": []}
    )
    assert r.status_code == 422
    # 全空白字符串
    r = _screen(client, "M9", [("  ", "C1")])
    assert r.status_code == 422


def test_10_large_baseline_small_screen_does_not_join_tables(client, monkeypatch):
    from app import service

    parts = [f"P{i:04d}" for i in range(200)]
    _load(client, "BIG", parts, ["C1", "C2", "C3", "C4"])

    executed_sql: list[str] = []
    real_get_connection = service.get_connection

    def tracing_get_connection():
        conn = real_get_connection()
        conn.set_trace_callback(
            lambda sql: executed_sql.append(" ".join(sql.split()).upper())
        )
        return conn

    monkeypatch.setattr(service, "get_connection", tracing_get_connection)

    # 小批次：一个证据不齐的新工况（只报 1 个在库备件）+ 一个合格新备件
    spec = [("P0000", "C5")] + [("Q1", c) for c in ("C1", "C2", "C3", "C4")]
    t0 = time.perf_counter()
    r = _screen(client, "BIG", spec)
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    assert elapsed < 2.0, f"筛选疑似展开在库全量配对，耗时 {elapsed:.3f}s"

    body = r.json()
    # 新备件 4 对全采纳；新工况缺另外 199 个备件的证据 → 待补证
    assert _set_of(body["acceptablePairs"]) == {
        ("Q1", "C1"), ("Q1", "C2"), ("Q1", "C3"), ("Q1", "C4")
    }
    assert body["pendingPairs"][0]["reasonCode"] == "CONDITION_INCOMPLETE"
    assert body["baseline"]["effectivePairCount"] == 800

    # 核心断言：不存在 FROM/JOIN 同时以两张持久表为行源的 SQL（拼回式遍历）
    import re

    def joins_two_tables(sql: str) -> bool:
        m = re.search(r"FROM(.+?)(?:WHERE|GROUP BY|ORDER BY|LIMIT|$)", sql)
        if not m:
            return False
        sources = m.group(1)
        return "MODEL_PARTS" in sources and "MODEL_CONDITIONS" in sources

    offenders = [sql for sql in executed_sql if joins_two_tables(sql)]
    assert not offenders, f"发现同时遍历两表的 SQL: {offenders}"
    # 只读：没有任何写语句
    assert not any(
        sql.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
        for sql in executed_sql
    )


def test_11_all_pairs_already_in_warehouse(client):
    _load(client, "M11", ["P1", "P2"], ["C1", "C2"])
    r = _screen(
        client,
        "M11",
        [("P1", "C1"), ("P2", "C2"), ("P1", "C1")],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["acceptablePairs"] == []
    assert body["pendingPairs"] == []
    assert body["summary"]["duplicateInWarehouseCount"] == 2
    assert body["summary"]["submittedPairCount"] == 2
    assert body["summary"]["plusReady"] is False
    assert body["summary"]["newPartNoCount"] == 0
