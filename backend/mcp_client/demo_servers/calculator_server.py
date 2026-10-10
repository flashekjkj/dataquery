from __future__ import annotations
import ast
import operator

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("calculator")

# 安全计算：用ast白名单解析，只允许数字、四则运算、幂与括号，拒绝一切其他表达式
_ALLOWED_OPS = {
    ast.Add:operator.add,ast.Sub:operator.sub,
    ast.Mult:operator.mul,ast.Div:operator.truediv,
    ast.USub:operator.neg,ast.UAdd:operator.pos,ast.Pow:operator.pow,
}

def _safe_eval(expression: str) -> float:
    tree = ast.parse(expression, mode="eval")
    def _walk(node):
        if isinstance(node, ast.Expression):
            return _walk(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](_walk(node.left), _walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](_walk(node.operand))
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"不支持的表达式成分：{ast.dump(node)}")
    return _walk(tree)

@mcp.tool()
def calculate(expression: str) -> float:
    """计算四则运算表达式（支持+ - * / ** 和括号），例如 calculate("3856*0.85")"""
    return _safe_eval(expression)

if __name__ == "__main__":
    mcp.run(transport="stdio")