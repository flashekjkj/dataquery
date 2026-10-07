from __future__ import annotations
"""
静态业务Schema定义
描述 customer/orders/products/三张电商表：字段名称、中文标签、类型、说明、别名、字段角色
用于Agent做Schema检索、理解表结构、生成SQL
"""
import json
from pathlib import Path

def _field(
        name:str,
        label:str,
        field_type:str,
        description:str,
        aliases:list[str],
        role:str,
        aggregation:str = "none",
) -> dict:
    """
    构建字段元信息
    role可选值：
        identifier：主键/唯一标识
        dimension：维度（分组字段）
        metric：指标（可sum/avg等聚合）
        filter：过滤字段
        time：时间字段
        foreign_key：外键，关联其他表
    """
    return {
        "name":name,
        "label":label,
        "type":field_type,
        "description":description,
        "aliases":aliases,
        "role":role,
        "aggregation":aggregation,
    }

# =========字段定义============
CUSTOMER_FIELDS = [
    _field("customer_id","客户编号", "整数", "客户唯一标识，订单表通过该字段关联客户", ["客户ID"], "identifier"),
    _field("customer_name", "客户名称", "文本", "客户姓名", ["客户名"], "dimension", "group"),
    _field("customer_level", "客户等级", "文本", "客户等级：战略客户/重点客户/普通客户", ["等级", "客户分层"], "dimension", "group"),
    _field("region", "所在地区", "文本", "客户所在地区", ["地区", "区域"], "dimension", "group"),
]

PRODUCT_FIELDS = [
    _field("product_id", "产品编号", "整数", "产品唯一标识，订单表通过该字段关联商品", ["产品ID", "商品ID"], "identifier"),
    _field("product_name", "产品名称", "文本", "商品名称", ["商品名"], "dimension", "group"),
    _field("category", "产品类别", "文本", "商品所属品类", ["品类", "产品类型"], "dimension", "group"),
]

ORDER_FIELDS = [
    _field("order_id", "订单编号", "整数", "订单唯一编号", ["订单号"], "identifier", "count"),
    _field("order_date", "下单日期", "日期", "订单创建日期 YYYY-MM-DD", ["下单时间", "成交日期"], "time", "group"),
    _field("region", "所在地区", "文本", "订单客户所在地区（冗余自客户表，便于直接按地区统计）", ["地区", "区域"], "dimension", "group"),
    _field("category", "商品品类", "文本", "订单商品所属品类（冗余自商品表，便于直接按品类统计）", ["品类", "产品类型"], "dimension", "group"),
    _field("order_amount", "订单总金额", "数值", "订单商品总金额", ["订单金额"], "metric", "sum"),
    _field("paid_amount", "实付金额", "数值", "客户实际支付金额", ["实付", "销售额"], "metric", "sum"),
    _field("status", "订单状态", "文本", "订单状态：已支付/已取消/已退款", ["支付状态"], "filter", "group"),
    _field("customer_id", "客户编号", "整数", "关联客户表的客户id", ["客户ID"], "foreign_key"),
    _field("product_id", "产品编号", "整数", "关联商品表的商品id", ["商品ID"], "foreign_key"),
]

# ========== 表Schema定义 ==========
SCHEMA = [
    {
        "id": "customers",
        "label": "客户信息表",
        "database": "askdata_mock",
        "domain": "客户管理",
        "description": "存储电商客户基础信息，可与订单表关联查询客户下单情况",
        "business_terms": ["客户信息", "客户画像"],
        "primary_key": ["customer_id"],
        "fields": CUSTOMER_FIELDS,
    },
    {
        "id": "products",
        "label": "商品信息表",
        "database": "askdata_mock",
        "domain": "商品管理",
        "description": "电商商品基础信息，包含商品名称、品类，可关联订单表统计商品销量",
        "business_terms": ["商品", "产品信息"],
        "primary_key": ["product_id"],
        "fields": PRODUCT_FIELDS,
    },
    {
        "id": "orders",
        "label": "订单明细表",
        "database": "askdata_mock",
        "domain": "交易订单",
        "description": "电商订单交易明细，记录每一笔订单的时间、金额，冗余了地区与品类信息，关联客户与商品",
        "business_terms": ["订单", "交易记录", "销售数据"],
        "primary_key": ["order_id"],
        "fields": ORDER_FIELDS,
    },
]

# ========== 表关联关系 ==========
RELATIONS = [
    {
        "left_table": "orders",
        "left_field": "customer_id",
        "right_table": "customers",
        "right_field": "customer_id",
        "description": "订单归属的客户，orders.customer_id关联customers.customer_id",
    },
    {
        "left_table": "orders",
        "left_field": "product_id",
        "right_table": "products",
        "right_field": "product_id",
        "description": "订单购买的商品，orders.product_id关联products.product_id",
    },
]

def physical_table_name(table:dict) -> str:
    """获取物理表名，后续生成SQL时调用"""
    return str(table.get("name") or table["id"])

def _load_generated_schema() -> None:
    """加载外部json扩展schema"""
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "database"
        / "ecommerce_ops"
        / "_schema.json"
    )
    if not schema_path.exists():
        return
    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    SCHEMA.extend(payload.get("tables") or [])
    RELATIONS.extend(payload.get("relations") or [])

_load_generated_schema()