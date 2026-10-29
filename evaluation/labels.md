# 标注审阅清单

作者：开发助手。预期结果由参考SQL在固定夹具上执行生成；尚无独立人员复核。

| ID | 类别 | 问题 | 模式 | 预期行为 |
|---|---|---|---|---|
| dev-001 | basic | 客户表一共有多少客户？ | single | query |
| dev-002 | basic | 订单表一共有多少条订单？ | single | query |
| dev-003 | basic | 商品表一共有多少种商品？ | single | query |
| dev-004 | filter | 华东地区共有多少客户？ | single | query |
| dev-005 | filter | 已支付的订单一共有多少条？ | single | query |
| dev-006 | metric | 所有订单的订单总金额合计多少？不要使用实付金额。 | single | query |
| dev-007 | metric | 所有已支付订单的实付金额合计多少？ | single | query |
| dev-008 | metric | 所有订单的实付金额平均值是多少？不按客户去重。 | single | query |
| dev-009 | group | 按客户等级统计客户人数，返回等级和人数。 | single | query |
| dev-010 | group | 按地区统计订单数量，返回地区和订单数。 | single | query |
| dev-011 | group | 按品类统计已支付订单的实付金额合计，返回品类和金额。 | single | query |
| dev-012 | time | 2026年9月下单的订单共有多少条？ | single | query |
| dev-013 | time | 2026年8月已支付订单实付金额合计多少？ | single | query |
| dev-014 | distinct | 所有订单中有下单记录的不同客户数是多少？ | single | query |
| dev-015 | join | 按客户等级统计已支付订单实付金额合计，返回等级和金额。 | single | query |
| dev-016 | join | 按商品名称统计订单数量，返回商品名称和订单数。 | single | query |
| dev-017 | empty | 查询华中地区客户的客户编号和客户名称。 | single | query |
| dev-018 | empty | 2027年1月的订单数量是多少？ | single | query |
| dev-019 | ranking | 实付金额最高的3笔订单，返回订单编号和实付金额；金额相同按订单编号升序。 | single | query |
| dev-020 | ratio | 所有订单中已退款订单所占比例是多少？返回0到1的小数。 | single | query |
| dev-021 | having | 订单数量大于10的客户编号及订单数有哪些？ | single | query |
| dev-022 | extreme | 订单最早和最晚下单日期分别是多少？返回两列。 | single | query |
| dev-023 | metric | 所有订单中最大和最小订单总金额分别是多少？返回两列。 | single | query |
| dev-024 | filter | 华东地区已支付订单实付金额合计多少？ | single | query |
| dev-025 | multi | 分别统计华东的客户数量与该地区已支付订单实付总额，清楚标注两个数字。 | multi | query |
| dev-026 | dialogue | 那已退款的呢？返回数量。 | single | query |
| dev-027 | clarification | 分析客户表现好不好。 | multi | clarification |
| holdout-001 | range | 订单总金额至少500且小于1000的订单数量是多少？ | single | query |
| holdout-002 | conditional | 按订单状态返回订单数量和订单总金额合计，共三列。 | single | query |
| holdout-003 | join_distinct | 按客户等级统计有已支付订单的去重客户数量，返回等级和客户数。 | single | query |
| holdout-004 | weighted | 每个购买客户的已支付实付总金额的平均值是多少？不是每笔订单的平均金额。 | single | query |
| holdout-005 | date_group | 按2026年的自然月份统计已支付订单实付总额，返回月份数字和金额。 | single | query |
| holdout-006 | cross_filter | 战略客户在2026年10月的所有订单实付金额合计多少？包含所有订单状态。 | single | query |
| holdout-007 | net | 2026年9月订单的订单总金额与实付金额之差合计多少？包含所有订单状态。 | single | query |
| holdout-008 | top_group | 按已支付订单实付总额降序列出前2个品类及金额，相同按品类升序。 | single | query |
| holdout-009 | anti_join | 哪些商品没有已支付订单？返回商品编号和名称。 | single | query |
| holdout-010 | distinct_pair | 订单中不同地区与品类组合共有多少种？ | single | query |
| holdout-011 | conditional_sum | 分别返回所有已支付订单和已退款订单的实付金额合计，输出两列。 | single | query |
| holdout-012 | date_boundary | 2026年9月1日至9月15日（含两端）的订单数量是多少？ | single | query |
| holdout-013 | multi | 分别统计华南的客户数量与该地区已支付订单实付总额，清楚标注两个数字。 | multi | query |
| holdout-014 | dialogue | 那已退款的呢？返回数量。 | single | query |
| holdout-015 | clarification | 帮我看看经营状况是否达标。 | multi | clarification |

参考SQL、必要字段及预期结果见对应JSONL。修订标注时必须升级数据版本并重新生成冻结清单。
留出题的参考信息只供评分和人工审阅，运行器不会将其注入模型。
