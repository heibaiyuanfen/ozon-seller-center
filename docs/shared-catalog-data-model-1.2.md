# 产品库上传适配：DATA_MODEL 1.2.0

依据：<https://github.com/wxz2551664230-alt/erp-shared-catalog-sdk/blob/main/DATA_MODEL.md>，以及同仓库 API.md、openapi.json。
文档版本 1.2.0，日期 2026-10-24；接口路径保持 `/api/v1/shared-catalog/v1`。

## 上传行为

上架与台账完成后，通过现有 POST products 接口上传产品资料。
发送前要求 capabilities 同时声明 version=1、authentication=none、upload_library=wff、initial_status=ready。
服务器负责写入 source_type=wff 与 status=ready，客户端不发送这两个字段。
ready 表示产品库中的可发布状态，不代表平台审核完成，也不会触发新的 Ozon/WB 发布。
本次没有新增登录、账号密码、Cookie 或 API Key 参数。

## 数据映射

| 台账字段 | 上传字段 |
| --- | --- |
| 货号、标题 | 产品 sku、name，以及规格 sku |
| 型号名称 | variants[].specification |
| 采购成本 | variants[].purchase_price_cny，人民币元，非负数 |
| 包装长度、宽度、高度 | variants[].package_length_mm / package_width_mm / package_height_mm，毫米 |
| 包装毛重 | variants[].weight_grams，克 |
| 主图链接、全部图片链接JSON、附图链接 | variants[].images，主图优先、去重，最多15张 |
| 1688采购链接 / Ozon来源链接 | 可选 source_url |
| 类目ID、类型ID | description_category_id、type_id，正整数或未知 |

未知数值传 null；尺寸与重量不能用0代替未知。
中文“测试”和空货源保留在本地台账，不放进 URL 字段；优先回退有效 Ozon 商品链接，否则省略 source_url。
Ozon/1688 商品页会去掉跟踪查询串，图片 URL 不擅自删除签名后冒充永久链接。
公开链接限制：HTTPS、默认端口或443、最长4000字符、无认证/查询串/片段；拒绝本机、单段主机名和私有/保留 IP 字面地址。
规格描述、颜色、尺码、供应商有对应数据时按协议限长；不会发送本机路径、文案、标签、完整属性、售价、库存或密钥。
通用客户端允许一个产品1–100个规格；当前台账适配继续沿用每条店铺货号一条产品的既有身份映射，不按标题或型号擅自重组历史商品。

## 身份与恢复

继续使用原有 namespace.json 和 source/external_id 算法，保留全部历史请求和回执。
缺少 namespace.json 但存在历史请求/回执时停止准备新请求，要求恢复身份文件。
已有服务端回执直接复用，不批量重新上传历史商品，不因归属 wff 改变已有身份。
同身份不同内容保留原请求，报告冲突，不通过更换 ID 绕过。
新增回执必须包含有效 product_id 和 created/replayed 布尔状态。

- 409：保留原资料，人工核对。
- 422：显示字段校验问题；不会尝试把多余字段提交给服务器。
- 429：解析 Retry-After 秒数或 HTTP 日期，按服务器地址记录本地冷却时间，暂停本轮批量补传。
- 超时/5xx：结果未知，保留原始请求供人工原样重试，不自动循环或重新执行上架。
- 错误日志只保留 HTTP 状态和受限格式 trace_id，不回显任意响应正文、请求头或凭据。

读取仍兼容其他产品库和未来 source_type，不把公开读取范围缩成仅 wff，不添加文档没有的服务端筛选或更新接口。

## 本次调试

只修改源码并执行测试；不生成或替换 EXE、ZIP、安装包，也不实际新增服务器产品。
运行源码调试需退出旧进程后重新启动；之前安装的 EXE 仍是旧版本，待调试完成后再打包。
