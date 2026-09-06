# 美客多独立上架框架

调研日期：2026-09-06。第一阶段提供产品表单、本地草稿、账号与类目只读查询、本地检查和请求预览。预览通过不代表平台接受，更不代表产品已经上架。

## 隔离边界

- 代码位于 `mercadolibre/`，不导入 Ozon/WB 的接口、任务队列、配置或商品映射。
- 界面由独立板块承载，普通操作使用表单；JSON 仅供高级编辑和请求检查。
- 运行数据位于 `APP_DATA_DIR/mercadolibre-data`。草稿、计划和图片按产品 ID 分目录保存，避免不同产品的图片混放。
- Access Token 仅在本次进程内存使用，不写入草稿、计划、图片目录或日志。
- 当前不交换 OAuth 授权码、不刷新 Token、不上传图片、不创建远程商品、不自动同步库存。

启动原来的启动器，进入“8. 美客多（独立）”，新建商品并填写英文家族名、货号、金额、采购信息和目标站点。添加本地图片后点击“保存草稿”；“本地校验 / 生成预览”会列出缺项。多规格在“属性 / 多规格”的高级编辑区填写，单规格留空数组。关闭软件前保存，切换商品时也会保存当前修改。

```text
mercadolibre-data/
  settings.json                 账号备注 / App ID / 回调地址
  products/<产品ID>/draft.json   单个产品家族及采购信息
  products/<产品ID>/images/      01_xxx.jpg、02_xxx.png 等独立编号图片
  products/<产品ID>/plans/       每次本地检查的独立快照
```

本地目录被 Git 忽略，不随源代码更新上传。新版请求需要真实图片 ID；仅有本地图片或来源 URL 时，检查会提示“待上传”，不会生成虚构 ID。

## 当前 API 选择

