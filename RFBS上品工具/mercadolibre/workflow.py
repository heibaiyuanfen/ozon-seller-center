from __future__ import annotations

import copy
import re
from decimal import Decimal, InvalidOperation

from .models import AccountSnapshot, JobState, ListingPlan, ListingRoute, ProductDraft, ValidationIssue, Variant


def _amount(value: str) -> float:
    try:
        number = Decimal(value)
        if not number.is_finite() or number <= 0 or number > Decimal("999999999999"):
            raise ValueError
        if number.as_tuple().exponent < -2:
            raise ValueError
        return float(number)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("金额须为大于 0 的有限数值，最多两位小数") from None


def _quantity(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("库存须为非负整数")
    return int(value)


def prepare_listing(draft: ProductDraft, *, account: AccountSnapshot | None = None,
                    category: dict | None = None, attribute_definitions: list[dict] | None = None) -> ListingPlan:
    """Build a local review candidate. No HTTP and no publication capability."""
    draft = copy.deepcopy(draft)
    for attribute in [*draft.attributes, *draft.sale_terms,
                      *(a for v in draft.variations for a in [*v.attributes, *v.attribute_combinations])]:
        attribute.id = attribute.id.strip()
        attribute.value_id = attribute.value_id.strip()
        attribute.value_name = attribute.value_name.strip()
    issues: list[ValidationIssue] = []

    def issue(key, message, severity="error"):
        issues.append(ValidationIssue(key, message, severity))

    for key, label in (("family_name", "英文家族名称"), ("description", "英文描述"),
                       ("category_id", "CBT 类目")):
        if not getattr(draft, key).strip():
            issue(key, f"请填写{label}")
    if not re.fullmatch(r"CBT[0-9]+", draft.category_id):
        issue("category_id", "跨境草稿需要 CBT 开头的类目 ID")
    if draft.currency_id != "USD":
        issue("currency_id", "Global Selling 草稿价格使用 USD")
    if draft.condition != "new" or draft.buying_mode != "buy_it_now":
        issue("condition", "当前框架支持新品、一口价")
    if not draft.listing_type_id.strip():
        issue("listing_type_id", "请填写刊登类型，并在接入后校验站点可用类型")
    if re.search(r"[\u3400-\u9fff\u0400-\u04ff]", draft.family_name + draft.description):
        issue("family_name", "家族名称/描述仍含中文或俄文，请准备英文内容")
    if draft.price_mode not in {"price", "global_net_proceeds"}:
        issue("price_mode", "价格模式须为售价 price 或净收入 global_net_proceeds")

    if draft.route not in {route.value for route in ListingRoute}:
        issue("route", "未知发布流程")
    if draft.route != ListingRoute.USER_PRODUCTS.value:
        issue("route", "当前生成新版 User Products 家族请求；传统、本土和全托管流程保留独立适配入口")
    if account and account.checked_at:
        if draft.account_id != account.user_id:
            issue("account_id", "草稿账号与当前已读取账号不一致，请重新绑定")
        if account.route.value != draft.route:
            issue("route", f"账号实际流程为 {account.route.value}，请调整草稿流程")
    else:
        issue("account_id", "尚未读取账号类型及已开通站点", "warning")

    targets = []
    seen_targets = set()
    for index, target in enumerate(draft.targets):
        key = (target.site_id, target.logistic_type)
        if key in seen_targets:
            issue("targets", "同一站点和物流方式不能重复")
        seen_targets.add(key)
        if not re.fullmatch(r"M[A-Z]{2}", target.site_id):
            issue("targets", f"第 {index + 1} 个站点 ID 格式不正确")
        if target.logistic_type not in {"remote", "fulfillment"}:
            issue("targets", "物流方式须为 remote 或 fulfillment")
        if account and account.checked_at and key not in {
            (item.get("site_id"), item.get("logistic_type")) for item in account.marketplaces
        }:
            issue("targets", f"账号未返回可用站点/物流组合：{target.site_id}/{target.logistic_type}")
        for capability in (account.marketplaces if account else []):
            if (capability.get("site_id"), capability.get("logistic_type")) == key:
                if capability.get("business_model") == "CBT CN Fulfillment Managed":
                    issue("targets", "已识别全托管站点，标准跨境请求不适用")
                mode = capability.get("pricing_model")
                if mode:
                    issue("price_mode", f"{target.site_id} 账号价格模型为 {mode}；当前选择 {draft.price_mode}，需联调核对", "warning")
        payload = {"site_id": target.site_id, "logistic_type": target.logistic_type}
        if target.price:
            issue("targets", "站点单独定价已保留在模型，当前家族创建预览使用每规格统一金额；请清空站点金额")
        if target.listing_type_id:
            payload["listing_type_id"] = target.listing_type_id
        targets.append(payload)
    if not targets:
        issue("targets", "至少添加一个目标站点/物流组合")
    if len(targets) > 30:
        issue("targets", "每个 User Product 最多 30 个销售条件")

    pictures = []
    for index, picture in enumerate(draft.pictures):
        value = {"id": picture.picture_id.strip()} if picture.picture_id.strip() else None
        if value is None:
            if not draft.variations:
                issue("pictures", f"第 {index + 1} 张图片尚未取得美客多图片 ID；本地图片/来源 URL 已保留，等待上传适配")
        else:
            pictures.append(value)
    if not draft.pictures and not draft.variations:
        issue("pictures", "至少添加一张产品图片")

    attrs = {}
    for attribute in draft.attributes:
        if not attribute.id or not (attribute.value_id or attribute.value_name):
            issue("attributes", "属性必须包含 ID 和值")
        if attribute.id in attrs:
            issue("attributes", f"属性 {attribute.id} 重复")
        attrs[attribute.id] = attribute.payload()
    if "SELLER_SKU" in attrs:
        issue("attributes", "SELLER_SKU 由货号字段生成，请从属性列表移除，避免重复")
    if "ITEM_CONDITION" not in attrs:
        attrs["ITEM_CONDITION"] = {"id": "ITEM_CONDITION", "value_id": "2230284", "value_name": "New"}
    if category is not None and category.get("id") != draft.category_id:
        issue("category_id", "类目缓存与当前草稿不一致，请重新读取类目")
    elif category is not None:
        settings = category.get("settings") or {}
        if settings.get("listing_allowed") is False or category.get("children_categories"):
            issue("category_id", "请选择允许发布的末级类目")
        limit = settings.get("max_title_length")
        if isinstance(limit, int) and len(draft.family_name) > limit:
            issue("family_name", f"家族名称超出当前类目标题限制 {limit} 字符")
        max_pictures = settings.get("max_pictures_per_item")
        if isinstance(max_pictures, int) and len(pictures) > max_pictures:
            issue("pictures", f"图片数量超出当前类目限制 {max_pictures}")
    else:
        issue("category_id", "尚未读取类目限制", "warning")
    if attribute_definitions is None:
        issue("attributes", "尚未读取必填属性、GTIN 与包装规格要求", "warning")
    else:
        for definition in attribute_definitions:
            tags = definition.get("tags") or {}
            required = tags.get("required") or tags.get("new_required")
            attr_id = definition.get("id")
            if required and attr_id not in attrs and attr_id != "SELLER_SKU" and not draft.variations:
                issue("attributes", f"缺少类目必填属性：{attr_id} / {definition.get('name', '')}")
            if tags.get("conditional_required"):
                issue("attributes", f"条件必填属性 {attr_id} 仍需平台验证", "warning")

    variants = draft.variations or [Variant(
        seller_sku=draft.seller_sku, available_quantity=draft.available_quantity,
        price=draft.price, picture_ids=[picture["id"] for picture in pictures],
    )]
    family_payload = []
    seen_skus = set()
    for index, variant in enumerate(variants):
        label = f"第 {index + 1} 个规格"
        sku = variant.seller_sku.strip()
        if not sku or sku in seen_skus:
            issue("seller_sku", f"{label}需填写独立、非重复货号")
        seen_skus.add(sku)
        try:
            amount = _amount(variant.price or draft.price)
            quantity = _quantity(variant.available_quantity)
            if quantity == 0:
                issue("available_quantity", f"{label}新品库存至少 1 件")
        except ValueError as error:
            issue("variations", f"{label}：{error}")
            amount, quantity = 0, 0
        variant_attrs = copy.deepcopy(attrs)
        own_ids = set()
        for attribute in [*variant.attributes, *variant.attribute_combinations]:
            if not attribute.id or not (attribute.value_name or attribute.value_id):
                issue("attributes", f"{label}属性 ID 或值为空")
            if attribute.id == "SELLER_SKU" or attribute.id in own_ids:
                issue("attributes", f"{label}属性 {attribute.id} 重复或与自动货号冲突")
            own_ids.add(attribute.id)
            variant_attrs[attribute.id] = attribute.payload()
        variant_attrs["SELLER_SKU"] = {"id": "SELLER_SKU", "value_name": sku}
        for definition in attribute_definitions or []:
            tags = definition.get("tags") or {}
            attr_id = definition.get("id")
            if (tags.get("required") or tags.get("new_required")) and attr_id not in variant_attrs:
                issue("attributes", f"{label}缺少必填属性 {attr_id}")
            if tags.get("read_only") and attr_id in variant_attrs:
                issue("attributes", f"{label}不能提交只读属性 {attr_id}")
        ids = [value.strip() for value in variant.picture_ids]
        if not ids or any(not value.strip() for value in ids):
            issue("pictures", f"{label}需关联上传后取得的美客多图片 ID")
        if len(ids) != len(set(ids)):
            issue("pictures", f"{label}图片 ID 重复")
        max_pictures = ((category or {}).get("settings") or {}).get("max_pictures_per_item")
        if isinstance(max_pictures, int) and len(ids) > max_pictures:
            issue("pictures", f"{label}图片数量超出当前类目限制 {max_pictures}")
        family_payload.append({
            "family_name": draft.family_name, "category_id": draft.category_id,
            "currency_id": "USD", draft.price_mode: amount, "available_quantity": quantity,
            "description": {"plain_text": draft.description},
            "pictures": [{"id": value} for value in ids],
            "attributes": list(variant_attrs.values()), "sites_to_sell": copy.deepcopy(targets),
        })
    seen_terms = set()
    for term in draft.sale_terms:
        if not term.id or not (term.value_id or term.value_name) or term.id in seen_terms:
            issue("sale_terms", "保修条款需要唯一 ID 和非空值")
        seen_terms.add(term.id)

    issue("api", "本次仅本地检查，未调用平台校验/发布；站点资格、类目业务规则仍待联调", "warning")
    issue("schema", "采用 2026-09-01 User Products 家族文档；Parent/Child PK 分组、属性枚举及物流库存规则仍待平台验证", "warning")
    errors = any(item.severity == "error" for item in issues)
    candidate = None
    if not errors:
        candidate = family_payload
        if draft.sale_terms:
            for item in candidate:
                item["sale_terms"] = [term.payload() for term in draft.sale_terms]
    steps = [
        {"step": "账号授权与流程识别", "method": "GET", "path": "/users/me", "implemented": True},
        {"step": "读取已开通站点/物流", "method": "GET", "path": "/marketplace/users/{user_id}", "implemented": True},
        {"step": "类目及属性检查", "method": "GET", "path": "/categories/{category_id}/attributes", "implemented": True},
        {"step": "图片上传及平台业务校验", "implemented": False},
        {"step": "创建商品家族", "method": "POST", "path": "/global/user-products/families" if draft.route == ListingRoute.USER_PRODUCTS.value else None, "implemented": False},
        {"step": "回查各站点结果，保存 global/site item ID；不明结果先核对再重试", "implemented": False},
    ]
    return ListingPlan(draft.id, draft.route, JobState.INVALID.value if errors else JobState.PREPARED.value,
                       candidate, issues, steps)


def publish(*_args, **_kwargs):
    raise RuntimeError("美客多第一阶段仅支持草稿和请求预览，尚未接通真实发布")
