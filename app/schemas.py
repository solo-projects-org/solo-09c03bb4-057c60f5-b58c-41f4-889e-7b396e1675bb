"""请求 / 响应模型（Swagger 文档即由此生成）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Pair(BaseModel):
    """一条"备件号 × 工况"配对录入。"""

    part_no: str = Field(..., alias="partNo", description="备件号", examples=["P-1001"])
    condition_code: str = Field(
        ..., alias="conditionCode", description="工况代码", examples=["C-HOT"]
    )

    model_config = {"populate_by_name": True}


class LoadRequest(BaseModel):
    model_id: str = Field(
        ..., alias="modelId", description="车型标识", examples=["SEDAN-A1"]
    )
    pairs: list[Pair] = Field(..., min_length=1, description="备件号与工况配对，重复配对只算一条")

    model_config = {"populate_by_name": True}


class CorrectionRequest(BaseModel):
    """批量更正：plus = 标记为"有"，minus = 标记为"无"。"""

    model_id: str = Field(..., alias="modelId", examples=["SEDAN-A1"])
    plus: list[Pair] = Field(
        default_factory=list, description="标记为有（新增/恢复）的组合"
    )
    minus: list[Pair] = Field(
        default_factory=list, description="标记为无（移除）的组合"
    )

    model_config = {"populate_by_name": True}


class ScreenRequest(BaseModel):
    """厂商补报筛选请求：提交已入库车型 + 本批补报配对（只读筛选，不写库）。"""

    model_id: str = Field(..., alias="modelId", examples=["SEDAN-A1"])
    pairs: list[Pair] = Field(
        ...,
        min_length=1,
        description="厂商补报的备件号与工况配对，重复配对只算一条",
    )

    model_config = {"populate_by_name": True}


class ExtraCombination(BaseModel):
    part_no: str = Field(..., alias="partNo")
    condition_code: str = Field(..., alias="conditionCode")

    model_config = {"populate_by_name": True}


class ModelResponse(BaseModel):
    model_id: str = Field(..., alias="modelId")
    status: str
    created_at: str | None = Field(default=None, alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")
    part_count: int = Field(..., alias="partCount")
    condition_count: int = Field(..., alias="conditionCount")
    effective_pair_count: int = Field(
        ..., alias="effectivePairCount", description="两表拼回后同时生效的配对数"
    )
    parts: list[str]
    conditions: list[str]
    before: dict | None = Field(
        default=None, description="仅批量更正响应中出现，记录更正前两表规模"
    )

    model_config = {"populate_by_name": True}


class ModelSummary(BaseModel):
    model_id: str = Field(..., alias="modelId")
    created_at: str = Field(..., alias="createdAt")
    updated_at: str = Field(..., alias="updatedAt")
    part_count: int = Field(..., alias="partCount")
    condition_count: int = Field(..., alias="conditionCount")
    effective_pair_count: int = Field(..., alias="effectivePairCount")

    model_config = {"populate_by_name": True}


class ModelListResponse(BaseModel):
    models: list[ModelSummary]


# --------------------------------------------------------------------------- #
# 补报筛选（只读）
# --------------------------------------------------------------------------- #
class ScreenBaseline(BaseModel):
    """筛选所依据的、同一已提交状态下的两张基准表（原样回显，未被改动）。"""

    part_count: int = Field(..., alias="partCount")
    condition_count: int = Field(..., alias="conditionCount")
    effective_pair_count: int = Field(
        ..., alias="effectivePairCount", description="两表拼回后同时生效的配对数"
    )
    parts: list[str]
    conditions: list[str]

    model_config = {"populate_by_name": True}


class PendingPair(BaseModel):
    """待补证配对：本批无法无损并入，需厂商补齐证据后重报。"""

    part_no: str = Field(..., alias="partNo")
    condition_code: str = Field(..., alias="conditionCode")
    reason_code: str = Field(
        ..., alias="reasonCode", description="PART_INCOMPLETE / CONDITION_INCOMPLETE / EDGE_MISSING"
    )
    reason: str

    model_config = {"populate_by_name": True}


class ScreenSummary(BaseModel):
    submitted_pair_count: int = Field(
        ..., alias="submittedPairCount", description="去重后本批补报配对条数"
    )
    duplicate_in_warehouse_count: int = Field(
        ...,
        alias="duplicateInWarehouseCount",
        description="已在库、从补报中剔除的配对条数（仅成员判定，未展开在库全量配对）",
    )
    new_part_no_count: int = Field(
        ..., alias="newPartNoCount", description="补报中不在基准备件表内的备件号种数"
    )
    accepted_pair_count: int = Field(..., alias="acceptedPairCount")
    pending_pair_count: int = Field(..., alias="pendingPairCount")
    accepted_new_part_count: int = Field(..., alias="acceptedNewPartCount")
    accepted_new_condition_count: int = Field(..., alias="acceptedNewConditionCount")
    plus_ready: bool = Field(
        ...,
        alias="plusReady",
        description="可采纳清单非空；为 true 时可原样作为原更正接口的 plus 新增项",
    )

    model_config = {"populate_by_name": True}


class ScreenResponse(BaseModel):
    model_id: str = Field(..., alias="modelId")
    baseline: ScreenBaseline
    acceptable_pairs: list[Pair] = Field(
        ..., alias="acceptablePairs", description="可采纳清单（可直接作为更正接口 plus）"
    )
    pending_pairs: list[PendingPair] = Field(
        ..., alias="pendingPairs", description="待补证清单（退回厂商补证）"
    )
    summary: ScreenSummary

    model_config = {"populate_by_name": True}
