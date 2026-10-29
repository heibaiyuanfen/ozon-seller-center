# Ozon API 核查与卡片缺陷处理（2026-10-29）

官方公告：https://dev.ozon.ru/news/690-Seller-API-Atribut-22232-stal-obiazatelnym-dlia-zapolneniia-dlia-mnozhestva-kategorii/
海关编码属性 22232 在多数类目必填。现有商品缺失时仍可能在售，新建商品可能无法创建。继续依实时类目属性 is_required 校验，不伪造编码。

官方公告：https://dev.ozon.ru/news/648-Izmeneniia-v-metodakh-Seller-API/
旧 /v1/warehouse/list 于 2026-03-20 停用；当前软件已使用 /v2/warehouse/list。

官方公告：https://dev.ozon.ru/news/652-Novye-limity-na-operatsii-s-tovarami-v-Seller-API/
商品操作共享每分钟 30000 次额度，超限返回 429。创建请求不能因响应不明而盲目重复提交。

流程调整：提交前登记台账；导入轮询不再因处理中附带 errors 就提前结束。
按准确货号匹配 imported，或通过 /v3/product/info/list 在当前账号确认同货号且正数商品 ID。
未确认则保留原任务，不写库存，不自动重复创建。已确认则继续核价及库存写入，保留卡片诊断。
库存仍要求 updated=true 且无库存接口错误；卡片存在不代表库存已成功或商品可销售。
台账完成状态附带卡片诊断，任务保留确认商品 ID 和诊断。失败仍保留提交前记录。

本次完整测试：306 项，1 项跳过。未调用真实创建/库存接口，未生成安装包。
官方交互式 API 文档页面无法提取完整正文，故本核查不宣称已验证所有最新字段或所有账户行为。
