"""Resolve each imported product category against the live Ozon catalog."""
import re


def resolve_pool_category(item, categories, fallback=""):
    raw = str(item.get('category_display') or item.get('category') or '').strip()
    if not raw:
        return fallback
    def normalize(value):
        return re.sub(r'\s*(?:/|>|→|»|\\|\|)\s*', '/', str(value or '').strip()).casefold()
    # Localized exports place Chinese and Russian aliases on separate lines.
    # Match each alias independently and intersect evidence to reject conflicts.
    candidates = [raw] if '\n' not in raw and '\r' not in raw else [v.strip() for v in raw.splitlines() if v.strip()]
    evidence = []
    for candidate in candidates:
        wanted = normalize(candidate)
        exact, leaf = set(), set()
        for display, record in categories.items():
            paths = [display, record.get('display_ru'), '/'.join(record.get('path') or []), '/'.join(record.get('path_ru') or [])]
            if wanted in {normalize(v) for v in paths if v}:
                exact.add(display)
            names = [record.get('name'), record.get('name_ru')]
            names.extend(str(v).split('/')[-1].strip() for v in paths if v)
            if '/' not in wanted and wanted in {normalize(v) for v in names if v}:
                leaf.add(display)
        found = exact or leaf
        if found:
            evidence.append(found)
    matches = set.intersection(*evidence) if evidence else set()
    if evidence and not matches:
        raise ValueError(f"{item.get('sku') or '商品'}：双语类目匹配结果冲突，请核对原始类目")
    matches = list(matches)
    if len(matches) == 1: return matches[0]
    reason = '匹配到多个类目，请填写完整类目路径' if matches else '未匹配到 Ozon 类目，请刷新类目或核对表格类目路径'
    raise ValueError(f"{item.get('sku') or item.get('title') or '商品'}：{raw}，{reason}")
