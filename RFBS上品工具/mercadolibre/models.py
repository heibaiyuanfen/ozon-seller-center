from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ListingRoute(str, Enum):
    UNKNOWN = "unknown"
    GLOBAL = "global_selling"
    USER_PRODUCTS = "user_products"
    LOCAL = "local"
    FULLY_MANAGED = "fully_managed"


class JobState(str, Enum):
    DRAFT = "draft"
    INVALID = "invalid"
    PREPARED = "prepared"  # Local plan only, never means API accepted.
    SUBMITTING = "submitting"
    RECONCILE = "reconcile"  # Uncertain HTTP outcome: query before retrying.
    PARTIAL = "partial"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass
class Picture:
    source: str = ""
    picture_id: str = ""
    local_path: str = ""


@dataclass
class Attribute:
    id: str = ""
    value_name: str = ""
    value_id: str = ""

    def payload(self) -> dict:
        result = {"id": self.id.strip()}
        if self.value_id.strip():
            result["value_id"] = self.value_id.strip()
        if self.value_name.strip():
            result["value_name"] = self.value_name.strip()
        return result


@dataclass
class MarketplaceTarget:
    site_id: str = "MLM"
    logistic_type: str = "remote"
    price: str = ""  # Optional USD override in the Global Selling route.
    listing_type_id: str = ""


@dataclass
class Variant:
    seller_sku: str = ""
    available_quantity: str = "0"
    price: str = ""
    attribute_combinations: list[Attribute] = field(default_factory=list)
    attributes: list[Attribute] = field(default_factory=list)
    picture_ids: list[str] = field(default_factory=list)


@dataclass
class Procurement:
    supplier_url: str = ""
    supplier_sku: str = ""
    cost_cny: str = ""
    notes: str = ""


def _record(cls, data: dict):
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__} 必须是对象")
    allowed = {f.name for f in fields(cls)}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"{cls.__name__} 包含未知字段：{', '.join(sorted(unknown))}")
    return cls(**data)


def records(cls, values: Any) -> list:
    if not isinstance(values, list):
        raise ValueError(f"{cls.__name__} 数据必须是数组")
    return [_record(cls, item) for item in values]


@dataclass
class ProductDraft:
    id: str = field(default_factory=lambda: uuid4().hex)
    schema_version: int = 1
    title: str = ""
    family_name: str = ""
    description: str = ""
    seller_sku: str = ""
    category_id: str = ""
    price: str = ""
    price_mode: str = "price"
    currency_id: str = "USD"
    available_quantity: str = "1"
    condition: str = "new"
    buying_mode: str = "buy_it_now"
    listing_type_id: str = "gold_pro"
    account_id: str = ""
    route: str = ListingRoute.USER_PRODUCTS.value
    procurement: Procurement = field(default_factory=Procurement)
    targets: list[MarketplaceTarget] = field(default_factory=lambda: [MarketplaceTarget()])
    pictures: list[Picture] = field(default_factory=list)
    attributes: list[Attribute] = field(default_factory=list)
    variations: list[Variant] = field(default_factory=list)
    sale_terms: list[Attribute] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProductDraft":
        if not isinstance(data, dict) or type(data.get("schema_version", 1)) is not int or data.get("schema_version", 1) != 1:
            raise ValueError("不支持的美客多草稿格式或版本")
        values = dict(data)
        for key, model in (("targets", MarketplaceTarget), ("pictures", Picture),
                           ("attributes", Attribute), ("sale_terms", Attribute)):
            if key in values:
                values[key] = records(model, values[key])
        if "procurement" in values:
            values["procurement"] = _record(Procurement, values["procurement"])
        if "variations" in values:
            variants = records(Variant, values["variations"])
            for variant in variants:
                variant.attributes = records(Attribute, variant.attributes)
                variant.attribute_combinations = records(Attribute, variant.attribute_combinations)
                if not isinstance(variant.picture_ids, list) or not all(
                    isinstance(value, str) for value in variant.picture_ids
                ):
                    raise ValueError("变体 picture_ids 必须是字符串数组")
            values["variations"] = variants
        result = _record(cls, values)
        # Allow incomplete form strings, but reject malformed imported JSON types.
        for model in [result, result.procurement, *result.targets, *result.pictures,
                      *result.attributes, *result.sale_terms, *result.variations,
                      *(a for v in result.variations for a in [*v.attributes, *v.attribute_combinations])]:
            for item in fields(model):
                if item.type == "str" and not isinstance(getattr(model, item.name), str):
                    raise ValueError(f"{item.name} 必须是文本")
        return result


@dataclass
class AccountSnapshot:
    user_id: str = ""
    site_id: str = ""
    tags: list[str] = field(default_factory=list)
    marketplaces: list[dict] = field(default_factory=list)
    checked_at: str = ""

    @classmethod
    def from_api(cls, me: dict, marketplace_response: dict) -> "AccountSnapshot":
        user_id = str(me.get("id") or "")
        site_id = me.get("site_id")
        tags = me.get("tags", [])
        marketplaces = marketplace_response.get("marketplaces", [])
        if not user_id.isascii() or not user_id.isdigit() or not isinstance(site_id, str) or not site_id:
            raise ValueError("账号 API 未返回有效的卖家 ID 和站点")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("账号标签格式异常")
        if not isinstance(marketplaces, list) or not all(
            isinstance(item, dict) and isinstance(item.get("site_id"), str)
            and isinstance(item.get("logistic_type"), str) for item in marketplaces
        ):
            raise ValueError("账号可用站点格式异常")
        merchant_id = marketplace_response.get("user_id")
        if merchant_id is not None and str(merchant_id) != user_id:
            raise ValueError("市场配置所属商家与当前授权账号不一致")
        return cls(user_id, site_id, list(tags), [dict(item) for item in marketplaces], utc_now())

    @property
    def route(self) -> ListingRoute:
        if not self.user_id or not self.site_id:
            return ListingRoute.UNKNOWN
        if self.site_id != "CBT":
            return ListingRoute.LOCAL
        if self.marketplaces and all(
            item.get("business_model") == "CBT CN Fulfillment Managed" for item in self.marketplaces
        ):
            return ListingRoute.FULLY_MANAGED
        if {"user_product_seller", "user_products_seller"}.intersection(self.tags):
            return ListingRoute.USER_PRODUCTS
        return ListingRoute.GLOBAL


@dataclass
class ValidationIssue:
    field: str
    message: str
    severity: str = "error"


@dataclass
class ListingPlan:
    draft_id: str
    route: str
    state: str
    payload: dict | list | None
    issues: list[ValidationIssue]
    steps: list[dict]
    publish_enabled: bool = False
    api_validated: bool = False
    schema_profile: str = "up-families-2026-09-01-candidate"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PublishJob:
    """Future execution checkpoint; local planning does not create remote items."""
    draft_id: str
    account_id: str
    id: str = field(default_factory=lambda: uuid4().hex)
    state: str = JobState.DRAFT.value
    payload_sha256: str = ""
    global_item_id: str = ""
    siteless_user_product_id: str = ""
    site_results: list[dict] = field(default_factory=list)
    last_error: str = ""