[User Products / Price per Variation](https://global-selling.mercadolibre.com/devsite/en_us/price-per-variation-cbt) 更新于 2026-09-01：新品默认以 UP Siteless 建模，每个规格是独立产品，共享家族名称；销售条件按目标站点配置。

默认请求预览使用 `POST /global/user-products/families`，根请求为数组，每个元素是一款规格。必填表列出 `family_name`、`sites_to_sell`、`attributes`、`pictures`、`category_id`、`currency_id`，以及取决于定价模型的 `price` 或 `global_net_proceeds`。图片仅提交平台 ID，放在产品根级；不发送 `title` 或旧 `variations` 数组。

以下仅说明请求层级；示例 ID 均为占位符，不能直接发布：

```json
[
  {
    "family_name": "Desk Lamp",
    "category_id": "CBT_CATEGORY_ID",
    "currency_id": "USD",
    "price": 29.90,
    "available_quantity": 10,
    "sites_to_sell": [{"site_id": "MLM", "logistic_type": "remote"}],
    "description": {"plain_text": "English product description."},
    "pictures": [{"id": "UPLOADED_PICTURE_ID"}],
    "attributes": [
      {"id": "ITEM_CONDITION", "value_id": "2230284", "value_name": "New"},
      {"id": "SELLER_SKU", "value_name": "DESK-LAMP-001"}
    ]
  }
]
```

实际属性必须由类目补全，示例不构成完整商品。采购链接、成本、供货商货号和备注只存本地，不进入平台请求。多规格沿用共同家族名，差异放属性；一期可通过高级 JSON 编辑规格并生成家族数组预览，可视化规格矩阵与提交留待后续接入。

## 账号能力与文档差异

[Publish items](https://global-selling.mercadolibre.com/devsite/en_us/seller-campaign/global-listing) 更新于 2026-07-03，声明旧发布模型在 2026-07-31 后逐步停用。不能只按旧 `/global/items` 示例设计新模块。

[2026-08-14 FAQ](https://global-selling.mercadolibre.com/devsite/en_us/size-chart-validation/global-selling-item-create-update-global-items) 与新版 UP 主文档在标签拼写、标题字段、图片层级和创建端点上不一致。主文档使用 `user_product_seller`；FAQ 使用 `user_products_seller`。实现兼容识别两种标签，但保留实际返回数据，并以 9 月 UP 主文档生成候选请求。旧模型和本土卖家暂不套用此候选请求。

账号检查读取 `/users/me` 与 `/marketplace/users/{merchant_id}`，目标站点必须匹配返回的站点/物流组合。商品价格和净回款不是同一含义，应结合 `pricing_model` 选择字段。[账号层级说明](https://global-selling.mercadolibre.com/devsite/manage-questions-answers-global-selling/items-and-searches-global-selling)

当市场配置含 `business_model = "CBT CN Fulfillment Managed"` 时，判定为全托管。全托管由平台管理站点、库存和最终售价，采用独立发布结构；一期仅识别，不生成普通跨境发布请求。[全托管 API，2026-07-03](https://global-selling.mercadolibre.com/devsite/fully-managed-product-publishing)

## 数据模型和流程

| 模型 | 用途 |
| --- | --- |
| `AccountSnapshot` | 账号、标签、已开通站点、物流、定价与经营模式的只读快照 |
| `ProductDraft` | 英文商品内容、家族名、货号、类目、采购资料和图片 |
| `Variant` / `MarketplaceTarget` | 规格与站点销售条件，分别维护 |
| `ValidationIssue` / `ListingPlan` | 字段问题、检查范围、候选请求和后续步骤 |
| `PublishJob` | 为后续任务状态、远程 ID 与部分成功结果预留的数据结构 |

流程：填写商品 → 保存草稿 → 读取账号能力 → 选择类目并读取属性 → 补齐资料和图片 → 本地检查 → 保存请求预览。每次修改商品后重新检查，不沿用旧结论。

本地 `prepared` 只表示计划已生成。未来执行层需要区分提交中、结果待核对、部分成功、发布成功与失败；网络超时不能直接重试创建。需保存各站点结果及 UP/family/item 映射后再处理失败站点。这些执行状态目前只是模型预留。

## 已核对的接口与后续阶段

| 用途 | 官方接口 | 当前边界 |
| --- | --- | --- |
| 账号与市场 | `GET /users/me`、`GET /marketplace/users/{id}` | 只读连接 |
| 类目预测 | `GET /marketplace/domain_discovery/search?q=...` | 后续接入英文名称查询 |
| 类目与属性 | `GET /categories/{id}`、`GET /categories/{id}/attributes` | 只读检查依据 |
| 技术规格 | `GET /categories/{id}/technical_specs/input` | 后续动态表单扩展 |
| 发布配额 | `GET /marketplace/users/cap` | 后续提交前检查 |
| 图片上传 | `POST /pictures/items/upload` | 后续接入；当前保存本地图片或已上传 ID |
| UP 创建 | `POST /global/user-products/families` | 仅候选预览，禁止执行 |
| 结果读取 | `GET /user-products/{id}` | 后续保存平台 ID 后接入 |

类目必填字段、GTIN 条件、取值范围、图片数量和包装参数来自平台规则，不能把通用表单视作完整规则。参考：[类目预测](https://global-selling.mercadolibre.com/devsite/devsite/category-predictor)、[属性](https://global-selling.mercadolibre.com/devsite/en_us/deals-gs/atributtes-global-selling)、[产品标识](https://global-selling.mercadolibre.com/devsite/category-predictor/product-identifiers-gs)。

图片 API 接收 multipart 文件并返回图片 ID；产品来源 URL 与平台图片 ID 分开保存。图片格式、大小与类目数量限制在实际上传阶段验证。[图片 API，2026-03-24](https://global-selling.mercadolibre.com/devsite/manage-questions-answers-global-selling/pictures)

未确认存在适用于所有账号的生产环境 dry-run。全托管文档明确 `/global/items/validate` 仅测试环境可用；当前校验只是本地检查。未来解析平台 `cause[]` 的错误级别、代码、消息和字段引用，并保留逐站点结果。[校验说明，2026-09-01](https://global-selling.mercadolibre.com/devsite/devsite/validations-cbt)

OAuth 接入作为下一阶段：授权地址为 `https://global-selling.mercadolibre.com/authorization`，Token 交换和刷新为 `POST /oauth/token`。校验 `state`、严格匹配 redirect URI；启用 PKCE 时使用 S256。刷新 Token 单次使用，应原子保存新值，过期时间取返回的 `expires_in`。[OAuth 文档，2026-04-01](https://global-selling.mercadolibre.com/devsite/authentication-and-authorization-global-selling)

后续顺序：OAuth 与安全凭据存储 → 图片上传 → 完整动态属性/规格编辑 → 测试卖家联调 → 独立发布执行器及结果回查。开启真实发布前，以实际账号能力和测试结果确认接口契约。
